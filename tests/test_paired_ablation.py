import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from capx.ablation.client import BaselineClient
from capx.ablation.execution import ProgramViolation, extract_program, run_program, validate_program
from capx.ablation.protocol import ROOT, TASKS, load_spec, read_json, readiness, request_for, sha256
from capx.ablation.server import BaselineServer, rgb_payload
from capx.ablation.trial import build_prompt


@pytest.fixture(autouse=True)
def formal_test_phase(monkeypatch):
    monkeypatch.setenv('CAPX_ABLATION_PHASE', 'formal')


@pytest.mark.parametrize('mode', ['no-tactile', 'tactile-rgb-opencv'])
def test_program_has_no_native_or_file_handles(mode):
    for code in ["env.check_success()", "APIS.get('x')", "import os", "import requests", "import pathlib",
                 "import numpy as np\nnp.load('/private.npy')", "get_robot_state.__self__",
                 "import cv2\ncv2.imread('/private.png')", "getattr(get_robot_state, '__self__')"]:
        with pytest.raises((ProgramViolation, NameError)):
            run_program(code, {}, mode)


def test_single_program_and_legal_end():
    assert extract_program('```python\ncapx_stop=True\n```') == 'capx_stop=True'
    for text in ['```python\nx=1\n```\n```python\nx=2\n```', 'Explanation\n```python\nx=1\n```', '']:
        with pytest.raises(ProgramViolation):
            extract_program(text)
    with pytest.raises(SyntaxError):
        extract_program('This is invalid python')
    with pytest.raises(ProgramViolation, match='missing_legal_END'):
        run_program('x=1', {}, 'no-tactile')


def test_cv_program_computes_internally_and_cannot_print_features(capsys):
    code = '''import numpy as np
import cv2
image = read_tactile_rgb('index')['rgb']
gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
feature = float(np.mean(gray))
print(feature)
capx_stop = True
'''
    assert run_program(code, {'read_tactile_rgb': lambda s: {'rgb':np.ones((10,10,3), np.uint8)}}, 'tactile-rgb-opencv')
    assert capsys.readouterr().out == ''
    with pytest.raises(ProgramViolation):
        run_program(code, {}, 'no-tactile')


def test_actual_rgb_reads_deduplicate_and_are_copies(tmp_path):
    logs = []
    client = BaselineClient('http://localhost', 'tactile-rgb-opencv', tmp_path, lambda e,v: logs.append((e,v)))
    image = np.arange(90, dtype=np.uint8).reshape(5,6,3)
    client.call = lambda *a, **k: dict(ok=True, episode='ep', sensor='index', physics_step=24, time_s=.1,
                                      rgb_base64=rgb_payload(image), key_forces=[999], score=1)
    first = client.read_tactile_rgb('index')
    first['rgb'][:] = 0
    second = client.read_tactile_rgb('index')
    assert np.array_equal(second['rgb'], image)
    assert len(list((tmp_path/'tactile_images').glob('*.png'))) == 1
    assert len(logs) == 1 and 'key_forces' not in str(logs) and 'score' not in str(logs)
    assert set(second) == {'rgb', 'sensor', 'physics_step', 'time_s'}


def test_no_sensor_has_no_files_or_interface(tmp_path):
    client = BaselineClient('http://localhost', 'no-tactile', tmp_path, lambda *a: None)
    assert 'read_tactile_rgb' not in client.functions()
    with pytest.raises(RuntimeError):
        client.read_tactile_rgb('left')
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('mode', ['no-tactile', 'tactile-rgb-opencv'])
def test_current_scene_is_the_only_prompt_image(mode):
    spec = {'task_prompt':'Goal','action_contract':'action API','mode':mode}
    scene = np.arange(90, dtype=np.uint8).reshape(5,6,3)
    prompt = build_prompt(spec, {'notes':['E4']}, scene, {'x':0})
    content = prompt[-1]['content']
    urls = [p['image_url']['url'] for p in content if p['type']=='image_url']
    assert len(urls) == 1 and urls[0].startswith('data:image/png;base64,')
    image = Image.open(io.BytesIO(base64.b64decode(urls[0].split(',',1)[1])))
    assert np.array_equal(np.asarray(image), scene)
    assert 'CURRENT episode initial scene:' in str(content)
    assert 'get_scene_rgb' in str(content)
    assert 'demonstration' not in str(content).lower()
    assert ('read_tactile_rgb' in str(content)) == (mode == 'tactile-rgb-opencv')
    if mode == 'no-tactile':
        assert 'depth' not in str(content)


