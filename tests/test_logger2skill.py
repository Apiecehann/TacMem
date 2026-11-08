from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from capx.logger2skill import BASE_COMMIT
from capx.logger2skill.claude import ClaudeSession, claude_environment, summarize_calls, usage_from_stream
from capx.logger2skill.curators import apply_cc_changes, curate_cc, curate_skillopt
from capx.logger2skill.evidence import action_counts, make_case, one_program, validate_public_program
from capx.logger2skill.experiment import STATE_SCHEMA, TASKS, episode_config, load_suite, metrics, run_loop
from capx.logger2skill.processes import run_process, without_proxy
from capx.logger2skill.storage import LibraryStore, digest, pack_library, read_json, render_memory, unpack_library, validate_library, write_json

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def suite():
    return load_suite(REPO / "env_configs/logger2skill/eight_tasks.yaml")


@pytest.fixture
def store(tmp_path):
    result = LibraryStore(tmp_path / "library")
    result.initialize()
    return result


def skill_text(name="release-observation", evidence="case tension_strap_44000: execution failed; suggested correction untested"):
    return (f"---\nid: A1\nname: {name}\nfamily: act\n"
            "summary: In a hold, use physical wait instead of wall-clock sleep; only the former advances the task.\n"
            f"task_types: []\ntasks: [tension_strap]\nevidence: '{evidence}'\n---\n"
            "The logged time.sleep call did not advance simulation time. Verify with a future run.\n")


def add_skill(path, name="release-observation"):
    target = path / "skills" / "act" / f"{name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(skill_text(name), encoding="utf-8")


def case(seed=44000, outcome=False):
    return make_case(f"tension_strap_{seed}", "tension_strap", seed, "hold the requested force", "wait(3)",
                     "wait completed", "", outcome, True)


def accept_review(root):
    return json.dumps({
        "candidate_sha256": digest(root / "candidate"), "verdict": "accept",
        "checks": [{"path": path, "assessment": "CPU fixture review"} for path in
                   ["proposal.json"] + [change["path"] for change in read_json(root / "proposal.json")["changes"]]],
        "issues": []})


def test_clean_suite_is_eight_tasks_with_explicit_budget(suite):
    assert suite["base_commit"] == BASE_COMMIT
    assert set(suite["tasks"]) == TASKS
    assert suite["budget"] == {"rounds": 10, "seconds": None}
    assert len(suite["seeds"]) == len(set(suite["seeds"])) == 10


def test_cc_configs_bound_responses_without_a_dollar_stop_threshold():
    for name in ("eight_tasks", "seven_tasks", "piano_smoke", "weight_initial21_pilot", "roughness_initial21_pilot"):
        cfg = load_suite(REPO / f"env_configs/logger2skill/{name}.yaml")
        assert cfg["backend"] == "cc"
        assert cfg["curator"]["max_responses"] == 12
        assert "max_budget_usd" not in cfg["curator"]
        assert cfg["curator"]["max_output_tokens"] == 4096
        assert "read_max_turns" not in cfg["curator"] and "edit_max_turns" not in cfg["curator"]
        assert cfg["execution"]["max_output_tokens"] == 8192


def test_cold_start_is_empty_and_does_not_fall_back(store):
    root = store.path(store.active())
    assert len(validate_library(root)) == 0
    assert render_memory(root, "tension_strap", 12000) == ""
    from capx.envs.tasks.base import CodeExecutionEnvBase
    env = CodeExecutionEnvBase.__new__(CodeExecutionEnvBase)
    env.cfg = SimpleNamespace(tactile_skill_root=str(root), tactile_skill_max_chars=12000)
    env.low_level_env = SimpleNamespace(task_name="tension_strap")
    assert env._tactile_skill_block() == ""


def test_promotion_validates_routing_and_preserves_old_version(store, tmp_path):
    import shutil
    before = store.active()
    candidate = tmp_path / "candidate"
    shutil.copytree(store.path(before), candidate)
    add_skill(candidate)
    after = store.adopt(candidate, before, "round_000")
    assert after != before
    assert len(validate_library(store.path(before))) == 0
    assert "physical wait" in render_memory(store.path(after), "tension_strap", 12000)
    assert render_memory(store.path(after), "weight_classify", 12000) == ""
    with pytest.raises(ValueError, match="changed during"):
        store.adopt(candidate, before, "round_001")


def test_invalid_candidate_does_not_replace_active(store, tmp_path):
    import shutil
    before = store.active()
    candidate = tmp_path / "candidate"
    shutil.copytree(store.path(before), candidate)
    add_skill(candidate)
    path = candidate / "skills/act/release-observation.md"
    path.write_text(skill_text().replace("task_types: []", "task_types: [nonexistent]"))
    with pytest.raises(ValueError):
        store.adopt(candidate, before, "bad")
    assert store.active() == before


def test_library_tampering_is_detected(store):
    path = store.path(store.active())
    add_skill(path)
    with pytest.raises(ValueError, match="modified"):
        store.active()


def test_pack_unpack_and_path_safety(store, tmp_path):
    root = store.path(store.active())
    document = "<!-- capx-file: skills/act/release-observation.md -->\n" + skill_text().rstrip() + "\n<!-- capx-end -->"
    import shutil
    target = tmp_path / "candidate"
    shutil.copytree(root, target)
    unpack_library(document, target)
    assert pack_library(target) == document
    assert len(validate_library(target)) == 1
    with pytest.raises(ValueError):
        unpack_library(document.replace("skills/act/release-observation.md", "skills/../escape.md"), target)
    with pytest.raises(ValueError):
        unpack_library("unstructured answer", target)


@pytest.mark.parametrize("source", ["", "```python\nx=1\n```\n```python\ny=2\n```", "I will do this\nx=1", "if:"])
def test_single_program_rejects_empty_prose_and_multiple_blocks(source):
    with pytest.raises((ValueError, SyntaxError)):
        one_program(source)


def test_single_program_supports_one_fence_and_public_imports():
    source = one_program("```python\nimport numpy as np\nx = np.mean([1,2])\n```")
    validate_public_program(source, {"check_success"})
    for code in ("check_success()", "env.compute_reward()", "import requests", "getattr(wait, '__globals__')"):
        with pytest.raises(ValueError):
            validate_public_program(code, {"check_success"})


def test_episode_overrides_do_not_enable_retries_or_legacy_memory(suite, store, tmp_path):
    for task, item in suite["tasks"].items():
        cfg = episode_config(task, item, 44007, tmp_path / task, store.path(store.active()), 12000)
        assert cfg["episode_seed"] == 44007
        assert cfg["single_program"] and cfg["trials"] == 1 and cfg["api_servers"] == []
        assert not cfg["use_parallel_ensemble"] and not cfg["evolve_skill_library"]
        assert cfg["env"]["cfg"]["multi_turn_prompt"] is None
        assert cfg["env"]["cfg"]["append_final_check"] == ""
        assert cfg["env"]["cfg"]["tactile_skill_root"] == str(store.path(store.active()))


def test_public_case_excludes_private_force_and_score_fields():
    record = make_case("a", "tension_strap", 1, "target 12 N", "wait(3)",
                       "estimated_tension_N=11.2\ntension_N=11.27\nmetadata=secret", "native_score=0.6", False, True)
    text = json.dumps(record)
    assert "estimated_tension_N=11.2" in text and "11.27" not in text and "secret" not in text
    assert record["final_success"] is False


def test_proxy_and_auth_environment_do_not_leak_into_saved_commands(monkeypatch, tmp_path):
    settings = tmp_path / "credentials.json"
    write_json(settings, {"env": {"ANTHROPIC_AUTH_TOKEN": "test-secret", "HTTPS_PROXY": "bad"}})
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "bad")
    monkeypatch.setenv("https_proxy", "bad")
    env = claude_environment(tmp_path / "work", {"credential_settings": str(settings)})
    assert env["ANTHROPIC_AUTH_TOKEN"] == "test-secret"
    assert not any(k.lower() in {"http_proxy", "https_proxy", "all_proxy"} for k in env)
    assert env["CLAUDE_CONFIG_DIR"].endswith("work/session")
    assert env["CLAUDE_CODE_MAX_RETRIES"] == "0"
    assert env["DISABLE_AUTO_COMPACT"] == "1"


