"""Reusable, task-agnostic PRIMITIVES for UniVTAC skill agents.

These are *infrastructure*, not policy: warmup, step recording, and camera
snapshots / magnified crops. The agent writes the task plan — which
objects, where, in what order, and what "done" means — and its motion
loops itself. No task name, object name, or threshold is hardcoded here.

Public API::

    ensure_warmup()                     # reuse saved memory, else run warmup
    StepRecorder(name, out_path=None)   # per-step tactile/extra JSON log
    snapshot(name)                      # save head/wrist images to scratch/
    zoom(name, cam, box=.../center=...) # save a magnified crop of a camera
    segmented_zoom(name, prompt)        #   image; the latter crops around a
                                        #   SAM3-prompted object's 2D mask

Internal (not exported): ``tools._config`` (config.json + warmup-memory
helpers used by warmup) and ``tools.warmup.run_warmup`` (called by
``ensure_warmup``).
"""

from .warmup import ensure_warmup
from .record import StepRecorder, snapshot, zoom, segmented_zoom

__all__ = [
    # warmup
    "ensure_warmup",
    # recording
    "StepRecorder", "snapshot", "zoom", "segmented_zoom",
]