def test_readiness_needs_native_audits_but_no_expert_video(tmp_path):
    source = tmp_path/'task.py'
    source.write_text('# mock native task\n')
    spec = load_spec('env_configs/ablations/tension_strap.yaml')
    item = {'environment_root':str(tmp_path), 'native_entry':source.name,
            'environment_commit':'pinned', 'native_sha256':sha256(source),
            'native_contract_verified':True, 'calibration_overlap_audited':True,
            'development_seeds':[42000,42001]}
    assets = {'tasks':{spec['task']:item}}
    assert readiness(spec, assets=assets) == []
    item['native_contract_verified'] = False
    assert 'native completion/action-rate/subtask contract not verified' in readiness(spec, assets=assets)
    item['native_contract_verified'] = True
    item['development_seeds'] = [41000]
    assert 'split overlap' in readiness(spec, assets=assets)


def test_frozen_40_requests_and_readiness_are_honest():
    manifest = read_json(ROOT/'env_configs/ablations/seeds40.json')
    seeds = manifest['test_seeds']
    assert len(seeds) == len(set(seeds)) == 40
    assert not set(seeds) & set(manifest['development_seeds'])
    for task in TASKS:
        spec = load_spec(f'env_configs/ablations/{task}.yaml')
        assert len(manifest['requests'][task]) == 40
        assert request_for(spec, seeds[-1])
        with pytest.raises(ValueError):
            request_for(spec, 0)
        assert readiness(spec)  # missing assets must not silently pass
        assert spec['model'] == 'gpt-6.1-sol'
    values = [tuple(r['force_time_sequence']) for r in manifest['requests']['tension_strap'].values()]
    assert len(set(values)) == 40


def test_server_preserves_native_bounds_terminal_and_result(tmp_path):
    cfg = SimpleNamespace(physics_dt=1/240)
    class Task:
        step_count=0
        _done=False
        action_space=SimpleNamespace(low=np.array([-.01,-.32,-.006,0]), high=np.array([.09,.26,.05,.5]))
        def step(self, target):
            self.step_count += 8
            self._done=True
        def _info(self):
            return {'success':False, 'score':123, 'key_forces':[42]}
    native=Task(); native.cfg=cfg
    server=object.__new__(BaselineServer)
    server.task=native
    server.spec={'task':'shadow_piano','mode':'no-tactile','action_call_budget':10}
    server.peer=SimpleNamespace(_jsonable=lambda x:x)
    server.output=tmp_path
    server.calls=0; server.ended=False; server.cache={}; server.started_step=0; server._capture_scene=lambda:None
    with pytest.raises(ValueError):
        server.action('shadow_target', {'target':[0,0,0,.6],'steps':2})
    response=server.action('shadow_target', {'target':[0,0,0,.2],'steps':5})
    assert response['physics_steps_executed']==8
    assert set(response)=={'ok','executed','physics_steps_executed','physics_step','time_s'}
    with pytest.raises(RuntimeError):
        server.action('shadow_target', {'target':[0,0,0,.2],'steps':1})
    with pytest.raises(ValueError):
        server.rgb('index')
    result=server.finish(True)
    assert result['native_result']['completed'] is False
    assert result['native_result']['native']['key_forces']==[42]


def test_http_model_error_is_one_request(monkeypatch):
    from capx.llm.client import query_model
    calls=[]
    class Session:
        trust_env=True
        def post(self, url, **kwargs):
            calls.append((url,kwargs,self.trust_env))
            return SimpleNamespace(status_code=500, raise_for_status=lambda: (_ for _ in ()).throw(RuntimeError('failed')))
    monkeypatch.setattr('capx.llm.client.requests.Session', Session)
    args=SimpleNamespace(model='gpt-6.1-sol',server_url='http://example/v1',max_tokens=8192,reasoning_effort='medium',
                         api_key=None,debug=False,single_request=True,request_timeout=300)
    with pytest.raises(RuntimeError):
        query_model(args,[{'role':'user','content':[{'type':'text','text':'x'}]}])
    assert len(calls)==1
    assert calls[0][2] is False
    assert json.loads(calls[0][1]['data'])['max_output_tokens']==8192


@pytest.mark.parametrize('code,expected_end', [('capx_stop=True',True),('raise ValueError("force=999")',False),
        ('x=1',False), ('```python\nx=1\n```\n```python\nx=2\n```',False)])