def test_usage_uses_terminal_totals_once_and_keeps_partial_status(tmp_path):
    stream = tmp_path / "stream.jsonl"
    assistant = {"type": "assistant", "message": {"id": "msg1", "usage": {"input_tokens": 100, "output_tokens": 10}}}
    final = {"type": "result", "is_error": False, "usage": {"input_tokens": 210, "output_tokens": 20, "cache_read_input_tokens": 30}}
    stream.write_text("\n".join(json.dumps(x) for x in (assistant, assistant, final)))
    usage = usage_from_stream(stream)
    assert usage["total_tokens"] == 260 and usage["complete"]
    stream.write_text(json.dumps(assistant) + "\n" + json.dumps(assistant))
    usage = usage_from_stream(stream)
    assert usage["total_tokens"] == 110 and not usage["complete"]


def test_process_timeout_is_nonzero_without_touching_other_processes(tmp_path):
    result = run_process([sys.executable, "-c", "import time; time.sleep(5)"], tmp_path,
                         without_proxy(), .05, tmp_path / "process.log")
    assert result["exit_code"] == 124 and result["timed_out"]


def test_usage_includes_auxiliary_model_calls_without_double_counting(tmp_path):
    stream = tmp_path / "stream.jsonl"
    final = {"type": "result", "is_error": False,
             "usage": {"input_tokens": 2, "output_tokens": 1957, "cache_read_input_tokens": 16473},
             "modelUsage": {"claude-sonnet-4-6": {
                 "inputTokens": 4, "outputTokens": 1970, "cacheReadInputTokens": 16473,
                 "cacheCreationInputTokens": 16748}}}
    stream.write_text(json.dumps(final))
    usage = usage_from_stream(stream)
    assert usage["source"] == "result_model_usage" and usage["complete"]
    assert usage["total_tokens"] == 35195
    assert usage["cache_creation_input_tokens"] == 16748
    final["modelUsage"]["auxiliary-model"] = {"inputTokens": 100, "outputTokens": 12}
    stream.write_text(json.dumps(final))
    assert usage_from_stream(stream)["total_tokens"] == 35307


def test_action_count_does_not_count_reset_or_reads(tmp_path):
    rows = [{"kind": "action", "a": "reset", "ok": True}, {"kind": "query", "a": "tactile", "ok": True},
            {"kind": "action", "a": "move", "ok": True}, {"kind": "action", "a": "gripper", "ok": False}]
    path = tmp_path / "actions.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    assert action_counts(path) == {"action_requests": 2, "completed_actions": 1}


def test_real_skillopt_templates_and_edits_use_one_mixed_batch_request(store, tmp_path):
    source = Path("/mnt/data/ljz/opentacbench/references/SkillOpt")
    if not source.is_dir():
        pytest.skip("Pinned SkillOpt source unavailable")
    import shutil
    work = tmp_path / "work"
    work.mkdir()
    candidate = work / "candidate"
    shutil.copytree(store.path(store.active()), candidate)
    document = "<!-- capx-file: skills/act/release-observation.md -->\n" + skill_text().rstrip() + "\n<!-- capx-end -->"
    patch = {"reasoning": "Only records an observed failure", "edits": [{"op": "append", "content": document}]}
    cases = [case(44000 + i, [False, True, None][i % 3]) for i in range(9)]
    cases[-1].update(final_success=True, execution_ok=False)
    class FakeSession:
        failed = False
        def __init__(self):
            self.work = work
            self.calls = []
        def ask(self, prompt, *, stage):
            assert "Frozen taxonomy" in prompt
            assert "SkillOpt template: analyst_error" in prompt
            assert "SkillOpt template: analyst_success" in prompt
            assert "name must exactly equal the filename stem" in prompt
            assert "task_types contains taxonomy category keys" in prompt
            assert "outcome=unknown" in prompt and "outcome=conflicting_outcomes" in prompt
            assert all(prompt.count('"case_id": "' + c["case_id"] + '"') == 1 for c in cases)
            self.calls.append({"stage": stage, "usage": {"total_tokens": 10}})
            return json.dumps({"patch": patch})
    session = FakeSession()
    result = curate_skillopt(session, cases, candidate, source)
    assert [c["stage"] for c in session.calls] == ["skillopt_batch"]
    assert result["method"] == "skillopt_single_call" and result["max_model_calls"] == 1
    assert result["environment_retest"] is False
    assert len(result["case_outcomes"]) == 9
    assert len(validate_library(candidate)) == 1
    assert result["applied"][0]["status"] == "applied_append"


@pytest.mark.parametrize("edit", [
    {"op": "replace", "target": "does-not-exist", "content": "new"},
    {"op": "append", "content": "invalid file delimiters"},
])
def test_skillopt_invalid_edits_do_not_change_candidate_or_request_again(store, tmp_path, edit):
    import shutil
    work = tmp_path / "work"
    candidate = work / "candidate"
    shutil.copytree(store.path(store.active()), candidate)
    before = digest(candidate)
    calls = []
    def ask(prompt, *, stage):
        calls.append(stage)
        return json.dumps({"patch": {"reasoning": "test", "edits": [edit]}})
    session = SimpleNamespace(work=work, ask=ask)
    with pytest.raises(ValueError):
        curate_skillopt(session, [case()], candidate, Path("/mnt/data/ljz/opentacbench/references/SkillOpt"))
    assert calls == ["skillopt_batch"]
    assert digest(candidate) == before


def test_each_episode_curates_before_the_next_task_and_resume_does_not_repeat(suite, tmp_path):
    suite = copy.deepcopy(suite)
    suite["budget"]["rounds"] = 2
    loaded = []
    curated = []
    events = []
    current = [0]
    def execute(config, task, seed, output, memory, timeout):
        events.append(("execute", task, seed))
        loaded.append(memory.name)
        current[0] += 2
        return {"case": make_case(f"{task}_{seed}", task, seed, "goal", "wait(1)", "done", "", seed == 44001, True),
                "action_requests": 3, "completed_actions": 2,
                "coding_usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10, "complete": True}}
    def curate(config, folder, evidence, store, pointer, timeout):
        import shutil
        assert isinstance(evidence, dict)
        assert folder.name == evidence["task"]
        events.append(("curate", evidence["task"], evidence["seed"]))
        candidate = folder / "candidate"
        shutil.copytree(store.path(pointer), candidate)
        add_skill(candidate)
        (candidate / "skills/act/release-observation.md").write_text(
            skill_text(evidence=evidence["case_id"]), encoding="utf-8")
        after = store.adopt(candidate, pointer, f"episode_{evidence['task']}_{evidence['seed']}")
        curated.append(after["version"])
        current[0] += 1
        return {"status": "adopted", "library_before": pointer, "library_after": after,
                "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30,
                          "calls": 1, "complete": True}}
    state = run_loop(suite, tmp_path, execute, curate, clock=lambda: current[0])
    assert loaded == ["empty"] + curated[:-1]
    assert len(set(curated)) == 2 * len(TASKS)
    assert events == [(stage, task, seed) for seed in suite["seeds"][:2]
                      for task in suite["tasks"] for stage in ("execute", "curate")]
    before_metrics = state["metrics"]
    state = run_loop(suite, tmp_path, execute, curate, clock=lambda: current[0])
    assert len(loaded) == 2 * len(TASKS)
    assert state["metrics"] == before_metrics == read_json(tmp_path / "metrics.json")
    assert state["metrics"]["coding_tokens"]["reported_total_tokens"] == 20 * len(TASKS)
    assert state["metrics"]["curation_tokens"]["reported_total_tokens"] == 60 * len(TASKS)
    assert state["metrics"]["total_seconds"] == 6 * len(TASKS)
    assert [r["macro_sr"] for r in state["metrics"]["round_history"]] == [0, .5]
    for task, result in state["metrics"]["per_task"].items():
        assert result["attempts"] == result["curations_attempted"] == result["curation_model_calls"] == 2
        assert result["successes"] == 1
        assert result["total_tokens"]["reported_total_tokens"] == 80
        assert result["total_tokens"]["complete"]
        assert result["total_execute_steps"] == 6
        assert result["total_completed_actions"] == 4
        assert result["total_seconds"] == 6
        assert [h["task_iteration"] for h in result["history"]] == [1, 2]
        assert [h["cumulative"]["sr_all_attempts"] for h in result["history"]] == [0, .5]
        assert [h["cumulative"]["total_tokens"]["reported_total_tokens"] for h in result["history"]] == [40, 80]
        for h in result["history"]:
            assert h["execution_elapsed_experiment_s"] == h["execution_index"] * 3 - 1
            assert h["elapsed_experiment_s"] == h["execution_index"] * 3
            record = read_json(tmp_path / f"round_{h['task_iteration'] - 1:03d}" / task / "episode_record.json")
            assert record["library_after"] == h["library_after"]


