from __future__ import annotations

import copy
import difflib
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
import yaml

from . import BASE_COMMIT
from .claude import summarize_calls
from .evidence import action_counts, coding_usage, make_case
from .processes import run_process, stop_owned_process, without_proxy
from .initial_library import verify_source
from .storage import render_memory, LibraryStore, pack_library, read_json, write_json

REPO = Path(__file__).resolve().parents[2]
TASKS = {"weight_classify", "grasp_classify", "hardness_classify", "tactile_memory_match",
         "tension_strap", "bulb_tightening", "blind_pick_balls", "arm_shadow_piano"}
STATE_SCHEMA = "logger2skill.per_episode.v1"


def load_suite(path: Path) -> dict:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if config.get("base_commit") != BASE_COMMIT or config.get("backend") not in {"cc", "skillopt"}:
        raise ValueError("Wrong base commit or curator backend")
    selected = set(config["tasks"])
    if config.get("smoke") is True:
        if not selected or not selected <= TASKS:
            raise ValueError("A smoke must explicitly select known tasks")
    elif selected not in (TASKS, TASKS - {"arm_shadow_piano"}):
        raise ValueError("Use the eight-task suite or the existing seven-task suite")
    budget = config["budget"]
    if (budget.get("rounds") is None) == (budget.get("seconds") is None):
        raise ValueError("Choose one budget: rounds or seconds")
    if float(next(v for v in budget.values() if v is not None)) <= 0:
        raise ValueError("Budget must be positive")
    seeds = config["seeds"]
    if not seeds or len(set(seeds)) != len(seeds) or any(type(s) is not int for s in seeds):
        raise ValueError("Provide distinct integer development seeds")
    if budget.get("rounds") is not None and not 0 < int(budget["rounds"]) <= len(seeds):
        raise ValueError("Not enough seeds for the requested rounds")
    if config["curator"]["timeout_s"] <= 0 or config["execution"]["timeout_s"] <= 0:
        raise ValueError("Timeouts must be positive")
    for task, item in config["tasks"].items():
        task_file = REPO / item["config"]
        if not task_file.is_file() or not Path(item["environment_root"]).is_dir():
            raise ValueError(f"Missing task config or environment root: {task}")
        item["config_sha256"] = hashlib.sha256(task_file.read_bytes()).hexdigest()
    if config.get("initial_library_path"):
        verify_source(Path(config["initial_library_path"]))
    return config


def episode_config(task: str, item: dict, seed: int, output: Path, memory: Path, max_chars: int) -> dict:
    cfg = yaml.safe_load((REPO / item["config"]).read_text(encoding="utf-8"))
    cfg = copy.deepcopy(cfg)
    cfg.update(api_servers=[], trials=1, num_workers=1, output_dir=str(output),
               record_video=bool(cfg.get("record_video", False)),
               use_visual_feedback=True, use_img_differencing=False, use_video_differencing=False,
               use_parallel_ensemble=False, use_multimodel=False, use_oracle_code=False, web_ui=False,
               evolve_skill_library=False, single_program=True, logger2skill_episode=True,
               episode_seed=seed, case_id=f"{task}_{seed}")
    env = cfg["env"]["cfg"]
    env.update(tactile_skill_root=str(memory), tactile_skill_max_chars=max_chars,
               logger2skill_episode=True, multi_turn_prompt=None, append_final_check="")
    if isinstance(env["low_level"], str):
        low_path = REPO / env["low_level"]
        env["low_level"] = yaml.safe_load(low_path.read_text(encoding="utf-8"))
    env["low_level"]["server_url"] = os.environ.get("UNIVTAC_SERVER_URL", "http://127.0.0.1:8120")
    return cfg


def wait_ready(proc, url: str, task: str, deadline: float) -> None:
    session = requests.Session()
    session.trust_env = False
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"Simulator exited during startup ({proc.returncode})")
            try:
                response = session.get(url + "/health", timeout=min(2, max(.1, deadline - time.monotonic())))
                if response.ok and response.json().get("task") == task:
                    return
            except requests.RequestException:
                pass
            time.sleep(.5)
    finally:
        session.close()
    raise TimeoutError("Simulator startup exceeded the episode budget")


