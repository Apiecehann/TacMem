from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from capx.logger2skill.experiment import load_suite, episode_config
from capx.logger2skill.storage import LibraryStore, render_memory, validate_library
from capx.serving.shadow_piano_runtime import ShadowPianoRuntime
from capx.serving.shadow_piano_force import PublicDigitKeyForce


ROOT = Path(__file__).resolve().parents[1]


def test_piano_route_is_separate_and_loads_curated_summary(tmp_path):
    store = LibraryStore(tmp_path / 'library')
    store.initialize()
    empty = store.path(store.active())
    assert render_memory(empty, 'arm_shadow_piano', 12000) == ''
    import shutil
    candidate = tmp_path / 'candidate'
    shutil.copytree(empty, candidate)
    skill = candidate / 'skills/act/piano-release.md'
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text('---\nid: P1\nname: piano-release\nfamily: act\n'
        'summary: On piano release, count physical time after measured arrival.\n'
        'task_types: [piano]\nevidence: Synthetic CPU routing case; no real execution.\n---\n'
        'A routing fixture only.\n', encoding='utf-8')
    assert len(validate_library(candidate)) == 1
    pointer = store.adopt(candidate, store.active(), 'piano')
    assert 'count physical time' in render_memory(store.path(pointer), 'arm_shadow_piano', 12000)
    for task in ('tension_strap', 'weight_classify', 'bulb_tightening', 'blind_pick_balls'):
        assert render_memory(store.path(pointer), task, 12000) == ''
    assert render_memory(empty, 'arm_shadow_piano', 12000) == ''


def test_smoke_is_one_program_one_task_with_fixed_force_config(tmp_path):
    suite = load_suite(ROOT / 'env_configs/logger2skill/piano_smoke.yaml')
    assert suite['smoke'] and list(suite['tasks']) == ['arm_shadow_piano']
    assert suite['budget']['rounds'] == 1 and len(suite['seeds']) == 1
    config = episode_config('arm_shadow_piano', suite['tasks']['arm_shadow_piano'],
                            suite['seeds'][0], tmp_path, tmp_path / 'library', 12000)
    assert config['single_program'] and not config['evolve_skill_library']
    assert config['env']['cfg']['apis'] == ['UniVTACShadowPianoApi']
    from capx.llm.client import is_vlm_model
    assert is_vlm_model(suite['execution']['model'])
    files = ROOT / 'env_configs/univtac/arm_shadow_piano'
    estimator = PublicDigitKeyForce(files / 'shadow_digit_key_top_force_5d_area_rate.v1.json',
                                    files / 'config_digit_piano_0_2.v1.yml')
    assert estimator.uses_area_rate
    d = np.full((8, 8), .01)
    result = estimator.predict(d, d, color='white', sample_step=0, current_step=0, physics_step=0)
    assert result['estimated_key_force_N'] is None


def runtime_fixture():
    runtime = ShadowPianoRuntime.__new__(ShadowPianoRuntime)
    runtime.task = SimpleNamespace(action_space=SimpleNamespace(
        low=np.array([-.01, -.32, -.006, 0.]), high=np.array([.09, .26, .05, .50])),
        step_count=28, cfg=SimpleNamespace(physics_dt=1 / 240))
    runtime._map = {'C4': {}}
    runtime._done = runtime._submitted = False
    runtime._release_since = 0
    runtime._attempts = 0
    runtime.hand_state = lambda: {'wrist_x_offset_m': 0., 'wrist_y_m': 0.,
                                  'wrist_and_index_velocity': [0., 0., 0.]}
    runtime.request = lambda: {'notes': ['C4']}
    return runtime


def test_action_checks_actual_arrival_and_terminal_state():
    runtime = runtime_fixture()
    assert runtime._validate_target([0., 0., .01, .5]).shape == (4,)
    with pytest.raises(ValueError, match='native bounds'):
        runtime._validate_target([0., 0., 0., .51])
    with pytest.raises(ValueError, match='arrival'):
        runtime._validate_target([0., .01, .01, .1])
    runtime._map = {}
    with pytest.raises(ValueError, match='Calibrate'):
        runtime._validate_target([0., 0., 0., .1])
    runtime._done = True
    with pytest.raises(RuntimeError, match='ended'):
        runtime.move([0., 0., .04, 0.], 1)


def test_release_checks_physical_time_without_advancing_native_score():
    runtime = runtime_fixture()
    with pytest.raises(ValueError, match='0.116667'):
        runtime.finish_attempt(0)
    runtime.task.step_count = 32
    assert runtime.finish_attempt(0) == {'ok': True, 'native_done': False}
    assert runtime._attempts == 1
    with pytest.raises(ValueError, match='order'):
        runtime.finish_attempt(0)