def test_time_budget_does_not_append_curation_or_next_seed(suite, tmp_path):
    suite = copy.deepcopy(suite)
    suite["budget"] = {"rounds": None, "seconds": 5}
    current = [0]
    def execute(config, task, seed, output, memory, timeout):
        current[0] += 5
        return {"case": make_case(f"{task}_{seed}", task, seed, "", "", "", "", False, True), "action_requests": 1}
    def curate(*args):
        pytest.fail("No curation may start after the budget cutoff")
    state = run_loop(suite, tmp_path, execute, curate, clock=lambda: current[0])
    assert state["metrics"]["attempts"] == 1
    episode = state["rounds"][0]["episodes"][0]
    assert episode["curation"]["status"] == "skipped_budget"
    result = state["metrics"]["per_task"][episode["case"]["task"]]
    assert result["curations_attempted"] == result["curation_model_calls"] == 0
    assert result["curation_tokens"]["reported_total_tokens"] == 0
    assert state["status"] == "budget_exhausted"
    assert run_loop(suite, tmp_path, execute, curate, clock=lambda: current[0])["metrics"] == state["metrics"]


@pytest.mark.parametrize("stage", ["execution", "curation"])
def test_interrupted_request_is_not_reissued(suite, tmp_path, stage):
    store = LibraryStore(tmp_path / "library")
    store.initialize()
    write_json(tmp_path / "round_000/round.json", {"schema": STATE_SCHEMA, "index": 0, "seed": 44000,
        "episodes": [], "inflight": {"stage": stage}, "status": "running"})
    with pytest.raises(RuntimeError, match="no automatic repeat"):
        run_loop(suite, tmp_path, execute=lambda *a: pytest.fail("Repeated execution"),
                 curate=lambda *a: pytest.fail("Repeated model request"))


def test_saved_execution_resumes_only_its_curation(suite, tmp_path, store):
    suite = copy.deepcopy(suite)
    suite["budget"]["rounds"] = 1
    suite["tasks"] = {"tension_strap": suite["tasks"]["tension_strap"]}
    write_json(tmp_path / "round_000/round.json", {"schema": STATE_SCHEMA, "index": 0, "seed": 44000,
        "status": "running", "episodes": [{"case": case(), "library_before": store.active(),
            "execution_index": 1, "elapsed_s": 7, "coding_usage": {"total_tokens": 17, "complete": True}}]})
    calls = []
    def curate(config, folder, evidence, library, pointer, timeout):
        calls.append(evidence["case_id"])
        return {"status": "unchanged", "library_before": pointer, "library_after": pointer,
                "elapsed_s": 2, "usage": {"total_tokens": 11, "complete": True, "calls": 1}}
    state = run_loop(suite, tmp_path, execute=lambda *a: pytest.fail("Repeated execution"), curate=curate)
    again = run_loop(suite, tmp_path, execute=lambda *a: pytest.fail("Repeated execution"), curate=curate)
    assert calls == ["tension_strap_44000"]
    assert state["metrics"] == again["metrics"]
    assert state["metrics"]["total_tokens"]["reported_total_tokens"] == 28
    assert state["metrics"]["total_seconds"] == 9


def test_failed_curation_keeps_library_for_next_task_and_charges_source_task(suite, tmp_path):
    suite = copy.deepcopy(suite)
    suite["budget"]["rounds"] = 1
    suite["tasks"] = {k: suite["tasks"][k] for k in ("tension_strap", "arm_shadow_piano")}
    loaded = []
    def execute(config, task, seed, output, memory, timeout):
        loaded.append(memory.name)
        return {"case": make_case(f"{task}_{seed}", task, seed, "", "", "", "", False, False),
                "elapsed_s": 3, "coding_usage": {"total_tokens": 5, "complete": True}}
    def curate(config, folder, evidence, library, pointer, timeout):
        return {"status": "failed", "library_before": pointer, "library_after": pointer,
                "elapsed_s": 4, "timed_out": evidence["task"] == "tension_strap",
                "usage": {"total_tokens": 91 if evidence["task"] == "tension_strap" else 13,
                          "complete": False, "calls": 1}}
    state = run_loop(suite, tmp_path, execute, curate)
    assert loaded == ["empty", "empty"]
    rows = state["metrics"]["per_task"]
    assert rows["tension_strap"]["curation_tokens"]["reported_total_tokens"] == 91
    assert rows["arm_shadow_piano"]["curation_tokens"]["reported_total_tokens"] == 13
    assert rows["tension_strap"]["curation_timeouts"] == 1
    assert state["metrics"]["curation_model_calls"] == 2
    assert not state["metrics"]["total_tokens"]["complete"]
    assert state["metrics"]["total_seconds"] == 14


def test_old_batch_records_are_preserved_without_cost_reattribution(suite, tmp_path):
    old = {"index": 0, "seed": 44000, "episodes": [], "curation": {"usage": {"total_tokens": 500}}}
    path = tmp_path / "round_000/round.json"
    write_json(path, old)
    original = path.read_bytes()
    with pytest.raises(ValueError, match="cannot be assigned"):
        metrics([old])
    with pytest.raises(RuntimeError, match="Old batch"):
        run_loop(suite, tmp_path, execute=lambda *a: pytest.fail("Unexpected execution"))
    assert path.read_bytes() == original
    assert not (tmp_path / "state.json").exists()
    assert not (tmp_path / "library").exists()


def test_pending_and_missing_usage_are_not_reported_as_complete():
    pending = {"case": case(outcome=None), "elapsed_s": 2,
               "coding_usage": {"total_tokens": 19, "complete": True}}
    first = metrics([{"episodes": [pending]}])
    assert first["native_outcomes_unknown"] == 1
    assert first["sr_all_attempts"] == 0 and first["sr_known_outcomes"] is None
    assert first["pending_curations"] == 1
    assert not first["total_tokens"]["complete"]
    assert not first["time_records_complete"]
    assert not first["action_counts_complete"]
    pending["curation"] = {"status": "failed", "usage": {"total_tokens": None, "complete": False, "calls": 1}}
    second = metrics([{"episodes": [pending]}])
    assert second["pending_curations"] == 0
    assert second["total_tokens"]["reported_total_tokens"] == 19
    assert second["total_tokens"]["missing_usage_records"] == 1
    assert not second["total_tokens"]["complete"]
    assert not second["time_records_complete"]


def test_coding_http_error_is_not_retried(monkeypatch):
    import requests
    from capx.envs.launch import LaunchArgs
    from capx.llm import client
    calls = []
    def post(url, **kwargs):
        calls.append((url, json.loads(kwargs["data"])))
        response = requests.Response()
        response.status_code = 503
        response._content = b'{"error":"unavailable"}'
        return response
    monkeypatch.setattr(client.requests, "post", post)
    args = LaunchArgs(config_path="unused", model="gpt-6.1-sol", max_tokens=8192, max_query_retries=0)
    with pytest.raises(requests.HTTPError):
        client.query_model(args, [{"role": "user", "content": "code please"}])
    assert len(calls) == 1
    assert calls[0][1]["max_output_tokens"] == 8192


