"""CPU checks for random requests, endpoint reuse and public-only reflection."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from capx.integrations.opentac_shadow_keyboard import make_calibration, validate_calibration, targets_from_calibration
from capx.integrations.opentac_shadow_vision import head_world_to_display_pixel
from capx.integrations.opentac_shadow_online_skills import initialize_library, public_evidence, reflect_once, validate_proposal
from capx.envs.simulators.opentac_shadow import ShadowPianoLowLevelEnv
from capx.envs.tasks.base import CodeExecEnvConfig, CodeExecutionEnvBase
from capx.envs import trial
from test_opentac_shadow_adapter import FakeShadowTask


def evidence_recorder(tmp_path, name='run', encoder=None, failure_triggered=False, **kwargs):
    from capx.integrations.opentac_shadow_online_skills import PublicTactileEvidence
    return PublicTactileEvidence(tmp_path/'evidence', tmp_path/name,
        {'sensor':'same', 'force_model':'frozen', 'action_repeat':4, 'physics_hz':240,
         'frame':'public_endpoint_geometry.v1'},
        {'notes':['F4'], 'dynamics':['light'], 'hold_seconds':[.12]}, encoder=encoder,
        failure_triggered=failure_triggered, **kwargs)


def repair_skill_fixture(index=0, *, status='local_verified'):
    from capx.integrations.opentac_shadow_online_skills import _evidence_hash
    identifier = 'skill_' + _evidence_hash(str(index))
    return {'skill_id':identifier,'version':1,'correction_id':identifier,'status':status,
        'applicable_conditions':{'compatibility':{'sensor':'same'}},
        'failure_context':{'kind':'release_rejected'},'numeric_correction':[],
        'repair_advice':'Start timing after measured arrival.','public_validation':{'passed':True},
        'provenance':{'episode_id':'old','source_version':'missing-source'},
        'has_code_change':True,'available_at_ns':10,'verified_at_ns':10,'keys':{'language':repair_language_fixture(index),'tactile':[
            {'vector':[float(index),0.], 'quality':'ok','contract':{'version':'frozen'}}]}}


def repair_language_fixture(index=0):
    from capx.integrations.opentac_shadow_online_skills import make_repair_language_key
    return make_repair_language_key({'notes':['F4'],'dynamics':['light'],'hold_seconds':[.12]},
        {'kind':'release_rejected','phase':'release'}, {'release_steps':index})


class RepairTextMock:
    contract = {'version':'mock_text.v1'}
    def encode(self,text):
        return [1.,float(json.loads(text)['control_parameters']['release_steps'])]


def test_independent_skill_store_needs_no_historical_program(tmp_path):
    from capx.integrations.opentac_shadow_online_skills import RepairSkillStore, retrieve_repair_skills
    store=RepairSkillStore(tmp_path);row=store.put(repair_skill_fixture())
    assert store.verified_view()[0]['skill_id']==row['skill_id']
    result=retrieve_repair_skills(store.search_records(),episode_id='new',cutoff_ns=row['available_at_ns']+1,
        compatibility={'sensor':'same'},language=repair_language_fixture(),tactile={'quality':'missing_history'},
        mode='language',encoder=RepairTextMock(),load_skill=store.load)
    assert len(result['matches'])==1
    assert not (tmp_path/'programs').exists()
    store.revoke(row['skill_id'],{'reason':'same-condition public check failed'})
    assert not store.verified_view()


@pytest.mark.parametrize('mode', ['language','tactile','joint'])
def test_repair_rankings_deduplicate_corrections_and_do_not_leak_future(mode):
    from capx.integrations.opentac_shadow_online_skills import retrieve_repair_skills
    rows=[repair_skill_fixture(i) for i in range(8)]
    rows[0]['keys']['tactile'] *= 12
    future=repair_skill_fixture(90);future['available_at_ns']=100
    same=repair_skill_fixture(91);same['provenance']['episode_id']='new'
    pending=repair_skill_fixture(92,status='proposed');pending['has_code_change']=False
    result=retrieve_repair_skills(rows+[future,same,pending],episode_id='new',cutoff_ns=20,
        compatibility={'sensor':'same'},language=repair_language_fixture(),tactile={'quality':'ok','vector':[0.,0.],
        'contract':{'version':'frozen'}},mode=mode,encoder=RepairTextMock())
    assert [m['skill']['skill_id'] for m in result['matches']]==[r['skill_id'] for r in rows[:2]]
    if mode=='joint':assert result['matches'][0]['score']==pytest.approx(2/61)
    assert len(result['candidate_ids'])==8


def test_repair_tactile_missing_explicit_fallback_and_incompatible_space():
    from capx.integrations.opentac_shadow_online_skills import retrieve_repair_skills
    args=dict(records=[repair_skill_fixture()],episode_id='new',cutoff_ns=20,
        compatibility={'sensor':'same'},language=repair_language_fixture(),tactile={'quality':'missing_history'},encoder=RepairTextMock())
    assert retrieve_repair_skills(**args,mode='joint')['fallback']=='language_only:tactile_missing_history'
    assert not retrieve_repair_skills(**args,mode='tactile')['matches']
    args['tactile']={'quality':'ok','vector':[0.,0.],'contract':{'version':'different'}}
    assert not retrieve_repair_skills(**args,mode='tactile')['matches']


def test_error_window_is_causal_at_same_step_and_does_not_require_force_quality(tmp_path):
    from capx.integrations.opentac_shadow_online_skills import causal_error_rows, error_window_key
    class Encoder:
        contract={'representation':'window4','version':'mock','preprocessing':'frozen',
                  'modalities':['features_4d'],'window':{'max_age_seconds':1/30}}
        def encode(self,window):
            assert all(t<=.5 for t in window['timestamps_s'])
            return {'quality':'ok','vector':[0.,1.]}
    rec=evidence_recorder(tmp_path,encoder=Encoder(),failure_triggered=True)
    for i in range(31):record_sample(rec,i,quality='no_contact')
    failure={'physics_step':120,'error_event_seq':rec.sequence+1}
    assert error_window_key(rec,failure)['quality']=='ok'
    late=copy.deepcopy(rec.frames[-1]);late['event_seq']=failure['error_event_seq']+1
    assert late not in causal_error_rows(rec.frames+[late],failure)
    stale={**failure,'physics_step':240}
    assert error_window_key(rec,stale)['quality']=='stale_history'
    empty=evidence_recorder(tmp_path,'fresh',encoder=Encoder(),failure_triggered=True)
    assert error_window_key(empty,failure)['quality']=='missing_history'


def test_error_windows_only_keep_logs_without_frame_retrieval_index(tmp_path):
    rec=evidence_recorder(tmp_path,failure_triggered=True);rec.error_windows_only=True
    for i in range(4):record_sample(rec,i)
    rec.close()
    assert len(rec.frames)==4 and sum(len(c['sample_ids']) for c in rec.clips)==4
    assert not [r for r in rec.index.records() if r['kind'] in ('representation','clip','retrieval')]


def test_language_template_has_no_tactile_or_future_diagnosis():
    from capx.integrations.opentac_shadow_online_skills import repair_language_key
    text=repair_language_key({'notes':['F4'],'dynamics':['light'],'hold_seconds':[.12]},
        {'kind':'release_rejected','phase':'release','messages':['depth=123 force=456'],
         'function_name':'release','future':'repair successful'}, {'release_steps':8,
            'estimated_key_force_N':456,'call_arguments':{'value':123,'z':.04}})
    assert 'release_steps' in text
    assert all(s not in text for s in ('123','456','future','successful'))


def test_numeric_skill_draft_keeps_reported_settings_and_rejects_private_fields():
    from capx.integrations.opentac_shadow_online_skills import validate_skill_draft
    draft={'repair_advice':'Use measured release time.','numeric_correction':[{
        'parameter':'wrist_z_m','before':.01,'after':.04,'unit':'m','limitations':'one release only',
        'before_source':{'kind':'public_sample','sample_id':'s','field':'hand.wrist_z_m'}}]}
    args=dict(failure={},request={},source_before='',source_after='',samples=[{'sample_id':'s','hand':{'wrist_z_m':.01}}])
    result=validate_skill_draft(draft,**args)
    assert result['numeric_correction'][0]['tested_conditions']==[]
    draft['numeric_correction'][0]['before']=.99
    pending=validate_skill_draft(draft,**args)['numeric_correction'][0]
    assert pending['before']==.99 and pending['before_status']=='reported'
    assert 'before_source' not in pending and pending['after_status']=='proposed'
    with pytest.raises(ValueError,match='Private'):validate_skill_draft({'repair_advice':'x','success':True},**args)


def record_sample(recorder, step, flex=.5, z=.01, quality='ok'):
    recorder.sample(action_step=step, physics_step=step*4, attempt=0, note='F4', dynamics='light',
        action=[0.,0.,z,flex], hand={'index_flexion_rad':flex,'wrist_x_offset_m':0.,'wrist_y_m':0.,'wrist_z_m':z,
                                  'key_forces':[987.], 'success':True},
        estimate={'estimated_key_force_N':.35 if quality=='ok' else None,'quality':quality,
                  'calibration_version':'frozen','reward':123}, features=[.1,.2,.3,.4,.5],
        features_4d=[.1,.2,.3,.4], valid_depth_fraction=1., scales=[1.]*5)


def test_numeric_error_window_has_no_image_or_action_time_disk_writes(tmp_path):
    from capx.integrations.opentac_shadow_online_skills import error_window_key
    class Encoder:
        contract={'representation':'window4','version':'fake.v1',
                  'preprocessing':'numeric.v1','modalities':['features_4d'],'window':{'max_age_seconds':1/30}}
        def encode(self, window):
            from capx.memory.trajectory.data import causal_windows
            ids=causal_windows(window['timestamps_s'],window['valid_features_4d'])[-1]
            assert len(ids)==16 and 'frames' not in window
            return {'vector':[0.,1.], 'quality':'ok',
                    'provenance':{'samples':[window['sources'][i] for i in ids]}}
    rec=evidence_recorder(tmp_path,encoder=Encoder(),failure_triggered=True)
    for i in range(100):
        record_sample(rec,i,quality='no_contact')
        rec.event('action',target=[0.,0.,.01,.5],hand_state={})
    assert not list(rec.directory.iterdir())
    failure={'physics_step':396,'error_event_seq':rec.sequence+1}
    key=error_window_key(rec,failure)
    assert key['quality']=='ok' and len(key['sources'])==16
    assert len(rec._error_samples)==16 and not list(rec.directory.iterdir())
    for i in range(100,150):record_sample(rec,i)
    rec.close()
    manifest=json.loads(rec.manifest_path.read_text())
    assert len(manifest['samples'])==16
    assert max(s['physics_step'] for s in manifest['samples'])<=failure['physics_step']
    assert not list(rec.directory.glob('*.npz')) and not (rec.directory/'samples.jsonl').exists()
    assert not any(e['kind']=='action' for e in manifest['events'])


def test_opt_in_full_numeric_evidence_retains_press_and_release_without_private_values(tmp_path):
    rec = evidence_recorder(tmp_path, failure_triggered=True, error_windows_only=True,
                            retain_full_numeric_evidence=True)
    for step in range(12):
        record_sample(rec, step, flex=.04 * step, quality='no_contact' if step < 3 else 'ok')
    for step in range(12, 20):
        record_sample(rec, step, flex=max(0., .44 - .08 * (step - 11)), quality='no_contact')
    assert not list(rec.directory.iterdir())
    assert not rec._error_samples
    rec.close()
    data = json.loads(rec.manifest_path.read_text())
    assert len(data['samples']) == 20
    assert {'press', 'release'} <= {s['phase'] for s in data['samples']}
    assert data['retention'] == 'full_numeric_public_episode.v1'
    assert len({i for c in data['clips'] for i in c['sample_ids']}) == 20
    assert not rec._error_samples  # Archiving does not change causal query selection.
    serialized = json.dumps(data)
    assert all(k not in serialized for k in ('key_forces', 'success', 'reward'))
    assert not list(rec.directory.glob('*.npz'))
    assert not [r for r in rec.index.records() if r['kind'] in ('representation','clip','retrieval','invocation','public_outcome')]


def test_invocation_traces_distinguish_retrieval_call_exception_and_duplicate_calls(tmp_path):
    rec=evidence_recorder(tmp_path)
    source='''def press():
    take_sample()
    raise ValueError('public failure')
def unused():
    pass
for i in range(2):
    try:
        press()
    except ValueError:
        pass
'''
    count=[0]
    def sample():
        count[0]+=1
        record_sample(rec,count[0])
    scope={'take_sample':sample}
    import sys
    previous=sys.gettrace()
    with rec.trace_execution(source,scope) as filename:
        exec(compile(source,filename,'exec'),scope)
    assert sys.gettrace() is previous
    assert len(rec.invocations)==2
    assert rec.invocations[0]['invocation_id']!=rec.invocations[1]['invocation_id']
    assert all(i['exceptions'][0]['type']=='ValueError' and i['end'] is not None for i in rec.invocations)
    assert all(i['outcome']=='raised' for i in rec.invocations)
    assert all(s['invocation_ids'] for s in rec.frames)
    assert any(f['name']=='unused' for f in rec.functions.values())
    assert not any(i['function_id']==f['function_id'] for i in rec.invocations for f in rec.functions.values() if f['name']=='unused')
    assert rec.index.related(rec.clips[0]['clip_id'])
    warm=evidence_recorder(tmp_path,'warm')
    record_sample(warm,1)
    assert warm.retrievals[0]['matches']
    warm.close()


def test_archived_encoder_rejects_changed_or_escaped_frame(tmp_path):
    rec=evidence_recorder(tmp_path)
    record_sample(rec,1)
    rec.close()
    frame=rec.directory/rec.frames[0]['frame']['path']
    frame.write_bytes(b'not the recorded image')
    with pytest.raises(ValueError,match='verification'):
        rec.window(rec.frames)


def test_adapter_records_every_action_without_private_force_queries_or_extra_steps(tmp_path):
    class Task(FakeShadowTask):
        @property
        def step_count(self):return self.steps*4
    fake=Task(stop_after=20)
    low=ShadowPianoLowLevelEnv(univtac_root=str(tmp_path),piano_notes=['F4'],piano_dynamics=['light'],
        task_factory=lambda _:fake,output_dir=str(tmp_path/'run'))
    low.reset()
    fake.tactile=SimpleNamespace(depth_m=np.full((2,3),.01),reference_depth_m=np.full((2,3),.0101))
    calls=[]
    def predict(depth,reference,**kw):
        calls.append(kw['physics_step'])
        kw['diagnostics_out']['features']=[.1,.2,.3,.4,.5]
        return {'quality':'ok','estimated_key_force_N':.35,'calibration_version':'frozen'}
    low._force_estimator=SimpleNamespace(uses_area_rate=True,observe_frame=lambda *a,**kw:None,
        predict=predict,sidecar={'models':{'white':{'iqr':[1.]*5}}})
    low.estimate_key_force=lambda *a:pytest.fail('Recorder must not enter private force audit')
    low._tactile_evidence=evidence_recorder(tmp_path)
    for action in ([0,0,.01,.2],[0,0,.01,.5],[0,0,.01,.5],[0,0,.04,0]):
        low.step(action)
    low._tactile_evidence.close()
    assert fake.steps==4 and calls==[4,8,12,16]
    assert len(low._tactile_evidence.frames)==4
    assert {c['phase'] for c in low._tactile_evidence.clips}=={'press','hold','release'}
    env=CodeExecutionEnvBase(CodeExecEnvConfig(low_level=low,apis=[],prompt='Public only.'))
    env._exec_globals['move_shadow_index']=lambda:low.step([0,0,.04,0])
    result=env._exec_user_code('def release_again():\n    move_shadow_index()\nrelease_again()\n')
    assert result['ok'] and fake.steps==5
    assert len(low._tactile_evidence.invocations)==1
    assert low._tactile_evidence.invocations[0]['outcome']=='returned'


def test_numeric_window_crosses_phase_but_not_reset(tmp_path):
    from capx.integrations.opentac_shadow_online_skills import error_window_key
    class Encoder:
        contract={'representation':'window4','version':'numeric.test','preprocessing':'test',
                  'modalities':['features_4d'],'window':{'max_age_seconds':1/30}}
        def encode(self,w):
            if w['timestamps_s'][-1]-w['timestamps_s'][0]<.5-1e-10:
                return {'quality':'insufficient_valid_history','vector':None}
            return {'quality':'ok','vector':[1.,0.], 'provenance':{'samples':w['sources']}}
    rec=evidence_recorder(tmp_path,'first',Encoder(),failure_triggered=True)
    for i in range(1,34):record_sample(rec,i,flex=.2 if i<31 else .5)
    failure={'physics_step':132,'error_event_seq':rec.sequence+1}
    assert error_window_key(rec,failure)['quality']=='ok'
    assert len({r['clip_id'] for r in rec.frames})>1
    nxt=evidence_recorder(tmp_path,'second',Encoder(),failure_triggered=True)
    for i in range(1,4):record_sample(nxt,i)
    assert error_window_key(nxt,{'physics_step':12,'error_event_seq':nxt.sequence+1})['quality']=='insufficient_valid_history'


def test_missing_normalization_does_not_disable_5d(tmp_path):
    rec=evidence_recorder(tmp_path,encoder={'run_dir':str(tmp_path/'missing'),'representations':['window4']})
    record_sample(rec,1)
    assert rec.frames[0]['instantaneous_5d']==[.1,.2,.3,.4,.5]
    assert rec.encoders[0].encode(rec.window(rec.frames))['quality']=='encoder_unavailable'


def test_temporal_encoding_and_retrieval_are_lazy_until_error(tmp_path):
    from capx.memory.trajectory.data import WINDOW
    from capx.integrations.opentac_shadow_code_memory import _repair_query
    calls=[]
    class Encoder:
        contract={'version':'test','preprocessing':'raw','modalities':['depth_m'],'window':WINDOW}
        def encode(self,w):
            calls.append(w)
            assert all(s['physics_step']<=w['current_physics_step'] for s in w['sources'])
            return {'vector':[1.,0.], 'quality':'ok' if w['timestamps_s'][-1]-w['timestamps_s'][0]>=.5 else 'insufficient_valid_history'}
    rec=evidence_recorder(tmp_path,encoder=Encoder(),failure_triggered=True)
    for i in range(1,35):record_sample(rec,i,flex=.2 if i<31 else .5)
    rec.close()
    assert not calls and not rec.retrievals
    failure={'kind':'code_exception','physics_step':136,'error_event_seq':10000}
    result=_repair_query(rec,failure)
    assert len(calls)==1 and len({s['clip_id'] for s in calls[0]['sources']})>1
    assert result['miss_reason']=='no_compatible_verified_repair' and not result['matches']
    assert len(rec.retrievals)==1

ROOT = Path(__file__).resolve().parents[1]


def endpoints():
    result = {"frame":"fixed_HEAD_display_image", "mirrored":True,
              "display_image_size_px":[960,662], "white_center_spacing_m":.0235}
    for note,midi,y in (("A0",21,-25.5*.0235),("C8",108,25.5*.0235)):
        point=[.515,y,.0725]
        result[note]={"midi":midi,"world_key_top_center_m":point,
                      "display_image_center_px":head_world_to_display_pixel(point,mirrored=True)}
    return result


def choices(e):
    return {k:{"u":e[k]["display_image_center_px"][0]/960,
               "v":(e[k]["display_image_center_px"][1]-22)/640,"color":"white"} for k in ("A0","C8")}


def test_endpoint_calibration_maps_unseen_notes_and_invalidates_geometry():
    e=endpoints(); record=make_calibration(choices(e),e,"first")
    notes=["C4","C#4","D4","D#4","E4","F4","F#4","G4","G#4","A4","A#4","B4"]
    mapped=targets_from_calibration(record,e,notes)
    expected_indices=[23,23.5,24,24.5,25,26,26.5,27,27.5,28,28.5,29]
    for note,index in zip(notes,expected_indices):
        assert mapped[note]["wrist_y_m"] == pytest.approx((index-25.5)*.0235-.033)
    assert mapped["F#4"]["wrist_x_offset_m"] == .07
    changed=copy.deepcopy(e); changed["mirrored"]=False
    with pytest.raises(ValueError): validate_calibration(record,changed)
    wrong=choices(e); wrong["A0"]["u"]+=.03
    with pytest.raises(ValueError,match="8 px"): make_calibration(wrong,e,"bad")


def test_explicit_task_seed_and_prompt_follow_reset(tmp_path):
    fake=FakeShadowTask()
    original=fake.reset
    def reset(*,seed=None):
        obs,info=original(seed=seed)
        rng=np.random.default_rng(seed)
        notes=[str(n) for n in rng.choice(["C4","F#4","B4"],6)]
        info["piano_sequence"]={"midi":[60]*6,"notes":notes,"dynamics":["light"]*6,"hold_seconds":[.12]*6}
        return obs,info
    fake.reset=reset
    low=ShadowPianoLowLevelEnv(univtac_root=str(tmp_path), piano_notes=None,piano_dynamics=None,
        task_seed=3300,task_factory=lambda _:fake,output_dir=str(tmp_path))
    low.endpoint_calibration=True
    low.get_keyboard_endpoints=lambda:{"calibration_ready":False,"mapped_keys":{}}
    env=CodeExecutionEnvBase(CodeExecEnvConfig(low_level=low,apis=[],prompt="Generic request."))
    obs,_=env.reset(seed=1)
    assert fake.seed==3300
    assert len(low.piano_notes)==6
    text=obs["full_prompt"][-1]["content"][0]["text"]
    assert "CURRENT PUBLIC REQUEST" in text and low.piano_notes[0] in text
    assert "actual_force" not in text
    env.reset(seed=17)
    assert fake.seed==3300
    assert env._task_prompt.count("CURRENT PUBLIC REQUEST")==1
    low.endpoint_calibration=False
    env.close()


def test_cached_initial_request_needs_no_image(monkeypatch):
    env=SimpleNamespace(low_level_env=SimpleNamespace(visual_key_map_ready=True))
    obs={"full_prompt":[{"content":[{"type":"text","text":"cached targets"}]}]}
    monkeypatch.setattr(trial,"_capture_raw_visual_snapshot",lambda *_:pytest.fail("Image captured on cache hit"))
    result=trial._capture_initial_visual_feedback(env,obs,{"visual_feedback_context":"shadow_piano","use_visual_feedback":True},None,None)
    assert result==([],[],"cached targets",[])


def test_reflection_whitelist_and_rejected_update_preserve_library(tmp_path,monkeypatch):
    library=tmp_path/"skills.yaml"
    initialize_library(ROOT/"env_configs/opentac/shadow_piano_public_skills_60hz.v1.yaml",library)
    original=library.read_bytes()
    run=tmp_path/"run";run.mkdir()
    (run/"shadow_public_episode.json").write_text(json.dumps({"seed":3300,"sequence":{"notes":["C4"]},
        "keyboard_cache_hit":False,"clear_action_steps":220,"native_notes_completed":999}))
    (run/"shadow_public_observations.jsonl").write_text(json.dumps({"kind":"force_estimate","action_step":1,
        "note":"C4","target":[0,0,0,0],"hand_state":{"wrist_x_offset_m":0,"wrist_y_m":0,"wrist_z_m":0,
        "index_flexion_rad":0,"wrist_and_index_velocity":[0,0,0]},
        "estimate":{"estimated_key_force_N":.3,"sample_step":1,"age_steps":0,"quality":"ok","calibration_version":"frozen"},
        "private_key_top_force_N":987})+'\n')
    (run/"run.log").write_text("native score 999 should never be opened")
    evidence=public_evidence(run)
    assert "987" not in json.dumps(evidence) and "999" not in json.dumps(evidence)
    previous=yaml.safe_load(original)
    proposal={"skills":previous["skills"],"evidence":[]}
    validate_proposal(proposal,previous,evidence)
    proposal=copy.deepcopy(proposal);proposal["skills"][0]["practice"]="Different strategy"
    with pytest.raises(ValueError,match="traceable"):validate_proposal(proposal,previous,evidence)
    from capx.llm import client
    monkeypatch.setattr(client,"query_model",lambda *_: {"content":'{"skills": [], "evidence": []}'})
    result=reflect_once(library,run,tmp_path/"reflection",SimpleNamespace())
    assert not result["accepted"]
    assert library.read_bytes()==original
    assert json.loads((tmp_path/"reflection"/"proposal.json").read_text()) == {"skills": [], "evidence": []}


def test_reflection_deadline_retains_previous_library(tmp_path,monkeypatch):
    import time
    from capx.integrations import opentac_shadow_online_skills as online
    from capx.llm import client
    library=tmp_path/"skills.yaml"
    initialize_library(ROOT/"env_configs/opentac/shadow_piano_public_skills_60hz.v1.yaml",library)
    original=library.read_bytes()
    monkeypatch.setattr(online,"public_evidence",lambda _: {"public_events":[]})
    monkeypatch.setattr(online,"REFLECTION_TIMEOUT_SECONDS",.01)
    monkeypatch.setattr(client,"query_model",lambda *_: time.sleep(1))
    result=reflect_once(library,tmp_path/"run",tmp_path/"reflection",SimpleNamespace())
    assert not result["accepted"] and result["error"].startswith("LLMRequestTimeout:")
    assert 'source=local_deadline phase=skill_reflection' in result['error']
    assert library.read_bytes()==original


def test_random_config_has_no_fixed_melody_and_uses_frozen_model():
    cfg=yaml.safe_load((ROOT/"env_configs/opentac/play_piano_shadow_digit_random6_online_skills.yaml").read_text())
    low=cfg["env"]["cfg"]["low_level"]
    assert low["piano_notes"] is None and low["piano_dynamics"] is None
    assert low["piano_note_count"]==6 and low["action_repeat"]==4
    assert "CAPX_SHADOW_TASK_SEED" in low["task_seed"]
    assert "matched60hz_8episode" in low["digit_force_sidecar"]
    assert "all eight" not in cfg["env"]["cfg"]["prompt"]


def test_empty_library_bootstraps_from_public_evidence_only(tmp_path):
    from capx.integrations.opentac_shadow_online_skills import validate_library
    from capx.integrations.opentac_shadow_skills import inject_shadow_piano_public_skills
    library=tmp_path/"skills.yaml"
    initialize_library(None,library)
    previous=validate_library(library.read_bytes())
    assert previous["skills"]==[]
    env={"cfg":{"prompt":"Fixed control requirements.","apis":["ShadowPianoVisualForceApi"],
        "low_level":{"_target_":"capx.envs.simulators.opentac_shadow.ShadowPianoLowLevelEnv",
            "endpoint_calibration":True}}}
    inject_shadow_piano_public_skills(env,config_path=tmp_path/"config.yaml",library_path=library)
    assert "library starts empty" in env["cfg"]["prompt"]
    assert "delayed_force_response" not in env["cfg"]["prompt"]
    proposal={"skills":[{"id":"fresh_contact_check","trigger":"A new contact was probed.",
        "practice":"Recheck fresh public DIGIT readings before acting; verify this hypothesis."}],
        "evidence":[{"skill_id":"fresh_contact_check","event_ids":[1],"basis":"hypothesis",
            "reason":"The first public contact sample was followed by another reading."}]}
    raw,refs=validate_proposal(proposal,previous,{"public_events":[{"event_id":1}]})
    assert len(validate_library(raw)["skills"])==1 and refs[0]["event_ids"]==[1]
