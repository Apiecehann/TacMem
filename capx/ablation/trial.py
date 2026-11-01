"""One initial request, one complete program, terminal native scoring only."""
from __future__ import annotations

import copy
import os
import time
from pathlib import Path

from .client import BaselineClient
from .demonstration import expert_parts, image_part
from .execution import ProgramViolation, extract_program, run_program
from .protocol import clear_proxies, contract_hash, read_json, request_for, require_ready, resolve, sha256, write_json


API_DOC = '''Write one complete Python program. Set capx_stop = True only when done.
No extra model calls, blocks, skills, saved programs, filesystem, networking or introspection.
You may import math, numpy and cv2. Use loops, functions and recovery inside this program.
get_scene_rgb(camera='head'): ordinary RGB image (H,W,3 uint8).
get_robot_state(): joint/arm positions and velocities, physical step and time.
action(operation, **arguments): positional motion; returns executed, physics_step, time_s.
No reward, completion or material identity is available during execution.
print is disabled; record keeping is performed by the host. Do not print observations.
'''
RGB_DOC = '''read_tactile_rgb(sensor): {'rgb': uint8 RGB array, 'sensor': str,
'physics_step': int, 'time_s': float}. Use sensor left/right for the gripper,
index for Shadow. You may import cv2 and design your own image analysis.
No numeric depth, marker arrays or pretrained tactile/force features are provided.
Keep all image-derived intermediate values inside your program.
'''


def build_prompt(spec, request, demo_parts, scene, state):
    import json
    text = (spec['task_prompt'] + '\nThis episode request: ' + json.dumps(request) + '\n' + API_DOC +
            spec['action_contract'] + '\nInitial robot state: ' + json.dumps(state))
    if spec['mode'] == 'tactile-rgb-opencv':
        text += '\n' + RGB_DOC
    return [{'role': 'system', 'content': 'Return executable Python only. Do not include prose.'},
            {'role': 'user', 'content': [{'type': 'text', 'text': text}, *demo_parts,
             {'type': 'text', 'text': 'CURRENT episode initial scene (not the demonstration):'}, image_part(scene)]}]


def run_trial(env, trial, args, config, partial_artifacts=None, *, query=None):
    from capx.llm.client import query_model
    from capx.utils.launch_utils import TrialSummary, _save_trial_artifacts
    from capx.serving.univtac_action_log import log_ablation
    spec = env.spec
    require_ready(spec)  # before reset, network or paid API requests
    clear_proxies()
    seed = int(trial)
    request = request_for(spec, seed)
    output = Path(config['output_dir']) / f'seed_{seed}'
    output.mkdir(parents=True, exist_ok=False)  # never overwrite a prior attempt
    log = lambda event, fields: log_ablation(output / 'univtac_actions.jsonl', event, fields)
    client = BaselineClient(env.cfg.server_url, spec['mode'], output, log, contract_hash(spec, seed))
    raw = code = ''
    calls = 0
    failure = None
    usage = None
    prompt = None
    native = None
    legal_end = False
    reset_verified = False
    start = time.monotonic()
    query_args = copy.copy(args)
    query_args.single_request = True
    query_args.request_timeout = 300
    query_args.max_tokens = 8192
    query_args.model = spec['model']
    query_args.temperature = spec['temperature']
    query_args.reasoning_effort = spec['reasoning_effort']
    query_args.debug = False
    # Secrets are never written to request/config artifacts.
    query_args.api_key = getattr(args, 'api_key', None) or os.environ.get('CAPX_API_KEY') or os.environ.get('OPENAI_API_KEY')
    assets = read_json(resolve(spec['assets_manifest']))['tasks'][spec['task']]
    write_json(output / 'effective_config.json', {'spec': spec, 'seed': seed, 'request': request,
               'config_sha256': sha256(resolve(env.cfg.config_path)), 'demo': assets['demo']})
    try:
        client.reset(seed)
        reset_verified = True
        demo = expert_parts(assets['demo'], output)
        prompt = build_prompt(spec, request, demo, client.get_scene_rgb(), client.get_robot_state())
        write_json(output / 'request.json', {'model': query_args.model, 'max_output_tokens': 8192,
                   'reasoning_effort': query_args.reasoning_effort, 'messages': prompt})
        calls = 1
        response = (query or query_model)(query_args, prompt)
        usage = response.get('usage')
        write_json(output / 'response.json', response)
        raw = response['content']
        code = extract_program(raw)
        (output / 'code.py').write_text(code, encoding='utf-8')
        run_program(code, client.functions(), spec['mode'])
        legal_end = True
    except Exception as exc:
        # A generated exception may embed a sensor array/value: never log str(exc).
        failure = ('program_' + str(exc)) if type(exc) is ProgramViolation else type(exc).__name__
        log('execution_error', {'error_type': failure})
    finally:
        try:
            # Host-only finish: never bound into generated code, never a second prompt.
            if reset_verified:
                native = client.call('finish', {'submitted_end': legal_end})['native_result']
                write_json(output / 'private' / 'native_result.json', native)
        except Exception as exc:
            failure = failure or 'native_audit_' + type(exc).__name__
        elapsed = time.monotonic() - start
        passed = bool(legal_end and failure is None and native and native.get('completed') is True)
        result = {'schema': 'cap_tac_ablation_result.v1', 'task': spec['task'], 'mode': spec['mode'],
                  'phase': spec['phase'],
                  'seed': seed, 'completed': passed, 'legal_end': legal_end, 'failure_type': failure,
                  'model_requests': calls, 'code_blocks': int(bool(code)), 'usage': usage,
                  'action_calls': client.action_calls, 'physics_steps': client.action_physics_steps,
                  'server_action_calls': native.get('action_calls') if native else None,
                  'server_physics_steps': native.get('execution_physics_steps') if native else None,
                  'elapsed_s': elapsed, 'native_audit': 'private/native_result.json'}
        write_json(output / 'result.json', result)
    summary_line = f"seed={seed} completed={passed} failure={failure} requests={calls} actions={client.action_calls}"
    (output / 'run.log').write_text(summary_line + '\n', encoding='utf-8')
    artifact_config = dict(config, output_dir=str(output))
    path = _save_trial_artifacts(artifact_config, seed, int(not passed), float(passed), passed,
            code, raw, [{'initial_prompt': prompt}] if prompt else [], [summary_line], [])
    return TrialSummary(trial=seed, success=failure is None, reward=float(passed), terminated=legal_end,
         truncated=failure == 'TimeoutError', sandbox_rc=int(failure is not None), log=summary_line,
         task_completed=passed, code_path=path, num_regenerations=0,
         num_finishes=int(legal_end), num_code_blocks=int(bool(code)))