def test_actual_trial_one_request_one_execution_with_loaded_memory(monkeypatch, tmp_path, store):
    from capx.envs import trial
    from capx.envs.launch import LaunchArgs
    out = tmp_path / "trial"
    out.mkdir()
    calls = []
    memory = store.path(store.active())
    class FakeEnv:
        low_level_env = SimpleNamespace()
        cfg = SimpleNamespace(append_final_check="", tactile_skill_root=str(memory))
        _task_prompt = "Test task"
        def reset(self, options, seed):
            assert seed == 44001
            return {"full_prompt": [{"role": "user", "content": [{"type": "text", "text": "test prompt"}]}]}, {}
        def step(self, code):
            assert code == 'print("once")'
            calls.append("execute")
            return {}, 0.0, False, False, {"sandbox_rc": 0, "stdout": "once", "stderr": "", "task_completed": False}
        def _task_name(self):
            return "tension_strap"
        def _tactile_skill_block(self):
            return ""
    def query(args, prompt):
        calls.append("query")
        return {"content": 'print("once")', "reasoning": None, "usage": {"input_tokens": 5, "output_tokens": 6}}
    monkeypatch.setattr(trial, "_query_model", query)
    monkeypatch.setattr(trial, "_capture_initial_visual_feedback", lambda *a: ([], [], "test"))
    monkeypatch.setattr(trial, "_save_trial_video", lambda *a, **kw: None)
    monkeypatch.setattr(trial, "_handle_multi_turn_step", lambda *a, **kw: pytest.fail("Unexpected regeneration"))
    cfg = {"output_dir": str(out), "episode_seed": 44001, "case_id": "tension_strap_44001",
           "logger2skill_episode": True, "single_program": True, "record_video": False,
           "use_img_differencing": False, "use_oracle_code": False, "use_parallel_ensemble": False}
    summary = trial._run_single_trial(FakeEnv(), 1, LaunchArgs(config_path="unused"), cfg, "try another coding block")
    assert calls == ["query", "execute"]
    assert summary.num_regenerations == 0 and summary.num_code_blocks == 1
    assert read_json(out / "public_case.json")["final_success"] is False
    assert read_json(out / "coding_usage.json")["input_tokens"] == 5
    assert read_json(out / "loaded_memory.json")["root"] == str(memory)


def test_cc_command_is_single_turn_without_tools_or_retries(monkeypatch, tmp_path):
    import capx.logger2skill.claude as module
    observed = []
    class FakeProcess:
        returncode = 0
        def __init__(self, command, **kwargs):
            observed.append((command, kwargs))
            self.output = kwargs["stdout"]
        def communicate(self, input, timeout):
            self.output.write(json.dumps({"type": "assistant", "message": {
                "id": "msg1", "content": [{"type": "text", "text": "done"}]}}) + "\n")
            self.output.write(json.dumps({"type": "result", "is_error": False, "result": "done",
                                          "num_turns": 1,
                                          "usage": {"input_tokens": 12, "output_tokens": 7}}) + "\n")
            self.output.flush()
    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)
    session = ClaudeSession(tmp_path, {"command": "never-executed", "model": "claude-sonnet-4-6",
                                       "credential_settings": str(tmp_path / "absent")},
                            time.monotonic() + 5, max_calls=1)
    assert session.ask("test", stage="maintenance") == "done"
    command, kwargs = observed[0]
    assert "--json-schema" not in command and "--resume" not in command and "--continue" not in command
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--max-turns") + 1] == "1"
    assert command[command.index("--name") + 1] == "logger2skill-maintenance"
    assert "--allowedTools" not in command
    assert kwargs["env"]["CLAUDE_CODE_MAX_RETRIES"] == "0"
    assert kwargs["cwd"] == tmp_path
    assert summarize_calls(tmp_path)["total_tokens"] == 19
    assert read_json(tmp_path / "calls.json")[0]["assistant_messages"] == 1
    with pytest.raises(RuntimeError, match="request limit"):
        session.ask("try again", stage="maintenance")
    assert len(observed) == 1


@pytest.mark.parametrize("violation", ["tool", "assistant_messages", "num_turns"])
def test_cc_rejects_unexpected_tool_use_or_extra_turns(monkeypatch, tmp_path, violation):
    import capx.logger2skill.claude as module
    messages = [{"type": "assistant", "message": {"id": "msg1", "content": []}}]
    if violation == "tool":
        messages[0]["message"]["content"] = [{"type": "tool_use", "name": "Read", "input": {}}]
    if violation == "assistant_messages":
        messages.append({"type": "assistant", "message": {"id": "msg2", "content": []}})
    messages.append({"type": "result", "is_error": False, "result": "done",
                     "num_turns": 2 if violation == "num_turns" else 1})
    calls = []
    class FakeProcess:
        returncode = 0
        def __init__(self, command, **kwargs):
            calls.append(command)
            self.output = kwargs["stdout"]
        def communicate(self, input, timeout):
            self.output.write("\n".join(json.dumps(m) for m in messages))
    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)
    session = ClaudeSession(tmp_path, {"model": "test", "credential_settings": str(tmp_path / "absent")},
                            time.monotonic() + 5)
    with pytest.raises(RuntimeError, match="single-response"):
        session.ask("test", stage="maintenance")
    with pytest.raises(RuntimeError, match="retry is disabled"):
        session.ask("retry", stage="maintenance")
    assert len(calls) == 1


def test_cc_reads_public_files_and_existing_skills(store, tmp_path):
    import shutil
    work = tmp_path / "curation"
    candidate = work / "candidate"
    shutil.copytree(store.path(store.active()), candidate)
    add_skill(candidate)
    changed = skill_text().replace("Verify with a future run.", "尚未验证，保留原单位和条件。")
    changes = [{"op": "upsert", "path": "skills/act/release-observation.md", "content": changed}]
    calls = []
    class FakeSession:
        def ask(self, prompt, *, stage, read_root=None, required_reads=(), write_root=None):
            calls.append((prompt, stage, read_root))
            if stage == "review":
                assert (candidate / "skills/act/release-observation.md").read_text() == skill_text()
                return accept_review(read_root)
            assert write_root == read_root / "working"
            (write_root / changes[0]["path"]).write_text(changed, encoding="utf-8")
            return "Clarify uncertainty"
    session = FakeSession()
    session.work = work
    report = curate_cc(session, [case()], candidate, REPO)
    assert len(calls) == 1 and calls[0][1] == "maintenance"
    assert report["independent_reviews"] == 0 and report["review_verdict"] == "not_run"
    prompt = calls[0][0]
    inputs = calls[0][2]
    assert inputs == (work / "public_inputs").resolve()
    assert skill_text().strip() not in prompt
    assert (inputs / "library/skills/act/release-observation.md").read_text() == skill_text()
    rules = (REPO / "capx/tactile_skills/MAINTENANCE.md").read_text(encoding="utf-8")
    assert rules not in prompt
    assert (inputs / "maintenance.txt").read_bytes() == (REPO / "capx/tactile_skills/MAINTENANCE.md").read_bytes()
    assert read_json(work / "maintenance_snapshot.json")["rules"] == rules
    assert "## 五、基线数字" not in prompt
    assert "第二十九组把灯泡块带到 654 词" not in prompt
    assert json.dumps([case()], ensure_ascii=False) not in prompt
    assert (inputs / "executed_code.py").read_text() == case()["code"]
    assert (inputs / "public_stdout.txt").read_text() == case()["stdout"]
    assert read_json(inputs / "result.json")["final_success"] is False
    task_context = read_json(inputs / "task_context.json")
    assert task_context["task_prompt"] == case()["task_prompt"] and task_context["final_success"] is False
    assert case()["stdout"] in (inputs / "execution_log.txt").read_text()
    index = read_json(inputs / "library_index.json")
    assert index["skills"][0]["path"] == str(inputs / "working/skills/act/release-observation.md")
    assert index["skills"][0]["applicable_to_current_task"] is True
    assert str(inputs / "library") + "/" not in prompt
    assert read_json(work / "public_input_context.json")["task_type"] == "manipulate"
    assert str(inputs / "maintenance.txt") in prompt
    assert "请按照 maintenance.txt 的现行维护规则，依次完成四个阶段" in prompt
    assert "3. 编辑技能：读取索引中的相关 Markdown，按维护规则直接新增、修改或保持不变。" in prompt
    assert "4. 复核修改：对照维护规则复核已编辑的 Markdown。" in prompt
    assert "简要说明修改" not in prompt and "Finish with a brief summary" not in prompt
    assert "Return exactly one JSON object" not in prompt
    assert "essential numbers/units/limits" not in prompt
    assert "An unsuccessful episode can still provide local observations" not in prompt
    assert "line numbers separately from simulation step numbers" not in prompt
    assert (candidate / "skills/act/release-observation.md").read_text(encoding="utf-8") == changed
    assert read_json(work / "cc_edits.json") == report
    assert report["before_sha256"] != report["after_sha256"]
    assert report["format_feedback_used"] is False


