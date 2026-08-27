"""Shared config / warmup-memory helpers for the univtac_skill tools.

Config comes from ``univtac_skill/config.json`` (task, server URL),
overridable via the ``UNIVTAC_TASK`` / ``UNIVTAC_SERVER_URL`` env vars —
the same semantics as :mod:`robo`. Warmup memory lives under
``univtac_skill/memory/warmup_<task>.json``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    return json.loads((SKILL_DIR / "config.json").read_text(encoding="utf-8"))


def task_name() -> str:
    return os.environ.get("UNIVTAC_TASK", load_config().get("task", "lift_can"))


def server_url() -> str:
    return os.environ.get(
        "UNIVTAC_SERVER_URL",
        load_config().get("server_url", "http://127.0.0.1:8120"),
    ).rstrip("/")


def memory_path(task: str | None = None) -> Path:
    return SKILL_DIR / "memory" / f"warmup_{task or task_name()}.json"


def load_memory(task: str | None = None) -> dict | None:
    """Return the saved warmup memory for *task* (default: current task), or None."""
    p = memory_path(task)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_memory(record: dict, task: str | None = None) -> Path:
    p = memory_path(task)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return p
