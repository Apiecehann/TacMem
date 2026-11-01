"""Episode-boundary skill reflection from a strictly public evidence file.

This module never opens native audits, run.log, rewards or trial summaries.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import time
import subprocess
import os
import re
import ast
import dis
import sys
import uuid
from collections.abc import Mapping
import numpy as np

import yaml

from capx.integrations.opentac_shadow_keyboard import atomic_json, untraced_host_recording
from capx.llm.deadline import request_deadline
from capx.integrations.opentac_shadow_skills import (
    SCHEMA, MAX_FILE_BYTES, MAX_SKILLS, _PRIVATE_MARKERS, inject_shadow_piano_public_skills,
)

REFLECTION_TIMEOUT_SECONDS = 120


def _evidence_hash(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


class FrozenRepairTextEncoder:
    """Local, pinned E5. Loading never downloads or changes simulator packages."""
    def __init__(self, directory, *, _local=False):
        self.directory = Path(directory)
        freeze = json.loads((self.directory / 'freeze.json').read_text())
        for name, expected in freeze['files'].items():
            if Path(name).name != name or _evidence_hash((self.directory / name).read_bytes()) != expected:
                raise ValueError('Text encoder checksum mismatch')
        self.contract = {'model': freeze['model'], 'revision': freeze['revision'],
                         'files': freeze['files'], 'runtime':freeze.get('runtime'),
                         'preprocessing': 'e5_query_mask_mean_l2_512.v1'}
        self.worker_python = self.directory.parent / 'cpu-env/bin/python'
        self.local = _local or not self.worker_python.is_file()
        if not self.local:
            return
        from transformers import AutoModel, AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.directory), local_files_only=True)
        self.model = AutoModel.from_pretrained(str(self.directory), local_files_only=True).to('cpu').eval()

    def encode(self, text):
        return self.encode_many([text])[0]

    def encode_many(self, texts):
        if not self.local:
            command = ('import json,sys; from capx.integrations.opentac_shadow_online_skills import FrozenRepairTextEncoder; '
                'x=json.load(sys.stdin); e=FrozenRepairTextEncoder(x["directory"],_local=True); '
                'print("CAPX_TEXT_VECTORS "+json.dumps(e.encode_many(x["texts"])))')
            env = dict(os.environ)
            for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
                env.pop(key,None)
            env.update(CUDA_VISIBLE_DEVICES='',TOKENIZERS_PARALLELISM='false',OMP_NUM_THREADS='2',
                       PYTHONPATH=str(Path(__file__).resolve().parents[2]))
            proc = subprocess.run([str(self.worker_python),'-c',command],
                input=json.dumps({'directory':str(self.directory),'texts':texts}),capture_output=True,
                text=True,env=env,timeout=120)
            lines = [line[18:] for line in proc.stdout.splitlines() if line.startswith('CAPX_TEXT_VECTORS ')]
            if proc.returncode or not lines:
                raise RuntimeError('CPU text encoder worker failed: '+proc.stderr[-500:])
            return json.loads(lines[-1])
        import torch
        batch = self.tokenizer(['query: ' + text for text in texts], max_length=512, padding=True,
                               truncation=True, return_tensors='pt')
        with torch.inference_mode():
            values = self.model(**batch).last_hidden_state
            mask = batch['attention_mask'].unsqueeze(-1)
            vector = (values * mask).sum(1) / mask.sum(1)
            return torch.nn.functional.normalize(vector, p=2, dim=1).cpu().tolist()


REPAIR_LANGUAGE_TEMPLATE = 'public_error_template.v2'


def _repair_language_context(request, failure, parameters):
    """Same pre-repair situation schema for queries and historical search documents."""
    def number(value):
        if type(value) not in (int,float) or not np.isfinite(value):
            return None
        return int(value) if float(value).is_integer() else value

    def identifier(value):
        if not isinstance(value,str): return 'unknown'
        value = value.strip()
        return value if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9.]{0,119}',value) else 'unknown'

    def sequence(value, convert):
        return [convert(v) for v in value] if isinstance(value,(list,tuple)) else None

    def note(value):
        from capx.integrations.opentac_shadow_vision import parse_note
        try:
            midi = parse_note(value.strip() if isinstance(value,str) else value)[0]
            return int(midi) if 21 <= midi <= 108 else None
        except (TypeError,ValueError):
            return None

    def dynamic(value):
        value = value.strip().lower() if isinstance(value,str) else None
        return value if value in ('light','heavy') else None

    control_names = {'x','y','z','flex','flexion','wrist_x_offset_m','wrist_y_m','wrist_z_m',
        'index_flexion_rad','hover_z_m','probe_trim_m','release_steps','press_steps','hold_steps',
        'approach_action_budget','press_action_budget','release_action_budget',
        'press_duration_s','curl_duration_s','release_duration_s','lift_duration_s',
        'release_seconds','hold_seconds','steps','duration','elapsed'}
    containers = {'execution_parameters','call_arguments','white_light','white_heavy','black_light','black_heavy'}
    def controls(value):
        if not isinstance(value,dict): return {}
        result = {}
        for key,item in sorted(value.items(),key=lambda pair:str(pair[0])):
            if not isinstance(key,str): continue
            key = key.strip().replace('/','_')
            if key in containers:
                result[key] = controls(item)
            elif key in control_names:
                result[key] = number(item)
            elif key == 'last_action_target':
                result[key] = sequence(item,number) if isinstance(item,(list,tuple)) and len(item)==4 else None
        return result
    phase = identifier(failure.get('phase')).lower()
    phase = phase if phase in ('approach','press','hold','release','recovery') else 'unknown'
    return {'task': 'shadow_piano', 'request': {
                'notes':sequence(request.get('notes'),note),
                'dynamics':sequence(request.get('dynamics'),dynamic),
                'hold_seconds':sequence(request.get('hold_seconds'),number)},
            'error':identifier(failure.get('kind')),'check':identifier(failure.get('check_id')),
            'api':identifier(failure.get('api')),'call':identifier(failure.get('function_name')),
            'role':identifier(failure.get('call_role')), 'phase':phase,
            'control_parameters':controls(parameters)}


def make_repair_language_key(request, failure, parameters):
    """Freeze once at the error. Store this exact key with the resulting skill."""
    context = _repair_language_context(request,failure,parameters)
    text = json.dumps(context,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    return {'template':REPAIR_LANGUAGE_TEMPLATE,'basis':'pre_repair_error','context':context,
            'context_sha256':_evidence_hash(context),'text':text,'text_sha256':_evidence_hash(text.encode())}


def repair_language_key(request, failure, parameters):
    return make_repair_language_key(request,failure,parameters)['text']


def _validated_repair_language_text(key):
    """Never substitute repair advice, after-values or unversioned prose for an error key."""
    if not isinstance(key,dict) or key.get('template') != REPAIR_LANGUAGE_TEMPLATE:
        raise ValueError('language_template_mismatch')
    if key.get('basis') != 'pre_repair_error':
        raise ValueError('language_basis_mismatch')
    context = key.get('context',{})
    if not isinstance(context,dict) or not isinstance(context.get('request'),dict):
        raise ValueError('invalid_language_context')
    expected = make_repair_language_key(context['request'],{
        'kind':context.get('error'),'check_id':context.get('check'),'api':context.get('api'),
        'function_name':context.get('call'),'call_role':context.get('role'),'phase':context.get('phase')},
        context.get('control_parameters'))
    if any(key.get(k) != expected[k] for k in expected):
        raise ValueError('language_context_or_checksum_mismatch')
    return expected['text']


def causal_error_rows(samples, failure):
    """Event order disambiguates samples taken later at the very same physics step."""
    return [s for s in samples if s['physics_step'] <= failure['physics_step'] and
            s.get('event_seq', float('inf')) < failure['error_event_seq']]


def error_window_key(evidence, failure, representation='window4'):
    rows = causal_error_rows(evidence.frames, failure)
    base = {'vector': None, 'quality': 'missing_history', 'contract': None, 'sources': [],
            'error_event_seq': failure['error_event_seq'], 'sample_age_seconds': None}
    if not rows:
        return base
    last = rows[-1]
    age = (failure['physics_step'] - last['physics_step']) / evidence.contract['physics_hz']
    base.update(sample_age_seconds=age, source_call_ids=last['invocation_ids'],
                episode_id=evidence.episode_id)
    encoder = next((e for e in evidence.encoders if e.contract.get('representation') == representation), None)
    if encoder is None:
        return {**base, 'quality': 'encoder_unavailable'}
    spec = encoder.contract.get('window', {})
    if age > spec.get('max_age_seconds', 1 / 30) + 1e-9:
        return {**base, 'quality': 'stale_history'}
    # The encoder performs the frozen causal 30 Hz selection. Never interpolate.
    rows = [s for s in rows if s['timestamp_s'] >= last['timestamp_s'] - .5 - spec.get('max_age_seconds', 1/30)]
    try:
        window = evidence.window(rows)
        value = encode_tactile_window(window, encoder)
        base['sources'] = (value.get('provenance') or {}).get('samples', [])
        selected = {s['sample_id'] for s in base['sources']}
        evidence._error_samples.update({s['physics_step']: s for s in rows if s['sample_id'] in selected})
        return {**base, **value, 'contract': {'sensor': evidence.contract['sensor'],
                'physics_hz': evidence.contract['physics_hz'], 'encoder': dict(encoder.contract)}}
    except (ValueError, KeyError, OSError, TypeError) as exc:
        return {**base, 'quality': 'invalid_history', 'error_type': type(exc).__name__}


def _public_skill_payload(value):
    def inspect(node):
        if isinstance(node,dict):
            if set(node) & {'key_forces','native_info','native_success','native_failure','reward','score','success'}:
                raise ValueError('Private verdict fields are not allowed in repair skills')
            for child in node.values(): inspect(child)
        elif isinstance(node,list):
            for child in node: inspect(child)
    inspect(value)
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if any(word in text.lower() for word in _PRIVATE_MARKERS):
        raise ValueError('Private fields are not allowed in repair skills')
    if len(text.encode()) > 128 * 1024:
        raise ValueError('Skill is too large; use evidence attachments')
    return value


class RepairSkillStore:
    """Independent versioned documents. Code paths are provenance, not dependencies."""
    def __init__(self, root):
        self.root = Path(root) / 'repair_skills'
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, skill):
        skill = _public_skill_payload(dict(skill))
        required = {'skill_id', 'version', 'status', 'applicable_conditions', 'failure_context',
                    'numeric_correction', 'repair_advice', 'public_validation', 'provenance'}
        if not required <= skill.keys() or not re.fullmatch(r'skill_[0-9a-f]{64}', skill['skill_id']):
            raise ValueError('Invalid repair skill schema')
        if skill['status'] not in ('proposed', 'rejected', 'unexecuted', 'executed_failed', 'executed_unverified', 'local_verified'):
            raise ValueError('Invalid repair skill status')
        directory = self.root / skill['skill_id']
        directory.mkdir(exist_ok=True)
        skill['updated_at_ns'] = time.time_ns()
        # Eligibility follows an accepted code change, not its validation outcome.
        # This timestamp belongs to this exact revision (including its results).
        skill['available_at_ns'] = skill['updated_at_ns']
        identifier = _evidence_hash(skill)
        atomic_json(directory / (identifier + '.json'), skill)
        # Search metadata is a view of this exact revision. No advice, after-values,
        # source snippets or validation details need loading before ranking.
        search = {k:skill[k] for k in ('skill_id','status','applicable_conditions','keys') if k in skill}
        search.update(correction_id=skill.get('correction_id',skill['skill_id']),
            verified_at_ns=skill.get('verified_at_ns'),revoked=skill.get('revoked',False),
            has_code_change=skill.get('has_code_change',False),available_at_ns=skill['available_at_ns'],
            provenance={'episode_id':skill['provenance']['episode_id']},content_revision=identifier)
        search_revision = _evidence_hash(search)
        atomic_json(directory / ('search_'+search_revision+'.json'),search)
        atomic_json(directory / 'head.json', {'revision': identifier, 'search_revision':search_revision})
        return skill

    def load(self, skill_id, revision=None):
        if not isinstance(skill_id,str) or not re.fullmatch(r'skill_[0-9a-f]{64}',skill_id):
            raise ValueError('Invalid skill ID')
        directory = self.root/skill_id
        revision = revision or json.loads((directory/'head.json').read_text())['revision']
        if not isinstance(revision,str) or not re.fullmatch('[0-9a-f]{64}',revision):
            raise ValueError('Invalid skill revision')
        row = json.loads((directory/(revision+'.json')).read_text())
        if _evidence_hash(row) != revision or row.get('skill_id') != skill_id:
            raise ValueError('Skill checksum mismatch')
        return _public_skill_payload(row)

    def search_records(self):
        """Snapshot only the search view; do not load complete skill bodies."""
        result = []
        for head in sorted(self.root.glob('skill_*/head.json')):
            try:
                pointer = json.loads(head.read_text())
                revision = pointer['search_revision']
                if not re.fullmatch('[0-9a-f]{64}',revision): continue
                row = json.loads((head.parent/('search_'+revision+'.json')).read_text())
                if (_evidence_hash(row)==revision and row['content_revision']==pointer['revision']
                        and row['skill_id']==head.parent.name):
                    result.append(_public_skill_payload(row))
            except (OSError,ValueError,KeyError,TypeError):
                # Preserve older records; do not invent keys or promote them during lookup.
                continue
        return result

    def records(self):
        records = []
        for head in sorted(self.root.glob('skill_*/head.json')):
            try:
                revision = json.loads(head.read_text())['revision']
                if not re.fullmatch('[0-9a-f]{64}', revision):
                    continue
                row = json.loads((head.parent / (revision + '.json')).read_text())
                if _evidence_hash(row) == revision:
                    records.append(_public_skill_payload(row))
            except (OSError, ValueError, KeyError):
                continue
        return records

    def append_usage(self, row):
        row = _public_skill_payload(row)
        with (self.root / 'usage.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, allow_nan=False) + '\n')

    def verified_view(self):
        return [{k: row[k] for k in ('skill_id', 'version', 'repair_advice', 'numeric_correction',
                'applicable_conditions', 'public_validation', 'provenance')}
                for row in self.records() if row['status'] == 'local_verified' and not row.get('revoked')]

    def revoke(self, skill_id, counterexample):
        row = next(r for r in self.records() if r['skill_id'] == skill_id)
        row.setdefault('counterexamples', []).append(_public_skill_payload(counterexample))
        row['revoked'] = True
        return self.put(row)


def retrieve_repair_skills(records, *, episode_id, cutoff_ns, compatibility, language, tactile,
                          mode='joint', encoder=None, context=None, load_skill=None):
    """Rank accepted code-change experiences; outcomes are context, not a gate."""
    if mode not in ('language', 'tactile', 'joint'):
        raise ValueError('Unknown repair retrieval mode')
    context = context or {}
    def conflicts(row):
        conditions = row['applicable_conditions']
        known = {**conditions.get('public_conditions',{}), 'phase':conditions.get('phase','unknown')}
        return any(k in context and context[k] != 'unknown' and v != 'unknown' and context[k] != v
                   for k,v in known.items() if isinstance(v,(str,int,float,bool)))
    eligible = [r for r in records if r.get('has_code_change') is True and not r.get('revoked')
        and isinstance(r.get('available_at_ns'),int) and r['available_at_ns'] < cutoff_ns
        and r['provenance']['episode_id'] != episode_id
        and r['applicable_conditions']['compatibility'] == compatibility and not conflicts(r)]
    result = {'matches': [], 'mode': mode, 'fallback': None, 'miss_reason': None,
              'language_quality': 'not_requested', 'tactile_quality': tactile['quality'],
              'rrf_c': 60, 'candidate_ids': [r['skill_id'] for r in eligible],
              'language_template':REPAIR_LANGUAGE_TEMPLATE,'language_skipped':[]}
    if not eligible:
        result['miss_reason'] = 'no_compatible_code_change_experience'
        return result
    rankings = {}
    if mode in ('language', 'joint'):
        try:
            query_text = _validated_repair_language_text(language)
            passages = []
            for row in eligible:
                for key in row['keys'].get('language_history',[row['keys'].get('language')]):
                    try:
                        passages.append((row,_validated_repair_language_text(key)))
                    except (ValueError,TypeError,KeyError) as exc:
                        result['language_skipped'].append({'skill_id':row['skill_id'],'reason':str(exc)})
            if not passages:
                result['language_quality'] = 'no_compatible_error_key'
            elif encoder is None:
                result['language_quality'] = 'encoder_unavailable'
            else:
                result['language_quality'] = 'ready'
        except (ValueError,TypeError,KeyError) as exc:
            result['language_quality'] = 'invalid_query_key'
            result['language_error'] = str(exc)
    if result['language_quality'] == 'ready':
        try:
            texts = [query_text] + [text for row,text in passages]
            vectors = encoder.encode_many(texts) if hasattr(encoder,'encode_many') else [encoder.encode(t) for t in texts]
            q = np.asarray(vectors[0], float)
            scores = []
            for (row,key), encoded in zip(passages,vectors[1:]):
                # Re-encoding is explicit and uses the same frozen encoder for both sides.
                v = np.asarray(encoded, float)
                if q.shape != v.shape or not np.isfinite(v).all() or not np.isfinite(q).all():
                    continue
                norm = np.linalg.norm(q)*np.linalg.norm(v)
                if norm > 0:
                    scores.append((float(np.dot(q,v)/norm), row))
            rankings['language'] = sorted(scores, key=lambda x: (-x[0], x[1]['skill_id']))
            result['language_quality'] = 'ok'
            result['text_encoder_contract'] = dict(encoder.contract)
        except Exception as exc:
            result['language_quality'] = 'encoder_unavailable'
            result['language_error'] = type(exc).__name__
    if mode in ('tactile', 'joint') and tactile['quality'] == 'ok':
        scores = []
        q = np.asarray(tactile['vector'], float)
        for row in eligible:
            for key in row['keys'].get('tactile', []):
                if key['quality'] != 'ok' or key['contract'] != tactile['contract']:
                    continue
                v = np.asarray(key['vector'], float)
                if v.shape == q.shape and np.isfinite(v).all() and np.isfinite(q).all():
                    scores.append((-float(np.sqrt(np.mean((v-q)**2))), row))
        rankings['tactile'] = sorted(scores, key=lambda x: (-x[0], x[1]['skill_id']))
    if mode == 'joint' and 'tactile' not in rankings:
        result['fallback'] = 'language_only:tactile_' + tactile['quality']
    if mode == 'joint' and 'language' not in rankings:
        result['fallback'] = 'tactile_only:' + ('text_encoder_unavailable' if
            result['language_quality']=='encoder_unavailable' else result['language_quality'])
    fused, channels = {}, {}
    for channel, scores in rankings.items():
        seen = set()
        for score, row in scores:
            identity = row['correction_id']
            if identity in seen:
                continue
            seen.add(identity)
            rank = len(seen)
            if rank > 5:
                break
            fused.setdefault(identity, {'skill': row, 'score': 0., 'ranks': {}})
            fused[identity]['score'] += 1 / (60 + rank) if mode == 'joint' else score
            fused[identity]['ranks'][channel] = {'rank': rank, 'score': score}
        channels[channel] = len(seen)
    selected = []
    for match in sorted(fused.values(), key=lambda x: (-x['score'], x['skill']['skill_id'])):
        if load_skill is not None:
            try:
                search = match['skill']
                match = {**match,'skill':load_skill(search['skill_id'],search['content_revision'])}
            except (OSError,ValueError,KeyError,TypeError) as exc:
                result.setdefault('content_unavailable',[]).append({'skill_id':search['skill_id'],'reason':type(exc).__name__})
                continue
        selected.append(match)
        if len(selected)==2: break
    result['matches'] = selected
    result['miss_reason'] = None if selected else 'no_compatible_code_change_experience'
    return result


def validate_skill_draft(draft, *, failure, request, source_before, source_after, samples, calls=(), manifest=None):
    """Normalize advice without making numeric provenance an admission requirement."""
    if not isinstance(draft, dict):
        raise ValueError('Missing skill draft')
    _public_skill_payload(draft)
    draft = json.loads(json.dumps(draft))  # Keep the raw LLM proposal intact for audit.
    warnings = []
    advice = draft.get('repair_advice')
    if not isinstance(advice, str) or not 1 <= len(advice) <= 2400:
        raise ValueError('Missing bounded repair advice')
    conditions = draft.get('applicable_conditions', {})
    if isinstance(conditions, str):
        conditions = {'description': conditions}
        warnings.append('applicable_conditions_text_normalized')
    if not isinstance(conditions, dict):
        raise ValueError('Invalid applicable conditions')
    corrections = draft.get('numeric_correction', [])
    if not isinstance(corrections, list) or len(corrections) > 8:
        raise ValueError('Invalid numeric corrections')
    frozen_words = ('force_target', 'threshold', 'minimum_hold', 'release_requirement', 'estimator', 'hard_bound')
    fixed_parameters = {'hold_seconds', 'requested_hold_seconds', 'piano_hold_seconds',
                        'piano_require_dynamics', 'action_repeat', 'range_force', 'max_deformation'}
    for c in corrections:
        if not isinstance(c, dict) or not {'parameter', 'before', 'unit', 'limitations'} <= c.keys() or not ('after' in c or 'rule' in c):
            raise ValueError('Numeric correction needs parameter, before, after/rule, unit and limitations')
        if any(not isinstance(c[k],str) or not c[k] for k in ('parameter','unit','limitations')):
            raise ValueError('Numeric parameter, unit and limitations must be nonempty text')
        units = {'wrist_x_offset_m':'m','wrist_y_m':'m','wrist_z_m':'m','index_flexion_rad':'rad'}
        if c['parameter'] in units and c['unit'] != units[c['parameter']]:
            raise ValueError('Numeric correction has an incompatible unit')
        if c['parameter'].lower() in fixed_parameters or any(w in c['parameter'].lower() for w in frozen_words):
            raise ValueError('Skill cannot override fixed requirements')
        # Keep reported settings as advice, not measurements or host attestations.
        # No AST/default/variable binding is needed to save or retrieve a change.
        for key in ('after_source', 'before_source', 'unverified_before_claim',
                    'before_quality', 'before_calibration_version'):
            c.pop(key, None)
        c['before_status'] = 'reported' if c['before'] is not None else 'unknown'
        c['after_status'] = 'proposed'
        c['tested_conditions'] = []
    snippets = draft.get('code_snippets', [])
    valid_snippets = [s for s in snippets if isinstance(s, str) and s.strip() and
                      len(s) <= 4000 and s.strip() in source_after][:2] if isinstance(snippets, list) else []
    if valid_snippets != snippets:
        warnings.append('unsupported_optional_snippets_omitted')
    return {'repair_advice': advice, 'applicable_conditions': conditions,
            'numeric_correction': corrections, 'code_snippets': valid_snippets, 'normalization_warnings': warnings,
            'cited_skill_ids': [s for s in draft.get('cited_skill_ids', []) if isinstance(s, str)]}


class _UnavailableTrajectoryEncoder:
    def __init__(self, config, method, error):
        self.contract = {'version': 'unavailable:' + _evidence_hash(dict(config)),
            'representation': method, 'preprocessing': 'unavailable', 'modalities': ['depth_m']}
        self.error_type = type(error).__name__

    def encode(self, window):
        return {'vector': None, 'quality': 'encoder_unavailable', 'error_type': self.error_type}


def trajectory_encoders(config):
    """Resolve an opt-in YAML configuration, or retain the existing plugin API."""
    if config is None:
        return []
    if not isinstance(config, Mapping):
        return list(config) if isinstance(config, (list, tuple)) else [config]
    methods = config.get('representations', ['window4'])
    if not isinstance(methods, (list, tuple)) or not methods or len(set(methods)) != len(methods):
        raise ValueError('Specify distinct trajectory representations')
    encoders = []
    for method in methods:
        try:
            from capx.memory.trajectory.model import EvidenceTrajectoryEncoder
            encoders.append(EvidenceTrajectoryEncoder(config['run_dir'], method))
        except Exception as exc:
            # Optional retrieval cannot disable the existing 5D controller.
            encoders.append(_UnavailableTrajectoryEncoder(config, method, exc))
    return encoders


def _temporal_contract(contract, encoder):
    return {**contract, 'encoder': dict(encoder.contract),
            'window': encoder.contract.get('window', 'commanded_phase_segment.v1')}


class TactileEvidenceIndex:
    """Append-only public provenance; representations never replace their source clips."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'associations.jsonl'

    @untraced_host_recording
    def append(self, kind, payload, links=()):
        record = {'kind': kind, 'payload': payload, 'links': list(links)}
        record['id'] = kind + ':' + _evidence_hash(record)
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, allow_nan=False) + '\n')
            stream.flush()
        return record['id']

    def records(self):
        if not self.path.exists():
            return []
        # A truncated final append is not a valid node, never guess its contents.
        result = []
        for line in self.path.read_text(encoding='utf-8').splitlines():
            try:
                row = json.loads(line)
                content = {k: row[k] for k in ('kind', 'payload', 'links')}
                if row['id'] == row['kind'] + ':' + _evidence_hash(content):
                    result.append(row)
            except (ValueError, KeyError, TypeError):
                continue
        return result

    def related(self, identifier):
        return [r for r in self.records() if r['id'] == identifier or identifier in r['links']]

    def resolve_skill_evidence(self, clip_id, *, records=None):
        """Follow actual clip -> invocation -> function; never infer a skill label."""
        records = self.records() if records is None else records
        clips = [r['payload'] for r in records if r['kind'] == 'clip' and r['payload']['clip_id'] == clip_id]
        if not clips:
            return None
        clip = clips[-1]
        calls = [r['payload'] for r in records if r['kind'] == 'invocation'
                 and r['payload']['invocation_id'] in clip['invocation_ids']]
        functions = {}
        for row in records:
            f = row['payload']
            if row['kind'] == 'function' and any(i['function_id'] == f['function_id'] for i in calls):
                if _evidence_hash(f['source'].encode()) == f['source_hash']:
                    functions[f['function_id']] = {k: f[k] for k in
                        ('function_id', 'name', 'source', 'source_hash', 'ast_hash')}
        return {'clip_id': clip_id, 'episode_id': clip['episode_id'], 'manifest': clip['manifest'],
                'invocations': [{k: i[k] for k in ('invocation_id', 'function_id', 'start', 'end', 'outcome', 'exceptions')}
                                for i in calls if i['function_id'] in functions],
                'functions': list(functions.values())}

    def put_representation(self, clip_id, kind, vector, contract, quality, physics_step, *, provenance=None):
        if kind not in ('instantaneous_5d', 'temporal'):
            raise ValueError('Unknown tactile representation type')
        if vector is not None:
            value = np.asarray(vector, float)
            if value.ndim != 1 or not value.size or not np.isfinite(value).all():
                raise ValueError('Invalid tactile representation')
            if kind == 'instantaneous_5d' and value.size != 5:
                raise ValueError('The existing instantaneous representation stays 5D')
            vector = value.tolist()
        return self.append('representation', {'clip_id': clip_id, 'type': kind,
            'vector': vector, 'contract': contract, 'quality': quality,
            'physics_step': int(physics_step), 'provenance': provenance}, [clip_id])

    def retrieve(self, kind, vector, contract, *, candidates=None, scales=None, limit=3):
        query = np.asarray(vector, float)
        if query.ndim != 1 or not np.isfinite(query).all():
            return []
        scale = np.ones(query.size) if scales is None else np.asarray(scales, float)
        if scale.shape != query.shape or not np.isfinite(scale).all() or np.any(scale <= 0):
            raise ValueError('Invalid representation distance scales')
        found = []
        for row in self.records() if candidates is None else candidates:
            p = row['payload']
            if row['kind'] != 'representation' or p['type'] != kind or p['contract'] != contract:
                continue
            if p['quality'] != 'ok' or p['vector'] is None:
                continue
            value = np.asarray(p['vector'], float)
            if value.shape != query.shape:
                continue
            delta = (value - query) / scale
            found.append({'representation_id': row['id'], 'clip_id': p['clip_id'],
                          'distance': float(np.sqrt(np.mean(delta * delta)))})
        return sorted(found, key=lambda r: (r['distance'], r['representation_id']))[:limit]