def test_cc_can_delete_add_or_leave_library_unchanged(store, tmp_path):
    import shutil
    candidate = tmp_path / "candidate"
    shutil.copytree(store.path(store.active()), candidate)
    add_skill(candidate)
    changes = [{"op": "delete", "path": "skills/act/release-observation.md"},
               {"op": "upsert", "path": "skills/act/physical-wait.md", "content": skill_text("physical-wait")}]
    apply_cc_changes(json.dumps({"summary": "Rename", "changes": changes}), candidate)
    assert not (candidate / "skills/act/release-observation.md").exists()
    assert len(validate_library(candidate)) == 1
    result = apply_cc_changes('{"summary":"No change needed","changes":[]}', candidate)
    assert result["before_sha256"] == result["after_sha256"]
    assert len(validate_library(store.path(store.active()))) == 0


def test_cc_quotes_invalid_text_without_rewriting_advice(store, tmp_path):
    import shutil
    candidate = tmp_path / "candidate"
    shutil.copytree(store.path(store.active()), candidate)
    content = skill_text()
    old = next(line for line in content.splitlines() if line.startswith("summary: "))
    advice = "During hold: correction = -0.4*(force-target)/stiffness; unverified."
    content = content.replace(old, "summary: " + advice)
    response = json.dumps({"summary": "Observed proposal", "changes": [
        {"op": "upsert", "path": "skills/act/release-observation.md", "content": content}]})
    report = apply_cc_changes(response, candidate)
    skill = validate_library(candidate).get("release-observation")
    assert skill.summary == advice
    assert report["format_repairs"][0]["quoted_text_fields"] == ["summary"]
    assert json.loads(response)["changes"][0]["content"] == content
    assert content.split("\n---\n", 1)[1] in (candidate / "skills/act/release-observation.md").read_text()
    assert store.active()["version"] == "empty"


@pytest.mark.parametrize("content", [
    '---\nsummary: "During hold: wait"\n---\nBody\n',
    '---\nsummary: |\n  During hold: wait\n---\nBody\n',
    '---\nsummary: [broken\n---\nBody\n',
    '---\ntask_types: [broken\n---\nBody\n',
    '---\nsummary: During hold: wait\n  ambiguous continuation\n---\nBody\n',
])
def test_cc_does_not_guess_at_other_frontmatter(content):
    from capx.logger2skill.curators import quote_invalid_frontmatter_text
    assert quote_invalid_frontmatter_text(content) == (content, [])


@pytest.mark.parametrize("invalid_change", [
    {"op": "upsert", "path": "skills/../escape.md", "content": "bad"},
    {"op": "upsert", "path": "taxonomy.yaml", "content": "bad"},
    {"op": "upsert", "path": "skills/act/bad.md", "content": "missing frontmatter"},
    {"op": "delete", "path": "skills/act/missing.md"},
    {"op": "delete", "path": "skills/act/release-observation.md"},
])
def test_cc_rejects_entire_invalid_batch_without_partial_writes(store, tmp_path, invalid_change):
    import shutil
    candidate = tmp_path / "candidate"
    shutil.copytree(store.path(store.active()), candidate)
    before = digest(candidate)
    changes = [{"op": "upsert", "path": "skills/act/release-observation.md", "content": skill_text()}, invalid_change]
    with pytest.raises(ValueError):
        apply_cc_changes(json.dumps({"summary": "test", "changes": changes}), candidate)
    assert digest(candidate) == before
    assert not (candidate / "escape.md").exists()


@pytest.mark.parametrize("backend", ["cc", "skillopt"])
def test_curator_invalid_response_keeps_active_with_bounded_format_feedback(monkeypatch, suite, store, tmp_path, backend):
    from capx.logger2skill import experiment, worker
    suite = copy.deepcopy(suite)
    suite["backend"] = backend
    calls = []
    def fake_ask(self, prompt, *, stage, read_root=None, required_reads=(), write_root=None):
        assert self.max_calls == (2 if backend == "cc" else 1)
        calls.append((prompt, stage, read_root))
        if write_root is not None:
            add_skill(write_root)
            (write_root / "skills/act/release-observation.md").write_text("invalid Markdown skill")
        return "Not valid JSON"
    def run_worker(command, *args):
        return {"exit_code": worker.main(Path(command[-1])), "timed_out": False}
    monkeypatch.setattr(ClaudeSession, "ask", fake_ask)
    monkeypatch.setattr(experiment, "run_process", run_worker)
    folder = tmp_path / "round_000"
    folder.mkdir()
    before = store.active()
    result = experiment.curate_episode(suite, folder, case(), store, before, 300)
    assert result["status"] == "failed" and len(calls) == (2 if backend == "cc" else 1)
    if backend == "cc":
        assert calls[0][2] is not None and calls[1][2] is not None
        assert calls[1][1] == "format_repair"
        assert "candidate files failed format validation" in calls[1][0]
    assert result["library_after"] == before == store.active()
    assert (folder / f"curation/{backend}_response.txt").read_text() == "Not valid JSON"
    assert read_json(folder / "curation/job.json")["cases"] == [case()]


@pytest.mark.parametrize("prefix,suffix", [("```json\n", "\n```"), ("Analysis complete.\n", "")])
def test_cc_accepts_unambiguous_json_envelopes_without_changing_values(store, tmp_path, prefix, suffix):
    import shutil
    candidate = tmp_path / "candidate"
    shutil.copytree(store.path(store.active()), candidate)
    payload = {"summary": 'Only an estimate: "0.5 N"; unverified.', "changes": [
        {"op": "upsert", "path": "skills/act/release-observation.md", "content": skill_text()}]}
    report = apply_cc_changes(prefix + json.dumps(payload) + suffix, candidate)
    assert report["envelope_removed"] is True
    assert report["summary"] == payload["summary"]
    assert (candidate / "skills/act/release-observation.md").read_text() == skill_text()


def test_cc_ambiguous_json_objects_do_not_mutate_candidate(store, tmp_path):
    import shutil
    candidate = tmp_path / "candidate"
    shutil.copytree(store.path(store.active()), candidate)
    before = digest(candidate)
    response = '{"summary":"first","changes":[]}\n{"summary":"second","changes":[]}'
    with pytest.raises(ValueError, match="Invalid JSON"):
        apply_cc_changes(response, candidate)
    assert digest(candidate) == before


@pytest.mark.parametrize("defect", ["yaml", "missing_field", "field_type"])
def test_cc_repairs_format_once_without_reloading_case_or_mutating_before_validation(store, tmp_path, defect):
    import shutil
    work = tmp_path / "curation"
    candidate = work / "candidate"
    shutil.copytree(store.path(store.active()), candidate)
    original = digest(candidate)
    content = skill_text()
    good = {"summary": 'Preserve "8 intervals"; proposed only.', "changes": [
        {"op": "upsert", "path": "skills/act/release-observation.md", "content": content}]}
    if defect == "yaml":
        bad = content.replace("task_types: []", "task_types: [broken")
    elif defect == "field_type":
        bad = content.replace("task_types: []", "task_types: 42")
    else:
        bad = content.replace("family: act\n", "")
    calls = []
    class FakeSession:
        def ask(self, prompt, *, stage, read_root=None, required_reads=(), write_root=None):
            calls.append((prompt, stage, read_root))
            assert digest(candidate) == original
            if stage == "review":
                return accept_review(read_root)
            if len(calls) == 1:
                assert read_root is not None
                add_skill(write_root)
                (write_root / "skills/act/release-observation.md").write_text(bad)
                return "Edited candidate"
            assert len(calls) == 2 and stage == "format_repair" and read_root is not None
            assert "public_stdout.txt" not in prompt
            assert "preserving the intended content" in prompt
            (write_root / "skills/act/release-observation.md").write_text(content)
            return good["summary"]
    session = FakeSession()
    session.work = work
    report = curate_cc(session, [case()], candidate, REPO)
    assert len(calls) == 2 and report["format_feedback_used"] is True
    assert (candidate / "skills/act/release-observation.md").read_text() == content
    assert read_json(work / "cc_accepted_response.json") == good
    assert [x["ok"] for x in read_json(work / "cc_validation.json")] == [False, True]
    assert (work / "cc_response.txt").read_text() == "Edited candidate"
    assert len(validate_library(store.path(store.active()))) == 0