def test_public_state_omits_native_keys_score_and_force():
    runtime = runtime_fixture()
    runtime._actions = 4
    runtime._obs = {'state': np.arange(111, dtype=np.float32)}
    runtime.task._action_state = lambda: [0., .1, .04, 0.]
    state = ShadowPianoRuntime.hand_state(runtime)
    assert state['arm_joint_position_rad'] == list(range(97, 104))
    assert set(state) == {'wrist_x_offset_m', 'wrist_y_m', 'wrist_z_m', 'index_flexion_rad',
        'wrist_and_index_velocity', 'arm_joint_position_rad', 'arm_joint_velocity_rad_s',
        'visual_key_map_ready', 'step', 'physics_step', 'action_step', 'native_done'}
    assert state['step'] == state['action_step'] == 4
    assert state['physics_step'] == 28


def test_startup_failure_does_not_spend_curation_tokens(tmp_path):
    from capx.logger2skill.experiment import run_loop
    suite = load_suite(ROOT / 'env_configs/logger2skill/piano_smoke.yaml')
    def execute(*args):
        return {'public_case_exported': False, 'infrastructure_error': 'server did not start',
            'case': {'case_id': 'arm_shadow_piano_44000', 'task': 'arm_shadow_piano',
                     'seed': 44000, 'final_success': None}, 'coding_usage': {'total_tokens': 0, 'complete': True}}
    def curate(*args):
        pytest.fail('No model call is allowed without a public execution case')
    result = run_loop(suite, tmp_path, execute, curate)
    assert result['rounds'][0]['episodes'][0]['curation']['status'] == 'skipped_no_public_case'
    assert result['metrics']['curation_tokens']['reported_total_tokens'] == 0


def test_head_frame_matches_calibration_coordinates():
    runtime = runtime_fixture()
    picture = np.zeros((640, 960, 3), np.uint8)
    picture[0, 0] = 123
    runtime.task.render = lambda: picture
    image = runtime.frame()
    assert image.shape == (662, 960, 3)
    assert not image[:22].any()
    assert (image[22, 959] == 123).all()


def test_responses_code_request_disables_tools_and_preserves_empty_usage(monkeypatch, tmp_path):
    import json
    import requests
    from capx.llm import client
    from capx.envs.launch import LaunchArgs
    original = [{'role': 'system', 'content': 'Generate robot Python only.'},
                {'role': 'user', 'content': [{'type': 'text', 'text': 'piano'},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,example'}}]}]
    calls = []
    def post(url, **kwargs):
        calls.append(json.loads(kwargs['data']))
        r = requests.Response()
        r.status_code = 200
        r._content = json.dumps({'status': 'completed', 'output': [],
            'usage': {'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30}}).encode()
        return r
    monkeypatch.setattr(client.requests, 'post', post)
    path = tmp_path / 'response.json'
    monkeypatch.setenv('CAPX_LLM_RESPONSE_PATH', str(path))
    args = LaunchArgs(config_path='unused', model='gpt-6.1-sol', max_query_retries=0)
    with pytest.raises(RuntimeError, match='no text'):
        client.query_model(args, original)
    assert len(calls) == 1
    assert calls[0]['tools'] == [] and calls[0]['tool_choice'] == 'none'
    assert calls[0]['instructions'] == 'Generate robot Python only.'
    assert calls[0]['input'][0]['content'][1]['type'] == 'input_image'
    assert original[1]['content'][1]['type'] == 'image_url'
    assert json.loads(path.read_text())['usage']['total_tokens'] == 30


def test_stream_recovers_only_completed_items_after_request_completion():
    import json
    from capx.llm.client import _completed_responses_stream, _responses_content
    item = {'type': 'message', 'content': [{'type': 'output_text', 'text': 'print("piano")'}]}
    def event(value):
        return ['data: ' + json.dumps(value), '']
    lines = event({'type': 'response.output_item.done', 'output_index': 0, 'item': item})
    terminal = event({'type': 'response.completed', 'response': {'status': 'completed',
        'output': [], 'usage': {'input_tokens': 2, 'output_tokens': 3}}})
    result = _completed_responses_stream(lines + terminal, 1)
    assert _responses_content(result) == 'print("piano")'
    assert result['usage']['output_tokens'] == 3
    with pytest.raises(RuntimeError, match='partial output rejected'):
        _completed_responses_stream(lines, 1)
    with pytest.raises(RuntimeError, match='failed'):
        _completed_responses_stream(lines + event({'type': 'response.incomplete'}), 1)


def test_exception_class_name_is_safe_but_other_reflection_remains_blocked():
    from capx.logger2skill.evidence import validate_public_program
    validate_public_program('try:\n    wait_steps(1)\nexcept Exception as exc:\n    print(type(exc).__name__)\n', set())
    for code in ('print(exc.__dict__)', 'print(type(exc).__mro__)', 'print(fn.__name__)', 'print(env.score)'):
        with pytest.raises(ValueError):
            validate_public_program(code, set())
