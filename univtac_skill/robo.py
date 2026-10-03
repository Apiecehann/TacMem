"""Agent-facing client for the UniVTAC tactile API — permanently non-privileged.

This module is the ONLY entry point an interactive coding agent (codex /
cursor / claude code) may import.  It is a thin wrapper that reuses the
existing caP-X UniVTAC API classes unchanged:

  - ``UniVTACPerceptionApi`` — motion / tactile / perception primitives,
    each function's docstring is the API contract for the agent.
    ``get_object_pose`` is NON-PRIVILEGED by design: it returns a SAM3
    text-segmentation centroid back-projected from the depth camera
    (noisy), never the simulator's ground-truth prim pose.  There is no
    privileged/switch toggle — this is permanent.
  - ``UniVTACStubEnv``       — pure-HTTP stub used as the placeholder env.

``get_object_pose`` also needs the SAM3 segmentation server (:8114) —
start it first via ``scripts/launch_sam3_server.sh`` (see
``docs/univtac_agent.md``).  ``check_success`` and ``sample_grasp_pose``
are unchanged (still server-backed).

Nothing here talks to the simulator directly: every call is forwarded to
the already-running UniVTAC server (see ``scripts/launch_univtac_server.sh``).

Usage (from the cap-x repo root, in the cap-x virtualenv)::

    cd univtac_skill
    ./.venv/bin/python -c "from robo import goto_pose, get_observation"

Configuration: ``config.json`` (task, task_description, server URL,
``sam3_prompts``), overridable via the ``UNIVTAC_TASK`` /
``UNIVTAC_TASK_DESCRIPTION`` / ``UNIVTAC_SERVER_URL`` environment variables.
"""

from __future__ import annotations

import os

from tools._config import load_config

# Task / server URL / sam3 prompts live in config.json; the file reading is
# shared with tools._config (same semantics, one implementation).
_CONFIG = load_config()

# Task and server URL (config.json, overridable via env).
TASK = os.environ.get("UNIVTAC_TASK", _CONFIG.get("task", "lift_can"))
# One-sentence goal for this task (config.json), overridable via env.
TASK_DESCRIPTION = os.environ.get(
    "UNIVTAC_TASK_DESCRIPTION", _CONFIG.get("task_description", "")
).strip()
SERVER_URL = os.environ.get(
    "UNIVTAC_SERVER_URL", _CONFIG.get("server_url", "http://127.0.0.1:8120")
).rstrip("/")

try:
    from capx.envs.tasks.tactile.low_level import UniVTACStubEnv
    from capx.integrations.tactile.univtac_perception_api import (
        UniVTACPerceptionApi,
    )
except ImportError:
    raise ImportError(
        "robo requires the caP-X package. Run the agent from inside the cap-x "
        "repo with its virtualenv, e.g.  ./.venv/bin/python -c 'import robo'  "
        "(or set PYTHONPATH to the cap-x repo root)."
    )

# Reuse the existing caP-X API unchanged.  UniVTACStubEnv is a pure-HTTP stub,
# so constructing it here does NOT initialize Isaac Sim.  UniVTACPerceptionApi
# is the NON-PRIVILEGED variant: get_object_pose comes from SAM3 segmentation
# + depth back-projection, never ground truth (permanent, no toggle).  It is
# also pure HTTP (its SAM3 client talks to :8114), so construction is safe.
_env = UniVTACStubEnv(task_name=TASK, server_url=SERVER_URL)
_api = UniVTACPerceptionApi(_env, server_url=SERVER_URL)

# SAM3 text prompts per object (config.json "sam3_prompts"; keys = the nouns
# in task_description).  Pass these to get_object_pose — bare semantic names
# mis-segment under SAM3 (e.g. "prism" catches the socket body).
SAM3_PROMPTS = {
    str(k): str(v) for k, v in _CONFIG.get("sam3_prompts", {}).items()
}

# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------
# NOTE: no reset(). The server initializes the scene at startup and never
# resets mid-run, so an agent cannot restart an episode — a failure is
# final unless it can be repaired in place. This deliberately prevents
# endless retry loops. (The server keeps its internal /reset endpoint for
# the operator; agents must not call endpoints by hand — see SKILL.md.)

# ---------------------------------------------------------------------------
# Re-exported primitives (docstrings from UniVTACTactileApi are the contract)
# ---------------------------------------------------------------------------

goto_pose = _api.goto_pose
move_delta = _api.move_delta
move_reflexive = _api.move_reflexive
open_gripper = _api.open_gripper
close_gripper = _api.close_gripper
get_tactile_data = _api.get_tactile_data
get_tactile_image = _api.get_tactile_image
detect_contact = _api.detect_contact
detect_slip = _api.detect_slip
get_object_pose = _api.get_object_pose
sample_grasp_pose = _api.sample_grasp_pose
rotate_gripper = _api.rotate_gripper
check_success = _api.check_success
get_observation = _api.get_observation
get_ee_pose = _api.get_ee_pose

# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

def health() -> bool:
    """Return True if the UniVTAC server is up and answering /health."""
    import requests as _requests

    try:
        return (
            _requests.get(f"{SERVER_URL}/health", timeout=3.0).status_code == 200
        )
    except Exception:
        return False


def episode_video() -> dict:
    """Finalize the episode's recording and return info (MP4 path, frame count).

    Calling this **assembles ``episode.mp4`` immediately** — the returned
    ``mp4`` exists on disk right away.  Use it once, at the very end of the
    episode: frames captured after this call are not recorded (the episode
    is closed; the next one opens on the next reset).  Returns::

        {"recording": bool, "mp4": str|None, "mp4_ready": bool, "frames": int,
         "dir": str|None, "index_json": str|None, ...}

    ``mp4_ready`` is False only if the server was killed before finalizing
    (the PNG frames in ``frames_dir`` are on disk regardless).  Treat the
    returned paths as opaque — do not read the video files or the
    ``outputs/agent/`` tree yourself.
    """
    import requests as _requests

    try:
        resp = _requests.get(f"{SERVER_URL}/episode_video", timeout=5.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        return {"recording": False, "error": str(exc)}


def api_doc() -> str:
    """Return the full API docstring (what the caP-X prompt would show)."""
    return _api.combined_doc()


__all__ = [
    # motion
    "goto_pose", "move_delta", "move_reflexive", "open_gripper", "close_gripper",
    "rotate_gripper",
    # tactile
    "get_tactile_data", "get_tactile_image", "detect_contact", "detect_slip",
    # perception
    "get_object_pose", "sample_grasp_pose",
    # observation & meta
    "get_observation", "get_ee_pose", "check_success", "health", "episode_video", "api_doc",
    # config (TASK / SERVER_URL stay module-internal: they only feed the
    # client construction above; agents read TASK_DESCRIPTION / SAM3_PROMPTS)
    "TASK_DESCRIPTION", "SAM3_PROMPTS",
]