def test_cc_transport_failure_does_not_trigger_format_feedback(store, tmp_path):
    import shutil
    candidate = tmp_path / "candidate"
    shutil.copytree(store.path(store.active()), candidate)
    calls = []
    class FakeSession:
        work = tmp_path
        def ask(self, prompt, *, stage, read_root=None, required_reads=(), write_root=None):
            calls.append(stage)
            raise RuntimeError("API unavailable")
    with pytest.raises(RuntimeError, match="API unavailable"):
        curate_cc(FakeSession(), [case()], candidate, REPO)
    assert calls == ["maintenance"]
    assert not (tmp_path / "cc_format_feedback.json").exists()












def test_cc_read_guard_blocks_private_changed_and_symlinked_files(tmp_path):
    import hashlib
    import subprocess
    from capx.logger2skill import read_guard
    root = tmp_path / "public"
    root.mkdir()
    public = root / "log.txt"
    public.write_text("public")
    private = tmp_path / "private.txt"
    private.write_text("private content must not be read")
    (root / "alias.txt").symlink_to(private)
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"root": str(root), "files": {
        str(public): hashlib.sha256(public.read_bytes()).hexdigest()}})
    audit = tmp_path / "access.jsonl"
    def check(path, allowed):
        result = subprocess.run([sys.executable, str(Path(read_guard.__file__)), str(manifest), str(audit)],
            input=json.dumps({"tool_name": "Read", "tool_input": {"file_path": str(path)}}),
            capture_output=True, text=True, check=True)
        assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == ("allow" if allowed else "deny")
    check(public, True)
    check(private, False)
    check(root / "alias.txt", False)
    check(root / "missing", False)
    public.write_text("changed")
    check(public, False)
    assert len(audit.read_text().splitlines()) == 5
    assert "private content" not in audit.read_text()


def test_local_cc_and_format_feedback_usage_is_charged_to_the_task(monkeypatch, tmp_path, store):
    import shutil
    import capx.logger2skill.claude as module
    work = tmp_path / "curation"
    candidate = work / "candidate"
    shutil.copytree(store.path(store.active()), candidate)
    seen = []
    class FakeProcess:
        returncode = 0
        def __init__(self, command, **kwargs):
            self.command, self.kwargs = command, kwargs
            seen.append((command, kwargs))
        def communicate(self, input, timeout):
            toolset = set(self.command[self.command.index("--tools") + 1].split(","))
            reading = "Read" in toolset
            editing = "Write" in toolset
            review = self.command[self.command.index("--name") + 1] == "logger2skill-review"
            if editing:
                target = self.kwargs["cwd"] / "working"
                add_skill(target)
                if len(seen) == 1:
                    (target / "skills/act/release-observation.md").write_text("invalid Markdown skill")
            required = json.loads(input.split("Required files: ")[-1]) if review else []
            count = 3 if reading else 1
            for i in range(count):
                content = [{"type": "tool_use", "name": "Read", "input": {"file_path": "maintenance.txt"}}] if reading and i == 0 else []
                if review and i == 0:
                    content = [{"type": "tool_use", "name": "Read", "id": f"read{j}",
                                "input": {"file_path": path}} for j, path in enumerate(required)]
                self.kwargs["stdout"].write(json.dumps({"type": "assistant", "message": {
                    "id": f"msg{i}", "content": content}}) + "\n")
            if review:
                self.kwargs["stdout"].write(json.dumps({"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": f"read{j}", "content": "public evidence"}
                    for j in range(len(required))]}}) + "\n")
            self.kwargs["stdout"].write(json.dumps({"type": "result", "is_error": False,
                "result": accept_review(self.kwargs["cwd"]) if review else "Edited candidate",
                "num_turns": 9 if reading else 1,
                "usage": {"input_tokens": 100 if reading else 40, "output_tokens": 20 if reading else 10,
                          "cache_read_input_tokens": 50 if reading else 0}}) + "\n")
    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)
    session = ClaudeSession(work, {"model": "test", "credential_settings": str(tmp_path / "absent")},
                            time.monotonic() + 30, max_calls=2)
    report = curate_cc(session, [case()], candidate, REPO)
    assert report["format_feedback_used"] is True and len(seen) == 2
    first, options = seen[0]
    assert options["cwd"] == work / "public_inputs"
    assert "brief summary" not in first[first.index("--system-prompt") + 1]
    assert first[first.index("--max-turns") + 1] == "6"
    settings = json.loads(first[first.index("--settings") + 1])
    assert settings["disableAllHooks"] is False
    assert settings["permissions"]["allow"] == [f"Read(/{options['cwd']}/**)",
        f"Edit(/{options['cwd']}/working/skills/**)", f"Write(/{options['cwd']}/working/skills/**)"]
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Bash|Edit|Read|Write"
    assert "Write" in seen[1][0][seen[1][0].index("--tools") + 1]
    assert seen[1][0][seen[1][0].index("--max-turns") + 1] == "3"
    usage = summarize_calls(work)
    assert usage["calls"] == 2 and usage["model_responses"] == 6 and usage["format_repair_calls"] == 1
    assert usage["content_review_calls"] == 0 and usage["content_repair_calls"] == 0
    assert usage["total_tokens"] == 340
    assert len({options["env"]["CLAUDE_CONFIG_DIR"] for _, options in seen}) == 2
    stats = metrics([{"episodes": [{"case": case(), "coding_usage": {},
        "curation": {"usage": usage, "status": "unchanged"}}]}])
    task_stats = stats["per_task"]["tension_strap"]
    assert task_stats["curation_tokens"]["reported_total_tokens"] == 340
    assert task_stats["curation_model_calls"] == 6 and task_stats["curation_invocations"] == 2
    assert task_stats["curation_format_repair_calls"] == 1
    assert task_stats["curation_content_review_calls"] == 0
    assert stats["per_task"]["arm_shadow_piano"]["curation_tokens"]["reported_total_tokens"] == 0
    incomplete = metrics([{"episodes": [{"case": case(), "coding_usage": {},
        "curation": {"usage": {"calls": 1, "model_responses": None}, "status": "failed"}}]}])
    assert incomplete["curation_model_calls"] == 0
    assert incomplete["curation_model_calls_complete"] is False
    assert incomplete["curation_invocations"] == 1
    with pytest.raises(RuntimeError, match="request limit"):
        session.ask("unexpected third request", stage="format_repair")


@pytest.mark.parametrize("read_outcome", ["success", "error", "not_read"])
def test_read_only_requests_require_successful_evidence_reads(monkeypatch, tmp_path, read_outcome):
    import capx.logger2skill.claude as module
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "executed_code.py").write_text("wait(3)")
    class FakeProcess:
        returncode = 0
        def __init__(self, command, **kwargs):
            self.output = kwargs["stdout"]
        def communicate(self, input, timeout):
            if read_outcome != "not_read":
                self.output.write(json.dumps({"type": "assistant", "message": {"id": "read_msg", "content": [
                    {"type": "tool_use", "name": "Read", "id": "read_code",
                     "input": {"file_path": str(root / "executed_code.py")}}]}}) + "\n")
                self.output.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result",
                    "tool_use_id": "read_code", "is_error": read_outcome == "error", "content": "wait(3)"}]}}) + "\n")
            self.output.write(json.dumps({"type": "assistant", "message": {"id": "final", "content": []}}) + "\n")
            self.output.write(json.dumps({"type": "result", "is_error": False, "result": "review",
                "usage": {"input_tokens": 10, "output_tokens": 5}}) + "\n")
    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)
    session = ClaudeSession(tmp_path, {"model": "test", "credential_settings": str(tmp_path / "absent")},
                            time.monotonic() + 5)
    if read_outcome == "success":
        assert session.ask("review", stage="review", read_root=root,
                           required_reads=("executed_code.py",)) == "review"
    else:
        with pytest.raises(RuntimeError, match="not successfully read"):
            session.ask("review", stage="review", read_root=root, required_reads=("executed_code.py",))
    assert session.calls[0]["missing_required_reads"] == ([] if read_outcome == "success" else ["executed_code.py"])