def encode_tactile_window(window, encoder=None):
    """Optional parallel encoder. Inputs are public; sampling remains causal.

    Host plugins implement encode(window) and contract={version, preprocessing,
    modalities, ...}. A window contains frames, timestamps_s, physics_steps,
    valid_masks and instantaneous_5d. Missing channels remain None. The learned
    representation has no fixed dimensionality and does not feed the force model.
    """
    if encoder is None:
        return {'vector': None, 'quality': 'not_configured', 'contract': None}
    contract = None
    try:
        steps = window['physics_steps']
        if not steps or any(b <= a for a, b in zip(steps, steps[1:])):
            raise ValueError('Window timestamps must be strictly increasing')
        if steps[-1] > window['current_physics_step']:
            raise ValueError('Future samples are not a causal query')
        channels = ('timestamps_s', 'instantaneous_5d') + (('features_4d', 'valid_features_4d')
            if 'features_4d' in window else ('frames', 'valid_masks'))
        if any(len(window[k]) != len(steps) for k in channels):
            raise ValueError('Window channels are not aligned')
        times = window['timestamps_s']
        if not np.isfinite(times).all() or any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError('Invalid window time order')
        contract = dict(encoder.contract)
        if not all(contract.get(k) for k in ('version', 'preprocessing', 'modalities')):
            raise ValueError('Encoder needs version, preprocessing and modalities')
        result = encoder.encode(window)
        value = result.get('vector')
        if not isinstance(result.get('quality'), str):
            raise ValueError('Encoder quality must be explicit')
        if result.get('quality') != 'ok':
            return {'vector': None, 'quality': result.get('quality', 'unavailable'), 'contract': contract,
                    'provenance': result.get('provenance'), 'error_type': result.get('error_type')}
        value = np.asarray(value, float)
        if value.ndim != 1 or not value.size or not np.isfinite(value).all():
            raise ValueError('Encoder returned invalid representation')
        return {'vector': value.tolist(), 'quality': 'ok', 'contract': contract,
                'provenance': result.get('provenance')}
    except Exception as exc:
        return {'vector': None, 'quality': 'encoder_error', 'contract': contract,
                'error_type': type(exc).__name__}


