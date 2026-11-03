"""Utility functions for launch.py."""

from __future__ import annotations

import base64
import copy
import csv
import hashlib
import re
import signal
import io
import json
import multiprocessing
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image

from capx.envs.configs.instantiate import instantiate
from capx.envs.configs.loader import DictLoader

# Re-export LLM client symbols for backward compatibility
from capx.llm.client import (  # noqa: F401
    CLAUDE_MODELS,
    ENSEMBLE_CONFIGS,
    GPT_MODELS,
    OPENROUTER_MODELS,
    OPENROUTER_SERVER_URL,
    OSS_MODELS,
    VLM_MODELS,
    ModelQueryArgs,
    _completions_to_responses_convert_prompt,
    collapse_text_image_inputs,
    is_openrouter_model,
    is_vlm_model,
)
from capx.llm.client import (  # noqa: F401
    query_model as _query_model,
)
from capx.llm.client import (  # noqa: F401
    query_model_ensemble as _query_model_ensemble,
)
from capx.llm.client import (  # noqa: F401
    query_model_streaming as _query_model_streaming,
)
from capx.llm.client import (  # noqa: F401
    query_single_model_ensemble as _query_single_model_ensemble,
)

if TYPE_CHECKING:
    from capx.envs.launch import LaunchArgs

multiprocessing.set_start_method("spawn", force=True)


@dataclass
class TrialSummary:
    """Summary of a single trial execution."""

    trial: int
    success: bool
    reward: float
    terminated: bool
    truncated: bool
    sandbox_rc: int
    log: str
    task_completed: bool | None = None
    code_path: str | None = None
    num_regenerations: int = 0
    num_finishes: int = 0
    num_code_blocks: int = 0
    metrics: dict[str, Any] | None = None
    failure_phase: str | None = None