def test_candidate_edit_tools_cannot_change_originals_or_run_other_shell_commands(tmp_path):
    import hashlib
    from capx.logger2skill.read_guard import check_read
    root = tmp_path / "inputs"
    original = root / "library"
    edited = root / "working"
    add_skill(original)
    add_skill(edited)
    rules = root / "maintenance.txt"
    rules.write_text("unchanged rules")
    taxonomy = edited / "taxonomy.yaml"
    taxonomy.write_text("frozen taxonomy")
    original_skill = original / "skills/act/release-observation.md"
    current = edited / "skills/act/release-observation.md"
    manifest = {"root": str(root), "write_root": str(edited), "files": {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (rules, taxonomy, original_skill)}}
    def allowed(tool, path):
        return check_read({"tool_name": tool, "tool_input": {"file_path": str(path)}}, manifest)["allowed"]
    assert allowed("Read", original_skill) and allowed("Read", taxonomy)
    for target in (original_skill, taxonomy, rules, edited / "skills/act/../../escape.md"):
        assert not allowed("Write", target)
        assert not allowed("Edit", target)
    assert allowed("Write", edited / "skills/act/new.md") and allowed("Edit", current)
    current.write_text("edited candidate")
    assert allowed("Read", current)
    link = edited / "skills/act/link.md"
    link.symlink_to(original_skill)
    assert not allowed("Write", link)
    hard = edited / "skills/act/hard.md"
    os.link(original_skill, hard)
    assert not allowed("Write", hard)
    for command in (f"rm -- {current}", f"rm -- {original_skill}", f"rm -rf {edited}",
                    f"rm -- {current}; cat {rules}", "env", "python -c 'pass'"):
        result = check_read({"tool_name": "Bash", "tool_input": {"command": command}}, manifest)
        assert result["allowed"] == (command == f"rm -- {current}")
    assert original_skill.read_text() == skill_text()


def test_direct_edits_preserve_untouched_skills_and_previous_versions(store, tmp_path):
    import shutil
    initial = tmp_path / "initial"
    shutil.copytree(store.path(store.active()), initial)
    add_skill(initial)
    kept = initial / "skills/act/kept.md"
    kept.write_text(skill_text("kept").replace("id: A1", "id: A2"))
    previous = store.adopt(initial, store.active(), "before_edit")
    before_hash = digest(store.path(previous))
    work = tmp_path / "curation"
    candidate = work / "candidate"
    shutil.copytree(store.path(previous), candidate)
    class FakeSession:
        def ask(self, prompt, *, stage, read_root=None, required_reads=(), write_root=None):
            if stage == "maintenance":
                old = write_root / "skills/act/release-observation.md"
                old.unlink()
                add_skill(write_root, "replacement")
                return "Renamed one skill; retained the other."
            assert "library/taxonomy.yaml" in required_reads
            assert (read_root / "library/skills/act/release-observation.md").read_text() == skill_text()
            return accept_review(read_root)
    session = FakeSession()
    session.work = work
    report = curate_cc(session, [case()], candidate, REPO)
    assert {c["op"] for c in report["changes"]} == {"upsert", "delete"}
    assert (candidate / "skills/act/kept.md").read_bytes() == kept.read_bytes()
    assert store.active() == previous
    current = store.adopt(candidate, previous, "after_edit")
    assert current != previous
    assert digest(store.path(previous)) == before_hash
    assert (store.path(previous) / "skills/act/release-observation.md").read_text() == skill_text()


@pytest.mark.parametrize("response", ["", "[no content]"])
@pytest.mark.parametrize("edit_file", [False, True])
def test_empty_final_text_accepts_valid_file_result_without_another_model_call(store, tmp_path, response, edit_file):
    import shutil
    candidate = tmp_path / "candidate"
    shutil.copytree(store.path(store.active()), candidate)
    before = digest(candidate)
    calls = []
    class FakeSession:
        work = tmp_path
        def ask(self, prompt, *, stage, read_root=None, required_reads=(), write_root=None):
            calls.append(stage)
            if edit_file:
                add_skill(write_root)
            return response
    report = curate_cc(FakeSession(), [case()], candidate, REPO)
    assert calls == ["maintenance"]
    assert (digest(candidate) != before) is edit_file
    assert len(report["changes"]) == int(edit_file)
    assert report["summary"] == f"{int(edit_file)} Markdown files changed."
    assert report["format_feedback_used"] is False
    assert store.active()["version"] == "empty"


def test_direct_markdown_quotes_all_unambiguous_scalars_without_model_feedback(store, tmp_path):
    import shutil
    from capx.logger2skill.curators import cc_file_changes
    original = store.path(store.active())
    edited = tmp_path / "edited"
    shutil.copytree(original, edited)
    for i, name in enumerate(("first", "second")):
        add_skill(edited, name)
        path = edited / "skills/act" / f"{name}.md"
        value = skill_text(name).replace("id: A1", f"id: A{i + 1}")
        value = value.replace("summary: In a hold,", "summary: In a hold: first,")
        path.write_text(value)
    report = cc_file_changes(original, edited, "Two proposals")
    assert len(report["format_repairs"]) == 2
    assert {s.summary for s in validate_library(edited)} == {
        "In a hold: first, use physical wait instead of wall-clock sleep; only the former advances the task."}
    assert len(validate_library(original)) == 0


def test_continued_json_response_is_reassembled_without_a_model_request(monkeypatch, tmp_path):
    import capx.logger2skill.claude as module
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "maintenance.txt").write_text("rules")
    chunks = ['{"verdict":"accept",', '"issues":[]}']
    class FakeProcess:
        returncode = 0
        def __init__(self, command, **kwargs):
            self.output = kwargs["stdout"]
        def communicate(self, input, timeout):
            for i, text in enumerate(chunks):
                self.output.write(json.dumps({"type": "assistant", "message": {"id": f"msg{i}",
                    "content": [{"type": "text", "text": text}]}}) + "\n")
            self.output.write(json.dumps({"type": "result", "is_error": False, "result": chunks[-1],
                "usage": {"input_tokens": 10, "output_tokens": 5}}) + "\n")
    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)
    session = ClaudeSession(tmp_path, {"model": "test", "credential_settings": str(tmp_path / "absent")},
                            time.monotonic() + 5)
    assert json.loads(session.ask("review", stage="review", read_root=root)) == {"verdict": "accept", "issues": []}
    assert len(session.calls) == 1 and session.calls[0]["response_assembled_from_stream"]


def test_cc_response_budget_is_shared_and_counts_streamed_messages_once(monkeypatch, tmp_path):
    import capx.logger2skill.claude as module
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "maintenance.txt").write_text("rules")
    turns = []
    class FakeProcess:
        returncode = 0
        def __init__(self, command, **kwargs):
            turns.append(int(command[command.index("--max-turns") + 1]))
            self.output = kwargs["stdout"]
            assert kwargs["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4096"
        def communicate(self, input, timeout):
            count = 4 if len(turns) == 1 else 2
            for i in range(count):
                event = {"type": "assistant", "message": {"id": f"m{i}", "content": []}}
                self.output.write(json.dumps(event) + "\n")
                self.output.write(json.dumps(event) + "\n")
            self.output.write(json.dumps({"type": "result", "is_error": False, "result": "ok",
                "num_turns": count, "usage": {"input_tokens": 10, "output_tokens": 5}}) + "\n")
    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)
    session = ClaudeSession(tmp_path, {"model": "test", "credential_settings": str(tmp_path / "absent"),
        "max_responses": 6, "max_output_tokens": 4096}, time.monotonic() + 5)
    assert session.ask("first", stage="maintenance", read_root=root) == "ok"
    assert session.ask("format", stage="format_repair", read_root=root) == "ok"
    with pytest.raises(RuntimeError, match="total response budget exhausted"):
        session.ask("extra", stage="format_repair", read_root=root)
    assert turns == [6, 2]
    assert session.calls[1]["responses_used_before"] == 4
    assert summarize_calls(tmp_path)["model_responses"] == 6


@pytest.mark.parametrize("settings,expected", [({"read_max_turns": 2}, 2),
    ({"max_responses": 3, "edit_max_turns": 8}, 3)])