class PublicTactileEvidence:
    """Host recorder, separate from force estimation and private evaluation.

    Numeric tactile windows stay in RAM; only queried error windows become
    skill attachments. The final compact manifest is written
    at episode end, never in the per-action path.
    Phase names describe commanded motion, not native scoring stages.
    """

    def __init__(self, root, run_dir, contract, request, *, encoder=None, retrieve_history=True,
                 failure_triggered=True, error_windows_only=False, retain_full_numeric_evidence=False):
        self.index = TactileEvidenceIndex(root)
        self.failure_triggered = bool(failure_triggered)
        self.error_windows_only = bool(error_windows_only)
        self.retain_full_numeric_evidence = bool(retain_full_numeric_evidence)
        self.encoders = trajectory_encoders(encoder)
        self.history = self.index.records() if retrieve_history and not self.error_windows_only else []
        self.episode_id = Path(run_dir).name + '_' + uuid.uuid4().hex
        self.directory = self.index.root / 'episodes' / self.episode_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.contract, self.request, self.encoder = contract, request, encoder
        self.frames, self.clips, self.invocations, self.functions = [], [], [], {}
        self.events, self.retrievals, self.active_calls = [], [], []
        self.sequence = 0
        self.execution_id = None
        self.executions = []
        self._text_buffer = ''
        self.current_clip = None
        self._error_samples = {}
        self._last_flushed_signature = None
        self.last_step = None
        self.last_action = None
        self.attempt = None
        self.releasing = False
        self.clock = {'action_step': 0, 'physics_step': 0}
        self.manifest_path = self.directory / 'manifest.json'
        self.index.append('episode', {'episode_id': self.episode_id, 'request': request,
            'contract': contract, 'manifest': str(self.manifest_path)})
        atomic_json(self.run_dir / 'piano_tactile_evidence.json', {
            'schema': 'public_tactile_evidence.v1', 'root': str(self.index.root),
            'episode_id': self.episode_id, 'manifest': str(self.manifest_path)})

    def stamp(self):
        self.sequence += 1
        return {'event_seq': self.sequence, 'execution_id': self.execution_id, **self.clock}

    def write(self, text):
        """Observe public stop markers at their call site, before the stack unwinds."""
        from capx.integrations.opentac_shadow_code_memory import _public_note_control_failure, _public_note_report
        self._text_buffer += text
        while '\n' in self._text_buffer:
            line, self._text_buffer = self._text_buffer.split('\n', 1)
            if line.startswith(('PIANO_STOP ', 'CAPX_FAILURE ')) and not any(m in line.lower() for m in _PRIVATE_MARKERS):
                self.event('public_stop', message=line[:1200])
            elif line.startswith('PIANO_NOTE '):
                report = _public_note_report(line,self.request)
                if report is not None:
                    self.event('public_note_report', report=report)
                failure = _public_note_control_failure(line, self.request, self.clock['action_step'])
                if failure:
                    canonical='PIANO_NOTE '+json.dumps(report)
                    self.event('public_stop', message=canonical, control_failure=failure)
                elif report is None:
                    self.event('public_stop', message='PIANO_STOP invalid_public_note_report',
                               control_failure={'reason':'invalid_public_note_report'})
        self._text_buffer = self._text_buffer[-1200:]
        return len(text)

    def _phase(self, attempt, action):
        if attempt != self.attempt:
            self.releasing, self.last_action = False, None
            self.attempt = attempt
        previous = self.last_action
        self.last_action = list(action)
        if previous is not None and (action[3] < previous[3] - 1.e-5 or
                (previous[3] > .05 and action[2] > previous[2] + .00005)):
            self.releasing = True
        if self.releasing:
            return 'release'
        if action[3] <= .05:
            return 'approach'
        if previous is not None and abs(action[3] - previous[3]) <= 1.e-5 and abs(action[2] - previous[2]) <= .00005:
            return 'hold'
        return 'press'

    @untraced_host_recording
    def sample(self, *, action_step, physics_step, attempt, note, dynamics, action,
               hand, estimate, features, scales, features_4d=None, valid_depth_fraction=None):
        self.instantaneous_scales = list(scales)
        if self.last_step == physics_step:
            return  # Repeated queries do not create new physical observations.
        if self.last_step is not None and physics_step < self.last_step:
            raise ValueError('Evidence physical time went backwards')
        self.last_step = int(physics_step)
        self.clock = {'action_step': int(action_step), 'physics_step': int(physics_step)}
        phase = self._phase(attempt, action)
        if self.current_clip is None or (self.current_clip['attempt'], self.current_clip['phase']) != (attempt, phase):
            self._finish_clip()
            self.current_clip = {'clip_id': 'clip:' + _evidence_hash([self.episode_id, attempt, phase, physics_step]),
                'attempt': attempt, 'note': note, 'dynamics': dynamics, 'phase': phase,
                'phase_basis': 'commanded_motion_not_native_stage', 'samples': []}
        vector = None
        if features is not None:
            value = np.asarray(features, float)
            if value.shape == (5,) and np.isfinite(value).all():
                vector = value.tolist()
        feature4 = features_4d if features_4d is not None else (vector[:4] if vector else None)
        feature4 = np.asarray(feature4, float) if feature4 is not None else None
        good4 = bool(feature4 is not None and feature4.shape == (4,) and np.isfinite(feature4).all()
                     and valid_depth_fraction is not None and valid_depth_fraction >= .60)
        public_hand = {k: hand.get(k) for k in ('index_flexion_rad', 'wrist_x_offset_m', 'wrist_y_m', 'wrist_z_m')}
        force = estimate.get('estimated_key_force_N')
        if force is not None and not np.isfinite(force):
            force = None
        row = {'sample_id': 'sample:' + _evidence_hash([self.episode_id, physics_step]),
            **self.stamp(), 'timestamp_s': physics_step / self.contract['physics_hz'],
            'clip_id': self.current_clip['clip_id'], 'attempt': attempt, 'phase': phase,
            'action': list(action), 'hand': public_hand, 'instantaneous_5d': vector,
            'estimate': {'estimated_key_force_N': force, 'quality': estimate.get('quality', 'unavailable'),
                         'calibration_version': estimate.get('calibration_version')},
            'features_4d': feature4.tolist() if good4 else None, 'valid_features_4d': good4,
            'invocation_ids': list(self.active_calls)}
        self.frames.append(row)
        self.current_clip['samples'].append(row)
        if self.error_windows_only:
            self._record_public_force_checks(row, note, dynamics)

    def _representation_contract(self, note, dynamics, phase):
        from capx.integrations.opentac_shadow_vision import parse_note
        return {**self.contract, 'feature_schema': 'instantaneous_5d.v1',
                'color': 'black' if parse_note(note)[1] else 'white', 'dynamics': dynamics, 'phase': phase}

    def _record_public_force_checks(self, row, note, dynamics):
        """Audit existing public sampled-control criteria; never native force/score."""
        from capx.integrations.opentac_shadow_code_memory import PUBLIC_PIANO_FORCE_LIMITS
        from capx.integrations.opentac_shadow_vision import parse_note
        quality = row['estimate']['quality']
        force = row['estimate']['estimated_key_force_N']
        available = quality=='ok' and force is not None and np.isfinite(force)
        previous = getattr(self,'_public_hold_sample',None)
        if available and (previous is None or previous['estimate']['quality']!='ok'):
            self.event('public_check',check_id='tactile_available',index=row['attempt'],passed=True,
                       measurements={'sample_id':row['sample_id'],'calibration_version':row['estimate']['calibration_version']})
        color = 'black' if parse_note(note)[1] else 'white'
        target,tolerance,gate = PUBLIC_PIANO_FORCE_LIMITS[color+'_'+dynamics]
        flex = row['hand'].get('index_flexion_rad')
        good = bool(available and gate <= force <= target+tolerance and flex is not None and flex > .02)
        contiguous = (previous is not None and previous['attempt']==row['attempt'] and
                      row['physics_step']-previous['physics_step']==self.contract['action_repeat'])
        if not good:
            self._public_hold_since = None
            self._public_hold_emitted = False
        elif not contiguous or getattr(self,'_public_hold_since',None) is None:
            self._public_hold_since = row['physics_step']
            self._public_hold_emitted = False
        elif not self._public_hold_emitted:
            elapsed = (row['physics_step']-self._public_hold_since)/self.contract['physics_hz']
            if elapsed+1e-10 >= self.request['hold_seconds'][row['attempt']]:
                self.event('public_check',check_id='public_hold',index=row['attempt'],passed=True,
                    measurements={'first_physics_step':self._public_hold_since,'last_physics_step':row['physics_step'],
                                  'sampled_seconds':elapsed,'calibration_version':row['estimate']['calibration_version']})
                self._public_hold_emitted = True
        self._public_hold_sample = row

    def window(self, samples):
        # Numeric observations only. No image buffer, NPZ, or disk read.
        return {'episode_id': self.episode_id, 'sensor_sha256': self.contract['sensor'],
            'physics_steps': [s['physics_step'] for s in samples],
            'timestamps_s': [s['timestamp_s'] for s in samples],
            'features_4d': [s['features_4d'] for s in samples],
            'valid_features_4d': [s['valid_features_4d'] for s in samples],
            'instantaneous_5d': [s['instantaneous_5d'] for s in samples],
            'sources': [{k: s[k] for k in ('sample_id', 'physics_step', 'event_seq',
                'timestamp_s', 'features_4d', 'valid_features_4d')} for s in samples],
            'current_physics_step': samples[-1]['physics_step']}

    def _finish_clip(self):
        clip = self.current_clip
        if not clip or not clip['samples']:
            return
        samples = clip.pop('samples')
        clip.pop('queried', None)
        clip.update(sample_ids=[r['sample_id'] for r in samples],
            start_physics_step=samples[0]['physics_step'], end_physics_step=samples[-1]['physics_step'],
            invocation_ids=sorted({i for r in samples for i in r['invocation_ids']}))
        self.clips.append(clip)
        self.current_clip = None

    @untraced_host_recording
    def event(self, kind, **fields):
        # Never serialize arbitrary generated values or native simulator info.
        if kind in ('attempt_finalized', 'release_rejected'):
            fields = {k: fields[k] for k in ('index', 'release_verified', 'continuous_release_seconds',
                'finalization_source', 'terminal_before_finalize') if k in fields}
        elif kind == 'native_terminal_end':
            fields = {'finalized_indices': list(fields.get('finalized_indices', []))}
        elif kind in ('tactile_skill_query', 'tactile_skill_applied', 'tactile_skill_recorded'):
            fields = {k: fields[k] for k in ('skill_id', 'function_name', 'function_hash', 'source_version',
                'status', 'z_correction_m', 'queried_step', 'applied_step', 'entry_features',
                'color', 'dynamics', 'hold_start_step', 'hold_end_step') if k in fields}
        elif kind == 'code_execution':
            fields = {'has_exception': bool(fields.get('has_exception')),
                'public_messages': [s[:8192] if s.startswith('PIANO_POLICY_STATE ') else s[:1200] for s in fields.get('public_messages', [])[-24:]
                    if not any(m in s.lower() for m in _PRIVATE_MARKERS)]}
        elif kind == 'recovery_read':
            if fields.get('modality') not in ('hand','tactile'):
                return
            fields = {'modality': fields['modality']}
        elif kind == 'public_stop':
            report = fields.get('control_failure')
            fields = {'message': fields['message'][:1200], 'invocation_ids': list(self.active_calls)}
            if report:
                fields['control_failure'] = {k: report[k] for k in ('index', 'note', 'reason',
                    'action_step', 'quality', 'had_valid_contact', 'public_hold_confidence') if k in report}
        elif kind == 'public_note_report':
            fields = {'report': {k: fields['report'][k] for k in
                ('index', 'quality', 'abort_reason', 'had_valid_contact', 'public_hold_confidence')
                if k in fields['report']}}
        elif kind == 'public_check':
            fields = {k: fields[k] for k in ('check_id', 'index', 'passed', 'measurements') if k in fields}
        elif kind == 'policy_end':
            fields = {}
        elif kind == 'action':
            fields = {k: fields[k] for k in ('target', 'hand_state') if k in fields}
        else:
            return
        event = {'kind': kind, **self.stamp(), **fields}
        event['recorded_at_ns'] = time.time_ns()
        event['invocation_ids'] = list(self.active_calls)
        self.events.append(event)
        if kind == 'attempt_finalized':
            self._finish_clip()

    @contextmanager
    def trace_execution(self, code, namespace):
        filename = '<capx-public-' + _evidence_hash(code.encode()) + '>'
        self._finish_clip()
        self.execution_id = 'execution:' + _evidence_hash([self.episode_id, len(self.executions)])
        policy = re.search(r'^# capx-piano-policy:([0-9a-f]{64})$', code, re.M)
        repair = re.search(r'^# capx-piano-repair:([0-9a-f]{64})$', code, re.M)
        execution = {'execution_id': self.execution_id, 'source_version': policy.group(1) if policy else None,
            'repair_id': repair.group(1) if repair else None, 'start': self.stamp(), 'end': None}
        parameters = re.search(r'^# capx-piano-parameters:(.+)$', code, re.M)
        execution['parameter_version'] = _evidence_hash(json.loads(parameters.group(1))) if parameters else None
        self.executions.append(execution)
        if repair:
            self.events.append({'kind': 'repair_execution_begin', 'repair_id': repair.group(1),
                                'source_version': execution['source_version'], **self.stamp()})
        tree = ast.parse(code)
        by_line = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                source = ast.get_source_segment(code, node)
                function_id = 'function:' + _evidence_hash(ast.dump(node))
                by_line[(node.lineno, node.name)] = function_id
                if function_id not in self.functions:
                    self.functions[function_id] = {'function_id': function_id, 'name': node.name,
                        'source': source, 'source_hash': _evidence_hash(source.encode()), 'line':node.lineno,
                        'ast_hash': hashlib.sha256(ast.dump(node).encode()).hexdigest()}
        previous = sys.gettrace()
        frames = {}
        instructions = {}
        def trace(frame, event, arg):
            if frame.f_code.co_filename != filename or frame.f_globals is not namespace:
                return None
            frame.f_trace_lines = False
            key = (frame.f_code.co_firstlineno, frame.f_code.co_name)
            if event == 'call' and key in by_line:
                identifier = 'invocation:' + _evidence_hash([self.episode_id, len(self.invocations)])
                row = {'invocation_id': identifier, 'function_id': by_line[key], 'start': self.stamp(),
                       'execution_id': self.execution_id, 'depth': len(self.active_calls),
                       'parent_invocation_id': self.active_calls[-1] if self.active_calls else None,
                       'end': None, 'exceptions': [], 'outcome': 'running'}
                row['numeric_arguments'] = {k:float(v) for k,v in frame.f_locals.items()
                    if type(v) in (int,float) and np.isfinite(v) and
                    not any(w in k.lower() for w in ('force','depth','estimate','reward','success','score'))}
                frames[id(frame)] = row
                self.invocations.append(row)
                self.active_calls.append(identifier)
            row = frames.get(id(frame))
            if row is not None and event == 'exception':
                row['exceptions'].append({'type': arg[0].__name__, 'generated_line': frame.f_lineno,
                                          'recorded_at_ns': time.time_ns(), **self.stamp()})
            if row is not None and event == 'return':
                if frame.f_code not in instructions:
                    instructions[frame.f_code] = {i.offset: i.opname for i in dis.get_instructions(frame.f_code)}
                opcode = instructions[frame.f_code].get(frame.f_lasti, '')
                row.update(end=self.stamp(), outcome='returned' if opcode.startswith('RETURN') else 'raised')
                # Only explicit public failure returns, never arbitrary return values or labels.
                returned_reason = arg if isinstance(arg, str) else (
                    arg[0] if isinstance(arg, tuple) and arg and isinstance(arg[0], str) else None)
                if row['outcome'] == 'returned' and returned_reason in (
                        'press_timeout', 'hold_timeout', 'overforce', 'OOD_REPROBE',
                        'release_timeout', 'arrival_timeout', 'arrival_lost'):
                    row['public_failure_return'] = returned_reason.lower()
                # Exception events are retained even when handled by the helper.
                self.active_calls.remove(row['invocation_id'])
                del frames[id(frame)]
            return trace
        try:
            sys.settrace(trace)
            yield filename
        finally:
            sys.settrace(previous)
            execution['end'] = self.stamp()
            self._finish_clip()

    def snapshot(self):
        """Live host view for repair/validation; no serialization or disk round trip."""
        return {'schema': 'public_tactile_evidence.v1',
            'episode_id': self.episode_id, 'contract': self.contract, 'request': self.request,
            'failure_triggered': self.failure_triggered, 'executions': self.executions,
            'trajectory_encoders': [{'contract': e.contract,
                'load_error_type': getattr(e, 'error_type', None)} for e in self.encoders],
            'clips': self.clips, 'samples': self.frames, 'invocations': self.invocations,
            'functions': list(self.functions.values()), 'events': self.events, 'retrievals': self.retrievals}

    @untraced_host_recording
    def flush(self):
        signature = (self.sequence, len(self.events), len(self._error_samples))
        if self._last_flushed_signature == signature:
            return
        data = self.snapshot()
        if not self.retain_full_numeric_evidence:
            data['samples'] = [s for s in self.frames if s['physics_step'] in self._error_samples]
        ids = {s['sample_id'] for s in data['samples']}
        data['clips'] = [{**c, 'sample_ids': [i for i in c['sample_ids'] if i in ids]}
                         for c in self.clips if any(i in ids for i in c['sample_ids'])]
        data['events'] = [e for e in self.events if e['kind'] != 'action']
        if self.error_windows_only:
            data['functions'] = [{k: v for k, v in f.items() if k != 'source'}
                                 for f in data['functions']]  # Complete source lives in the program store.
        data['retention'] = ('full_numeric_public_episode.v1' if self.retain_full_numeric_evidence
                             else 'numeric_window4_in_ram_until_episode_end.v1')
        atomic_json(self.manifest_path, data)
        self._last_flushed_signature = signature

    def close(self):
        self._finish_clip()
        self.flush()


