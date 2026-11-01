"""Opt-in frozen SkillOpt experience context for a bounded CaP-X repair.

Selection and code repair share one existing model request. No simulator access,
native grading, post-episode request, or automatic library deployment occurs here.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.util
import json
import math
from pathlib import Path

from capx.integrations.opentac_shadow_keyboard import atomic_json
from capx.integrations.opentac_shadow_skills import _PRIVATE_MARKERS


def _pinned(cfg, name):
    path = Path(cfg[name + '_path'])
    data = path.read_bytes()
    if len(data) > 262144 or hashlib.sha256(data).hexdigest() != cfg[name + '_sha256']:
        raise ValueError('SkillOpt pinned input changed: ' + name)
    return data.decode('utf-8')


def _public_check(value):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if any(marker in text.lower() for marker in _PRIVATE_MARKERS):
        raise ValueError('Nonpublic field in SkillOpt context')


def _attempt_context(manifest, failure):
    rows = [s for s in manifest['samples'] if s.get('attempt') == failure.get('attempt')
            and s['physics_step'] <= failure['physics_step']
            and s['event_seq'] < failure['error_event_seq']]
    summaries = []
    for phase in dict.fromkeys(s['phase'] for s in rows):
        group = [s for s in rows if s['phase'] == phase]
        forces = [s['estimate'].get('estimated_key_force_N') for s in group
                  if s.get('estimate', {}).get('quality') == 'ok']
        forces = [f for f in forces if isinstance(f, (int, float)) and math.isfinite(f)]
        summaries.append({'phase': phase, 'frames': len(group),
            'physics_step_range': [group[0]['physics_step'], group[-1]['physics_step']],
            'quality_counts': dict(Counter(s.get('estimate', {}).get('quality') for s in group)),
            'estimated_force_range_N': [min(forces), max(forces)] if forces else None})
    # A small view of the entire attempt complements the released error window.
    indices = {round(i * (len(rows)-1) / 11) for i in range(12)} if rows else set()
    valid = [i for i, s in enumerate(rows) if s.get('estimate', {}).get('quality') == 'ok'
             and isinstance(s['estimate'].get('estimated_key_force_N'), (int, float))]
    if valid:
        indices.update([min(valid, key=lambda i: rows[i]['estimate']['estimated_key_force_N']),
                        max(valid, key=lambda i: rows[i]['estimate']['estimated_key_force_N'])])
    samples = [[rows[i]['event_seq'], rows[i]['physics_step'], rows[i]['action_step'], rows[i]['phase'],
                rows[i].get('features_4d'), rows[i].get('estimate', {}).get('quality'),
                rows[i].get('estimate', {}).get('estimated_key_force_N'), rows[i].get('action'),
                [rows[i].get('hand', {}).get(k) for k in ('wrist_x_offset_m', 'wrist_y_m',
                     'wrist_z_m', 'index_flexion_rad')]] for i in sorted(indices)]
    # This is a necessary public force condition, not a reconstruction of native grading.
    gate = None
    request = manifest.get('request', {})
    attempt = failure.get('attempt')
    if isinstance(attempt, int) and 0 <= attempt < len(request.get('midi', [])):
        from capx.integrations.opentac_shadow_code_memory import PUBLIC_PIANO_FORCE_LIMITS
        color = 'black' if request['midi'][attempt] % 12 in (1, 3, 6, 8, 10) else 'white'
        group = color + '_' + request['dynamics'][attempt]
        target, tolerance, median = PUBLIC_PIANO_FORCE_LIMITS[group]
        good = [s['action_step'] for s in rows if s.get('estimate', {}).get('quality') == 'ok'
                and isinstance(s['estimate'].get('estimated_key_force_N'), (int, float))
                and median < s['estimate']['estimated_key_force_N'] < target + tolerance]
        longest = streak = 0
        previous = None
        for step in good:
            streak = streak + 1 if previous is not None and step == previous + 1 else 1
            longest = max(longest, streak)
            previous = step
        gate = {'group': group, 'fixed_counter_gate_N': median, 'control_target_N': target,
                'qualified_frames': len(good), 'longest_consecutive_samples': longest,
                'longest_elapsed_seconds': max(0, longest-1) * manifest['contract']['action_repeat'] /
                                           manifest['contract']['physics_hz'],
                'scope': 'Force condition only; excludes flexion/stability gates, and is not a native verdict.'}
    return {'summaries': summaries, 'samples': samples, 'force_condition_summary': gate,
            'columns': ['event_seq', 'physics_step', 'action_step', 'phase', 'features_4d',
                        'quality', 'estimated_key_force_N', 'command_xy_z_flexion', 'measured_xy_z_flexion'],
            'scope': 'Public pre-error same-attempt data only. Phase is inferred from commands. '
                     'Sparse samples do not establish continuous hold; force values are estimates.'}


def prepare_context(config, prompt_input, manifest, failure, output):
    cfg = config.get('shadow_piano_skillopt_experiment')
    if not cfg:
        return ''
    template = _pinned(cfg, 'template')
    library = _pinned(cfg, 'library')
    candidates = json.loads(_pinned(cfg, 'cards'))
    _pinned(cfg, 'editor')
    if cfg['history_enabled'] not in (True, False):
        raise ValueError('history_enabled must be boolean')
    # Compute the evidence budget against the full frozen library in BOTH arms,
    # so the no-history control does not receive extra current tactile frames.
    offered = candidates
    cards = [{k: c[k] for k in ('skill_id', 'status', 'applicable_conditions', 'avoid_when',
        'actions', 'numeric_observations', 'limitations', 'outcome_scope') if k in c} for c in offered]
    prompt_input['historical_skills'] = cards
    prompt_input['skillopt_context'] = _attempt_context(manifest, failure)
    prompt_input['skillopt_context']['history_available'] = bool(cards)
    prompt_input['skillopt_context']['indexing'] = 'Attempt index is zero based; use request.dynamics[index], never guess light/heavy.'
    # Same limit and reduction policy in both arms; retain complete current code.
    while len(json.dumps(prompt_input, ensure_ascii=False).encode()) > 24 * 1024:
        if len(prompt_input['samples']) > 2:
            prompt_input['samples'].pop(1)
        elif len(prompt_input['skillopt_context']['samples']) > 2:
            prompt_input['skillopt_context']['samples'].pop(1)
        else:
            raise ValueError('SkillOpt repair evidence exceeds 24 KiB')
    if not cfg['history_enabled']:
        cards = []
        prompt_input['historical_skills'] = []
        prompt_input['skillopt_context']['history_available'] = False
    _public_check(prompt_input)
    atomic_json(output / 'skillopt_offered.json', {'history_enabled': cfg['history_enabled'],
        'skill_ids': [c['skill_id'] for c in cards], 'frozen_library_sha256': cfg['library_sha256'],
        'current_error_event_seq': failure['error_event_seq'], 'api_calls_added': 0})
    return '\nSKILLOPT REFLECTION TEMPLATE (output extensions specified below):\n' + template + '''
This is one failure, batch_size=1. Analyze CURRENT public evidence and optional historical_skills.
Choose at most two offered skills; none is valid. For each, decide select/partial/reject and explain
whether the current program already implements it. Do not duplicate existing behavior and call it a fix.
Historical skills are candidate observations with counterexamples, not successful controllers.
The 0.311/0.473/0.615/1.040 N hold counter gates are FIXED, not removable internal heuristics.
Task constraints in the control instructions override all advisory text, including generated edits.
Force quality=ok alone does not establish continuous qualified hold. Inspect the contact period, not
only the released error window. Use request.dynamics[index] and zero-based indices accurately.
SELECTION AND REPAIR MUST BE RETURNED TOGETHER in this single request. Return the full JSON module
repair required above, including source, functions=[], mode, reason, expected_effect and skill_draft.
Additionally return skillopt={batch_size:1,failure_summary,selected_skill_ids,experience_decisions,patch}.
Each decision is {skill_id,decision,reason,already_implemented}. Use only offered IDs; select <=2.
patch uses the SkillOpt edits format, at most two edits; [] is allowed. It edits only an offline
candidate document, never task requirements or runtime settings. Cite selected IDs in skill_draft.
For an empty history, selected_skill_ids=[] and experience_decisions=[]; diagnose and repair from
the same current evidence. Do not pretend a historical match exists. Do not add another source copy.
The complete replacement source must implement your proposed remedy, not just describe it.
Selection, a text patch, or a claimed benefit is not proof of executed repair success.
'''


def save_selection(config, proposal, output):
    cfg = config.get('shadow_piano_skillopt_experiment')
    if not cfg:
        return
    selection = proposal.get('skillopt')
    result = {'schema': 'skillopt_repair_selection.v1', 'selection_valid': False,
              'selected_skill_ids': [], 'runtime_library_modified': False}
    try:
        if not isinstance(selection, dict):
            raise ValueError('Selection metadata not returned')
        offered = set(json.loads((output / 'skillopt_offered.json').read_text())['skill_ids'])
        ids = selection['selected_skill_ids']
        if not isinstance(ids, list) or len(ids) > 2 or len(set(ids)) != len(ids) or not set(ids) <= offered:
            raise ValueError('Invalid selected skill IDs')
        patch = selection.get('patch', {'edits': []})
        if not isinstance(patch.get('edits'), list) or len(patch['edits']) > 2:
            raise ValueError('Invalid SkillOpt edit budget')
        _public_check(selection)
        _pinned(cfg, 'editor')
        spec = importlib.util.spec_from_file_location('capx_skillopt_editor', cfg['editor_path'])
        editor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(editor)
        before = _pinned(cfg, 'library') if cfg['history_enabled'] else '# Empty historical skill library\n'
        updated, reports = editor.apply_patch_with_report(before, patch)
        (output / 'skillopt_candidate_library.md').write_text(updated, encoding='utf-8')
        result.update(selection_valid=True, selected_skill_ids=ids, selection=selection,
                      edit_reports=reports, source_returned=isinstance(proposal.get('source'), str))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        # Metadata failure must not discard a separately valid code repair.
        result['metadata_error'] = str(exc)
    atomic_json(output / 'skillopt_selection.json', result)