def test_cc_legacy_turn_settings_cannot_bypass_total_budget(monkeypatch, tmp_path, settings, expected):
    import capx.logger2skill.claude as module
    root = tmp_path / "inputs"
    edited = root / "working"
    edited.mkdir(parents=True)
    (root / "maintenance.txt").write_text("rules")
    seen = []
    class FakeProcess:
        returncode = 0
        def __init__(self, command, **kwargs):
            seen.append(int(command[command.index("--max-turns") + 1]))
            self.output = kwargs["stdout"]
        def communicate(self, input, timeout):
            self.output.write(json.dumps({"type": "assistant", "message": {"id": "final", "content": []}}) + "\n")
            self.output.write(json.dumps({"type": "result", "is_error": False, "result": "ok"}) + "\n")
    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)
    session = ClaudeSession(tmp_path, {"model": "test", "credential_settings": str(tmp_path / "absent"),
        **settings}, time.monotonic() + 5)
    assert session.ask("first", stage="maintenance", read_root=root, write_root=edited) == "ok"
    assert seen == [expected]


def test_exhausted_cc_budget_does_not_launch_format_repair_or_adopt_candidate(monkeypatch, tmp_path, store):
    import shutil
    import capx.logger2skill.claude as module
    work = tmp_path / "curation"
    candidate = work / "candidate"
    shutil.copytree(store.path(store.active()), candidate)
    before = digest(candidate)
    processes = []
    class FakeProcess:
        returncode = 0
        def __init__(self, command, **kwargs):
            processes.append(command)
            self.options = kwargs
        def communicate(self, input, timeout):
            edited = self.options["cwd"] / "working"
            add_skill(edited)
            (edited / "skills/act/release-observation.md").write_text("invalid frontmatter")
            for i in range(6):
                self.options["stdout"].write(json.dumps({"type": "assistant", "message": {
                    "id": f"msg{i}", "content": []}}) + "\n")
            self.options["stdout"].write(json.dumps({"type": "result", "is_error": False, "result": "edited",
                "num_turns": 6, "usage": {"input_tokens": 100, "output_tokens": 20}}) + "\n")
    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)
    session = ClaudeSession(work, {"model": "test", "credential_settings": str(tmp_path / "absent"),
        "max_responses": 6}, time.monotonic() + 5, max_calls=2)
    with pytest.raises(RuntimeError, match="total response budget exhausted"):
        curate_cc(session, [case()], candidate, REPO)
    assert len(processes) == 1
    assert digest(candidate) == before and store.active()["version"] == "empty"


@pytest.mark.parametrize("limit", [0, -1, 13, True, 2.5, "6"])
def test_invalid_total_response_budget_is_rejected(tmp_path, limit):
    with pytest.raises(ValueError, match="max_responses"):
        ClaudeSession(tmp_path, {"max_responses": limit}, time.monotonic() + 5)


def test_curator_cost_budget_is_shared_across_requests(monkeypatch, tmp_path):
    import capx.logger2skill.claude as module
    budgets = []
    class FakeProcess:
        returncode = 0
        def __init__(self, command, **kwargs):
            budgets.append(float(command[command.index("--max-budget-usd") + 1]))
            self.output = kwargs["stdout"]
        def communicate(self, input, timeout):
            cost = .05 if len(budgets) == 1 else .20
            self.output.write(json.dumps({"type": "assistant", "message": {"id": "final", "content": []}}) + "\n")
            self.output.write(json.dumps({"type": "result", "is_error": False, "result": "ok", "num_turns": 1,
                "total_cost_usd": cost, "usage": {"input_tokens": 10, "output_tokens": 5}}) + "\n")
    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)
    session = ClaudeSession(tmp_path, {"model": "test", "credential_settings": str(tmp_path / "absent"),
                                      "max_budget_usd": .25}, time.monotonic() + 5)
    assert session.ask("first", stage="maintenance") == "ok"
    assert session.ask("review", stage="review") == "ok"
    with pytest.raises(RuntimeError, match="dollar budget exhausted"):
        session.ask("extra", stage="content_repair")
    assert budgets == pytest.approx([.25, .20])
    assert summarize_calls(tmp_path)["reported_cost_usd"] == pytest.approx(.25)


def test_single_case_worker_adoption_has_distinct_versions_across_seeds(monkeypatch, suite, store, tmp_path):
    from capx.logger2skill import experiment
    seen = []
    def run_worker(command, *args):
        job_path = Path(command[-1])
        cases = read_json(job_path)["cases"]
        assert len(cases) == 1
        seen.append(cases[0]["case_id"])
        candidate = job_path.parent / "candidate"
        add_skill(candidate)
        (candidate / "skills/act/release-observation.md").write_text(
            skill_text(evidence=seen[-1]), encoding="utf-8")
        write_json(job_path.parent / "result.json", {"ok": True})
        return {"exit_code": 0, "elapsed_s": 1, "timed_out": False}
    monkeypatch.setattr(experiment, "run_process", run_worker)
    versions = []
    for i, seed in enumerate((44000, 44001)):
        folder = tmp_path / f"round_{i:03d}" / "tension_strap"
        result = experiment.curate_episode(suite, folder, case(seed), store, store.active(), 300)
        assert result["status"] == "adopted"
        assert result["library_after"] == store.active()
        assert read_json(folder / "curation_result.json") == result
        versions.append(result["library_after"]["version"])
    assert versions == ["episode_tension_strap_44000", "episode_tension_strap_44001"]
    assert seen == ["tension_strap_44000", "tension_strap_44001"]


def test_curate_episode_refuses_batch_before_starting_a_worker(monkeypatch, suite, store, tmp_path):
    from capx.logger2skill import experiment
    monkeypatch.setattr(experiment, "run_process", lambda *a: pytest.fail("Unexpected worker"))
    folder = tmp_path / "unused"
    with pytest.raises(ValueError, match="exactly one execution"):
        experiment.curate_episode(suite, folder, [case()], store, store.active(), 300)
    assert not folder.exists()


def test_tension_end_has_no_native_verdict_and_refuses_followup():
    from capx.integrations.tactile.univtac_tension_api import UniVTACTensionApi
    api = UniVTACTensionApi.__new__(UniVTACTensionApi)
    calls = []
    api._post = lambda path, body: calls.append((path, body)) or {"completed": True}
    functions = api.functions()
    assert functions["finish_episode"]() is None
    assert calls == [("/submit_episode", {})]
    with pytest.raises(RuntimeError, match="END"):
        functions["wait"](1)


def test_unverified_native_check_is_unknown_not_task_failure(monkeypatch):
    from capx.envs.tasks.tactile import low_level
    env = low_level.UniVTACStubEnv(task_name="tension_strap")
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"completed": False, "verified": False}
    monkeypatch.setattr(low_level.requests, "get", lambda *a, **kw: Response())
    assert env.compute_reward() == 0.0
    assert env.task_completed() is None


def test_failed_curator_retains_library_and_reports_usage(monkeypatch, suite, store, tmp_path):
    from capx.logger2skill import experiment
    folder = tmp_path / "round_000"
    folder.mkdir()
    monkeypatch.setattr(experiment, "run_process", lambda *a, **kw: {"exit_code": 124, "timed_out": True})
    before = store.active()
    result = experiment.curate_episode(suite, folder, case(), store, before, 300)
    assert result["status"] == "failed"
    assert result["library_after"] == before == store.active()
    assert result["usage"]["calls"] == 0


def test_launch_dispatches_new_suite_without_starting_servers(monkeypatch):
    from capx.envs import launch, runner
    from capx.logger2skill import experiment
    seen = []
    monkeypatch.setattr(experiment, "run_experiment", lambda *args: seen.append(args))
    monkeypatch.setattr(runner, "_start_api_servers", lambda *args: pytest.fail("Unexpected simulator startup"))
    launch.main(launch.LaunchArgs(logger2skill_config="suite.yaml"))
    assert seen == [("suite.yaml", None)]


def test_public_tactile_log_contains_anisotropy_without_images_or_private_fields(monkeypatch, capsys):
    from capx.integrations.tactile.univtac_api import _trim_motion_tactile
    monkeypatch.setenv("CAPX_LOGGER2SKILL", "1")
    data = {"left_depth_mm": 27.1, "right_depth_mm": 27.2,
            "left_marker_anisotropy": 1.23, "right_marker_anisotropy": 1.24,
            "left_contact": True, "right_contact": True,
            "left_rgb_base64": "private_image_payload", "native_score": 1}
    result = _trim_motion_tactile(data)
    log = capsys.readouterr().out
    assert result["left_marker_anisotropy"] == 1.23
    assert "left_marker_anisotropy" in log and "1.23" in log and "27.1" in log
    assert "native_score" not in log and "private_image_payload" not in log