@contextmanager
def _reflection_deadline(seconds):
    """The text-library organizer retains its independent 120-second budget."""
    with request_deadline(seconds, phase='skill_reflection'):
        yield


def library_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_library(raw: bytes) -> dict:
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise ValueError("Library exceeds the fixed 8 KB budget")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict) or set(data) != {"schema", "skills"} or data["schema"] != SCHEMA:
        raise ValueError("Unsupported library schema")
    skills = data["skills"]
    if not isinstance(skills, list) or not 0 <= len(skills) <= MAX_SKILLS:
        raise ValueError("Library requires 0..16 skills")
    ids = set()
    for row in skills:
        if not isinstance(row, dict) or set(row) != {"id", "trigger", "practice"}:
            raise ValueError("Skill fields must be id/trigger/practice")
        if not isinstance(row["id"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", row["id"]) or row["id"] in ids:
            raise ValueError("Duplicate or invalid skill id")
        ids.add(row["id"])
        if not isinstance(row["trigger"], str) or not 1 <= len(row["trigger"]) <= 240:
            raise ValueError("Invalid trigger length")
        if not isinstance(row["practice"], str) or not 1 <= len(row["practice"]) <= 600:
            raise ValueError("Invalid practice length")
    text = raw.decode("utf-8").lower()
    if any(marker in text for marker in _PRIVATE_MARKERS):
        raise ValueError("Private audit markers are prohibited")
    if re.search(r"(?:ignore|disable|bypass|override|remove)\s+(?:the\s+)?(?:hard|safety|force|action|native|fixed|task)", text):
        raise ValueError("Skill attempts to override a fixed constraint")
    return data


def initialize_library(source: Path | None, destination: Path) -> None:
    """None starts with no historical entries; task constraints remain fixed."""
    data = {"schema": SCHEMA, "skills": []} if source is None else validate_library(source.read_bytes())
    for row in data["skills"]:
        if row["id"] == "visual_key_map":
            row["practice"] = ("Read get_keyboard_endpoints(). When calibration_ready is true, reuse mapped_keys without an image. "
                "Otherwise confirm the supplied A0/C8 endpoints in the clear HEAD image and pass only those two normalized "
                "white-key pixels to locate_visual_keys. Save the returned requested-key targets in piano_state['keys']. "
                "The public endpoint calibration works for new notes across compatible seeds; fresh hand/DIGIT feedback is still required.")
    raw = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")
    validate_library(raw)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(raw)


def continual_episode_feedback(run_dir, arm, *, feedback_contract=None):
    """Whole-episode public feedback; A never receives numeric tactile telemetry.

    No private files, arbitrary stdout or generated source are read here. The
    source is supplied once by the caller. Windows end at the last force read
    of each attempt; if a policy reads during release, those reads remain
    release evidence. Samples are matched by physical time, never interpolated.
    """
    from capx.integrations.opentac_shadow_code_memory import _public_note_report, PUBLIC_PIANO_FORCE_LIMITS
    from capx.integrations.opentac_shadow_vision import parse_note
    if arm not in ('A', 'B'):
        raise ValueError('Unknown continual arm')
    strict = feedback_contract == 'strict_tactile_ablation.v1'
    if feedback_contract not in (None, 'strict_tactile_ablation.v1'):
        raise ValueError('Unknown continual feedback contract')
    run = Path(run_dir)
    episode = json.loads((run/'shadow_public_episode.json').read_text())
    request = episode['sequence']
    events = [json.loads(l) for l in (run/'shadow_public_observations.jsonl').read_text().splitlines()]
    actions = [e for e in events if e['kind'] == 'action']
    reports = {}
    errors = []
    for e in events:
        if e['kind'] != 'code_execution':
            continue
        for line in e.get('public_messages', []):
            row = _public_note_report(line, request)
            if row:
                if strict and arm == 'A':
                    continue  # No measured quality/contact/hold/abort outcomes.
                # Arbitrary generated text may contain force values: only a
                # categorical reason is allowed on the common evidence path.
                reason = row.get('abort_reason')
                row['abort_reason'] = reason if reason is None or re.fullmatch(r'[A-Za-z_]+', reason) else 'unclassified_public_stop'
                reports[row['index']] = row
            elif line.startswith('PIANO_STOP ') or re.match(r'^\w*(?:Error|Exception):', line):
                errors.append(('program_reported_stop' if line.startswith('PIANO_STOP ') else 'program_exception')
                    if strict else re.sub(r'(?<![A-Za-z_])-?\d+(?:\.\d+)?', '<number>', line[:600]))
    finalized = [e for e in events if e['kind'] == 'attempt_finalized']
    ends = {e['index']: e for e in finalized}
    previous = episode.get('clear_action_steps', 0) + episode.get('force_history_warmup_action_steps', 0)
    notes, motion, force_windows, summaries = [], [], [], []
    rounded = lambda v: round(float(v), 6) if isinstance(v, (int, float)) and np.isfinite(v) else None
    milli = lambda v: round(1000*rounded(v), 3) if rounded(v) is not None else None
    def segments(values):
        result = []
        for i, value in enumerate(values):
            if result and result[-1][2] == value:
                result[-1][1] += 1
            else:
                result.append([i, 1, value])
        return result
    for index, note in enumerate(request['notes']):
        stop = ends.get(index, {}).get('action_step', max((a['action_step'] for a in actions), default=previous))
        aa = [a for a in actions if previous < a['action_step'] <= stop]
        ff = [e for e in events if e['kind'] == 'force_estimate' and previous < e['action_step'] <= stop]
        previous = stop
        phase_counts = Counter()
        pressed = False
        for a in aa:
            target = a['target']
            pressed = pressed or target[3] > .05
            phase_counts['release' if pressed and target[2] >= .035 and target[3] <= .05 else
                         'press_hold' if pressed else 'approach'] += 1
        notes.append({'index': index, 'report': reports.get(index),
                      'finalized': index in ends,
                      'release_verified': ends.get(index, {}).get('release_verified'),
                      'actions': len(aa), 'command_phase_actions': dict(phase_counts)})
        if strict and arm == 'A':
            notes[-1].pop('report')
        if not aa:
            continue
        if strict:
            # Shared motion-only selection: neither force-read timing nor its
            # quality selects which proprioceptive history reaches A.
            pressing = [a for a in aa if a['target'][3] > .05 and a['target'][2] < .035]
            anchor = (pressing or aa)[-1]['physics_step']
        else:
            anchor = ff[-1]['physics_step'] if ff else aa[-1]['physics_step']
        steps = [anchor - (15-i)*8 for i in range(16)]
        by_step = {a['physics_step']: a for a in aa if a['physics_step'] <= anchor}
        by_force = {f['physics_step']: f for f in ff if f['physics_step'] <= anchor}
        selected = [by_step.get(s) for s in steps]
        targets = [[rounded(s['target'][c]) if s else None for s in selected] for c in range(4)]
        motion.append({'index': index, 'physics_step_start': steps[0],
            'target_by_column': [v[0] if all(x == v[0] for x in v) else v for v in targets],
            'missing_target_samples': [i for i, s in enumerate(selected) if s is None],
            'actual': [[milli(s['hand_state'].get(k)) for k in
                ('wrist_x_offset_m','wrist_y_m','wrist_z_m','index_flexion_rad')] +
                [milli((s['hand_state'].get('wrist_and_index_velocity') or [None])[0])]
                if s else None for s in selected]})
        if arm == 'A':
            continue
        estimates = [by_force.get(s, {}).get('estimate', {}) for s in steps]
        values = [rounded(e.get('estimated_key_force_N')) for e in estimates]
        quality = [e.get('quality', 'missing_observation') for e in estimates]
        ages = [e.get('age_steps') for e in estimates]
        force_windows.append({'index': index, 'physics_step_start': steps[0], 'physics_step_stride': 8,
            'estimated_force_N': values, 'quality_segments': segments(quality), 'age_actions_segments': segments(ages),
            'calibration_segments': segments([e.get('calibration_version') for e in estimates]),
            'complete': all(bool(e) for e in estimates)})
        midi = parse_note(note)[0]
        color = 'black' if midi % 12 in (1,3,6,8,10) else 'white'
        target, tolerance, gate = PUBLIC_PIANO_FORCE_LIMITS[color+'_'+request['dynamics'][index]]
        # The compact window does not replace the whole attempt's arithmetic
        # summary: early OOD/overforce must not disappear behind later release.
        all_estimates = [f['estimate'] for f in ff]
        valid = [rounded(e.get('estimated_key_force_N')) for e in all_estimates
                 if rounded(e.get('estimated_key_force_N')) is not None
                 and e.get('quality') == 'ok' and e.get('age_steps') == 0]
        summaries.append({'index': index, 'valid_samples': len(valid),
            'scope':'all_force_reads_in_attempt',
            'minimum_N': min(valid) if valid else None, 'maximum_N': max(valid) if valid else None,
            'mean_N': rounded(np.mean(valid)) if valid else None,
            'std_N': rounded(np.std(valid)) if valid else None,
            'band_N': [gate, rounded(target+tolerance)],
            'below_inside_above': [sum(v < gate for v in valid), sum(gate <= v <= target+tolerance for v in valid),
                                   sum(v > target+tolerance for v in valid)],
            'quality_counts':dict(Counter(e.get('quality','missing_observation') for e in all_estimates))})
    # Columnar force-only payload removes repeated field names without removing
    # readings, quality changes or calibration identity. Common A/B inputs stay
    # byte-for-byte identical for the same public execution.
    calibrations = sorted({v for w in force_windows for _,_,v in w['calibration_segments'] if v is not None})
    packed_windows = [[w['index'],w['physics_step_start'],w['estimated_force_N'],
        w['quality_segments'],w['age_actions_segments'],
        [[i,n,calibrations.index(v) if v is not None else None] for i,n,v in w['calibration_segments']],
        w['complete']] for w in force_windows]
    summary_columns = ['index','valid_samples','minimum_N','maximum_N','mean_N','std_N',
                       'band_N','below_inside_above','quality_counts']
    return {'schema': feedback_contract or 'piano_continual_public_feedback.v2', 'seed': episode['seed'], 'request': request,
        'notes': notes, 'public_errors': errors,
        'end_submitted': any(e['kind'] in ('policy_end','native_terminal_end') for e in events),
        'generated_actions': len(actions)-episode.get('clear_action_steps',0)-episode.get('force_history_warmup_action_steps',0),
        'actions_and_state': {'target_columns':['wrist_x_offset_m','wrist_y_m','lift_z_m','index_flexion_rad'],
            'actual_columns':['wrist_x_offset_m','wrist_y_m','wrist_z_m','index_flexion_rad','wrist_y_speed_m_s'],
            'actual_units':['mm','mm','mm','mrad','mm/s'], 'target_units':['m','m','m','rad'],
            'target_encoding':'target_by_column follows target_columns; scalar repeats for all samples, array retains every sample; missing_target_samples marks absent records',
            'physics_step_stride':8, 'samples':16,
            'windows': motion, 'physics_hz':240, 'sampling_hz':30,
            'window_anchor':('last commanded press action (flexion>0.05 and Z<0.035), or last action if none; independent of tactile reads'
                if strict else 'last force read in this attempt, or last action if no reads; may include release'),
            'missing': 'null means missing, never zero-filled; no cross-attempt or cross-reset samples'},
        'estimated_force_history': {'columns':['index','physics_step_start','estimated_force_N',
            'quality_segments','age_actions_segments','calibration_id_segments','complete'],
            'physics_step_stride':8,'samples':16,'calibration_ids':calibrations,'rows':packed_windows}
            if arm == 'B' else {'available':False,'reason':'withheld_by_ablation'},
        'computed_summary': {'scope':'all_force_reads_in_attempt','columns':summary_columns,
            'rows':[[s[k] for k in summary_columns] for s in summaries]}
            if arm == 'B' else {'available':False,'reason':'withheld_by_ablation'}}


def public_evidence(run_dir: Path) -> dict:
    episode_path = run_dir / "shadow_public_episode.json"
    if not episode_path.is_file():
        raise ValueError("No completed public reset record; retain the previous library")
    episode = json.loads(episode_path.read_text(encoding="utf-8"))
    # Explicit field selection: do not pass arbitrary artifact fields onward.
    request = {k: episode[k] for k in ("seed", "sequence", "keyboard_cache_hit", "clear_action_steps")}
    source = run_dir / "shadow_public_observations.jsonl"
    rows = []
    for line in source.read_text(encoding="utf-8").splitlines() if source.is_file() else []:
        row = json.loads(line)
        kind = row.get("kind")
        safe = {"kind": kind, "action_step": row["action_step"]}
        if kind == "force_estimate":
            hand = {k: row["hand_state"][k] for k in ("wrist_x_offset_m", "wrist_y_m", "wrist_z_m", "index_flexion_rad", "wrist_and_index_velocity")}
            safe.update(note=row["note"], target=row["target"], hand_state=hand,
                estimate={k: row["estimate"].get(k) for k in
                    ("estimated_key_force_N", "sample_step", "age_steps", "quality", "calibration_version")})
        elif kind == "action":
            safe.update(target=row["target"], hand_state=row["hand_state"], native_done=row["native_done"])
        elif kind == "attempt_finalized":
            safe["index"] = row["index"]
        elif kind == 'release_rejected':
            safe.update(index=row['index'], hand_state=row['hand_state'],
                continuous_release_seconds=row['continuous_release_seconds'])
        elif kind == "code_execution":
            safe["public_messages"] = row["public_messages"]
        else:
            continue
        safe["event_id"] = len(rows)
        rows.append(safe)
    forces = [r for r in rows if r["kind"] == "force_estimate"]
    quality = Counter(r["estimate"]["quality"] for r in forces)
    # Keep representative contact dynamics and all rare invalid states within a
    # bounded prompt. Full public telemetry remains available for reproduction.
    selected = {r["event_id"] for r in rows if r["kind"] in ("attempt_finalized", "code_execution")}
    mandatory = set(selected)
    # Preserve measured release trajectories, not only sparse press estimates.
    for event in rows:
        if event['kind'] in ('attempt_finalized', 'release_rejected'):
            mandatory.add(event['event_id'])
            prior_actions = [r for r in rows if r['kind']=='action' and r['event_id']<event['event_id']]
            mandatory.update(r['event_id'] for r in prior_actions[-8:])
    for note in dict.fromkeys(r["note"] for r in forces):
        group = [r for r in forces if r["note"] == note]
        stride = max(1, len(group) // 5)
        selected.update(r["event_id"] for r in group[::stride][:6])
        selected.update(r["event_id"] for r in group if r["estimate"]["quality"] not in ("ok", "no_contact"))
        selected.add(group[-1]["event_id"])
    selected.update(mandatory)
    keep = set(sorted(mandatory)[-128:])
    for i in sorted(selected - keep):
        if len(keep)>=128:break
        keep.add(i)
    selected_rows = [r for r in rows if r['event_id'] in keep]
    code_paths = sorted(run_dir.glob("trial_*/code.py")) + sorted(run_dir.glob("**/in_progress_code.py"))
    code = code_paths[0].read_text(encoding="utf-8", errors="replace") if code_paths else ""
    if any(marker in code.lower() for marker in _PRIVATE_MARKERS):
        code = "[Code omitted: contained a prohibited private marker.]"
    if len(code) > 16000:
        code = code[:10000] + "\n# [middle omitted]\n" + code[-6000:]
    result = {"schema": "shadow_piano_skill_public_evidence.v1", "run_id": run_dir.name,
              "request": request, "quality_counts": dict(quality), "public_events": selected_rows,
              "generated_code": code,
              "limitations": "No native score/true force is available. Public hold confidence is not native success."}
    # This verifies the whitelist output, rather than relying on prompts alone.
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if any(marker in encoded.lower() for marker in _PRIVATE_MARKERS):
        raise ValueError("Public evidence contains a prohibited field")
    return result


def validate_proposal(proposal: dict, previous: dict, evidence: dict) -> tuple[bytes, list]:
    if not isinstance(proposal, dict) or set(proposal) != {"skills", "evidence"}:
        raise ValueError("Reflection must return skills and evidence")
    raw = yaml.safe_dump({"schema": SCHEMA, "skills": proposal["skills"]}, sort_keys=False, allow_unicode=True).encode("utf-8")
    data = validate_library(raw)
    if not isinstance(proposal["evidence"], list):
        raise ValueError("Evidence must be a list")
    event_ids = {r["event_id"] for r in evidence["public_events"]}
    supported = {}
    for row in proposal["evidence"]:
        if not isinstance(row, dict) or set(row) != {"skill_id", "event_ids", "basis", "reason"}:
            raise ValueError("Invalid evidence record")
        if row["basis"] not in ("observed", "hypothesis") or not isinstance(row["event_ids"], list) or not row["event_ids"]:
            raise ValueError("Each update needs public events and an evidence basis")
        if any(isinstance(i, bool) or not isinstance(i, int) or i not in event_ids for i in row["event_ids"]):
            raise ValueError("Reflection cites an unavailable public event")
        if not isinstance(row["reason"], str) or not 1 <= len(row["reason"]) <= 500:
            raise ValueError("Invalid evidence explanation")
        supported[row["skill_id"]] = row
    old = {r["id"]: r for r in previous["skills"]}
    for row in data["skills"]:
        if old.get(row["id"]) != row and row["id"] not in supported:
            raise ValueError("Every changed skill requires traceable public evidence")
    if len({r["id"] for r in data["skills"]} & old.keys()) < min(4, len(old)):
        raise ValueError("Reflection discarded the fixed safety foundation")
    return raw, proposal["evidence"]


def reflect_once(library: Path, run_dir: Path, output: Path, query_args) -> dict:
    from capx.llm.client import collect_llm_usage, query_model, summarize_llm_usage
    output.mkdir(parents=True, exist_ok=False)
    before = library.read_bytes()
    (output / "library_input.yaml").write_bytes(before)
    previous = validate_library(before)
    events = []
    result = {"input_sha256": hashlib.sha256(before).hexdigest(), "accepted": False}
    try:
        evidence = public_evidence(run_dir)
        atomic_json(output / "public_evidence.json", evidence)
        instructions = (
            "整理钢琴公开技能库，输出一个 JSON 对象，仅有 skills 和 evidence。skills 是完整的下一版条目列表，"
            "每条仅 id/trigger/practice，保持英文便于后续代码生成，每条 trigger<=240字符、practice<=600字符，最多16条、总YAML<=8192字节。"
            "合并去重，不为更新而更新。维持现有安全基础，不修改固定动作边界、力阈值、估计器、原生判分或任务时限。"
            "可建议更好的规划、接近、等待、稳定、微调与释放，但不能读私有数据、绕过保护或声称已获官方成功。"
            "仅依据给出的公开反馈；每条新增或修改建议必须在 evidence 给出 {skill_id,event_ids:[整数],basis:'observed'或'hypothesis',reason:简短解释}。"
            "没有确证的控制改善必须标为 hypothesis，并在 practice 明确需要新触觉验证。未改条目无须 evidence。"
            "只有公开估计，没有真实力或原生得分；不能推断哪一音官方成功。端点标定跨兼容seed复用，缓存命中无需图片；"
            "get_hand_state没有native_done，终止标记来自motion返回。输出纯JSON，不要代码围栏。")
        prompt = [{"role": "system", "content": instructions},
                  {"role": "user", "content": json.dumps({"previous_library": previous, "public_evidence": evidence}, ensure_ascii=False)}]
        atomic_json(output / "reflection_prompt.json", {"messages": prompt})
        with collect_llm_usage(events, "skill_reflection"), _reflection_deadline(REFLECTION_TIMEOUT_SECONDS):
            response = query_model(query_args, prompt)
        (output / "response.txt").write_text(response["content"], encoding="utf-8")
        text = response["content"].strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        proposal = json.loads(text)
        atomic_json(output / "proposal.json", proposal)
        raw, references = validate_proposal(proposal, previous, evidence)
        atomic_json(output / "provenance.json", {"run_id": run_dir.name, "evidence": references})
        temp = library.with_suffix(".next.yaml")
        temp.write_bytes(raw)
        temp.replace(library)
        result.update(accepted=True, changed=raw != before)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    (output / "library_output.yaml").write_bytes(library.read_bytes())
    result["output_sha256"] = library_digest(library)
    result["usage"] = summarize_llm_usage(events)
    atomic_json(output / "result.json", result)
    return result
