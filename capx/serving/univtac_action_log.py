"""Append-only JSONL action log for the UniVTAC serving server.

Every agent action (move / move_delta / gripper / rotate_gripper / reset)
is recorded as one JSON line — success or failure, with the request args,
the returned status/message, and the duration.  Read-only endpoints
(tactile, observation, object_pose, ...) are recorded only when they fail
(503 busy / 500), because they are called constantly and normally succeed.

The log is server-side only: agents never see it (no API endpoint exposes
it), and the recording must never crash the API — all writes are wrapped
in try/except.  It is the operator's debugging record, complementing the
frame-level video (see ``univtac_video.py``): each line carries the same
wall-clock ``t_ms`` as ``index.json``, so a failing action can be joined
to the frames around it.

One file **per episode**, written inside the episode directory so that a
single run's video + index + action log live together::

    outputs/agent/univtac_<task>/<YYYYmmdd_HHMMSS>/
        episode.mp4  index.json  univtac_actions.jsonl

(Frames are scratch, under /tmp — see ``univtac_video.py``.)  The path is
fixed at the first reset (``log_episode_reset``) of each episode;
``UNIVTAC_ACTION_LOG`` overrides it with a single global file.  One open()
per line (same precedent as ``outputs/univtac_tactile_calibration.log``).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# This file lives at <cap-x>/capx/serving/univtac_action_log.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_OUTPUTS_AGENT = _REPO_ROOT / "outputs" / "agent"

# Default path, resolved once the current episode is known (set by
# log_episode_reset).  UNIVTAC_ACTION_LOG overrides it unconditionally.
_LOG_PATH: Path | None = None
_TASK: str | None = None
_EPISODE: str | None = None
_HARNESS_MODE = False  # eval harness announced itself — stop logging


def set_harness_mode() -> None:
    """Stop writing the action log.

    The log is the agent-path debugging record (one file per agent
    episode, mirroring the video layout).  During cap-x evals the harness
    announces itself (``POST /recording/mode``) before its first reset —
    from then on nothing is appended and no episode directories are
    created in ``outputs/agent``.  One-way, like the recorder's own
    harness mode.
    """
    global _HARNESS_MODE
    _HARNESS_MODE = not bool(os.environ.get("CAPX_LOGGER2SKILL"))


def _default_path(task: str, ep: str) -> Path:
    """Episode-scoped log path, mirroring the video layout."""
    return _OUTPUTS_AGENT / f"univtac_{task}" / ep / "univtac_actions.jsonl"


def _append(rec: dict) -> None:
    """Write one JSON line, timestamped.  Never raises."""
    if _HARNESS_MODE:
        return
    rec.setdefault("t_ms", int(time.time() * 1000))
    if _LOG_PATH is None:  # before the first reset — nothing to attach to yet
        return
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:  # disk full / permission — degrade, never crash the API
        logger = __import__("logging").getLogger("univtac_action_log")
        logger.warning("action log write failed (%s): %s", _LOG_PATH, exc)


def log_server_start() -> None:
    """Mark the start of a server run (separates runs in the file)."""
    _append({"evt": "server_start"})


def log_episode_reset(task: str, seed: int, ep: str) -> None:
    """Mark a new episode and switch the log into its directory.

    Called from the sim-side reset, after the recorder opened the new
    episode directory — *ep* is the episode's directory name.  From here
    on every line goes to ``<recording root>/univtac_<task>/<ep>/``
    (``univtac_actions.jsonl``), unless ``UNIVTAC_ACTION_LOG`` pins a
    single file.
    """
    global _TASK, _EPISODE, _LOG_PATH
    _TASK = task
    _EPISODE = ep
    _LOG_PATH = (
        Path(os.environ["UNIVTAC_ACTION_LOG"])
        if os.environ.get("UNIVTAC_ACTION_LOG")
        else _default_path(task, ep)
    )
    _append({"evt": "reset", "ep": ep, "task": task, "seed": seed})


def log_action(
    a: str,
    args: dict | None = None,
    *,
    ok: bool,
    msg: str | None = None,
    dur_s: float | None = None,
    step: int | None = None,
    extra: dict | None = None,
) -> None:
    """Record one action-endpoint call (every call, success or failure).

    Args:
        a: endpoint name ("move", "move_delta", "gripper", "rotate_gripper", "reset").
        args: request parameters (light, scalar fields).
        ok: whether the action succeeded (``success`` in the result dict).
        msg: returned message / error detail, kept verbatim.
        dur_s: wall time the action took.
        step: sim step count reported by the action, if any.
        extra: additional diagnostic fields (e.g. tactile depths on failure).
    """
    rec: dict = {"kind": "action", "a": a, "ok": ok}
    if args:
        rec["args"] = args
    if msg is not None:
        rec["msg"] = msg
    if dur_s is not None:
        rec["dur_s"] = round(dur_s, 3)
    if step is not None:
        rec["step"] = step
    if extra:
        rec.update(extra)
    if _EPISODE is not None:
        rec["ep"] = _EPISODE
    _append(rec)


def log_query_failure(a: str, msg: str, *, args: dict | None = None) -> None:
    """Record a failed read-only endpoint call (503 busy / 500 / 404)."""
    rec: dict = {"kind": "query", "a": a, "ok": False, "msg": msg}
    if args:
        rec["args"] = args
    if _EPISODE is not None:
        rec["ep"] = _EPISODE
    _append(rec)


def log_event(evt: str, **fields) -> None:
    """Record a free-form event marker (e.g. ``evt="cuda_fatal"``)."""
    rec: dict = {"evt": evt}
    rec.update(fields)
    if _EPISODE is not None:
        rec["ep"] = _EPISODE
    _append(rec)