def verify_gpu_process(pid: int, expected_uuid: str, output: Path,
                       allow_auxiliary_graphics: bool = False) -> None:
    xml = subprocess.check_output(["nvidia-smi", "-q", "-x"], text=True)
    report = []
    for gpu in ET.fromstring(xml).findall("gpu"):
        for process in gpu.findall("./processes/process_info"):
            if process.findtext("pid") == str(pid):
                report.append({"gpu_uuid": gpu.findtext("uuid"), "pid": pid,
                               "type": process.findtext("type"),
                               "used_memory": process.findtext("used_memory")})
    write_json(output / "gpu_process_check.json", report)
    unexpected = [row for row in report if row["gpu_uuid"] != expected_uuid
                  and not (allow_auxiliary_graphics and row["type"] == "G")]
    if not any(row["gpu_uuid"] == expected_uuid for row in report) or unexpected:
        raise RuntimeError("GPU isolation check failed: simulator has an unexpected compute or graphics context")


def execute_episode(suite: dict, task: str, seed: int, output: Path, memory: Path, timeout: float) -> dict:
    gpu = os.environ.get("GPU", "")
    if not gpu.isdecimal():
        raise ValueError("Set GPU explicitly to one physical GPU index")
    gpu_uuid = subprocess.check_output(
        ["nvidia-smi", "-i", gpu, "--query-gpu=uuid", "--format=csv,noheader"], text=True).strip()
    if not gpu_uuid.startswith("GPU-") or "\n" in gpu_uuid:
        raise ValueError("GPU must resolve to exactly one NVIDIA device UUID")
    if suite.get("gpu") is not None and gpu != str(suite["gpu"]):
        raise ValueError("The selected GPU differs from the frozen experiment")
    if suite.get("gpu_uuid") and gpu_uuid != suite["gpu_uuid"]:
        raise ValueError("The selected GPU UUID differs from the frozen experiment")
    gpu_check = {"index": int(gpu), "uuid": gpu_uuid}
    if suite.get("require_idle_gpu"):
        values = subprocess.check_output(
            ["nvidia-smi", "-i", gpu, "--query-gpu=memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"], text=True).strip().split(",")
        gpu_check.update(memory_MiB=int(values[0]), utilization_percent=int(values[1]))
        if gpu_check["memory_MiB"] > 256 or gpu_check["utilization_percent"] > 5:
            raise RuntimeError("Selected GPU is busy; no fallback GPU is allowed")
    execution = suite["execution"]
    port = int(execution.get("port", 8120))
    with socket.socket() as check:
        check.bind(("127.0.0.1", port))
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "gpu_isolation.json", gpu_check)
    delivered = render_memory(memory, task, suite["memory_max_chars"])
    (output / "skill_delivery.txt").write_text(delivered, encoding="utf-8")
    write_json(output / "skill_delivery.json", {
        "library_path": str(memory), "characters": len(delivered),
        "sha256": hashlib.sha256(delivered.encode()).hexdigest(), "runtime_confirmed": False})
    started = time.monotonic()
    deadline = started + timeout
    url = f"http://127.0.0.1:{port}"
    item = suite["tasks"][task]
    env = without_proxy()
    sim_prefix = Path(execution['sim_python']).resolve().parents[1]
    env.update(CUDA_VISIBLE_DEVICES=gpu_uuid, CAPX_RENDER_GPU=gpu, CAPX_GPU=gpu,
               EGL_DEVICE_ID=gpu, CAPX_LOGGER2SKILL="1",
               UNIVTAC_ROOT=item["environment_root"], VITAFORGE_ROOT=suite["opentac_root"],
               UNIVTAC_SERVER_URL=url, UNIVTAC_ACTION_LOG=str(output / "private_actions.jsonl"),
               UNIVTAC_CALIBRATION_LOG=str(output / "private_calibration.log"),
               PYTHONPATH=str(REPO), TMPDIR=str(output / "tmp"))
    env.update(CUDA_HOME=str(sim_prefix), PYTHONUNBUFFERED='1', HEADLESS='1',
               LD_LIBRARY_PATH=str(sim_prefix / 'lib') + os.pathsep + env.get('LD_LIBRARY_PATH', ''))
    server_deps = execution.get('server_dependency_path')
    if server_deps:
        if not Path(server_deps).is_dir():
            raise ValueError(f'Missing isolated server dependencies: {server_deps}')
        env['PYTHONPATH'] += os.pathsep + server_deps
    (output / "tmp").mkdir()
    for name in ("XDG_CACHE_HOME", "CUDA_CACHE_PATH", "OMNI_USER_CACHE_PATH", "OMNI_USER_DATA_PATH", "OMNI_USER_LOG_PATH"):
        path = output / "runtime" / name.lower()
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
    cfg = episode_config(task, item, seed, output / "client", memory, suite["memory_max_chars"])
    cfg["env"]["cfg"]["low_level"]["server_url"] = url
    cfg_file = output / "episode.yaml"
    cfg_file.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    command = [execution["sim_python"], "-m", "capx.serving.launch_univtac_server", "--task", task,
               "--port", str(port), "--host", "127.0.0.1", "--headless", "--device", "cuda:0",
               "--seed", str(seed), "--no-watchdog"]
    result = {"exit_code": None, "timed_out": False}
    with (output / "server.log").open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(command, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            wait_ready(proc, url, task, min(deadline, started + execution["startup_timeout_s"]))
            if suite.get("require_idle_gpu"):
                verify_gpu_process(proc.pid, gpu_uuid, output,
                                   suite.get("allow_auxiliary_graphics", False))
            base_url = os.environ.get(execution["api_url_env"], "").rstrip("/")
            if not base_url:
                raise ValueError(f"Set {execution['api_url_env']} before starting an experiment")
            if not base_url.endswith(("/responses", "/chat/completions")):
                base_url += "/responses"
            client_env = dict(env)
            client_env['PYTHONPATH'] = str(REPO)
            client_env['CAPX_LLM_RESPONSE_PATH'] = str(output / 'client/raw_model_response.json')
            client_env["CAPX_CODING_API_KEY"] = os.environ.get(execution["api_key_env"], "")
            client = [execution["client_python"], "-m", "capx.envs.launch", "--config-path", str(cfg_file),
                      "--model", execution["model"], "--server-url", base_url,
                      "--max-tokens", str(execution["max_output_tokens"]),
                      "--temperature", str(execution["temperature"]),
                      "--reasoning-effort", execution["reasoning_effort"],
                      "--single-program", "--max-query-retries", "0"]
            result = run_process(client, REPO, client_env, deadline - time.monotonic(), output / "run.log")
        except Exception as exc:
            result.update(exit_code=124 if isinstance(exc, TimeoutError) else 1,
                          timed_out=isinstance(exc, TimeoutError), infrastructure_error=str(exc))
        finally:
            stop_owned_process(proc)
    case_path = output / "client" / "public_case.json"
    if case_path.is_file():
        case = read_json(case_path)
        result['public_case_exported'] = True
    else:
        case = make_case(f"{task}_{seed}", task, seed, "", "", "", "No completed public case was exported.", None, None)
        result.setdefault("infrastructure_error", "No completed public case; inspect local logs")
        result['public_case_exported'] = False
    loaded_path = output / "client/loaded_memory.json"
    if loaded_path.exists():
        loaded = read_json(loaded_path)["text"]
        delivery_record = read_json(output / "skill_delivery.json")
        delivery_record["runtime_confirmed"] = loaded == delivered
        write_json(output / "skill_delivery.json", delivery_record)
        if loaded != delivered:
            result["infrastructure_error"] = "Actual delivered memory differs from the frozen version"
    usage_path = output / 'client/coding_usage.json'
    raw_path = output / 'client/raw_model_response.json'
    if not usage_path.exists() and raw_path.exists():
        write_json(usage_path, read_json(raw_path).get('usage') or {})
    result.update(case=case, elapsed_s=time.monotonic() - started,
                  coding_usage=coding_usage(output / "client" / "coding_usage.json"),
                  **action_counts(output / "private_actions.jsonl"))
    write_json(output / "episode_result.json", result)
    return result


def curate_episode(suite: dict, folder: Path, case: dict, store: LibraryStore,
                   pointer: dict, timeout: float) -> dict:
    if not isinstance(case, dict) or case.get("task") not in TASKS or type(case.get("seed")) is not int:
        raise ValueError("Curate exactly one execution case with a task and seed")
    version = f"episode_{case['task']}_{case['seed']}"
    work = folder / "curation"
    work.mkdir(parents=True, exist_ok=False)
    shutil.copytree(store.path(pointer), work / "candidate")
    job = {"backend": suite["backend"], "curator": suite["curator"], "repo": str(REPO),
           "skillopt_source": suite["skillopt_source"], "cases": [case], "timeout_s": timeout}
    write_json(work / "job.json", job)
    env = without_proxy()
    env.update(PYTHONPATH=str(REPO), CUDA_VISIBLE_DEVICES="")
    result = run_process([suite["execution"]["client_python"], "-m", "capx.logger2skill.worker", str(work / "job.json")],
                         work, env, timeout, work / "worker.log")
    result["usage"] = summarize_calls(work)
    result["library_before"] = pointer
    result["library_after"] = pointer
    result.update(case_id=case["case_id"], task=case["task"], seed=case["seed"])
    worker_result = read_json(work / "result.json") if (work / "result.json").exists() else {}
    if result["exit_code"] == 0 and worker_result.get("ok"):
        try:
            result["library_after"] = store.adopt(work / "candidate", pointer, version)
            result["status"] = "adopted" if result["library_after"] != pointer else "unchanged"
        except Exception as exc:
            result.update(status="rejected", error=str(exc))
    else:
        result.update(status="failed", error=worker_result.get("error", "No successful curator result"))
        for stream in work.glob("calls/*/response.jsonl"):
            for line in stream.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "result" and event.get("is_error") and re.search(
                        r"authentication|unauthorized|invalid.{0,10}key|credit|quota|rate.limit|overloaded",
                        str(event.get("result", "")), re.I):
                    result["infrastructure_error"] = "Curator credential, quota or service failure"
    try:
        from .storage import digest
        digest(work / "candidate")
        diff = difflib.unified_diff(pack_library(store.path(pointer)).splitlines(True),
                                    pack_library(work / "candidate").splitlines(True), fromfile="before", tofile="candidate")
        (work / "library.diff").write_text("".join(diff), encoding="utf-8")
    except (ValueError, OSError, UnicodeError) as exc:
        result["diff_error"] = str(exc)
    write_json(folder / "curation_result.json", result)
    return result


def _token_totals(rows: list[dict]) -> dict:
    result = {"reported_total_tokens": sum(r.get("total_tokens") or 0 for r in rows),
              "complete": all(r.get("complete", False) and r.get("total_tokens") is not None for r in rows),
              "missing_usage_records": sum(r.get("total_tokens") is None for r in rows)}
    for name in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        result["reported_" + name] = sum(r.get(name) or 0 for r in rows)
    return result


def _episode_metrics(episodes: list[dict]) -> dict:
    eligible = [e for e in episodes if not e.get("infrastructure_error")]
    successes = sum(e["case"]["final_success"] is True for e in eligible)
    known = sum(type(e["case"]["final_success"]) is bool for e in eligible)
    successful_actions = [e["action_requests"] for e in eligible
                          if e["case"]["final_success"] is True and e.get("action_requests") is not None]
    actions = [e["action_requests"] for e in episodes if e.get("action_requests") is not None]
    completed = [e["completed_actions"] for e in episodes if e.get("completed_actions") is not None]
    coding = [e.get("coding_usage", {}) for e in episodes]
    curations = [e["curation"] for e in episodes if e.get("curation")]
    curation_usage = [c.get("usage", {}) for c in curations]
    curation_runs = [c for c in curations if not c.get("status", "").startswith("skipped_")]
    execution_times = [e["elapsed_s"] for e in episodes if e.get("elapsed_s") is not None]
    curation_times = [c["elapsed_s"] for c in curations if c.get("elapsed_s") is not None]
    attempted_times = [c["elapsed_s"] for c in curation_runs if c.get("elapsed_s") is not None]
    pending = len(episodes) - len(curations)
    curation_tokens = _token_totals(curation_usage)
    total_tokens = _token_totals(coding + curation_usage)
    if pending:
        curation_tokens["complete"] = total_tokens["complete"] = False
    statuses = {}
    for curation in curations:
        name = curation.get("status", "unknown")
        statuses[name] = statuses.get(name, 0) + 1
    execution_seconds = sum(execution_times)
    curation_seconds = sum(curation_times)
    return {"attempts": len(episodes), "native_outcomes_known": known, "successes": successes,
            "task_attempts": len(eligible),
            "native_failures": known - successes, "native_outcomes_unknown": len(eligible) - known,
            "sr_all_attempts": successes / len(eligible) if eligible else None,
            "sr_known_outcomes": successes / known if known else None,
            "infrastructure_failures": sum(bool(e.get("infrastructure_error")) for e in episodes),
            "public_execution_failures": sum(e["case"].get("execution_ok") is False for e in episodes),
            "execution_timeouts": sum(bool(e.get("timed_out")) for e in episodes),
            "total_execute_steps": sum(actions), "total_completed_actions": sum(completed),
            "action_counts_complete": len(actions) == len(completed) == len(episodes),
            "mean_execute_steps": sum(actions) / len(actions) if actions else None,
            "mean_successful_execute_steps": (sum(successful_actions) / len(successful_actions)
                                              if successful_actions else None),
            "execute_step_unit": "native_server_action_requests_not_physics_steps",
            "coding_tokens": _token_totals(coding), "curation_tokens": curation_tokens,
            "total_tokens": total_tokens,
            "curations_attempted": len(curation_runs), "curation_status_counts": statuses,
            "pending_curations": pending,
            "curation_model_calls": sum(
                c.get("usage", {}).get("model_responses", c.get("usage", {}).get("calls")) or 0
                for c in curations),
            "curation_invocations": sum(c.get("usage", {}).get("calls") or 0 for c in curations),
            "curation_format_repair_calls": sum(c.get("usage", {}).get("format_repair_calls") or 0 for c in curations),
            "curation_content_review_calls": sum(c.get("usage", {}).get("content_review_calls") or 0 for c in curations),
            "curation_content_repair_calls": sum(c.get("usage", {}).get("content_repair_calls") or 0 for c in curations),
            "curation_model_calls_complete": not pending and all(
                c.get("usage", {}).get("model_responses", c.get("usage", {}).get("calls")) is not None
                for c in curations),
            "curation_timeouts": sum(bool(c.get("timed_out")) for c in curations),
            "execution_seconds": execution_seconds, "curation_seconds": curation_seconds,
            "total_seconds": execution_seconds + curation_seconds,
            "mean_execution_seconds": execution_seconds / len(execution_times) if execution_times else None,
            "mean_curation_seconds": sum(attempted_times) / len(attempted_times) if attempted_times else None,
            "time_records_complete": not pending and len(execution_times) == len(episodes) and len(curation_times) == len(curations),
            "time_unit": "seconds_spent_on_this_task_execution_and_curation_excluding_other_tasks"}


def metrics(rounds: list[dict]) -> dict:
    if any("curation" in row for row in rounds):
        raise ValueError("Batch curation costs cannot be assigned to individual tasks; keep the original report")
    episodes = [episode for row in rounds for episode in row.get("episodes", [])]
    result = _episode_metrics(episodes)
    result["per_task"] = {}
    for task in sorted(TASKS):
        subset = [e for e in episodes if e["case"]["task"] == task]
        task_result = _episode_metrics(subset)
        task_result["history"] = [dict(
            task_iteration=i + 1, execution_index=e.get("execution_index"),
            case_id=e["case"]["case_id"], seed=e["case"].get("seed"),
            final_success=e["case"]["final_success"] if not e.get("infrastructure_error") else None,
            excluded_infrastructure=bool(e.get("infrastructure_error")),
            execution_elapsed_experiment_s=e.get("execution_elapsed_experiment_s"),
            elapsed_experiment_s=e.get("elapsed_experiment_s"),
            library_before=e.get("library_before"), library_after=e.get("library_after"),
            curation_status=e.get("curation", {}).get("status", "pending"),
            cumulative=_episode_metrics(subset[:i + 1])) for i, e in enumerate(subset)]
        result["per_task"][task] = task_result
    rates = [r["sr_all_attempts"] for r in result["per_task"].values() if r["sr_all_attempts"] is not None]
    result["macro_sr_observed_tasks"] = sum(rates) / len(rates) if rates else None
    result["observed_task_count"] = len(rates)
    result["round_history"] = []
    for i, row in enumerate(rounds):
        if row.get("status") != "complete":
            continue
        previous = [e for r in rounds[:i + 1] for e in r.get("episodes", [])]
        rates = {task: _episode_metrics([e for e in previous if e["case"]["task"] == task])["sr_all_attempts"]
                 for task in sorted({e["case"]["task"] for e in previous})}
        rates = {task: rate for task, rate in rates.items() if rate is not None}
        result["round_history"].append({"round_index": row["index"], "seed": row["seed"],
            "per_task_sr": rates, "macro_sr": sum(rates.values()) / len(rates) if rates else None})
    return result


def run_loop(suite: dict, root: Path, execute=execute_episode, curate=curate_episode, clock=time.time) -> dict:
    state_path = root / "state.json"
    previous = [read_json(p) for p in sorted(root.glob("round_*/round.json"))]
    state = read_json(state_path) if state_path.exists() else {
        "schema": STATE_SCHEMA, "started_at": clock(), "rounds": []}
    if state.get("schema") != STATE_SCHEMA or any(r.get("schema") != STATE_SCHEMA for r in previous):
        raise RuntimeError("Old batch experiment records are read-only; use a new output directory")
    store = LibraryStore(root / "library")
    store.initialize(suite.get("initial_library_path"), tuple(suite["tasks"]))

    def persist(record=None, path=None):
        if record is not None:
            write_json(path, record)
        state["rounds"] = [read_json(p) for p in sorted(root.glob("round_*/round.json"))]
        state["metrics"] = metrics(state["rounds"])
        state["elapsed_s"] = max(0, clock() - state["started_at"])
        write_json(state_path, state)
        write_json(root / "metrics.json", state["metrics"])

    if state.get("status") == "infrastructure_error":
        raise RuntimeError("Experiment stopped on infrastructure failure; no automatic retry")
    persist()
    budget = suite["budget"]
    deadline = state["started_at"] + budget["seconds"] if budget.get("seconds") is not None else float("inf")
    limit = int(budget.get("rounds") or len(suite["seeds"]))
    for index, seed in enumerate(suite["seeds"][:limit]):
        folder = root / f"round_{index:03d}"
        record_path = folder / "round.json"
        if record_path.exists():
            record = read_json(record_path)
            if record.get("inflight"):
                raise RuntimeError(f"Interrupted {record['inflight']} in {folder}; no automatic repeat")
            if record.get("status") == "complete":
                continue
        else:
            if clock() >= deadline:
                break
            folder.mkdir(exist_ok=False)
            record = {"schema": STATE_SCHEMA, "index": index, "seed": seed,
                      "episodes": [], "status": "running"}
            persist(record, record_path)
        for task in suite["tasks"]:
            episode = next((e for e in record["episodes"] if e["case"]["task"] == task), None)
            output = folder / task
            if episode is None:
                if clock() >= deadline:
                    break
                pointer = store.active()
                record["inflight"] = {"stage": "execution", "task": task, "seed": seed,
                                      "library_before": pointer}
                persist(record, record_path)
                started = clock()
                try:
                    episode = execute(suite, task, seed, output, store.path(pointer),
                                      min(suite["execution"]["timeout_s"], deadline - clock()))
                except Exception as exc:
                    if not suite.get("stop_on_infrastructure_error"):
                        raise
                    record["infrastructure_error"] = str(exc)
                    record["status"] = state["status"] = "infrastructure_error"
                    record.pop("inflight")
                    persist(record, record_path)
                    return state
                episode.setdefault("elapsed_s", max(0, clock() - started))
                if episode["case"]["task"] != task or episode["case"].get("seed") != seed:
                    raise ValueError("Execution result does not match the scheduled task and seed")
                episode.update(library_before=pointer,
                               execution_elapsed_experiment_s=max(0, clock() - state["started_at"]),
                               execution_index=state["metrics"]["attempts"] + 1)
                record["episodes"].append(episode)
                record.pop("inflight")
                persist(record, record_path)
            if suite.get("stop_on_infrastructure_error") and episode.get("infrastructure_error"):
                episode["curation"] = {
                    "status": "skipped_infrastructure_error", "elapsed_s": 0,
                    "usage": {"total_tokens": 0, "calls": 0, "complete": True},
                    "library_before": episode["library_before"], "library_after": episode["library_before"]}
                episode["library_after"] = episode["library_before"]
                write_json(output / "episode_record.json", episode)
                record["status"] = state["status"] = "infrastructure_error"
                persist(record, record_path)
                return state
            if "curation" in episode:
                continue
            pointer = episode["library_before"]
            if store.active() != pointer:
                raise RuntimeError("Active library changed before this execution's curation")
            if not episode.get("public_case_exported", True) or clock() >= deadline:
                status = "skipped_no_public_case" if not episode.get("public_case_exported", True) else "skipped_budget"
                curation = {"status": status, "elapsed_s": 0,
                            "usage": {"total_tokens": 0, "calls": 0, "complete": True},
                            "library_before": pointer, "library_after": pointer}
                write_json(output / "curation_result.json", curation)
            else:
                record["inflight"] = {"stage": "curation", "task": task, "seed": seed,
                                      "case_id": episode["case"]["case_id"]}
                persist(record, record_path)
                started = clock()
                curation = curate(suite, output, episode["case"], store, pointer,
                                  min(suite["curator"]["timeout_s"], deadline - clock()))
                curation.setdefault("elapsed_s", max(0, clock() - started))
                if curation["library_after"] != store.active():
                    raise RuntimeError("Curation result and active library disagree")
                record.pop("inflight")
            episode.update(curation=curation, library_after=curation["library_after"],
                           elapsed_experiment_s=max(0, clock() - state["started_at"]))
            write_json(output / "episode_record.json", episode)
            persist(record, record_path)
            if suite.get("stop_on_infrastructure_error") and curation.get("infrastructure_error"):
                record["status"] = state["status"] = "infrastructure_error"
                persist(record, record_path)
                return state
        record["status"] = "complete" if (len(record["episodes"]) == len(suite["tasks"])
            and all("curation" in e for e in record["episodes"])) else "budget_exhausted"
        record["elapsed_s"] = sum(e.get("elapsed_s", 0) + e.get("curation", {}).get("elapsed_s", 0)
                                  for e in record["episodes"])
        persist(record, record_path)
        if clock() >= deadline:
            break
    state["status"] = "complete" if len(state["rounds"]) == limit and all(
        r["status"] == "complete" for r in state["rounds"]) else "budget_exhausted"
    persist()
    return state


def run_experiment(config_path: str, output_dir: str | None = None) -> dict:
    suite = load_suite(Path(config_path))
    execution = suite["execution"]
    if not os.environ.get("GPU", "").isdecimal():
        raise ValueError("Set GPU explicitly to one physical GPU index")
    for variable in (execution["api_url_env"], execution["api_key_env"]):
        if not os.environ.get(variable):
            raise ValueError(f"Set {variable} before starting an experiment")
    for interpreter in (execution["sim_python"], execution["client_python"]):
        if not Path(interpreter).is_file():
            raise ValueError(f"Missing interpreter: {interpreter}")
    root = Path(output_dir or suite["output_root"]).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        frozen = root / "experiment.json"
        if frozen.exists() and read_json(frozen) != suite:
            raise ValueError("This output directory belongs to a different frozen experiment config")
        write_json(frozen, suite)
        source = {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for directory in ("capx", "env_configs/logger2skill")
                  for p in sorted((REPO / directory).rglob("*"))
                  if p.suffix in {".py", ".yaml", ".md"} and "third_party" not in p.parts and p.is_file()}
        source_file = root / "source.json"
        if source_file.exists() and read_json(source_file) != source:
            raise ValueError("Source changed since this experiment began; use a new output directory")
        write_json(source_file, source)
        return run_loop(suite, root)