def _run_server_cli(api_cfg):
    command = [str(api_cfg["python"]), "-m", api_cfg["_target_"].rsplit(".", 1)[0]]
    for key, value in api_cfg.items():
        if key in {"_target_", "python"} or value is None or value is False:
            continue
        command.append("--" + (key if key in {"kit_args", "enable_cameras"} else key.replace("_", "-")))
        if value is not True:
            command.append(str(value))
    env = {key: value for key, value in os.environ.items()
           if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
    gpu = env.get("GPU")
    if gpu is None or not gpu.isdecimal():
        raise ValueError("Set GPU to exactly one physical GPU index")
    uuid = subprocess.check_output(
        ["nvidia-smi", "-i", gpu, "--query-gpu=uuid", "--format=csv,noheader"],
        text=True, timeout=10).strip()
    if not uuid.startswith("GPU-") or "\n" in uuid:
        raise ValueError("Expected exactly one physical GPU UUID")
    env["CUDA_VISIBLE_DEVICES"] = uuid
    env["CAPX_GPU"] = gpu
    env["EGL_DEVICE_ID"] = gpu
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get("CAPX_SIM_HTTP_DEPS", "")
    run_dir = api_cfg.get("run_dir")
    if run_dir:
        folder = Path(run_dir) / "private"
        folder.mkdir(parents=True, exist_ok=True)
        stream = os.open(str(folder / "simulator.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.dup2(stream, 1)
        os.dup2(stream, 2)
        os.close(stream)
    os.execve(command[0], command, env)


def run_server_proc(api_cfg) -> multiprocessing.Process:
    # Make sure we use spawn for CUDA
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(
        target=_run_server_cli if api_cfg.get("python") else instantiate,
        args=(api_cfg,),
        daemon=True,
    )
    proc.start()
    return proc


def _load_config(args: LaunchArgs) -> tuple[Any, dict[str, Any], list]:
    """Load YAML configuration and merge with command-line arguments.

    Args:
        args: Command-line arguments

    Returns:
        Tuple of (env_factory, merged_config_dict)
        - env_factory: Dict with _target_ for instantiating the environment
        - merged_config_dict: Execution config with CLI overrides applied
    """
    config_path = os.path.expanduser(args.config_path)
    configs_dict = DictLoader.load([config_path])

    # Extract environment factory (don't instantiate yet - that happens per worker)
    if "env" not in configs_dict:
        raise ValueError(
            f"YAML config {config_path} must contain 'env' key with environment configuration"
        )
    env_factory = configs_dict["env"]

    api_servers = configs_dict.get("api_servers", None)

    # Merge server URLs and VDM model settings from YAML into args
    # (CLI args take priority; YAML fills in when CLI uses defaults)
    _CLI_DEFAULTS = {
        "model": "google/gemini-3.1-pro-preview",
        "max_tokens": 65536,
        "temperature": 1.0,
        "server_url": "http://127.0.0.1:8110/chat/completions",
        "visual_differencing_model": "google/gemini-3.1-pro-preview",
        "visual_differencing_model_server_url": "http://127.0.0.1:8110/chat/completions",
        "visual_differencing_model_api_key": None,
    }
    for field, cli_default in _CLI_DEFAULTS.items():
        current_value = getattr(args, field, cli_default)
        if current_value == cli_default and field in configs_dict:
            setattr(args, field, configs_dict[field])

    # Build merged config dict (CLI args override YAML)
    merged_config = {
        "total_trials": args.total_trials
        if args.total_trials is not None
        else configs_dict.get("trials", 10),
        "num_workers": args.num_workers
        if args.num_workers is not None
        else configs_dict.get("num_workers", 1),
        "record_video": args.record_video
        if args.record_video is not None
        else configs_dict.get("record_video", False),
        "output_dir": args.output_dir
        if args.output_dir is not None
        else configs_dict.get("output_dir", None),
        "use_oracle_code": args.use_oracle_code
        if args.use_oracle_code is not None
        else configs_dict.get("use_oracle_code", False),
        "resume_idx": configs_dict.get("resume_idx", None),
        "use_visual_feedback": args.use_visual_feedback
        if args.use_visual_feedback is not None
        else configs_dict.get("use_visual_feedback", False),
        "use_img_differencing": args.use_img_differencing
        if args.use_img_differencing is not None
        else configs_dict.get("use_img_differencing", False),
        "use_parallel_ensemble": args.use_parallel_ensemble
        if args.use_parallel_ensemble is not None
        else configs_dict.get("use_parallel_ensemble", False),
        "use_video_differencing": args.use_video_differencing
        if args.use_video_differencing is not None
        else configs_dict.get("use_video_differencing", False),
        "use_wrist_camera": args.use_wrist_camera
        if args.use_wrist_camera is not None
        else configs_dict.get("use_wrist_camera", False),
        "use_multimodel": args.use_multimodel
        if args.use_multimodel is not None
        else configs_dict.get("use_multimodel", False),
        "web_ui": getattr(args, "web_ui", None)
        if getattr(args, "web_ui", None) is not None
        else configs_dict.get("web_ui", False),
        "web_ui_port": getattr(args, "web_ui_port", None)
        if getattr(args, "web_ui_port", None) is not None
        else configs_dict.get("web_ui_port", 8200),
        "save_multiturn_prompts": configs_dict.get("save_multiturn_prompts", False),
        # Per-task override of trial.py's post-reset execution alarm, for tasks
        # whose ops are structurally too long for the 2400 s default.  None
        # means "use the default", so configs that omit it are unaffected.
        "exec_alarm_seconds": configs_dict.get("exec_alarm_seconds", None),
    }

    mode = configs_dict.get("agent0_mode")
    if mode is not None:
        if mode not in {"raw", "none"}:
            raise ValueError("agent0_mode must be raw or none")
        seed = int(merged_config["resume_idx"])
        if merged_config["total_trials"] != seed or merged_config["num_workers"] != 1:
            raise ValueError("Agent 0 runs one seed in a fresh process")
        for key in ("use_oracle_code", "use_parallel_ensemble", "use_multimodel", "use_img_differencing", "use_video_differencing", "web_ui"):
            if merged_config[key]:
                raise ValueError(f"Agent 0 requires {key}=false")
        if not merged_config["use_visual_feedback"] or not is_vlm_model(args.model):
            raise ValueError("Agent 0 requires a vision-capable model and initial scene image")
        env_factory["cfg"]["multi_turn_prompt"] = ""
        manifest_path = Path(configs_dict["seed_manifest"]).resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        task = configs_dict["task_name"]
        request_key = "combined_match" if task == "tactile_memory_match" else task
        request = (manifest["requests"].get(request_key, {}).get(str(seed))
                   or manifest.get("smoke_requests", {}).get(request_key, {}).get(str(seed)))
        if seed not in manifest["test_seeds"] + manifest["development_seeds"]:
            raise ValueError("Seed is not in the frozen test/development manifest")
        if task == "tension_strap" and request is not None:
            env_factory["cfg"]["prompt"] = env_factory["cfg"]["prompt"].replace(
                "{force_request}", json.dumps(request, ensure_ascii=False))
        else:
            env_factory["cfg"]["prompt"] = env_factory["cfg"]["prompt"].replace(
                "{force_request}", "12 N for 3 s, then 18 N for 3 s, tolerance +/-0.5 N")
        base = Path(merged_config["output_dir"])
        run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"_{os.getpid()}"
        output = base / task / f"seed_{seed}" / run_id
        output.mkdir(parents=True, exist_ok=False)
        os.environ["CAPX_RUN_DIR"] = str(output)
        merged_config.update(agent0_mode=mode, task_name=task, output_dir=str(output),
                             suite_dir=str(base / task), test_seeds=manifest["test_seeds"],
                             seed_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                             max_trial_attempts=1)
        env_factory["cfg"]["raw_observation_dir"] = str(output / "raw_tactile") if mode == "raw" else None
        for server in api_servers or []:
            server["run_dir"] = str(output)
            server["episode_request_file"] = str(manifest_path)
            server["seed"] = seed
        args.single_program = True
        args._code_request_count = 0
        args.request_log_dir = str(output / "model")
        args.model_timeout_seconds = 300
        args.max_query_retries = 0
        args.disable_proxy = True
        if args.api_key is None:
            args.api_key = os.environ.get("CAPX_API_KEY") or os.environ.get("OPENAI_API_KEY")
        args.trial_timeout = getattr(args, "trial_timeout", None) or configs_dict.get("trial_timeout_seconds", 1200)
        from capx.tactile_skills import default_library
        library = default_library()
        skill_records = []
        for name in env_factory["cfg"].get("tactile_skills", []):
            skill = library.get(name)
            skill_records.append({"name": name, "content": skill.render(compact=False, provenance=True)})
        skills_json = json.dumps(skill_records, sort_keys=True, ensure_ascii=False)
        merged_config["skills_sha256"] = hashlib.sha256(skills_json.encode()).hexdigest()
        (output / "skills.json").write_text(skills_json, encoding="utf-8")
        (output / "config.yaml").write_bytes(Path(config_path).read_bytes())
        (output / "protocol.json").write_text(json.dumps({
            **merged_config, "seed": seed, "model": args.model,
            "max_tokens": args.max_tokens, "temperature": args.temperature,
            "process_timeout_seconds": configs_dict.get("process_timeout_seconds"),
            "reasoning_effort": args.reasoning_effort,
            "request": request, "config_sha256": hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return env_factory, merged_config, api_servers


def _extract_code(content: str | None) -> list[str]:
    """Extract Python code from Markdown fenced code block.

    Args:
        content: Raw model response (may be None if model returned empty output)

    Returns:
        Extracted Python code list
    """
    if content is None:
        return ["# (model returned empty response)"]
    fence_start = "```python\n"
    fence_end = "```"
    start_idx = 0
    end_idx = len(content) + 1
    if fence_start in content:
        start_idx = content.find(fence_start) + len(fence_start)
        content = content[start_idx:]
    if fence_end in content:
        end_idx = content.rfind(fence_end)
        content = content[:end_idx]

    content = content.strip()
    # NOTE: might gen empty code block at the end
    # content_list = content.split("breakpoint_code_block()")

    return [content]


def _build_multi_turn_decision_prompt_legacy(
    obs: dict[str, np.ndarray],
    complete_multi_turn_prompt: str,
    visual_feedback: str | None = None,
    visual_differencing_feedback: str | None = None,
    *,
    is_video_feedback: bool = False,
) -> list[dict]:
    """Build the multi-turn decision prompt for the model.

    Args:
        obs: The observation
        complete_multi_turn_prompt: The formatted multi-turn prompt with the executed and remaining code blocks
        visual_feedback: The base64 encoded image of the current state of the environment
        visual_differencing_feedback: Text description from VDM (image or video based)
        is_video_feedback: If True, use video-appropriate framing for the differencing feedback.

    Returns:
        The multi-turn decision prompt as a list of dictionaries
    """
    multi_turn_decision_prompt = copy.deepcopy(obs["full_prompt"])
    # TODO: I think the order of the content is important, unsure if it should be before or after the complete_multi_turn_prompt
    multi_turn_decision_prompt[-1]["content"].append(
        {"type": "text", "text": complete_multi_turn_prompt}
    )
    if visual_feedback is not None:
        multi_turn_decision_prompt[-1]["content"].append(
            {
                "type": "text",
                "text": "Included below is an image of the current state of the environment (after the code above was executed).",
            }
        )
        multi_turn_decision_prompt[-1]["content"].append(
            {"type": "image_url", "image_url": {"url": visual_feedback}}
        )
    if visual_differencing_feedback is not None:
        if is_video_feedback:
            feedback_header = (
                "Included below is a description of the robot's execution based "
                "on video observation of this turn:"
            )
        else:
            feedback_header = (
                "Included below is the observed differences between the current "
                "state of the environment (afer the code above was executed) and "
                "the previous state of the environment (before the code above was "
                "executed):"
            )
        multi_turn_decision_prompt[-1]["content"].append(
            {"type": "text", "text": f"{feedback_header}\n{visual_differencing_feedback}"}
        )
    multi_turn_decision_prompt[-1]["content"].append(
        {"type": "text", "text": "Based on the code output, potential error messages, and the observation made above, carefully reason about the following:\nPlease respond with EXACTLY ONE of the following:\n- The word 'REGENERATE' followed immediately by new Python code in a fenced code block (```python...```) if you want to modify the code.\n- The word 'FINISH' if the task appears to be complete"}
    )
    # collapse the last message
    multi_turn_decision_prompt[-1]["content"] = collapse_text_image_inputs(multi_turn_decision_prompt[-1]["content"])
    return multi_turn_decision_prompt


def _build_multi_turn_decision_prompt(
    obs: dict[str, np.ndarray],
    complete_multi_turn_prompt: str,
    visual_feedback: str | None = None,
    visual_differencing_feedback: str | None = None,
    *,
    is_video_feedback: bool = False,
) -> list[dict]:
    """Build the multi-turn decision prompt for the model.

    Args:
        obs: The observation
        complete_multi_turn_prompt: The formatted multi-turn prompt with the executed and remaining code blocks
        visual_feedback: The base64 encoded image of the current state of the environment
        visual_differencing_feedback: Text description from VDM (image or video based)
        is_video_feedback: If True, use video-appropriate framing for the differencing feedback.

    Returns:
        The multi-turn decision prompt as a list of dictionaries
    """
    multi_turn_decision_prompt = copy.deepcopy(obs["full_prompt"])
    # TODO: I think the order of the content is important, unsure if it should be before or after the complete_multi_turn_prompt
    multi_turn_decision_prompt[-1]["content"].append(
        {"type": "text", "text": complete_multi_turn_prompt}
    )
    if visual_feedback is not None:
        multi_turn_decision_prompt[-1]["content"].append(
            {
                "type": "text",
                "text": "Included below is an image of the current state of the environment (after the code above was executed).",
            }
        )
        multi_turn_decision_prompt[-1]["content"].append(
            {"type": "image_url", "image_url": {"url": visual_feedback}}
        )
    if visual_differencing_feedback is not None:
        if is_video_feedback:
            feedback_header = (
                "Included below is a description of the robot's execution based "
                "on video observation of this turn:"
            )
        else:
            feedback_header = (
                "Included below is the observed differences between the current "
                "state of the environment (afer the code above was executed) and "
                "the previous state of the environment (before the code above was "
                "executed):"
            )
        multi_turn_decision_prompt[-1]["content"].append(
            {"type": "text", "text": f"{feedback_header}\n{visual_differencing_feedback}"}
        )
    multi_turn_decision_prompt[-1]["content"].append(
        {"type": "text", "text": "Based on the code output, potential error messages, and the observation made above, carefully reason about the following:\nPlease respond with EXACTLY ONE of the following:\n- The word 'REGENERATE' followed immediately by new Python code in a fenced code block (```python...```) if you want to modify the code.\n- The word 'FINISH' if the task appears to be complete"}
    )
    # collapse the last message
    multi_turn_decision_prompt[-1]["content"] = collapse_text_image_inputs(multi_turn_decision_prompt[-1]["content"])
    return multi_turn_decision_prompt


def _parse_multi_turn_decision(content: str) -> tuple[str, str | None]:
    """Parses the multi-turn decision response from the model

    Args:
        content: The model's response to the multi-turn decision prompt

    Returns:
        A tuple containing the decision and the new code (if any)
    """
    if content is not None and ("REGENERATE" in content):
        return "regenerate", content.split("REGENERATE")[1].strip()  # new code
    else:
        return "finish", content


def _get_visual_feedback(
    env, use_wrist_camera: bool = False,
) -> tuple[str | list[str] | None, Image.Image | list[Image.Image] | None]:
    """Get visual feedback from the environment.

    Args:
        env: The environment.
        use_wrist_camera: If True, also render from the wrist camera and return
            lists of base64/PIL images (main camera first, wrist camera second).
            When False (default), returns a single base64 string and PIL image
            for backward compatibility.

    Returns:
        When ``use_wrist_camera`` is False (default):
            ``(base64_image, pil_image)`` — a single data-URL string and PIL image,
            or ``(None, None)`` on failure.
        When ``use_wrist_camera`` is True:
            ``(base64_list, pil_list)`` — lists of data-URL strings and PIL images
            (index 0 = main camera, index 1 = wrist camera), or ``(None, None)``
            on failure.
    """
    render_img = env.render()
    if render_img is None:
        return None, None

    def _encode(img_array):
        pil_img = Image.fromarray(img_array)
        buf = io.BytesIO()
        pil_img.save(buf, format="png")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{b64}", pil_img

    main_b64, main_pil = _encode(render_img)

    if not use_wrist_camera:
        return main_b64, main_pil

    # Multiview: collect main + wrist camera images
    b64_list = [main_b64]
    pil_list = [main_pil]

    if hasattr(env, "render_wrist"):
        wrist_img = env.render_wrist()
        if wrist_img is not None:
            wrist_b64, wrist_pil = _encode(wrist_img)
            b64_list.append(wrist_b64)
            pil_list.append(wrist_pil)

    return b64_list, pil_list


def _save_trial_artifacts(
    config: dict[str, Any],
    trial: int,
    sandbox_rc: int,
    reward: float,
    task_completed: bool,
    final_code: str,
    raw_code: str | None,
    all_responses: list[dict],
    log_lines: list[str],
    visual_feedback_imgs: list[Image.Image],
    ensemble_data: dict[str, str] | None = None,
    multiturn_ensemble_data: list[dict[str, str]] | None = None,
) -> str | None:
    """Save trial artifacts (code, logs, images) to the output directory.

    Returns:
        Path to the saved code file, or None if output_dir is not set.
    """
    if not config["output_dir"]:
        return None
    trial_dir = (
        Path(config["output_dir"])
        / f"trial_{trial:02d}_taskcompleted_{int(task_completed)}"
    )
    trial_dir.mkdir(parents=True, exist_ok=True)

    code_path_obj = trial_dir / "code.py"
    code_path_obj.write_text(final_code)
    code_path = str(code_path_obj)

    if raw_code:
        (trial_dir / "raw_response.sh").write_text(raw_code)

    (trial_dir / "all_responses.json").write_text(json.dumps(all_responses, indent=2))
    (trial_dir / "summary.txt").write_text("\n".join(log_lines))

    # Save initial ensemble data if provided
    if ensemble_data:
        if ensemble_data.get("ensemble_candidates_txt"):
            (trial_dir / "ensemble_candidates.txt").write_text(ensemble_data["ensemble_candidates_txt"])
        if ensemble_data.get("ensemble_synthesis_txt"):
            (trial_dir / "ensemble_synthesis.txt").write_text(ensemble_data["ensemble_synthesis_txt"])

    # Save multi-turn ensemble data (one file per regeneration)
    if multiturn_ensemble_data:
        for entry in multiturn_ensemble_data:
            regen_num = entry.get("regeneration", 0)
            if entry.get("ensemble_candidates_txt"):
                (trial_dir / f"ensemble_candidates_regen_{regen_num:02d}.txt").write_text(
                    entry["ensemble_candidates_txt"]
                )
            if entry.get("ensemble_synthesis_txt"):
                (trial_dir / f"ensemble_synthesis_regen_{regen_num:02d}.txt").write_text(
                    entry["ensemble_synthesis_txt"]
                )

    i = 0
    (trial_dir / "prompts_and_responses").mkdir(parents=True, exist_ok=True)
    for response in all_responses:
        if "task_seg_description" in response:
            (trial_dir / "prompts_and_responses" / "task_seg_description.txt").write_text(response["task_seg_description"])
        if "task_seg_prompt" in response:
            (trial_dir / "prompts_and_responses" / "task_seg_prompt.txt").write_text(str(response["task_seg_prompt"]))
        if "initial_prompt" in response:
            try:
                initial_prompt_content = response["initial_prompt"][-1]["content"][0]["text"]
                if isinstance(initial_prompt_content, list):
                    initial_prompt_content = "\n".join(map(str, initial_prompt_content))
                (trial_dir / "prompts_and_responses" / "initial_prompt.txt").write_text(str(initial_prompt_content))
            except Exception as e:
                print(f"Error saving initial_prompt: {e}")
        if "multi_turn_prompt" in response and response["multi_turn_prompt"] is not None:
            try:
                multi_turn_prompt_content = response["multi_turn_prompt"][-1]["content"][0]["text"]
                if isinstance(multi_turn_prompt_content, list):
                    multi_turn_prompt_content = "\n".join(map(str, multi_turn_prompt_content))
                (trial_dir / "prompts_and_responses" / f"multi_turn_prompt_{i:02d}.txt").write_text(str(multi_turn_prompt_content))
            except Exception as e:
                print(f"Error saving multi_turn_prompt_{i}: {e}")
            i += 1
    for i, img in enumerate(visual_feedback_imgs):
        img.save(trial_dir / f"visual_feedback_{i:02d}.png")

    return code_path



def _single_program(content: str) -> str:
    fences = re.findall(r"```(?:python)?\s*\n(.*?)```", content, flags=re.DOTALL)
    if "```" in content:
        if len(fences) != 1 or content.count("```") != 2:
            raise ValueError("Expected one complete Python program")
        code = fences[0].strip()
    else:
        code = content.strip()
    if not code:
        raise ValueError("Empty Python program")
    compile(code, "<generated>", "exec")
    return code


def _agent0_metrics(summary, config, elapsed_seconds):
    folder = Path(config["output_dir"])
    events = []
    log_file = folder / "univtac_actions.jsonl"
    if log_file.exists():
        for line in log_file.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    motions = {"move", "move_delta", "gripper", "rotate_gripper", "shadow_move", "shadow_wait"}
    starts = {event["action_id"] for event in events
              if event.get("evt") == "action_begin" and event.get("a") in motions}
    ends = {event["action_id"]: event for event in events
            if event.get("kind") == "action" and "action_id" in event}
    def span(event):
        first, last = event.get("physics_start"), event.get("physics_end")
        return last - first if isinstance(first, int) and isinstance(last, int) and last >= first else None
    values = [span(ends.get(action_id, {})) for action_id in starts]
    sent_file = folder / "actions_sent.jsonl"
    sent = len(sent_file.read_text(encoding="utf-8").splitlines()) if sent_file.exists() else 0
    physics = sum(values) if sent == len(starts) and all(value is not None for value in values) else None
    query_values = [span(event) for event in events if event.get("evt") == "query_span" and event.get("category") != "end"]
    end_values = [span(event) for event in events if event.get("evt") == "query_span" and event.get("category") == "end"]
    reset_values = [span(event) for event in events if event.get("kind") == "action" and event.get("a") == "reset"]
    usage = None
    response_path = folder / "model/response.json"
    if response_path.exists():
        try:
            usage = json.loads(response_path.read_text(encoding="utf-8")).get("usage")
        except (ValueError, AttributeError):
            pass
    def token(name, fallback):
        value = (usage or {}).get(name, (usage or {}).get(fallback))
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    request_status = {}
    status_path = folder / "model/request_status.json"
    if status_path.exists():
        try:
            request_status = json.loads(status_path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    failure = None
    if summary.truncated:
        failure = "timeout"
    elif not summary.success:
        if summary.num_code_blocks:
            failure = "code_execution"
        elif request_status.get("completed") is True:
            failure = "generation"
        elif request_status or summary.failure_phase in {"startup", "runner", "reset", "generation"}:
            failure = "infrastructure"
        else:
            failure = "unknown"
    elif summary.task_completed is not True:
        failure = "native_task_failure" if summary.task_completed is False else "native_result_unavailable"
    result = {
        "task": config["task_name"], "condition": config["agent0_mode"], "seed": summary.trial,
        "split": "test" if summary.trial in config["test_seeds"] else "development",
        "task_completed": summary.task_completed, "code_executed": summary.num_code_blocks > 0,
        "code_success": summary.success, "sandbox_rc": summary.sandbox_rc, "failure_type": failure,
        "Execute_steps": sent, "physics_steps": physics,
        "measured_physics_steps": sum(value for value in values if value is not None),
        "failure_phase": summary.failure_phase,
        "server_motion_requests": len(starts),
        "physics_steps_complete": physics is not None,
        "query_physics_steps": sum(query_values) if all(v is not None for v in query_values) else None,
        "end_physics_steps": sum(end_values) if all(v is not None for v in end_values) else None,
        "reset_physics_steps": sum(reset_values) if reset_values and all(v is not None for v in reset_values) else None,
        "input_tokens": token("input_tokens", "prompt_tokens"),
        "output_tokens": token("output_tokens", "completion_tokens"), "provider_usage": usage,
        "elapsed_seconds": elapsed_seconds, "num_code_blocks": summary.num_code_blocks,
        "num_regenerations": summary.num_regenerations,
        "model_requests": int((folder / "model/request.json").exists()),
        "skills_sha256": config["skills_sha256"], "seed_manifest_sha256": config["seed_manifest_sha256"],
        "run_dir": str(folder), "code_path": summary.code_path,
    }
    (folder / "metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    with (folder / "metrics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result))
        writer.writeheader()
        writer.writerow({key: json.dumps(value) if isinstance(value, dict) else value for key, value in result.items()})
    return result


def _save_agent0_summary(summaries, config):
    root = Path(config["suite_dir"])
    rows = []
    for path in sorted(root.glob("seed_*/*/metrics.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    if rows:
        with (root / "runs.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    formal = [r for r in rows if r["split"] == "test"]
    counts = {seed: sum(r["seed"] == seed for r in formal) for seed in config["test_seeds"]}
    unique = [r for r in formal if counts[r["seed"]] == 1]
    duplicates = [seed for seed, count in counts.items() if count > 1]
    successes = sum(r["task_completed"] is True for r in unique)
    report = {"expected_seeds": 40, "unique_seeds_run": len(unique), "duplicate_seeds": duplicates,
              "not_run": [seed for seed, count in counts.items() if count == 0],
              "success_count": successes,
              "success_rate": successes / 40 if len(unique) == 40 and not duplicates and all(r["task_completed"] is not None for r in unique) else None,
              "provisional_success_rate": successes / len(unique) if unique else None,
              "infrastructure_failures": sum(r["failure_type"] == "infrastructure" for r in unique),
              "generation_failures": sum(r["failure_type"] == "generation" for r in unique),
              "failure_types": {kind: sum(r["failure_type"] == kind for r in unique)
                                for kind in sorted({r["failure_type"] for r in unique if r["failure_type"]})},
              "final": len(unique) == 40 and not duplicates and all(r["task_completed"] is not None for r in unique),
              "development_runs": sum(r["split"] == "development" for r in rows)}
    for key in ("Execute_steps", "physics_steps", "input_tokens", "output_tokens", "elapsed_seconds"):
        available = [r[key] for r in unique if r[key] is not None]
        report[key] = {"known_count": len(available), "unknown_count": len(unique) - len(available),
                       "sum_known": sum(available) if available else None,
                       "mean_known": sum(available) / len(available) if available else None}
    (root / "aggregate.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def _print_and_save_summary(
    summaries: list[TrialSummary], args: LaunchArgs, config: dict[str, Any], start_time: float
) -> None:
    """Compute statistics, print them, and save to a file.

    Args:
        summaries: List of trial summaries
        args: Launch arguments
        config: Configuration dictionary
    """
    if config.get("agent0_mode"):
        _save_agent0_summary(summaries, config)
    # Compute statistics
    success_count = 0
    task_completed_count = 0
    total_reward = 0.0
    total_code_blocks = 0
    total_regenerations = 0
    total_finishes = 0
    executed_trials = len(summaries)

    for summary in summaries:
        print(summary.log)
        if summary.code_path:
            print(f"Code saved to {summary.code_path}")
        success_count += int(summary.success)
        total_reward += summary.reward
        task_completed_count += int(summary.task_completed is not None and summary.task_completed)
        total_code_blocks += summary.num_code_blocks
        total_regenerations += summary.num_regenerations
        total_finishes += summary.num_finishes

    if executed_trials == 0:
        print("No trials completed.")
        return

    success_rate = success_count / executed_trials
    average_reward = total_reward / executed_trials
    average_code_blocks = total_code_blocks / executed_trials
    average_regenerations = total_regenerations / executed_trials
    average_finishes = total_finishes / executed_trials

    # Retrieve Git info
    import subprocess

    try:
        git_commit = (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.STDOUT
            )
            .decode("utf-8")
            .strip()
        )
        git_dirty = (
            subprocess.check_output(["git", "status", "--porcelain"], stderr=subprocess.STDOUT)
            .decode("utf-8")
            .strip()
        )
        is_dirty = bool(git_dirty)
    except (subprocess.CalledProcessError, FileNotFoundError):
        git_commit = "unknown"
        is_dirty = False


    elapsed_time = time.time() - start_time
    print("\nSummary Statistics:")
    print(f"Model: {args.model}")
    print(f"Visual Differencing Model: {args.visual_differencing_model}") if config[
        "use_img_differencing"
    ] else ""
    print(f"Config Path: {args.config_path}")
    print(f"Git Commit: {git_commit} (Dirty: {is_dirty})")
    print(f"Total number of trials: {executed_trials}")
    print(
        f"Code generation success rate / Average reward / Task completed: \n{success_rate:.3f}/{average_reward:.3f}/{task_completed_count}"
    )
    print(f"Average code blocks: {average_code_blocks:.3f}")
    print(f"Average regenerations: {average_regenerations:.3f}")
    print(f"Average finishes: {average_finishes:.3f}")
    print(f"Elapsed time: {elapsed_time:.2f} seconds")

    # Write summaries to a txt file
    if config["output_dir"]:
        with open(Path(config["output_dir"]) / "summaries.txt", "w") as f:
            f.write(f"Model: {args.model}\n")
            f.write(f"Visual Differencing Model: {args.visual_differencing_model}\n") if config[
                "use_img_differencing"
            ] else ""
            f.write(f"Config Path: {args.config_path}\n")
            f.write(f"Git Commit: {git_commit} (Dirty: {is_dirty})\n")
            f.write(f"Total number of trials: {executed_trials}\n")
            f.write(
                f"Code generation success rate / Average reward / Task completed: \n{success_rate:.3f}/{average_reward:.3f}/{task_completed_count}\n"
            )
            f.write(f"Average code blocks: {average_code_blocks:.3f}\n")
            f.write(f"Average regenerations: {average_regenerations:.3f}\n")
            f.write(f"Average finishes: {average_finishes:.3f}\n")
            f.write(f"Elapsed time: {elapsed_time:.2f} seconds\n")
