"""Structured tactile trace: one JSONL row per reading, off by default.

Set ``CAPX_TACTILE_TRACE`` to a path and every call that brings tactile
readings home appends one row — what was called, with what arguments, and
every scalar the response carried.  A value with no suffix is treated as a
directory and gets ``tactile_trace.jsonl`` inside it (created on first row);
a value with a suffix is used as the file.  Unset means nothing is written.

Why a separate file exists at all: the printed ``Tactile:`` line is a
summary somebody chose, and what it left out is the half the retrieval work
needs.  The marker shear vector and the anisotropy were computed, sent over
HTTP, and then dropped on the floor, so a finished trial could not be read
back for the channels that separate a slip from a squeeze.  A row here is
the response itself, field for field, so a judgement made in one run can be
re-made — or re-made differently — in another.

Nothing is derived here.  No baseline subtraction, no phase label, no
distance from the gel face: a value computed at trace time becomes a
definition the next version has to keep agreeing with, and the point of
keeping the raw scalar is that later definitions stay free.  Rows are
appended, never rewritten, and are not rotated — a trial that polls tactile
in a loop will make a large file, which is the caller's decision to make.
"""

from __future__ import annotations

import json
import math
import numbers
import os
import time
from pathlib import Path
from typing import Any

ENV_VAR = "CAPX_TACTILE_TRACE"

_FILENAME = "tactile_trace.jsonl"
# Bounds on what one row may become.  A tactile payload is flat scalars plus
# two PNGs; the limits exist for the arguments, which can carry a commanded
# pose, a list of waypoints, or an error string with a whole traceback in it.
_MAX_DEPTH = 3
_MAX_ITEMS = 32
_MAX_STR = 80


def _destination() -> Path | None:
    """Resolve the trace file for this call, or None when tracing is off."""
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    # Decided by suffix, not by "does it exist": the directory a trial writes
    # into usually does not exist yet on the first row, and asking the
    # filesystem would make the meaning of the same env value change mid-run.
    return path if path.suffix else path / _FILENAME


def _compact(value: Any, depth: int = 0) -> Any:
    """Reduce one value to something safe to serialize and cheap to read."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else f"<str {len(value)} chars>"
    if isinstance(value, numbers.Real):
        number = float(value)
        # NaN and inf are not JSON.  They also are not readings; the API's
        # own convention is that 0.0 means missing, and writing a null here
        # keeps the same story legible in the trace.
        return number if math.isfinite(number) else None
    if depth >= _MAX_DEPTH:
        return f"<{type(value).__name__}>"
    if isinstance(value, dict):
        return {str(k): _compact(v, depth + 1)
                for k, v in list(value.items())[:_MAX_ITEMS]
                if not str(k).endswith("base64")}
    if isinstance(value, (list, tuple)):
        return [_compact(v, depth + 1) for v in list(value)[:_MAX_ITEMS]]
    item = getattr(value, "item", None)  # numpy scalar from a derived field
    if callable(item):
        return _compact(item(), depth)
    return f"<{type(value).__name__}>"


def record(call: str, readings: dict[str, Any] | None,
           args: dict[str, Any] | None = None) -> None:
    """Append one row to the trace, if tracing is on and there is a reading.

    Args:
        call: The endpoint or primitive the readings came from
            (``/tactile``, ``/close_gripper``).
        readings: The tactile mapping the response carried.  A call with no
            tactile payload records nothing — the trace is a record of
            touch, not of traffic.
        args: The request body, for calls that have one.

    Returns:
        None.  A trace that cannot be written — no permission, full disk, a
        path that is not a file — is a lost row, not a lost trial, and is
        discarded silently; this must never raise into a caller mid-motion.
    """
    if not readings:
        return
    # Re-read the environment on every call: tracing can be switched on for
    # one segment of a trial (the close that matters) without restarting the
    # process that is holding the arm.
    destination = _destination()
    if destination is None:
        return
    row = {
        "t": round(time.time(), 3),
        "call": call,
        "args": _compact(args or {}),
        "readings": _compact(readings),
    }
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    except Exception:
        return