def test_full_trial_one_request_no_repair_or_skills(monkeypatch,tmp_path,code,expected_end):
    import capx.ablation.trial as module
    monkeypatch.setattr(module,'require_ready',lambda spec:None)
    spec=load_spec('env_configs/ablations/shadow_piano.yaml')
    calls=[]
    class FakeClient:
        action_calls=0; action_physics_steps=0
        def __init__(self,*a): pass
        def reset(self,seed): pass
        def get_scene_rgb(self): return np.zeros((8,8,3),np.uint8)
        def get_robot_state(self): return {'physics_step':0}
        def functions(self): return {}
        def call(self,name,body):
            calls.append((name,body))
            return {'native_result':{'completed':True}}
    monkeypatch.setattr(module,'BaselineClient',FakeClient)
    queries=[]
    def query(args,prompt):
        queries.append(prompt)
        return {'content':code,'usage':{'total_tokens':7}}
    env=SimpleNamespace(spec=spec,cfg=SimpleNamespace(server_url='fake',config_path='env_configs/ablations/shadow_piano.yaml'))
    result=module.run_trial(env,41000,SimpleNamespace(api_key=None),{'output_dir':str(tmp_path)},query=query)
    assert len(queries)==1 and len(calls)==1
    assert calls[0]==('finish',{'submitted_end':expected_end})
    assert result.task_completed==expected_end
    data=json.loads((tmp_path/'seed_41000/result.json').read_text())
    assert data['model_requests']==1 and data['legal_end']==expected_end
    assert sum(p['type']=='image_url' for p in queries[0][-1]['content']) == 1
    assert not (tmp_path/'seed_41000/expert_frames').exists()
    assert 'demo' not in read_json(tmp_path/'seed_41000/effective_config.json')
    logs=tmp_path/'seed_41000/univtac_actions.jsonl'
    if logs.exists(): assert 'force=999' not in logs.read_text()


def test_report_does_not_count_missing_as_measured_failure(tmp_path):
    from capx.ablation.report import summarize
    rows,trials=summarize([],list(range(41000,41040)))
    assert len(rows)==16 and trials==[]
    assert all(r['missing']==40 and r['success_rate'] is None for r in rows)


def test_wall_deadline_independent_of_health_and_pid_identity():
    import os
    from capx.serving.univtac_watchdog import _deadline_reached, _start_token
    assert _start_token(os.getpid()) is not None
    assert not _deadline_reached(10, 30, 39)
    assert _deadline_reached(10, 30, 40)
    assert not _deadline_reached(10, None, 999)


def test_server_client_contract_mismatch_fails_before_generation(tmp_path):
    client=BaselineClient('fake', 'no-tactile', tmp_path, lambda *a:None, 'expected')
    client.call=lambda *a,**k:{'physics_step':0,'contract_hash':'different'}
    with pytest.raises(RuntimeError, match='protocol_mismatch'):
        client.reset(41000)


def test_existing_http_routes_and_sensor_endpoint_are_blocked():
    import asyncio
    import httpx
    from fastapi import FastAPI
    from capx.ablation.server import mount
    app=FastAPI()
    @app.get('/check_success')
    def private():
        raise AssertionError('must never run')
    baseline=SimpleNamespace(spec={'mode':'no-tactile'},contract_value='test')
    for name in ('reset','state','scene','action','finish'):
        setattr(baseline,name,lambda **kw:{'ok':True})
    peer=SimpleNamespace(app=app,_run_on_main=lambda fn, timeout, **kw:fn(**kw))
    mount(peer,baseline)
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'X-Capx-Contract':'test'}) as client:
            assert (await client.get('/check_success')).status_code==403
            assert (await client.post('/ablation/rgb',json={'sensor':'left'})).status_code==403
            assert (await client.post('/ablation/state',json={})).json()=={'ok':True}
            assert (await client.post('/ablation/finish',json={},headers={'X-Capx-Contract':'other'})).status_code==403
    asyncio.run(check())


@pytest.mark.parametrize('mode', ['tactile-rgb-opencv', 'no-tactile'])
def test_same_position_grasp_without_sensor_feedback_and_recoverable_planner(monkeypatch, mode):
    import sys
    from types import ModuleType
    atom=ModuleType('envs.utils.atom')
    atom.Action=lambda **kw:SimpleNamespace(**kw)
    transforms=ModuleType('envs.utils.transforms')
    transforms.Pose=lambda **kw:SimpleNamespace(**kw)
    monkeypatch.setitem(sys.modules,'envs.utils.atom',atom)
    monkeypatch.setitem(sys.modules,'envs.utils.transforms',transforms)
    cfg=SimpleNamespace(use_adaptive_grasp=True)
    task=SimpleNamespace(cfg=cfg,plan_success=True)
    def move(commands, **kw):
        assert cfg.use_adaptive_grasp is False
        assert commands[0].action=='gripper'
        assert commands[0].target_gripper_pos==.4
        task.plan_success=False
        return False
    task.move=move
    server=object.__new__(BaselineServer)
    server.task=task; server.spec={'mode':mode}; server.stopped=lambda:False
    assert server._franka_action('gripper', {'position':.4}) is False
    assert cfg.use_adaptive_grasp is True and task.plan_success is True
    server.stopped=lambda:True
    server._franka_action('gripper', {'position':.4})
    assert task.plan_success is False  # native terminal status cannot be cleared
