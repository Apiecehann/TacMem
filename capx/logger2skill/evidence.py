from __future__ import annotations

import ast
import re
from pathlib import Path

from .storage import read_json, write_json


def one_program(response: str) -> str:
    if not response or not response.strip():
        raise ValueError("The model returned no program")
    if "```" in response:
        matches = list(re.finditer(r"```(?:python|py)?\s*\n(.*?)```", response, flags=re.S))
        if len(matches) != 1 or (response[:matches[0].start()] + response[matches[0].end():]).strip():
            raise ValueError("Expected one Python program, without additional blocks or prose")
        response = matches[0].group(1)
    ast.parse(response)
    return response


def validate_public_program(code: str, hidden: set[str]) -> None:
    tree = ast.parse(code)
    forbidden = hidden | {"env", "APIS", "eval", "exec", "compile", "open", "__import__",
                          "globals", "locals", "vars", "getattr", "setattr", "get_observation",
                          "check_success", "task_completed", "compute_reward", "breakpoint_code_block"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in forbidden:
            raise ValueError(f"Only the documented public API is allowed: {node.id}")
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in forbidden):
            raise ValueError(f"Private/reflection access is not allowed: {node.attr}")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [node.module] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names]
            if any(not m or m.split(".")[0] not in {"numpy", "math", "time", "cv2", "collections"} for m in modules):
                raise ValueError("Imports are restricted to numpy, math, time, cv2 and collections")


def public_text(text: str) -> str:
    private = re.compile(r"\b(key_forces|true_force_N|true_tension_N|tension_N|native_score|"
                         r"thread_spec|accepted_result|strap_acceptance|ground_truth|metadata|reward)\b", re.I)
    return "\n".join("[private field omitted]" if private.search(line) else line for line in text.splitlines())


def make_case(case_id: str, task: str, seed: int, task_prompt: str, code: str,
              stdout: str, stderr: str, final_success: bool | None, execution_ok: bool | None) -> dict:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", case_id):
        raise ValueError("Invalid case id")
    return {"case_id": case_id, "task": task, "seed": int(seed),
            "task_prompt": public_text(task_prompt), "code": public_text(code),
            "stdout": public_text(stdout), "stderr": public_text(stderr),
            "final_success": final_success if type(final_success) is bool else None,
            "execution_ok": execution_ok if type(execution_ok) is bool else None}


def save_case(env, config: dict, code: str, info: dict) -> None:
    out = Path(config["output_dir"])
    task = env._task_name()
    case = make_case(config["case_id"], task, config["episode_seed"], env._task_prompt or "",
                     code, info.get("stdout", ""), info.get("stderr", ""),
                     info.get("task_completed"), info.get("sandbox_rc") == 0)
    write_json(out / "public_case.json", case)
    write_json(out / "loaded_memory.json", {
        "root": env.cfg.tactile_skill_root, "task": task, "text": env._tactile_skill_block()})


def action_counts(path: Path) -> dict:
    actions = completed = 0
    if not path.exists():
        return {"action_requests": None, "completed_actions": None}
    import json
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") == "action" and row.get("a") not in {"reset", "submit_episode"}:
            actions += 1
            completed += int(row.get("ok") is True)
    return {"action_requests": actions, "completed_actions": completed}


def coding_usage(path: Path) -> dict:
    usage = read_json(path) if path.is_file() else {}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": usage.get("total_tokens", input_tokens + output_tokens
                                      if input_tokens is not None and output_tokens is not None else None),
            "raw_usage": usage, "complete": input_tokens is not None and output_tokens is not None}
