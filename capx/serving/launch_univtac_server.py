"""
UniVTAC API Server for CaP-X.

Exposes tactile-enabled robot manipulation tasks as HTTP endpoints.
Must be run from the UniVTAC conda environment (requires Isaac Sim).

Usage (from UniVTAC conda env)::

    python -m capx.serving.launch_univtac_server \\
        --task insert_hole --port 8120 --host 127.0.0.1

Or run directly::

    python capx/serving/launch_univtac_server.py \\
        --task insert_hole --port 8120
"""

from __future__ import annotations

import argparse
import base64
import builtins
import io
import logging
import os
import signal
import socket
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel

try:
    from capx.serving.univtac_video import (
        EpisodeRecorder,
        frames_root,
        recording_root,
    )
except ImportError:  # direct-script run: capx/serving/ on sys.path
    from univtac_video import EpisodeRecorder, frames_root, recording_root

try:
    from capx.serving import univtac_action_log as action_log
except ImportError:  # direct-script run: capx/serving/ on sys.path
    from univtac_action_log import action_log

try:
    from capx.serving import tactile_reflex
except ImportError:  # direct-script run: capx/serving/ on sys.path
    import tactile_reflex

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("univtac_server")

# IsaacSim's python bridge raises the root logger to DEBUG when it is
# imported below, so PIL's PngImagePlugin logs every PNG chunk read
# (~12 "STREAM b'IHDR' ..." DEBUG lines per captured frame).  Pin the
# chatty subtree back down; the server reports its own errors/warnings.
logging.getLogger("PIL").setLevel(logging.WARNING)

# UniVTAC's envs/_base_task.py prints a per-step progress line with
# end="\r" (~3 Hz while an action runs).  Piped to a log file or mixed
# with logging output the carriage return is not honored, so every step
# becomes its own line and collides with other output.  Nothing else in
# the process prints with end="\r", so dropping those calls keeps the
# terminal for actual warnings/errors.
_orig_print = builtins.print


def _print(*args, **kwargs):
    if kwargs.get("end") == "\r":
        return
    _orig_print(*args, **kwargs)


builtins.print = _print

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# cap-x repo root — this file lives at <repo>/capx/serving/launch_univtac_server.py
_REPO_ROOT = Path(__file__).resolve().parents[2]

# UniVTAC is cloned as a sibling of cap-x by default; override with UNIVTAC_ROOT.
UNIVTAC_ROOT = Path(os.environ.get("UNIVTAC_ROOT", str(_REPO_ROOT.parent / "UniVTAC")))
# ViTaForge is a fork/superset of UniVTAC with the same ``envs/`` +
# ``task_config/`` layout, so its tasks are served by this same module — they
# just need a different backing root on sys.path (see TASK_BACKING below).
VITAFORGE_ROOT = Path(os.environ.get("VITAFORGE_ROOT", str(_REPO_ROOT.parent / "ViTaForge")))

# One line per tactile reading (marker displacement + depth + ground-truth
# texture) appended on every tactile read, for calibrating the
# rough/smooth classification thresholds in the grasp_classify prompt.
TACTILE_CALIBRATION_LOG = _REPO_ROOT / "outputs" / "univtac_tactile_calibration.log"

AVAILABLE_TASKS = [
    "insert_hole",
    "insert_tube",
    "insert_HDMI",
    "lift_bottle",
    "lift_can",
    "pull_out_key",
    "put_bottle_in_shelf",
    "grasp_classify",
    "tactile_transfer_rearrange_clean",
    "tactile_memory_match",
    "weight_classify",
    "hardness_classify",
    "bulb_tightening",
    "wipe_vase",
    "blind_pick",
    "blind_pick_balls",
    "tension_strap",
]


@dataclass(frozen=True)
class TaskBacking:
    """Where a task's ``envs/`` package and task_config live.

    Attributes:
        root: Repository root containing ``envs/`` and ``task_config/``.
        config_file: Task config YAML under ``root/task_config``.
        cfg_overrides: ``(key, value)`` pairs applied to the TaskCfg after
            ``TaskCfg()`` and before ``Task(...)``.  Used where the config
            file alone would leave the task in a degenerate state (e.g.
            weight_classify defaults to one weight label, which would make
            every trial identical).
        vitforge_tacex: stage ViTaForge's own TacEx fork ahead of the
            environment's editable install for this task (see
            ``_prefer_vitforge_tacex``).  Set where the task's code reaches
            for the fork-only ``vertex_positions`` property.  The two
            classification profiles came before the gate and stay on the
            environment's fork, where their thresholds were measured.
    """

    root: Path
    config_file: str
    cfg_overrides: tuple[tuple[str, Any], ...] = ()
    vitforge_tacex: bool = False


DEFAULT_BACKING = TaskBacking(UNIVTAC_ROOT, "contact.yml")
# Tasks served out of ViTaForge instead of UniVTAC.  The root is a property of
# the task, not of the invocation, so it lives here rather than in a CLI flag —
# an unsupported task/root combination is then unrepresentable.
TASK_BACKING: dict[str, TaskBacking] = {
    "weight_classify": TaskBacking(
        VITAFORGE_ROOT,
        "gelsight.yml",
        (("weight_label", "random"),),
    ),
    # hardness_classify defaults to hardness_label="random" already, but state
    # it here for the same reason as weight_classify: a config that pinned one
    # label would make every trial identical and the eval meaningless.
    "hardness_classify": TaskBacking(
        VITAFORGE_ROOT,
        "gelsight.yml",
        (("hardness_label", "random"),),
    ),
    # Spike (2026-09-25): bulb_tightening is ViTaForge's consolidated successor
    # to screw_light_bulb, built on the force-task contract in
    # envs/_force_task_utils.py.  Registered so the server can construct it at
    # all; whether it can be *driven and scored* through this server's
    # atom-based API is what the spike measures (see /force_state and
    # /submit_episode below).
    #
    # Its task_parameters block does NOT reach the task through
    # _apply_task_config (a 4-field whitelist), so init_task binds it
    # explicitly with ViTaForge's own prepare_force_task_config — which is
    # also what supplies the derived ``workspace`` key.  No
    # VITAFORGE_TASK_CONFIG export is needed (see the binding block in
    # init_task; exporting it instead only gets you as far as
    # KeyError: 'workspace').
    # The task NAME must stay "bulb_tightening": FINAL_TASKS keys the
    # force-task binder off the name (envs/_force_task_utils.py:702), and an
    # unregistered name would silently skip the binder and run on code
    # defaults.
    #
    # REVERTED 2026-09-27 to the reference profile.  bulb_tightening_seated.yml
    # (initial_depth_m .020 / seat_top_m .0284, free travel 0.6 mm) was tried and
    # is FALSIFIED, not merely disappointing.  check_success needs
    # formal_net_turns >= 1 AND phase_error_m < .002 at the same time, and
    # expected_advance(yaw) = (yaw/360)*lead (:614) means one full turn demands
    # 7.69 mm of real axial advance.  A seated bulb cannot advance: the compliant
    # seat reaches axial_overload (seat_force_N > 180) after ~1.35 mm
    # (k ~= 132 kN/m, measured 10.97 N at 83 um).  So the two gates are jointly
    # unsatisfiable at zero free travel.  The 13 mm free travel IS the thread's
    # own travel -- 13.000/7.6895 = 1.6907 turns = 608.6 deg, which is where
    # net_turns comes from and why phase_error stays ~0; the expert's 594 deg is
    # exactly that.  Removing it removed the only phase in which both hold.
    # The seated profile file is kept for its evidence value and marked
    # FALSIFIED in its own header -- do not repoint this at it.
    #
    # UPDATE 2026-10-10: the profile this points at is no longer the 13 mm one.
    # seat_top_m went .022 -> .0285 in the task config, so the free travel is
    # half -- 6.5 mm -- to halve the turns an insertion needs and with them the
    # release/re-grasp cycles the episode spends its time in.  Nothing above
    # changes: 0.6 mm is still unsatisfiable and now further outside the window,
    # since the turn window came down with the travel (1<=turns<=3 became
    # .5<=turns<=2, in both halves -- ViTaForge/envs/bulb_tightening.py's
    # check_success and its check_success_detail, and this repo's oracle, whose
    # seat-arm depth is now 5.5 mm of hand rather than 8).  The 13 mm sentences
    # above describe the profile as it stood when the seated one was falsified;
    # the same inequality is what rules the seated one out at either travel.
    "bulb_tightening": TaskBacking(
        VITAFORGE_ROOT,
        "bulb_tightening.yml",
        (),
    ),
    # wipe_vase (2026-10-13) is the second ViTaForge force task served here,
    # registered for the same reason as bulb_tightening and needing the same
    # three things said about it:
    #
    # 1. It is in FINAL_TASKS (envs/_force_task_utils.py:715), so the binder in
    #    init_task fires on the name alone and supplies the derived
    #    ``workspace`` key.  Nothing extra to pass here.
    # 2. wipe_vase.yml's ``task_parameters`` carries ``controller: marker_rgb``
    #    + a calibration file, which under serving is INERT, not broken: the
    #    marker-RGB path only runs when the policy sets ``self.reference``, and
    #    the cap-x oracle never does.  Everything that is scored is a physical
    #    quantity (contact force, cleaned fraction, sliding distance), so the
    #    scoring path is unaffected.  Registered rather than stripped so the
    #    served profile stays byte-identical to the training one.
    # 3. Its episode budget needs no handling here: the task calls
    #    configure_final_task(cfg, params, max_policy_seconds=160) from its own
    #    __init__ (wipe_vase.py:367), unlike the other profiles that the
    #    binder has to size.
    "wipe_vase": TaskBacking(
        VITAFORGE_ROOT,
        "wipe_vase.yml",
        (),
    ),
    # blind_pick (2026-10-15) is the first ViTaForge task served here that is
    # NOT a force task: it is a MemoryTask (envs/_memory_task.py), so none of
    # the force-task machinery below applies -- the name is absent from
    # FINAL_TASKS, its profile has no ``task_parameters`` block for the binder
    # to carry, and it sizes its own episode (step_lim = 600).  Three things
    # still have to be said about it.
    #
    # 1. vitforge_tacex=True.  blind_pick reads ``self.object.vertices`` on
    #    EVERY control step (blind_pick.py:85) and ``pad.vertex_positions``
    #    inside ``_pad_contacts`` (:196, called every 12th step).  ViTaForge's
    #    UipcObject adds the ``vertex_positions`` property and UniVTAC's does
    #    not, so without this the task dies with an AttributeError deep inside
    #    its first step -- not at import time.  The config-keyed gate in
    #    init_task cannot see it: blind_pick.yml has no ``task_parameters``
    #    block, which is exactly the marker that gate keys on.
    #
    # 2. memory_timeout_seconds is raised.  Two step hooks latch a failure off
    #    the same field -- MemoryTask's own ('timeout', _memory_task.py:118)
    #    and blind_pick's ('search_timeout', blind_pick.py:104) -- each
    #    comparing ``(step_count - policy_start_step) * cfg.sim.dt`` against
    #    it.  cfg.sim.dt is 1/120 (_base_task.py:202) with decimation 1, so the
    #    10.0 the profile ships is 1200 control steps of SIMULATED time, sized
    #    for a loop this server does not run: ViTaForge's own episode stops at
    #    step_lim = 600 first, so the clock never fires there.  Serving never
    #    consults step_lim (_sim_reset only reports it), which leaves 1200
    #    steps as the real budget -- roughly 15-30 planned motions, measured
    #    against wipe_vase's 2576-step episode (65 motions).  A touch search is
    #    made of motions, and an episode that runs out scores 0 however good
    #    the eventual grasp was: ``failure`` latches for the rest of the
    #    episode and ``plan_success`` follows it down.  60 s is 7200 steps,
    #    three times the largest episode measured on this rig.
    #
    # 3. The wall-clock side of that budget is the trial's, not the server's,
    #    and it is deliberately the TIGHTER of the two.  Measured on wipe_vase
    #    (2026-10-15): 2576 control steps in 985.9 s, 0.38 s of wall per step,
    #    so 7200 steps would be ~2740 s -- and the config in
    #    env_configs/univtac/blind_pick/ sets exec_alarm_seconds to 2400, i.e.
    #    about 6300 steps.  That is right for a ported oracle of a dozen
    #    motions (low hundreds of seconds): an episode that reaches the wall
    #    clock has spent 52 of the 60 simulated seconds and is heading for 0
    #    either way, so nothing is lost by not distinguishing the two endings.
    "blind_pick": TaskBacking(
        VITAFORGE_ROOT,
        "blind_pick.yml",
        (("memory_timeout_seconds", 60.0),),
        vitforge_tacex=True,
    ),
    # blind_pick_balls (2026-10-22) is the touch-only multi-material ball
    # retrieval task synced from OpenTacBench `tactile-memory-tasks` @3266266.
    # It is the one entry here that is NOT a ViTaForge task at all: it is a
    # standalone Gymnasium environment (ViTaForge/env/tasks/blind_pick_balls.py)
    # with its own PhysX/UIPC world, its own differential-IK controller and its
    # own scorer.  init_task therefore branches on the name before it touches
    # TaskCfg, and capx/serving/univtac_ball_adapter.py supplies the
    # RobotManager/TactileManager/ActorManager surface every endpoint reads.
    # Three things worth having written down:
    #
    # 1. vitforge_tacex=True, for the same reason as blind_pick above: the task
    #    is written against ViTaForge's TacEx/UIPC fork (it calls
    #    ``marker_motion_simulator``, ``get_surface_vertices_world`` and
    #    ``UipcObject._vertex_count``), and the fork must be on sys.path before
    #    ``import envs.*`` binds.  The config-keyed gate cannot see this one
    #    either: blind_pick_balls.yml has no task_parameters block.
    # 2. It is NOT in FINAL_TASKS and its profile is a TaskCfg fragment, not a
    #    training config — the force-task binder must not run on it.  The
    #    init_task branch returns before that code.
    # 3. Nothing about the episode budget is configured here.  The task sizes
    #    its own: ``failure='timeout'`` once step_count*physics_dt exceeds
    #    episode_seconds (240 s = 28800 physics steps at 120 Hz), which the
    #    adapter republishes as ``cfg.step_lim`` for /reset to report.
    "blind_pick_balls": TaskBacking(
        VITAFORGE_ROOT,
        "blind_pick_balls.yml",
        (),
        vitforge_tacex=True,
    ),
    # tension_strap (2026-10-31) is the third ViTaForge force task served here
    # and the first whose verdict is a continuous physical quantity rather than
    # a geometric end state.  Four things worth having written down:
    #
    # 1. It is in FINAL_TASKS (envs/_force_task_utils.py:715), so the binder in
    #    init_task fires on the name alone and supplies ``workspace``.  Its
    #    profile carries a ``task_parameters`` block, which is what puts
    #    ViTaForge's TacEx fork ahead of the environment's editable install.
    #
    # 2. ``controller: marker_rgb`` + ``calibration`` in the profile are INERT
    #    under serving, the same way wipe_vase's are: that path only runs when
    #    the expert ``_perform_strap_episode`` sets ``self.reference``, and the
    #    cap-x policy never calls it.  The profile is registered unmodified
    #    rather than stripped so the served task stays byte-identical to the
    #    training one -- and because ``create_actors`` validates the frozen
    #    calibration's physical contract against these very parameters, so
    #    editing either side would raise at construction.
    #
    # 3. One process serves ONE physics seed, and the seed is drawn before UIPC
    #    initialises: ``_reset_actors`` raises unless ``cfg.seed`` equals
    #    ``params['physics_seed']``.  That is prepare_force_task_config's seed
    #    argument, i.e. the server's --seed.  Same wall as bulb_tightening.
    #
    # 4. The verdict is ``check_success()`` = the two-stage acceptance in
    #    StrapLifecycle: 12 N then 18 N, each held inside +-0.5 N for 3 s of
    #    SIMULATED time, 60 s total.  Nothing has to be "submitted" -- the task
    #    does not have a submission ritual (see /force_state's
    #    ``submission_requested``, which stays False here).
    "tension_strap": TaskBacking(
        VITAFORGE_ROOT,
        "tension_strap.yml",
        (),
    ),
}


def _backing_for(task_name: str) -> TaskBacking:
    """Return the backing root/config for a task (UniVTAC unless registered)."""
    return TASK_BACKING.get(task_name, DEFAULT_BACKING)


def _reserve_listener(host: str, port: int) -> socket.socket:
    """Bind and retain the API listener until Uvicorn takes ownership.

    Task initialization can take several minutes. Reserving the address before
    it starts makes a competing server fail immediately instead of wasting that
    initialization time and later spawning a watchdog for a server that never
    bound its port.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(socket.SOMAXCONN)
    except OSError:
        listener.close()
        raise
    return listener

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="UniVTAC Tactile API Server")

# Global state (initialized after Isaac Sim app starts)
_TASK: Any = None
_TASK_NAME: str = ""
# Marker positions at reset time (gripper open, no contact), used as the
# zero-displacement reference for marker flow.  The sim-internal "initial"
# frame is captured once at startup and carries a constant per-finger offset,
# so absolute displacement read against it is meaningless.
_MARKER_REFERENCE: dict[str, Any] | None = None
# Object-relative grasp offset, keyed by object name.  On the first
# sample_grasp_pose call (EE still at the valid pre-grasp pose left by
# pre_move) we cache offset = ee_pose - object_pose plus the grasp quaternion;
# subsequent calls return object_pose + offset so a re-grasp tracks the object
# wherever it has moved.  Cleared on every reset.
_GRASP_OFFSET: dict[str, tuple[np.ndarray, np.ndarray]] = {}
# Tasks whose pre_move does NOT leave the EE above the object, so the
# EE-relative offset above has no meaning (the EE is wherever it reset to,
# typically far from the object).  A *privileged* caller — one that already
# reads ground truth — gets a real grasp pose for these instead: the object's
# own pose from the simulator, plus the task's own grasp rule.  Maps task name
# -> {object name: name of the task-module constant holding the grasp centre's
# lift above the object origin}; the number is read off the task module so it
# keeps a single owner.  Non-privileged callers never take this branch, which is
# the whole point: the offset cache above is what keeps ground truth out of
# their grasp poses.
_GT_GRASP_RULES: dict[str, dict[str, str]] = {
    "bulb_tightening": {"light_bulb": "GRASP_LOCAL_Z"},
}
# Tasks that must NOT take the precise-landing close (grasp_servo).  The
# 2026-10-19 ViTaForge port charges 5 mm per sim step up to first touch and
# then adds accumulated 1.95 mm rungs without re-anchoring.  On a bulb that
# stays kinematically fixed until BOTH pads reach the band, the first contact
# is already ~5 mm deep and the pad force spikes past the 170 N glass-break
# threshold (seeds 12/13, 2026-10-21: 0 -> ~205 N in one sim step, glass
# crushed).  These tasks keep the historical close: 0.5 mm steps until
# contact, then 0.05 mm re-anchored steps.
_GRASP_SERVO_EXCLUDED: frozenset[str] = frozenset({"bulb_tightening"})
# Durable episode video recorder: frames go to scratch (frames_root), and
# episode.mp4 + index.json are assembled into the recording root on
# finalize (episode_video / reset / shutdown).  See univtac_video.py.
_RECORDER = EpisodeRecorder(recording_root(), frames_root=frames_root())

# ---------------------------------------------------------------------------
# Thread-safe command queue — Isaac Sim APIs must be called from main thread.
# FastAPI handlers (uvicorn thread) post to _cmd_queue; the main-thread
# _dispatch_loop() executes them.
# ---------------------------------------------------------------------------
import queue as _queue
import threading as _threading
import time as _time

_cmd_queue: _queue.Queue = _queue.Queue()


# Timeout for main-thread operations.  Motion planning (cuRobo IK)
# can hang indefinitely on degenerate inputs, so every _run_on_main
# call must have a bounded wait.  2026-09-07: raised 120 -> 550 so a
# slow-but-finite physics grind (observed: contact-heavy ops taking a
# few hundred seconds, /health still answering) completes instead of
# being cut at 125 s.  2026-10-02: raised 550 -> 1500 — a re-grasp
# close in bulb_tightening ran 752 s on a loaded machine and a 550 s
# cut killed the whole trial at 56% of its budget.  The client read
# timeout (1800 s) outwaits this 1505 s wait by design, so a
# merely-slow op returns its real response instead of a client-side
# ReadTimeout (which the runner treats as a retryable timeout and
# restarts from depth 0, throwing away all progress).
_MOTION_TIMEOUT = 1500.0  # goto_pose / move_delta / rotate_gripper
_QUERY_TIMEOUT  = 10.0   # get_ee_pose, get_object_pose, etc.

def _exec_wrapper(fn, args, kwargs, out: dict) -> None:
    """Execute *fn* and store result/exception in *out* (thread-safe)."""
    try:
        out["result"] = fn(*args, **kwargs)
        out["error"] = None
    except Exception as exc:
        out["result"] = None
        out["error"] = exc


def _run_on_main(fn, *args, timeout: float = _QUERY_TIMEOUT, **kwargs):
    """Post a function to the main thread and block until it completes.

    All simulation operations run **synchronously** on the main thread
    because Isaac Sim / PhysX / cuRobo are NOT thread-safe at the
    C++/CUDA level.  The dispatch loop serialises queued operations and
    executes them one at a time.

    Args:
        timeout: Maximum seconds to wait for the operation to be picked
                 up and completed.  When exceeded a *TimeoutError* is
                 raised (but the dispatch loop continues running the
                 operation — the caller must decide whether to retry).
    """
    kwargs["_op_timeout"] = timeout

    result_holder: dict = {}
    done_event = _threading.Event()
    _cmd_queue.put((fn, args, kwargs, result_holder, done_event))

    if not done_event.wait(timeout=timeout + 5):
        # +5 s grace: dispatch loop may be busy with a previous
        # operation, or the operation itself is slow/stuck.
        raise TimeoutError(
            f"Simulation operation '{getattr(fn, '__name__', fn)}' "
            f"timed out after {timeout:.0f}s.  The physics engine may "
            f"be stuck — consider resetting the environment."
        )

    if result_holder.get("error"):
        raise result_holder["error"]
    return result_holder["result"]


def _run_action(name: str, args: dict, timeout: float, fn, *fargs,
                detail_prefix: str) -> dict:
    """Run an action endpoint's sim call, logging one trajectory line.

    Every action call (success or failure) is appended to the action log
    (see univtac_action_log.py); each line carries the left/right tactile
    depths from the result dict when present, so the log is self-contained
    for physical progress (e.g. a wedged prism shows as a plateau/jump in
    the depths even though the action returned success).  Raises
    HTTPException(500, f"{detail_prefix}: {e}") on error — the same
    response text the handlers produced before.
    """
    t0 = _time.perf_counter()
    try:
        result = _run_on_main(fn, *fargs, timeout=timeout)
    except Exception as e:
        action_log.log_action(
            name, args, ok=False, msg=f"{detail_prefix}: {e}",
            dur_s=_time.perf_counter() - t0,
        )
        raise HTTPException(status_code=500, detail=f"{detail_prefix}: {e}") from e
    dur = _time.perf_counter() - t0
    ok = bool(result.get("success", True))
    extra = None
    if isinstance(result.get("tactile"), dict):
        extra = {"tactile": {
            "L_depth_mm": result["tactile"].get("left_depth_mm"),
            "R_depth_mm": result["tactile"].get("right_depth_mm"),
        }}
    action_log.log_action(
        name, args, ok=ok, msg=result.get("message"),
        dur_s=dur, step=result.get("step"), extra=extra,
    )
    return result


def _motion_message(success: bool, default: str) -> str:
    """What an action endpoint puts in the response's ``message``.

    A failure carries the reason the task recorded for it when it has one, so
    a call refused for a reason of its own -- a motion after the episode
    ended, which the blind-ball adapter refuses rather than pretending to
    move -- reaches the client as itself instead of as "Motion planning
    failed".  Tasks that record no such reason keep ``default``.

    A success normally reads ``OK``, with one exception: the ball adapter
    reports a move that stopped a few millimetres short of its goal as
    executed (SETTLE_M) and records how short in ``last_motion_note``, so the
    log and the tool output still show where the hand actually is.
    """
    if success:
        return getattr(_TASK, "last_motion_note", "") or "OK"
    return getattr(_TASK, "last_motion_error", "") or default


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _rgb_to_uint8(tensor: torch.Tensor) -> np.ndarray:
    """Quantize an RGB tensor (H, W, 3) to the uint8 array the payload carries."""
    if tensor.dtype != torch.uint8:
        tensor = (tensor.clamp(0, 1) * 255).to(torch.uint8)
    return tensor.cpu().numpy()


def _rgb_tensor_to_base64(tensor: torch.Tensor) -> str:
    """Convert RGB tensor (H, W, 3) to base64 PNG string."""
    img = Image.fromarray(_rgb_to_uint8(tensor))
    buf = io.BytesIO()
    img.save(buf, format="png")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _rgb_tensor_anisotropy(tensor: torch.Tensor | None) -> float | None:
    """Row-to-row over column-to-column gradient of the marker image.

    The one scalar that sees spatial structure instead of an image average:
    pressed against a rough surface the gel picks up the ridges as banding,
    which raises the row gradient while leaving the column gradient alone.
    1.0 is an evenly loaded gel; None when there is no image to read.

    Computed from ``_rgb_to_uint8`` -- the same array the base64 payload
    carries -- so this is the identical number a client decodes out of that
    image.
    """
    if tensor is None:
        return None
    gray = _rgb_to_uint8(tensor).astype(np.float64).mean(axis=2)
    if gray.ndim != 2 or gray.shape[0] < 2 or gray.shape[1] < 2:
        return None
    row = float(np.abs(np.diff(gray, axis=0)).mean())
    col = float(np.abs(np.diff(gray, axis=1)).mean())
    return row / col if col > 1e-9 else None


def _depth_tensor_to_base64(tensor: torch.Tensor) -> str:
    """Convert depth tensor (H, W) to base64 PNG (16-bit grayscale)."""
    arr = tensor.cpu().numpy()
    # Normalize to 0-65535 for 16-bit
    if arr.max() > arr.min():
        arr = ((arr - arr.min()) / (arr.max() - arr.min()) * 65535).astype(np.uint16)
    else:
        arr = arr.astype(np.uint16)
    img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format="png")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _depth_tensor_to_float32_base64(tensor: torch.Tensor) -> str:
    """Convert metric depth tensor (H, W) in metres to raw float32 base64.

    Unlike :func:`_depth_tensor_to_base64` (which min-max normalizes for display
    and loses absolute scale), this preserves the exact metre values so a client
    can back-project pixels into a metric point cloud.
    """
    arr = tensor.detach().float().cpu().numpy()
    return base64.b64encode(arr.tobytes()).decode("utf-8")


def _camera_metric_data(name: str) -> dict[str, Any]:
    """Read a camera's metric depth, intrinsics, and world pose (ROS convention).

    The serving task config requests ``rgb`` only, so depth/intrinsics/pose are
    read directly from the Isaac Lab camera's cached data instead of the task
    observation dict. Non-privileged perception uses these to reconstruct a
    metric point cloud from the RGB image.

    Args:
        name: Camera name ("head" or "wrist").

    Returns:
        dict with keys ``depth`` (H, W float32 metres or None), ``shape``
        (``[H, W]`` or None), ``intrinsics`` (3x3 list or None), ``pos_w``
        (3-list metres or None), ``quat_w_ros`` (4-list wxyz or None).
    """
    manager = getattr(_TASK, "_camera_manager", None)
    cameras = getattr(manager, "cameras", {}) if manager is not None else {}
    cam = cameras.get(name)
    if cam is None or cam.data is None:
        return {"depth": None, "shape": None, "intrinsics": None,
                "pos_w": None, "quat_w_ros": None}
    data = cam.data
    try:
        depth = data.output.get("depth")
        if depth is not None:
            depth = depth.detach().squeeze(0).squeeze(-1)  # [H, W] float32 metres
        intrinsics = data.intrinsic_matrices
        pos = data.pos_w
        # ROS convention: camera frame (optical axis +Z) -> world. This matches
        # the depth backprojection x=(u-cx)z/fx, y=(v-cy)z/fy, z=depth.
        quat = data.quat_w_ros
        return {
            "depth": depth,
            "shape": list(depth.shape) if depth is not None else None,
            "intrinsics": intrinsics.squeeze(0).tolist() if intrinsics is not None else None,
            "pos_w": pos.squeeze(0).tolist() if pos is not None else None,
            "quat_w_ros": quat.squeeze(0).tolist() if quat is not None else None,
        }
    except Exception:
        return {"depth": None, "shape": None, "intrinsics": None,
                "pos_w": None, "quat_w_ros": None}


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------


class ResetRequest(BaseModel):
    seed: int = 0


class RecordingModeRequest(BaseModel):
    mode: str = "harness"


class ResetResponse(BaseModel):
    success: bool = True
    seed: int = 0
    step_limit: int = 300


class MoveRequest(BaseModel):
    position: list[float]  # [x, y, z]
    quaternion_wxyz: list[float]  # [w, x, y, z]
    z_approach: float = 0.0
    gripper_action: str | None = None  # "open", "close", or None
    gripper_pos: float | None = None  # 0=closed, 1=open
    tag: str = "move"


class MoveDeltaRequest(BaseModel):
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0
    drx: float = 0.0
    dry: float = 0.0
    drz: float = 0.0
    coord: Literal["world", "ee"] = "world"
    tag: str = "delta"


class StopOn(BaseModel):
    """Fast-loop stop conditions for /move_reflexive (all ORed).

    A key present in the JSON enables its condition; threshold values
    absent fall back to the module defaults (tactile_reflex.
    DEFAULT_THRESHOLDS).  contact=True enables the first-touch stop.
    """

    contact: bool = False
    hard_depth: float | None = None    # mm; default 20.0
    shear_spike: float | None = None   # px; default 2.0
    slip: float | None = None          # deg; default 30.0
    asymmetry: float | None = None     # mm; default 3.0
    plateau: dict[str, float] | None = None  # {"mm": 0.5, "steps": 5}
    max_steps: int | None = None       # safety net


class MoveReflexiveRequest(MoveDeltaRequest):
    stop_on: StopOn | None = None
    tag: str = "delta_reflexive"


class GripperRequest(BaseModel):
    action: Literal["open", "close"]
    force_threshold: float | None = None
    use_adaptive_grasp: bool | None = None  # None → fall back to task config
    position: float | None = None  # 0.0 = fully closed, 1.0 = fully open; None → 0.0 / 1.0


class ActionResponse(BaseModel):
    success: bool = True
    plan_success: bool = True
    message: str = ""
    step: int = 0
    tactile: dict[str, Any] | None = None


class MoveReflexiveResponse(ActionResponse):
    event: dict[str, Any] | None = None
    terminated_by: str | None = None


class TactileResponse(BaseModel):
    left_contact: bool = False
    right_contact: bool = False
    left_depth_mm: float = 0.0
    right_depth_mm: float = 0.0
    left_marker_displacement_px: float = 0.0
    right_marker_displacement_px: float = 0.0
    left_marker_coherence: float = 0.0
    right_marker_coherence: float = 0.0
    # None rather than a neutral 1.0 when there is no image to read, so a
    # caller can skip the reading instead of trusting a fabricated one.
    left_marker_anisotropy: float | None = None
    right_marker_anisotropy: float | None = None
    # Coherent shear direction (mean marker displacement vector), sensor UV
    # pixel frame — the direction signal behind the reflex events.
    left_marker_shear_dx: float = 0.0
    left_marker_shear_dy: float = 0.0
    right_marker_shear_dx: float = 0.0
    right_marker_shear_dy: float = 0.0
    # Fine relief of the gel's indentation map, mm; only tasks that expose a
    # height map report it.  None rather than 0.0 when unreported — 0.0 is a
    # real reading (a smooth imprint) and the two must not be confused.  Keep
    # this model in step with the client's _MOTION_TACTILE_FIELDS: a field the
    # motion responses carry and this model omits is silently stripped from
    # /tactile and /observation.
    left_height_roughness_mm: float | None = None
    right_height_roughness_mm: float | None = None
    left_rgb_base64: str = ""
    right_rgb_base64: str = ""
    left_depth_base64: str = ""
    right_depth_base64: str = ""


class ObservationResponse(BaseModel):
    head_rgb_base64: str = ""
    wrist_rgb_base64: str = ""
    head_depth_base64: str = ""
    wrist_depth_base64: str = ""
    # Metric depth (float32 metres, raw bytes) + camera calibration for
    # non-privileged perception. The normalized *_depth_base64 fields above are
    # for display only and lose absolute scale.
    head_depth_float32_base64: str = ""
    wrist_depth_float32_base64: str = ""
    head_image_shape: list[int] | None = None
    wrist_image_shape: list[int] | None = None
    head_intrinsics: list[list[float]] | None = None
    wrist_intrinsics: list[list[float]] | None = None
    head_cam_pos_w: list[float] | None = None
    wrist_cam_pos_w: list[float] | None = None
    head_cam_quat_w_ros: list[float] | None = None
    wrist_cam_quat_w_ros: list[float] | None = None
    tactile: TactileResponse | None = None
    ee_position: list[float] = []
    ee_quaternion: list[float] = []
    gripper_qpos: float = 0.0


class PoseResponse(BaseModel):
    position: list[float]  # [x, y, z]
    quaternion_wxyz: list[float]  # [w, x, y, z]


class SuccessResponse(BaseModel):
    completed: bool = False
    check_early_stop: bool = False
    detail: dict | None = None


class RotateGripperRequest(BaseModel):
    object_name: str
    angle_rad: float
    steps: int = 4
    # Lead-coupled helix mode: axial advance per full turn, in metres.  None
    # keeps the plain rotate path that every other task uses.
    feed_lead_m: float | None = None


class RotateGripperResponse(ActionResponse):
    feed_lead_m: float | None = None
    requested: int = 0
    completed: int = 0
    grip_released: bool = False
    aborted: str | None = None
    reached: str | None = None
    trace: list[dict[str, Any]] | None = None


class ObjectPoseResponse(BaseModel):
    found: bool = False
    position: list[float] | None = None
    quaternion_wxyz: list[float] | None = None
    bbox_extent: list[float] | None = None


# ---------------------------------------------------------------------------
# Simulator operations (blocking, run on executor thread)
# ---------------------------------------------------------------------------


def _capture_frame() -> str:
    """Capture current composite frame shot (cameras + tactile) as base64 PNG.

    Forces a render update so that the camera images are fresh even when the
    task is running in ``mode="collect"`` (which normally skips per-step
    rendering for performance).
    """
    global _TASK
    _TASK._update_render()
    obs = _TASK._get_observations()
    img = _TASK.get_frame_shot(obs)
    buf = io.BytesIO()
    # Convert torch tensor (H,W,3) to PIL and save as PNG
    pil_img = Image.fromarray(img.cpu().numpy().astype(np.uint8))
    pil_img.save(buf, format="png")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


_LAST_RECORDED_STEP: int | None = None


def _record_frame(label: str) -> str:
    """Capture the composite frame and persist it to the episode recording.

    ``_RECORDER`` writes each frame to disk immediately, so the episode can
    be replayed even if the server dies before finalizing.

    Skipped when the task's step count has not advanced since the last
    recorded frame.  This recording is one frame per API call, not per unit of
    time, so a call the simulation refused would add the same picture again:
    on 2026-10-26 (seed 1) the blind-ball episode ended with the ball in the
    bowl and the program's next 88 calls, refused and motionless, became 176
    of the video's 293 frames -- the last minute of it showed an arm that had
    simply finished.  The step that ends an episode still gets its frame: it
    did advance the count.
    """
    global _LAST_RECORDED_STEP
    step = int(getattr(_TASK, "step_count", -1))
    if step == _LAST_RECORDED_STEP:
        return ""
    _LAST_RECORDED_STEP = step
    b64 = _capture_frame()
    _RECORDER.record(b64, label)
    return b64


def _sim_reset(seed: int, *, log: bool = True) -> dict:
    """Reset the task environment.

    Args:
        seed: scene-randomization seed for this episode.
        log: whether to open an action-log episode.  The startup reset
            passes False: there is no real episode yet, and opening the
            log here would create an ``outputs/agent`` episode directory
            before the eval harness has a chance to announce harness mode.
    """
    global _TASK, _MARKER_REFERENCE, _LAST_RECORDED_STEP
    _RECORDER.finalize()  # assemble previous episode's video (no-op if none)
    _GRASP_OFFSET.clear()
    _TASK.reset(seed=seed)
    # The new episode counts its steps from zero again; forget where the last
    # one stopped so its first frame is never mistaken for a repeat.
    _LAST_RECORDED_STEP = None
    _rec_info = _RECORDER.start(_TASK_NAME, seed)
    if log:
        action_log.log_episode_reset(_TASK_NAME, seed, _rec_info.get("started") or "")
    _record_frame("reset:initial")  # initial state
    try:
        obs = _TASK._get_observations()
        tactile = obs.get("tactile", {})
        _MARKER_REFERENCE = {
            "left": tactile.get("left_tactile", {}).get("marker"),
            "right": tactile.get("right_tactile", {}).get("marker"),
        }
        if _MARKER_REFERENCE["left"] is not None:
            _MARKER_REFERENCE["left"] = _MARKER_REFERENCE["left"][1].clone()
        if _MARKER_REFERENCE["right"] is not None:
            _MARKER_REFERENCE["right"] = _MARKER_REFERENCE["right"][1].clone()
    except Exception:
        _MARKER_REFERENCE = None
    try:
        truth = getattr(_TASK, "choice", None)
        with open(TACTILE_CALIBRATION_LOG, "a") as _f:
            _f.write(f"--- reset task={_TASK_NAME} seed={seed} truth={truth} ---\n")
    except Exception:
        pass
    return {"success": _TASK.plan_success, "seed": seed, "step_limit": _TASK.cfg.step_lim}


def _sim_get_ee_pose() -> dict:
    """Get current end-effector pose."""
    pose = _TASK._robot_manager.get_ee_pose()
    return {"position": pose.p.tolist(), "quaternion_wxyz": pose.q.tolist()}


def _sim_get_gripper_state() -> dict:
    """Get current gripper state."""
    qpos = float(_TASK._robot_manager.get_gripper_qpos())
    return {"qpos": qpos, "is_open": qpos > 0.5}


# Travel below which a failed motion call counts as a planning failure -- the
# hand never got anywhere -- rather than a refusal by the world.  Only the
# first kind is worth a retry: a move that travelled and then stopped was
# blocked, and the same goal asked for again meets the same block.  2026-10-26
# (blind balls, seed 2): the last 9 cm toward the bowl stopped 3 mm short and
# every retry the endpoints could make was either pointless or, in
# move_delta's case, aimed backward from where the move began.
_RETRY_PROGRESS_M = 0.001


def _sim_move(position: list, quat_wxyz: list, z_approach: float,
              gripper_action: str | None, gripper_pos: float | None, tag: str) -> dict:
    """Move end-effector to target pose with optional gripper action."""
    global _TASK

    from envs.utils.atom import Action
    from envs.utils.transforms import Pose

    target_pose = Pose(p=np.array(position), q=np.array(quat_wxyz))

    if gripper_action:
        if gripper_action == "open":
            gripper_pos = 1.0
        elif gripper_action == "close":
            gripper_pos = 0.0

    if gripper_action and gripper_pos is not None:
        action = Action(
            action="all",
            target_pose=target_pose,
            target_gripper_pos=gripper_pos,
            pre_dis=z_approach if z_approach > 0 else None,
        )
    else:
        action = Action(
            action="move",
            target_pose=target_pose,
            pre_dis=z_approach if z_approach > 0 else None,
        )

    # Capture before-move frame
    _record_frame(f"move:tag={tag}:before")

    origin = np.asarray(_TASK._robot_manager.get_ee_pose().p,
                        dtype=np.float64).reshape(3)
    try:
        success = _TASK.move([action], tag=tag, delay=True, is_save=False)
    except Exception as exc:
        logger.error("_TASK.move() raised exception: %s", exc)
        success = False

    # --- fallback: retry without z-approach waypoint -------------------------
    # The pre_dis waypoint adds an intermediate IK query that cuRobo often
    # cannot solve (especially near workspace boundaries).  When the primary
    # plan fails and z_approach was used, retry with a direct move -- but only
    # when the first attempt did not move the hand at all: a move that
    # travelled and then stopped was refused by the world, and asking for the
    # same target again meets the same refusal.  The retry also restores
    # ``plan_success``, which a failed ``_TASK.move`` latches to False; without
    # that the retry hits move()'s entry guard and answers without planning,
    # which is what this branch did until 2026-10-26.
    if not success and z_approach > 0 and not gripper_action:
        travelled = float(np.linalg.norm(
            np.asarray(_TASK._robot_manager.get_ee_pose().p,
                       dtype=np.float64).reshape(3) - origin))
        if travelled >= _RETRY_PROGRESS_M:
            logger.warning(
                "move: the hand travelled %.1f mm and stopped; not retrying "
                "without the z-approach waypoint (tag=%s)",
                travelled * 1000.0, tag)
        else:
            action_no_app = Action(
                action="move",
                target_pose=target_pose,
                pre_dis=None,
            )
            _TASK.plan_success = True  # the failed attempt latched it
            try:
                success = _TASK.move([action_no_app], tag=tag + "_noapp",
                                     delay=True, is_save=False)
            except Exception as exc:
                logger.error("_TASK.move() fallback raised exception: %s", exc)
                success = False

    # If the motion planner failed, reset plan_success so that subsequent
    # gripper / move_delta calls are not blocked by a stale failure flag.
    if not success:
        _TASK.plan_success = True

    tactile_data = _sim_get_tactile_data()
    _record_frame(f"move:tag={tag}:after")

    return {
        "success": success,
        "plan_success": _TASK.plan_success,
        "message": _motion_message(success, "Motion planning failed"),
        "step": _TASK.step_count,
        "tactile": tactile_data,
    }


def _sim_move_delta(dx: float, dy: float, dz: float,
                    drx: float, dry: float, drz: float,
                    coord: str, tag: str) -> dict:
    """Move end-effector by a delta amount.

    Enforces a minimum displacement of 0.002 m / 0.02 rad to avoid
    cuRobo IK failures on extremely small deltas, and a **maximum** of
    0.10 m translation / 0.5 rad rotation per call (single-step safety
    limit — use multiple calls for larger moves).

    Also refuses moves that would place the end-effector below the table
    surface (Z < 0), which can hang the physics engine.
    """
    global _TASK

    from envs.utils.atom import Action

    # --- safety limits ---------------------------------------------------------
    MIN_TRANS = 0.002   # 2 mm
    MIN_ROT   = 0.02    # ~1 degree
    MAX_TRANS = 0.10    # 10 cm — single-step cap
    MAX_ROT   = 0.5     # ~30 degrees


    # Reject huge single-step deltas that can crash the physics engine
    for val, name, limit in [(abs(dx), "dx", MAX_TRANS), (abs(dy), "dy", MAX_TRANS),
                              (abs(dz), "dz", MAX_TRANS), (abs(drx), "drx", MAX_ROT),
                              (abs(dry), "dry", MAX_ROT), (abs(drz), "drz", MAX_ROT)]:
        if val > limit:
            return {
                "success": False,
                "plan_success": _TASK.plan_success,
                "message": (
                    f"|{name}|={val:.3f} exceeds per-step limit ({limit:.3f}). "
                    f"Use multiple smaller steps instead."
                ),
                "step": _TASK.step_count,
                "tactile": _sim_get_tactile_data(),
            }

    # Skip trivially tiny deltas (below all minimums).  Report it as a
    # failure, not as "OK": nothing moved, and a caller running a
    # converge-to-target loop would otherwise see an endless run of successes
    # while the arm stands still.
    if abs(dx) < MIN_TRANS and abs(dy) < MIN_TRANS and abs(dz) < MIN_TRANS \
       and abs(drx) < MIN_ROT and abs(dry) < MIN_ROT and abs(drz) < MIN_ROT:
        return {
            "success": False,
            "plan_success": _TASK.plan_success,
            "message": (
                f"Delta is below the {MIN_TRANS * 1000:.0f} mm / "
                f"{MIN_ROT:.2f} rad minimum on every axis - the arm did NOT "
                f"move.  Command at least {MIN_TRANS * 1000:.0f} mm, or stop: "
                f"repeated no-op commands mean the target is blocked."
            ),
            "step": _TASK.step_count,
            "tactile": _sim_get_tactile_data(),
        }

    if coord == "ee":
        ee_pose = _TASK._robot_manager.get_ee_pose()
        new_pose = ee_pose.add_bias([dx, dy, dz], coord="local")
        if any([drx, dry, drz]):
            new_pose = new_pose.add_rotation(euler=[drx, dry, drz], coord="local")
    else:
        ee_pose = _TASK._robot_manager.get_ee_pose()
        new_pose = ee_pose.add_bias([dx, dy, dz], coord="world")
        if any([drx, dry, drz]):
            new_pose = new_pose.add_rotation(euler=[drx, dry, drz], coord="world")

    # --- table-surface guard --------------------------------------------------
    # Reject moves that would place the EE at or below the table surface.
    # cuRobo cannot plan to Z ≈ 0 — the IK solver treats the table as a hard
    # constraint.  Require at least 1 cm clearance.
    target_z = float(new_pose.p[2])
    if target_z < 0.01:
        _TASK.plan_success = True
        return {
            "success": False,
            "plan_success": _TASK.plan_success,
            "message": (
                f"Target Z={target_z:.3f} is below the table surface. "
                f"Reduce the downward delta."
            ),
            "step": _TASK.step_count,
            "tactile": _sim_get_tactile_data(),
        }

    # Capture before-move frame
    _record_frame(f"move_delta:tag={tag}:before")

    # --- motion planning with fallback ----------------------------------------
    # cuRobo can transiently fail even for simple deltas (random seed, joint
    # limits, workspace boundary).  Retry with progressively smaller deltas
    # before reporting failure -- but only when the hand did not move at all:
    # a move that travelled and then stopped was refused by the world (a ball
    # between the pads, a wall, a joint limit), and the same direction at half
    # the distance runs into the same refusal.
    #
    # This ladder used to get two things wrong and so never moved the arm.
    # ``_TASK.move`` latches ``plan_success = False`` when it fails, and the
    # retries hit move()'s entry guard and returned False without planning
    # anything; the reflexive endpoint restores the flag between attempts for
    # exactly this reason.  And every retry was built on the pose captured
    # before the first attempt, so once a retry did run it would ask a hand
    # that had already travelled most of the way to go back to a point behind
    # it.  Each retry now re-reads the pose and restores the flag first.
    origin = np.asarray(ee_pose.p, dtype=np.float64).reshape(3)
    try:
        success = _TASK.move(
            [Action(action="move", target_pose=new_pose)],
            tag=tag,
            delay=True,
            is_save=False,
        )
    except Exception as exc:
        logger.error("_TASK.move() delta raised exception: %s", exc)
        success = False

    if not success:
        travelled = float(np.linalg.norm(
            np.asarray(_TASK._robot_manager.get_ee_pose().p,
                       dtype=np.float64).reshape(3) - origin))
        if travelled >= _RETRY_PROGRESS_M:
            logger.warning(
                "_sim_move_delta: the hand travelled %.1f mm and stopped; "
                "not retrying a smaller delta (%s)",
                travelled * 1000.0,
                {"dx": dx, "dy": dy, "dz": dz, "drx": drx, "dry": dry, "drz": drz},
            )
        else:
            for scale in [0.5, 0.25]:
                logger.warning(
                    "_sim_move_delta: retrying with scale=%.0f%% (was %s)",
                    scale * 100,
                    {"dx": dx, "dy": dy, "dz": dz, "drx": drx, "dry": dry, "drz": drz},
                )
                scaled_dx, scaled_dy, scaled_dz = dx * scale, dy * scale, dz * scale
                scaled_drx, scaled_dry, scaled_drz = drx * scale, dry * scale, drz * scale

                # Skip if the scaled delta is below the minimum
                if (abs(scaled_dx) < MIN_TRANS * 0.5 and abs(scaled_dy) < MIN_TRANS * 0.5
                        and abs(scaled_dz) < MIN_TRANS * 0.5
                        and abs(scaled_drx) < MIN_ROT * 0.5 and abs(scaled_dry) < MIN_ROT * 0.5
                        and abs(scaled_drz) < MIN_ROT * 0.5):
                    logger.warning("_sim_move_delta: scaled delta too small, skipping retry")
                    continue

                base = _TASK._robot_manager.get_ee_pose()
                if coord == "ee":
                    scaled_pose = base.add_bias([scaled_dx, scaled_dy, scaled_dz],
                                                coord="local")
                    if any([scaled_drx, scaled_dry, scaled_drz]):
                        scaled_pose = scaled_pose.add_rotation(
                            euler=[scaled_drx, scaled_dry, scaled_drz], coord="local")
                else:
                    scaled_pose = base.add_bias([scaled_dx, scaled_dy, scaled_dz],
                                                coord="world")
                    if any([scaled_drx, scaled_dry, scaled_drz]):
                        scaled_pose = scaled_pose.add_rotation(
                            euler=[scaled_drx, scaled_dry, scaled_drz], coord="world")

                scaled_target_z = float(scaled_pose.p[2])
                if scaled_target_z < 0.01:
                    continue

                _TASK.plan_success = True  # the failed attempt latched it
                try:
                    success = _TASK.move(
                        [Action(action="move", target_pose=scaled_pose)],
                        tag=tag + f"_retry{int((1-scale)*100)}",
                        delay=True,
                        is_save=False,
                    )
                except Exception as exc:
                    logger.error("_TASK.move() delta retry raised exception: %s", exc)
                    success = False

                if success:
                    break

    # If the delta move failed, reset plan_success so subsequent
    # actions are not blocked by a stale failure flag.
    if not success:
        _TASK.plan_success = True

    tactile_data = _sim_get_tactile_data()
    _record_frame(f"move_delta:tag={tag}:after")

    return {
        "success": success,
        "plan_success": _TASK.plan_success,
        "message": _motion_message(success, "Motion planning failed"),
        "step": _TASK.step_count,
        "tactile": tactile_data,
    }


def _sim_move_reflexive(dx: float, dy: float, dz: float,
                        drx: float, dry: float, drz: float,
                        coord: str, tag: str, stop_on: dict | None) -> dict:
    """Move by a delta, stopping early when a tactile event fires.

    Mirrors _sim_move_delta (same safety limits and retry loop), but a
    per-step reflex callback evaluates the tactile fast loop (see
    tactile_reflex.py) while the motion runs.  The first enabled event
    freezes the arm in place — ``plan_success=False`` plus a no-op
    ``set_arm``; plan_success alone cannot stop the kinematic write in
    ``take_dense_action`` — and an event digest is returned instead of the
    full trajectory.

    ``stop_on=None`` runs the motion to completion and only reports the
    digest (monitoring mode).  A reflex-terminated motion is NEVER retried
    at a smaller scale.  ``plan_success`` is always restored to True before
    returning so the next motion is not blocked by a stale failure flag.
    """
    global _TASK

    from envs.utils.atom import Action

    # --- safety limits (mirrors _sim_move_delta; keep in sync) --------------
    MIN_TRANS = 0.002   # 2 mm
    MIN_ROT   = 0.02    # ~1 degree
    MAX_TRANS = 0.10    # 10 cm — single-step cap
    MAX_ROT   = 0.5     # ~30 degrees

    for val, name, limit in [(abs(dx), "dx", MAX_TRANS), (abs(dy), "dy", MAX_TRANS),
                              (abs(dz), "dz", MAX_TRANS), (abs(drx), "drx", MAX_ROT),
                              (abs(dry), "dry", MAX_ROT), (abs(drz), "drz", MAX_ROT)]:
        if val > limit:
            return {
                "success": False,
                "plan_success": _TASK.plan_success,
                "message": (
                    f"|{name}|={val:.3f} exceeds per-step limit ({limit:.3f}). "
                    f"Use multiple smaller steps instead."
                ),
                "step": _TASK.step_count,
                "tactile": _sim_get_tactile_data(),
            }

    # Skip trivially tiny deltas (below all minimums).  Report it as a
    # failure, not as "OK": nothing moved, and a caller running a
    # converge-to-target loop would otherwise see an endless run of successes
    # while the arm stands still.
    if abs(dx) < MIN_TRANS and abs(dy) < MIN_TRANS and abs(dz) < MIN_TRANS \
       and abs(drx) < MIN_ROT and abs(dry) < MIN_ROT and abs(drz) < MIN_ROT:
        return {
            "success": False,
            "plan_success": _TASK.plan_success,
            "message": (
                f"Delta is below the {MIN_TRANS * 1000:.0f} mm / "
                f"{MIN_ROT:.2f} rad minimum on every axis - the arm did NOT "
                f"move.  Command at least {MIN_TRANS * 1000:.0f} mm, or stop: "
                f"repeated no-op commands mean the target is blocked."
            ),
            "step": _TASK.step_count,
            "tactile": _sim_get_tactile_data(),
        }

    if coord == "ee":
        ee_pose = _TASK._robot_manager.get_ee_pose()
        new_pose = ee_pose.add_bias([dx, dy, dz], coord="local")
        if any([drx, dry, drz]):
            new_pose = new_pose.add_rotation(euler=[drx, dry, drz], coord="local")
    else:
        ee_pose = _TASK._robot_manager.get_ee_pose()
        new_pose = ee_pose.add_bias([dx, dy, dz], coord="world")
        if any([drx, dry, drz]):
            new_pose = new_pose.add_rotation(euler=[drx, dry, drz], coord="world")

    # --- table-surface guard (mirrors _sim_move_delta; keep in sync) -------
    target_z = float(new_pose.p[2])
    if target_z < 0.01:
        _TASK.plan_success = True
        return {
            "success": False,
            "plan_success": _TASK.plan_success,
            "message": (
                f"Target Z={target_z:.3f} is below the table surface. "
                f"Reduce the downward delta."
            ),
            "step": _TASK.step_count,
            "tactile": _sim_get_tactile_data(),
        }

    _record_frame(f"move_reflexive:tag={tag}:before")

    # --- motion with reflex evaluation --------------------------------------
    # Each attempt installs a FRESH detector (the digest reflects the
    # attempt that actually ran) and restores the hook in a finally.  A
    # terminated motion breaks the loop: re-executing it at a smaller scale
    # would re-run the same contact.  Between attempts plan_success is
    # restored explicitly — move() sets it False on a planning failure and
    # the next attempt's entry guard would otherwise return False without
    # planning anything.
    success = False
    ctx = ReflexContext()
    detector: Any = None
    for scale in [1.0, 0.5, 0.25]:
        if scale < 1.0:
            logger.warning(
                "_sim_move_reflexive: retrying with scale=%.0f%% (was %s)",
                scale * 100,
                {"dx": dx, "dy": dy, "dz": dz, "drx": drx, "dry": dry, "drz": drz},
            )
            scaled_dx, scaled_dy, scaled_dz = dx * scale, dy * scale, dz * scale
            scaled_drx, scaled_dry, scaled_drz = drx * scale, dry * scale, drz * scale

            # Skip if the scaled delta is below the minimum
            if (abs(scaled_dx) < MIN_TRANS * 0.5 and abs(scaled_dy) < MIN_TRANS * 0.5
                    and abs(scaled_dz) < MIN_TRANS * 0.5
                    and abs(scaled_drx) < MIN_ROT * 0.5 and abs(scaled_dry) < MIN_ROT * 0.5
                    and abs(scaled_drz) < MIN_ROT * 0.5):
                logger.warning("_sim_move_reflexive: scaled delta too small, skipping retry")
                continue

            if coord == "ee":
                target = ee_pose.add_bias([scaled_dx, scaled_dy, scaled_dz],
                                          coord="local")
                if any([scaled_drx, scaled_dry, scaled_drz]):
                    target = target.add_rotation(
                        euler=[scaled_drx, scaled_dry, scaled_drz], coord="local")
            else:
                target = ee_pose.add_bias([scaled_dx, scaled_dy, scaled_dz],
                                          coord="world")
                if any([scaled_drx, scaled_dry, scaled_drz]):
                    target = target.add_rotation(
                        euler=[scaled_drx, scaled_dry, scaled_drz], coord="world")

            scaled_target_z = float(target.p[2])
            if scaled_target_z < 0.01:
                continue
            attempt_tag = tag + f"_retry{int((1-scale)*100)}"
        else:
            target = new_pose
            attempt_tag = tag

        detector = tactile_reflex.TactileEventDetector(stop_on=stop_on)
        ctx = ReflexContext()
        _install_reflex(detector, stop_on, ctx)
        try:
            success = _TASK.move(
                [Action(action="move", target_pose=target)],
                tag=attempt_tag,
                delay=True,
                is_save=False,
            )
        except Exception as exc:
            logger.error("_TASK.move() reflexive move raised exception: %s", exc)
            success = False
        finally:
            _uninstall_reflex(ctx)

        if ctx.triggered:
            break  # terminated motions are never re-executed at a smaller scale
        if success:
            break
        _TASK.plan_success = True  # next scale must not hit move()'s entry guard

    # Unconditional restore: a triggered motion leaves plan_success=False
    # (the freeze), which would otherwise no-op the NEXT move() at its
    # entry guard — the exact stale-flag leak this branch used to cause
    # for the trigger path (only non-triggered failures were restored).
    _TASK.plan_success = True

    tactile_data = _sim_get_tactile_data()
    _record_frame(f"move_reflexive:tag={tag}:after")

    ok = success or ctx.triggered
    if ctx.triggered:
        message = f"OK (reflex: {ctx.reason} @ step {ctx.step})"
    else:
        message = _motion_message(success, "Motion planning failed")
    digest = {
        **detector.summary,
        "terminated_by": ctx.reason,
        "steps": ctx.step or _TASK.step_count,
        "callback_error": ctx.error,
    }
    return {
        "success": ok,
        # Reflex termination is a SUCCESS outcome and the restore is
        # unconditional — a stale False would no-op the next move_delta at
        # its entry guard and freeze every subsequent _step.
        "plan_success": True,
        "message": message,
        "step": _TASK.step_count,
        "tactile": tactile_data,
        "terminated_by": ctx.reason,
        "event": digest,
    }


# Position closes are verified against the opening they commanded, but the
# fingers approach that opening asymptotically instead of landing on it: on
# 2026-10-24 a close to 0.0340 m per jaw settled at 0.03432 m after the ball
# adapter's four hold steps and stopped there, and a zero-tolerance check
# answered "Gripper action failed" on every such call — the program that asked
# for it lost its whole row to a bare except.  A millimetre of slack absorbs
# the settle without weakening what the check exists for: an object in the way
# stops the fingers millimetres short (a 60 mm ball met by a 56 mm command
# holds each jaw at ~30.0 mm, 2 mm short).  The adaptive branch below already
# allows +1.0 mm on the depth it reads, for the same reason.
_POSITION_CLOSE_TOL_M = 0.001


def _sim_gripper(action: str, force_threshold: float | None,
                 use_adaptive_grasp: bool | None = None,
                 position: float | None = None) -> dict:
    """Open or close gripper.  Uses adaptive grasp for closing (tactile feedback).

    Matches the expert's ``atom.close_gripper()`` / ``atom.open_gripper()``
    behaviour: when closing and ``use_adaptive_grasp`` is enabled, the
    ``gripper_depth_threshold`` is passed through the Action args so that
    ``adaptive_set_gripper`` stops at the correct tactile deformation level.
    Without the threshold the adaptive grasp degenerates to a simple
    position close, which may not grip firmly enough.

    ``use_adaptive_grasp`` — when explicitly provided in the request it
    overrides the task-level ``use_adaptive_grasp`` config.  This lets the
    tactile API enable adaptive grasp while the motion API disables it,
    even though both share the same task instance.  Honouring that override
    takes more than resolving the threshold here: ``_base_task.move()`` picks
    the adaptive branch from ``self.cfg.use_adaptive_grasp``, so the config
    itself is flipped for the duration of this one action (see the
    try/finally around the move below).  Without that flip a task shipping
    ``use_adaptive_grasp = False`` — ViTaForge's weight_classify — silently
    ignored ``force_threshold`` and every close slammed to full force.

    ``position`` — gripper opening as a percentage, 0.0 = fully closed and
    1.0 = fully open.  This is the primitive grip lever: it commands the
    finger separation directly, so on a rigid object it sets the squeeze
    force without depending on the depth servo landing where it was asked.
    None keeps the historical full close (0.0) / full open (1.0).

    Args:
        action: "open" or "close".
        force_threshold: gel-depth target in mm for a tactile-stopped close.
        use_adaptive_grasp: per-request override of the task-level config.
        position: gripper opening percentage, 0.0-1.0.

    Returns:
        dict with the action result, final gripper state and tactile data.
    """
    global _TASK

    from envs.utils.atom import Action

    _use_adaptive = (
        use_adaptive_grasp
        if use_adaptive_grasp is not None
        else _TASK.cfg.use_adaptive_grasp
    )
    if action == "open":
        target_pos = 1.0 if position is None else float(position)
        depth_threshold = None  # open never uses adaptive threshold
        # Opens keep whatever control law the task itself uses.
        adaptive_for_action = bool(_TASK.cfg.use_adaptive_grasp)
    else:
        target_pos = 0.0 if position is None else float(position)
        # Resolve adaptive-grasp threshold (matches atom.close_gripper logic)
        adaptive_for_action = bool(_use_adaptive)
        depth_threshold = (
            (
                force_threshold
                if force_threshold is not None
                else _TASK.cfg.adaptive_grasp_depth_threshold
            )
            if adaptive_for_action
            else None
        )

    # Capture frame before the gripper action
    _record_frame(f"gripper:{'open' if action == 'open' else 'close'}:before")

    # ``_base_task.move()`` selects the adaptive branch from the task config,
    # not from the request, so a request-level override only takes effect if
    # the config says so for the duration of this call.  Flip it here and
    # restore it below: the task instance is shared with the motion APIs,
    # which must keep the task's own setting.
    _cfg_adaptive_before = _TASK.cfg.use_adaptive_grasp
    _TASK.cfg.use_adaptive_grasp = adaptive_for_action

    try:
        # grasp_servo=True (LOCAL 2026-09-09): explicit force_threshold closes
        # land precisely on the requested depth (servo mode in
        # adaptive_set_gripper) instead of overshooting by 2-4 mm.  Closes
        # without a threshold keep the historical behaviour, so default firm
        # grasps everywhere else are untouched.  Tasks in
        # _GRASP_SERVO_EXCLUDED keep the historical close on purpose.
        success = _TASK.move(
            [Action(action="gripper", target_gripper_pos=target_pos,
                    gripper_depth_threshold=depth_threshold,
                    grasp_servo=(action == "close"
                                 and force_threshold is not None
                                 and _TASK_NAME not in _GRASP_SERVO_EXCLUDED))],
            tag="gripper",
            delay=True,
            is_save=False,
        )
    except Exception as exc:
        logger.error("_TASK.move() gripper raised exception: %s", exc)
        success = False
    finally:
        # Restore the task's own setting: the instance is shared with every
        # other action, and only this one action asked for the override.
        _TASK.cfg.use_adaptive_grasp = _cfg_adaptive_before

    # If the gripper action failed, reset plan_success so subsequent
    # actions are not blocked by a stale failure flag.
    if not success:
        _TASK.plan_success = True

    # adaptive_set_gripper silently stops at max_steps if the target is
    # never reached (e.g. a full-range open), and move() does not propagate
    # that — verify the final state so the client learns the truth.
    final_qpos = _TASK._robot_manager.get_gripper_qpos()
    if action == "open":
        target_qpos = _TASK._robot_manager.gripper_percent2qpos(target_pos)
        reached = final_qpos >= 0.95 * target_qpos
        detail = f"qpos={final_qpos:.4f} target={target_qpos:.4f}"
    else:
        depth = float(_TASK._tactile_manager.get_min_depth().min())
        if adaptive_for_action and depth_threshold is not None:
            # Tactile-stopped close ends at the contact qpos, not zero:
            # accept if the gel deformation reached the depth threshold.
            # The threshold must be the one this request asked for, not the
            # task config's: with a request-level force_threshold the task
            # config usually holds None, and the check below would then
            # reject a close that landed exactly as instructed.
            reached = depth < depth_threshold + 1.0
            detail = (f"qpos={final_qpos:.4f} depth={depth:.2f}mm "
                      f"target={depth_threshold:.2f}mm")
        else:
            # Position close: the fingers stop on the commanded qpos — or
            # earlier, when the object blocks them — so demand only that the
            # gripper closed as far as the target, or at least halfway when
            # the target is (near) fully closed.  The tolerance is the
            # fingers' settle, not an error bar on the command (see
            # _POSITION_CLOSE_TOL_M); a blocked close misses by millimetres.
            target_qpos = _TASK._robot_manager.gripper_percent2qpos(target_pos)
            halfway = 0.5 * _TASK._robot_manager.gripper_percent2qpos(1.0)
            reached = (
                final_qpos <= max(target_qpos, halfway) + _POSITION_CLOSE_TOL_M
            )
            detail = (f"qpos={final_qpos:.4f} target={target_qpos:.4f} "
                      f"depth={depth:.2f}mm")
    if not reached:
        logger.warning(
            "Gripper %s did not reach its target state (%s) — "
            "adaptive_set_gripper likely exhausted max_steps",
            action, detail,
        )
        success = False

    tactile_data = _sim_get_tactile_data()
    _record_frame(f"gripper:{'open' if action == 'open' else 'close'}:after")

    return {
        "success": success,
        "plan_success": _TASK.plan_success,
        # The numbers ride along on the failure: without them the caller sees
        # only "Gripper action failed" and cannot tell a blocked close from
        # one that settled a fraction of a millimetre short.
        "message": _motion_message(success, f"Gripper action failed ({detail})"),
        "step": _TASK.step_count,
        "tactile": tactile_data,
    }


def _sim_get_tactile_data() -> dict:
    """Get current tactile sensor readings."""
    global _TASK

    try:
        obs = _TASK._get_observations()
        tactile = obs.get("tactile", {})

        left_data = tactile.get("left_tactile", {})
        right_data = tactile.get("right_tactile", {})

        # Extract depth minima for contact detection
        left_depth = left_data.get("depth")
        right_depth = right_data.get("depth")

        if left_depth is not None:
            left_min_depth = float(left_depth.min())
            left_contact = left_min_depth < _TASK.cfg.robot.tactile_far_plane - 0.1
        else:
            left_min_depth = float("inf")
            left_contact = False

        if right_depth is not None:
            right_min_depth = float(right_depth.min())
            right_contact = right_min_depth < _TASK.cfg.robot.tactile_far_plane - 0.1
        else:
            right_min_depth = float("inf")
            right_contact = False

        # RGB images
        left_rgb = left_data.get("rgb_marker")
        if left_rgb is None:
            left_rgb = left_data.get("rgb")
        right_rgb = right_data.get("rgb_marker")
        if right_rgb is None:
            right_rgb = right_data.get("rgb")

        left_rgb_b64 = _rgb_tensor_to_base64(left_rgb) if left_rgb is not None else ""
        right_rgb_b64 = _rgb_tensor_to_base64(right_rgb) if right_rgb is not None else ""

        # Depth images
        left_depth_b64 = (
            _depth_tensor_to_base64(left_depth) if left_depth is not None else ""
        )
        right_depth_b64 = (
            _depth_tensor_to_base64(right_depth) if right_depth is not None else ""
        )

        # Marker displacement: marker tensor shape is [2, num_markers, 2]
        #   dim0: [initial, current] marker positions
        #   dim2: [x, y] UV coordinates
        # Displacement = mean absolute flow since reset (no-contact baseline),
        # in px.  The reset-time reference is captured in _sim_reset because
        # the sim-internal "initial" frame carries a constant per-finger
        # offset and cannot serve as the zero-contact baseline.
        left_marker = left_data.get("marker")
        right_marker = right_data.get("marker")

        def _ref(side: str):
            if _MARKER_REFERENCE is None:
                return None
            return _MARKER_REFERENCE.get(side)

        def _marker_stats(marker, ref):
            """Per-marker displacement stats relative to a reference frame.

            The last two values are the coherent shear direction (mean
            displacement vector) in the sensor's UV pixel frame — the
            direction signal the tactile API exposes as
            *_marker_shear_dx/dy.
            """
            base = ref if ref is not None else marker[0]
            delta = (marker[1] - base).float()          # [M, 2]
            mag = delta.pow(2).sum(-1).sqrt()           # [M] per-marker L2
            mean_vec = delta.mean(dim=0)                # coherent (shear) part
            mean_mag = float(mag.mean())
            coherence = (
                float(torch.norm(mean_vec) / mean_mag) if mean_mag > 1e-6 else 0.0
            )
            return (mean_mag, float(mag.max()), coherence,
                    float(mean_vec[0]), float(mean_vec[1]))

        left_displacement = 0.0
        right_displacement = 0.0
        left_coherence = 0.0
        right_coherence = 0.0
        left_shear_dx = 0.0
        left_shear_dy = 0.0
        right_shear_dx = 0.0
        right_shear_dy = 0.0
        if left_marker is not None:
            base = _ref("left")
            left_displacement = float(
                (left_marker[1] - (base if base is not None else left_marker[0]))
                .abs().mean()
            )
            (_, _, left_coherence,
             left_shear_dx, left_shear_dy) = _marker_stats(left_marker, base)
        if right_marker is not None:
            base = _ref("right")
            right_displacement = float(
                (right_marker[1] - (base if base is not None else right_marker[0]))
                .abs().mean()
            )
            (_, _, right_coherence,
             right_shear_dx, right_shear_dy) = _marker_stats(right_marker, base)

        # Fine relief of the gel's indentation map, where the task exposes one
        # (the ball task does).  Depth, marker displacement and every other
        # scalar is an average over the gel and follows how hard the pad is
        # pressed, so none of them separates a raised surface from a polished
        # one at a fixed squeeze; the residual left after a smooth contact
        # envelope is removed does.  Tasks without a height map keep exactly
        # the payload they had.
        height_roughness = getattr(_TASK, "height_roughness_mm", None)
        left_roughness = right_roughness = None
        if height_roughness is not None:
            try:
                values = [float(v) for v in height_roughness()]
                left_roughness, right_roughness = values[0], values[1]
            except Exception:
                logger.warning(
                    "Height-map roughness unavailable: %s", traceback.format_exc()
                )

        try:
            truth = getattr(_TASK, "choice", None)
            ld = left_min_depth if left_min_depth != float("inf") else -1.0
            rd = right_min_depth if right_min_depth != float("inf") else -1.0
            parts = [
                f"step={_TASK.step_count}", f"truth={truth}",
                f"L_disp={left_displacement:.3f}", f"R_disp={right_displacement:.3f}",
            ]
            for side, key, marker in (
                ("L", "left", left_marker), ("R", "right", right_marker)
            ):
                if marker is not None and _ref(key) is not None:
                    m, mx, coh, _, _ = _marker_stats(marker, _ref(key))
                    parts.append(
                        f"{side}_mean={m:.3f} {side}_max={mx:.3f} "
                        f"{side}_coh={coh:.3f}"
                    )
            if left_roughness is not None:
                parts.append(
                    f"L_rgh={left_roughness:.4f} R_rgh={right_roughness:.4f}"
                )
            with open(TACTILE_CALIBRATION_LOG, "a") as _f:
                _f.write(" ".join(parts) + f" L_depth={ld:.1f} R_depth={rd:.1f}\n")
        except Exception:
            pass

        result = {
            "left_contact": left_contact,
            "right_contact": right_contact,
            # Depth from GelSight sensor is already in mm (same unit as
            # cfg.robot.tactile_far_plane ≈ 34.0 mm)
            "left_depth_mm": round(left_min_depth, 3) if left_min_depth != float("inf") else 0.0,
            "right_depth_mm": round(right_min_depth, 3) if right_min_depth != float("inf") else 0.0,
            "left_marker_displacement_px": round(left_displacement, 4),
            "right_marker_displacement_px": round(right_displacement, 4),
            "left_marker_coherence": round(left_coherence, 4),
            "right_marker_coherence": round(right_coherence, 4),
            "left_marker_anisotropy": _rgb_tensor_anisotropy(left_rgb),
            "right_marker_anisotropy": _rgb_tensor_anisotropy(right_rgb),
            "left_marker_shear_dx": round(left_shear_dx, 4),
            "left_marker_shear_dy": round(left_shear_dy, 4),
            "right_marker_shear_dx": round(right_shear_dx, 4),
            "right_marker_shear_dy": round(right_shear_dy, 4),
            "left_rgb_base64": left_rgb_b64,
            "right_rgb_base64": right_rgb_b64,
            "left_depth_base64": left_depth_b64,
            "right_depth_base64": right_depth_b64,
        }
        if left_roughness is not None:
            result["left_height_roughness_mm"] = round(left_roughness, 4)
            result["right_height_roughness_mm"] = round(right_roughness, 4)
        return result
    except Exception as exc:
        logger.warning("Tactile data unavailable: %s", traceback.format_exc())
        action_log.log_query_failure("tactile", f"Tactile data unavailable: {exc}")
        return {
            "left_contact": False,
            "right_contact": False,
            "left_depth_mm": 0.0,
            "right_depth_mm": 0.0,
            "left_marker_displacement_px": 0.0,
            "right_marker_displacement_px": 0.0,
            "left_marker_coherence": 0.0,
            "right_marker_coherence": 0.0,
            "left_marker_anisotropy": None,
            "right_marker_anisotropy": None,
            "left_marker_shear_dx": 0.0,
            "left_marker_shear_dy": 0.0,
            "right_marker_shear_dx": 0.0,
            "right_marker_shear_dy": 0.0,
            "left_rgb_base64": "",
            "right_rgb_base64": "",
            "left_depth_base64": "",
            "right_depth_base64": "",
        }


# ---------------------------------------------------------------------------
# Reflexive motion (fast loop) — event model lives in tactile_reflex.py
# ---------------------------------------------------------------------------

_MISSING = object()


def _noop_arm(*args, **kwargs):
    """Instance-attribute stand-in for RobotManager.set_arm.

    Freezes the arm: refuses to write the commanded joint positions into
    the simulation.  ``set_arm`` calls ``set_dof_positions(force=True)``
    which teleports the arm kinematically (Isaac Lab writes straight into
    PhysX), so ``plan_success=False`` alone cannot stop a trajectory —
    the write must be neutralised here as well.
    """
    pass


class ReflexContext:
    """Per-attempt state of the reflexive-motion hook."""

    def __init__(self) -> None:
        self.triggered: bool = False
        self.reason: str | None = None
        self.step: int | None = None
        self.error: str | None = None
        self.saved_callback: Any = _MISSING
        self.arm_patched: bool = False


def _read_tactile_features(obs: dict) -> tuple[Any, Any]:
    """Extract left/right SideFeatures from an observation dict.

    Marker tensors are measured against the reset-time reference
    (_MARKER_REFERENCE) so displacement and shear are counted from the
    no-contact baseline; the sim-internal "initial" frame carries a
    constant per-finger offset and cannot serve as the baseline.
    """
    tactile = obs.get("tactile", {})
    far_plane = float(_TASK.cfg.robot.tactile_far_plane)
    ref = _MARKER_REFERENCE or {}
    left = tactile_reflex.extract_side_features(
        tactile.get("left_tactile", {}).get("marker"),
        ref.get("left"),
        tactile.get("left_tactile", {}).get("depth"),
        far_plane,
    )
    right = tactile_reflex.extract_side_features(
        tactile.get("right_tactile", {}).get("marker"),
        ref.get("right"),
        tactile.get("right_tactile", {}).get("depth"),
        far_plane,
    )
    return left, right


def _install_reflex(detector: Any, stop_on: dict | None, ctx: ReflexContext) -> None:
    """Install the per-step reflex callback on the task instance.

    ``_step`` calls ``self._step_callback(status_dict)`` at the end of
    every step (envs/_base_task.py), and instance attributes shadow class
    attributes, so assigning the closure here intercepts every step.  The
    previous value (the class staticmethod, or an instance attr installed
    earlier) is saved on the context and restored by _uninstall_reflex.
    """
    ctx.saved_callback = _TASK.__dict__.get("_step_callback", _MISSING)

    def _reflex_callback(status: dict) -> str:
        """One fast-loop evaluation; runs at the end of every _step."""
        if ctx.error:
            return "reflex"  # latched failure: no-op the rest of the motion
        try:
            # Collect mode + save_frequency=0: the sensor's data.output is
            # only refreshed by a render, so force one every step (~2 FPS).
            _TASK._update_render()
            obs = _TASK._get_observations()
            left, right = _read_tactile_features(obs)
            detector.update(left, right)
            triggered, reason = tactile_reflex.evaluate(stop_on, detector)
            if triggered and not ctx.triggered:
                ctx.triggered, ctx.reason, ctx.step = True, reason, _TASK.step_count
                _TASK.plan_success = False        # freeze physics + skip delay()
                _TASK._robot_manager.set_arm = _noop_arm
                ctx.arm_patched = True
        except Exception as exc:
            ctx.error = str(exc)
            logger.error("tactile_reflex callback error: %s", traceback.format_exc())
        return "reflex"

    _TASK._step_callback = _reflex_callback


def _uninstall_reflex(ctx: ReflexContext) -> None:
    """Restore the task's original _step_callback and set_arm binding."""
    if ctx.saved_callback is _MISSING:
        _TASK.__dict__.pop("_step_callback", None)
    else:
        _TASK.__dict__["_step_callback"] = ctx.saved_callback
    if ctx.arm_patched:
        # Deleting the instance attribute restores the class-method binding;
        # assigning the unbound function back would break ``self``.
        try:
            del _TASK._robot_manager.set_arm
        except AttributeError:
            pass


def _sim_get_observation() -> dict:
    """Get full observation including cameras and tactile."""
    global _TASK

    try:
        obs = _TASK._get_observations()

        # Cameras
        head_rgb = obs.get("observation", {}).get("head", {}).get("rgb")
        wrist_rgb = obs.get("observation", {}).get("wrist", {}).get("rgb")
        head_depth = obs.get("observation", {}).get("head", {}).get("depth")
        wrist_depth = obs.get("observation", {}).get("wrist", {}).get("depth")

        # Metric camera data for non-privileged perception (depth/intrinsics/
        # pose), read directly from the Isaac Lab camera data.
        head_cam = _camera_metric_data("head")
        wrist_cam = _camera_metric_data("wrist")

        # EE pose
        ee_pose = _TASK._robot_manager.get_ee_pose()
        gripper_qpos = float(_TASK._robot_manager.get_gripper_qpos())

        # Tactile
        tactile = _sim_get_tactile_data()

        return {
            "head_rgb_base64": _rgb_tensor_to_base64(head_rgb) if head_rgb is not None else "",
            "wrist_rgb_base64": _rgb_tensor_to_base64(wrist_rgb) if wrist_rgb is not None else "",
            "head_depth_base64": _depth_tensor_to_base64(head_depth) if head_depth is not None else "",
            "wrist_depth_base64": _depth_tensor_to_base64(wrist_depth) if wrist_depth is not None else "",
            "head_depth_float32_base64": _depth_tensor_to_float32_base64(head_cam["depth"]) if head_cam["depth"] is not None else "",
            "wrist_depth_float32_base64": _depth_tensor_to_float32_base64(wrist_cam["depth"]) if wrist_cam["depth"] is not None else "",
            "head_image_shape": head_cam["shape"],
            "wrist_image_shape": wrist_cam["shape"],
            "head_intrinsics": head_cam["intrinsics"],
            "wrist_intrinsics": wrist_cam["intrinsics"],
            "head_cam_pos_w": head_cam["pos_w"],
            "wrist_cam_pos_w": wrist_cam["pos_w"],
            "head_cam_quat_w_ros": head_cam["quat_w_ros"],
            "wrist_cam_quat_w_ros": wrist_cam["quat_w_ros"],
            "tactile": tactile,
            "ee_position": ee_pose.p.tolist(),
            "ee_quaternion": ee_pose.q.tolist(),
            "gripper_qpos": gripper_qpos,
        }
    except Exception as exc:
        logger.warning("Observation unavailable: %s", traceback.format_exc())
        action_log.log_query_failure("observation", f"Observation unavailable: {exc}")
        return {
            "head_rgb_base64": "",
            "wrist_rgb_base64": "",
            "head_depth_base64": "",
            "wrist_depth_base64": "",
            "head_depth_float32_base64": "",
            "wrist_depth_float32_base64": "",
            "head_image_shape": None,
            "wrist_image_shape": None,
            "head_intrinsics": None,
            "wrist_intrinsics": None,
            "head_cam_pos_w": None,
            "wrist_cam_pos_w": None,
            "head_cam_quat_w_ros": None,
            "wrist_cam_quat_w_ros": None,
            "tactile": None,
            "ee_position": [],
            "ee_quaternion": [],
            "gripper_qpos": 0.0,
        }


def _sim_check_success() -> dict:
    """Check if the current task is completed."""
    global _TASK
    try:
        completed = _TASK.check_success()
        early_stop = _TASK.check_early_stop()
        detail = None
        if hasattr(_TASK, "check_success_detail"):
            detail = _TASK.check_success_detail()
        return {"completed": bool(completed), "check_early_stop": bool(early_stop),
                "detail": detail}
    except Exception as exc:
        logger.warning("check_success failed: %s", traceback.format_exc())
        action_log.log_query_failure("check_success", f"check_success failed: {exc}")
        return {"completed": False, "check_early_stop": False, "detail": None}


def _jsonable(value):
    """Best-effort conversion of sim state into JSON-serializable values.

    ``_measure()`` mixes numpy arrays, numpy scalars, nested dicts and plain
    Python types.  This is a debug surface, so it must degrade to ``repr``
    rather than fail on whichever field happens to be exotic.
    """
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _sim_force_state() -> dict:
    """Return the force-task acceptance state.

    Tasks built on ViTaForge's force-task contract keep their physical
    progress in ``self.measured`` and their verdict in ``check_success()``.
    Returns ``available: False`` for tasks that are not force tasks instead of
    raising.
    """
    global _TASK
    if not hasattr(_TASK, "measured"):
        return {"available": False,
                "message": f"task '{_TASK_NAME}' exposes no force-task state"}
    try:
        measured = _TASK._measure() if hasattr(_TASK, "_measure") else _TASK.measured
        spec = getattr(_TASK, "thread_spec", None)
        return {
            "available": True,
            "phase": _jsonable(getattr(_TASK, "phase", None)),
            "failure": _jsonable(getattr(_TASK, "failure", None)),
            "held_steps": int(getattr(_TASK, "held_steps", 0)),
            "probe_physical_complete": bool(
                getattr(_TASK, "_probe_physical_complete", False)),
            "submission_requested": bool(
                getattr(_TASK, "_submission_requested", False)),
            "accepted_result": _jsonable(getattr(_TASK, "_accepted_result", None)),
            "monitor_active": bool(getattr(_TASK, "_monitor_bulb", False)
                                   or getattr(_TASK, "_monitor_strap", False)),
            "reset_grip_released": bool(
                getattr(_TASK, "_reset_grip_released", False)),
            # tension_strap: the task holds the strap's rest shape until both
            # pads have acquired the tab, and only then does the strap follow
            # the hand.  Without this the two states look identical from
            # outside -- the pull moves the arm and the tension stays at zero.
            "grip_released": _jsonable(getattr(_TASK, "_grip_released", None)),
            "plan_success": bool(getattr(_TASK, "plan_success", False)),
            "step_count": int(getattr(_TASK, "step_count", 0)),
            # Privileged: the hidden thread specification the task samples per
            # seed.  This is what a privileged oracle would read instead of
            # inferring it from the reversible probe.
            "thread_spec": None if spec is None else {
                "handedness": int(spec.handedness),
                "lead_m": float(spec.lead_m),
                "initial_depth_m": float(spec.initial_depth_m),
                "nominal_free_travel_m": float(spec.nominal_free_travel_m),
            },
            "params": _jsonable(dict(getattr(_TASK, "params", {}))),
            "measured": _jsonable(dict(measured)),
        }
    except Exception as exc:
        logger.warning("force_state failed: %s", traceback.format_exc())
        return {"available": False, "message": f"force_state failed: {exc}"}


def _sim_submit_episode() -> dict:
    """Finish a force-task episode and report the frozen verdict.

    ``_finish_bulb_episode`` freezes the verdict, lights the scene and walks
    out the terminal review; its in-tree callers are ``take_action`` (on a
    physical failure), the expert ``_play_once`` and the review scenarios.
    The server drives tasks through the atom API and calls none of them.

    Since 2026-10-09 the *verdict* no longer needs it: ``check_success()``
    reads the physical state whenever it is asked (the submission gate was
    removed with the torque band), so ``/check_success`` alone answers.
    This endpoint is kept for the terminal review and for freezing a state
    before the review motions disturb it.
    """
    global _TASK
    if not hasattr(_TASK, "_finish_bulb_episode"):
        return {"completed": False, "check_early_stop": False, "detail": None,
                "message": f"task '{_TASK_NAME}' has no force-task submission"}
    try:
        _TASK._finish_bulb_episode()
        return _sim_check_success()
    except Exception as exc:
        logger.warning("submit_episode failed: %s", traceback.format_exc())
        return {"completed": False, "check_early_stop": False, "detail": None,
                "message": f"submit_episode failed: {exc}"}


# Feed mode (lead-coupled helix).  One servo iteration is the env's own
# granularity -- 1.5 deg per iteration at efficient_wrist_motion
# (bulb_tightening.py:1979, 120 iterations per pi of segment_angle).  The pad
# guard sits below the task's own glass_break_force_N (135 N) because a
# fracture is terminal for the whole process: it must not be reached to be
# observed.
_FEED_MAX_STEPS = 800
# The other end of the same guard, and the one that costs more.  One iteration
# is one `_ik_to_center`, which refuses a target needing more than 0.08 rad of
# joint motion (bulb_tightening.py:1940) -- and that is not a failed call but a
# latched task failure, so a count too coarse for the angle ends the episode
# (2026-10-07: 30 deg asked for in 2 iterations, refused at 15.0 deg on the
# first one).  Raised rather than refused, at the 2.5 deg an iteration the
# empty-hand wind-back runs at; 1.5 deg is the working blocks' own
# granularity, and the ceiling itself is 4.6 deg.
_FEED_MIN_STEP_RAD = float(np.radians(2.5))
_FEED_SAMPLE_EVERY = 2
_FEED_PAD_ABORT_N = 128.0
# Command scale once the seat takes load, mirroring the profile's
# progressive_seating_speed (bulb_tightening.py:2006-2011).  Once the thread
# bottoms out the commanded feed has nowhere to go and turns into force on the
# glass, and the whole window from first seat contact to the 128 N pad guard is
# about 1.3 mm wide -- a fifth of one 30 deg block at the reference lead -- so
# the last stretch has to be spent in smaller steps.  (The 0.108..0.132 Nm
# torque band this used to protect went with the criteria on 2026-10-09; what
# it protects now is the seat limits, 0.15 Nm over-torque and the pad guard.)
_FEED_SEATING_SCALE = 0.2
# The helix commands the pose itself, so nothing else records a frame while it
# turns -- the Jacobian path (_sim_rotate_gripper) logs its own rotate:step
# frames, and this one logged none, which left the episode video showing the
# re-grasps and none of the insertion.  A coarser stride than
# _FEED_SAMPLE_EVERY on purpose: _capture_frame forces a full render that this
# loop otherwise never pays for, so the stride is the wall-clock dial.  Every
# 5th iteration is 7.5 deg of twist (1.5 deg per iteration at
# efficient_wrist_motion), which reads as turning at the video's 10 fps.  A
# hold is a constant pose and records nothing: the twist that seated it
# already put that state in the video.
_FEED_FRAME_EVERY = 5
_FEED_TRACE_KEYS = (
    "rotation_deg", "signed_world_rotation_deg", "formal_net_turns",
    "formal_advance_m", "advance_m", "phase_error_m",
    "torque_Nm", "signed_torque_Nm", "seat_force_N", "seat_compression_m",
    "bulb_z_m", "center_drift_m", "tilt_deg", "peak_pad_normal_force_N",
    "angular_speed_deg_s", "axial_speed_m_s",
)


def _sim_gripper_feed(object_name: str, total_angle_rad: float, steps: int,
                      lead_m: float) -> dict:
    """Twist while feeding along the thread's own lead, using the env's servo.

    Rotation and axial advance stay rigidly coupled at ``lead_m`` per full
    turn -- the relation ``bulb_tightening`` scores against
    (``expected_advance``, bulb_tightening.py:614) and the one a cuRobo
    trajectory cannot hold, because a plan is resolved before contact
    exists.  Each of the ``steps`` iterations is exactly one
    ``_ik_to_center`` call: one differential-IK step plus two physics ticks,
    recomputed from the live gripper-centre pose, so the helix follows the
    bulb rather than a precomputed path.

    Ends on the task's own terminal condition, reported through ``reached``:
    the part has bottomed -- it has taken the whole thread's travel and is
    pressed onto its seat.  Since 2026-10-09 that is the acceptance bar itself
    (``check_success`` scores the seat, not a torque), so ``reached`` is the
    finished job and the signal to stop turning.  It is checked a millimetre
    short of the scorer's own two-millimetre tolerance, so stopping on it
    settles the criterion with margin.  ``aborted`` is a physical overload or
    a lost grip, and a run that stops that way is ``success: False`` because
    the stop *is* the finding.

    Once the seat takes load the command is scaled by
    ``_FEED_SEATING_SCALE`` and sampled every iteration instead of every
    ``_FEED_SAMPLE_EVERY``: at the nominal cadence one sample is 0.064 mm of
    feed, against a whole 3 N -> 180 N window only ~1.3 mm wide.

    Physics is sampled every ``_FEED_SAMPLE_EVERY`` iterations and a frame is
    recorded every ``_FEED_FRAME_EVERY``, both plus the last iteration; the
    frame lands on the first sample of each pair, so the pose it shows is the
    one that sample measured.  The frames are worth their cost here for a
    reason the trace rows are not: ``_FEED_TRACE_KEYS`` holds no marker-shear
    key, so the gel's numbers can only be read between primitives -- but the
    composite's tactile panels are the marker image itself, and during a
    twist these frames are the only record of it.

    Args:
        object_name: The name of the object being held (reporting only).
        total_angle_rad: Signed total twist, wound the world-z axis.
        steps: Servo iterations.  One iteration is one IK step carrying
            1 / steps of the twist; 120 of them cover 180 deg at the env's
            granularity.  A careless count is clamped rather than refused at
            both ends: an oversized one is cut to ``_FEED_MAX_STEPS`` so it
            cannot pin the main thread, and an over-coarse one is raised to
            at least one iteration per ``_FEED_MIN_STEP_RAD``, because a
            single IK step that asks a joint for more than 0.08 rad is
            refused, and that refusal latches the task for the rest of the
            episode.
        lead_m: Axial advance per full turn, in metres.  The thread's own
            lead: the bulb advances this far per 2*pi of twist.
    """
    global _TASK
    step_count = int(getattr(_TASK, "step_count", 0))

    if not hasattr(_TASK, "_ik_to_center"):
        return {"success": False,
                "plan_success": bool(getattr(_TASK, "plan_success", False)),
                "message": f"task '{_TASK_NAME}' has no _ik_to_center servo",
                "step": step_count, "tactile": _sim_get_tactile_data()}

    import transforms3d as t3d

    from envs.utils.transforms import Pose

    magnitude = abs(float(total_angle_rad))
    spin = 1.0 if float(total_angle_rad) >= 0.0 else -1.0
    # Both clamps guard against a careless count and they fail in opposite
    # directions: too many iterations pins the main thread, too few latches
    # the task.  A hold asks for a duration rather than an angle and derives
    # nothing from one -- magnitude 0 leaves its count exactly as asked.
    steps = max(1, min(int(steps), _FEED_MAX_STEPS))
    steps = max(steps, int(np.ceil(magnitude / _FEED_MIN_STEP_RAD)))
    # A zero twist is a constant-pose hold, not an approach.  Nothing waits on
    # a hold any more (the submission gesture went with the criteria on
    # 2026-10-09): it just steps physics for the iterations it was asked for.
    # It must not report ``reached``, though -- an already-seated part is
    # exactly the state a hold starts from, and a hold declares nothing.
    holding = magnitude <= 1e-9
    start = _TASK._robot_manager.get_gripper_center_pose()
    params = getattr(_TASK, "params", None) or {}
    target_torque = float(getattr(_TASK, "target_torque", 0.12))
    over_torque = float(params.get("over_torque_Nm", 0.15))
    # The thread's own travel, from the task's spec: the depth a bottomed part
    # has taken.  ``inf`` when the task has no spec, which no advance reaches,
    # so a spec-less task simply never reports ``reached``.
    travel = float(getattr(getattr(_TASK, "thread_spec", None),
                           "nominal_free_travel_m", np.inf))

    trace: list[dict[str, Any]] = []
    aborted: str | None = None
    reached: str | None = None
    ended: str | None = None
    completed = 0
    step_angle = magnitude / steps
    angle = 0.0
    scale = 1.0
    seating = False
    for index in range(steps):
        angle += step_angle * scale
        # Wound about world z from the pose the segment started at, descended
        # by the matched feed -- the same curve _screw_segment builds
        # (bulb_tightening.py:1984-1988), minus its image-shear servo, which
        # needs the expert's tactile reference frames.
        quat = t3d.quaternions.qmult(
            t3d.quaternions.axangle2quat([0.0, 0.0, 1.0], spin * angle), start.q)
        center = Pose(
            start.p - np.array([0.0, 0.0, lead_m * angle / (2.0 * np.pi)]), quat)
        steps_before = int(getattr(_TASK, "step_count", 0))
        if not _TASK._ik_to_center(center):
            aborted = (f"IK servo refused at {np.rad2deg(angle):.1f} deg "
                       f"(task failure: {getattr(_TASK, 'failure', None)})")
            break
        completed = index + 1
        if int(getattr(_TASK, "step_count", 0)) == steps_before:
            # A frozen step_count means the command was never stepped.  There
            # are two causes and only one of them is a fault:
            #
            #  * The episode already ended.  take_action returns *before*
            #    super().take_action once _accepted_result is set
            #    (bulb_tightening.py:1724), which since 2026-10-09 happens on a
            #    physical failure, and take_force_task_action does the same once
            #    eval_success is set or the step budget is spent
            #    (_force_task_utils.py:834).
            #  * Nothing ended and nothing moved: the silent spent-budget
            #    no-op, the same shape as move_delta's MIN_TRANS.
            #
            # step_count, not take_action_cnt: _ik_to_center reaches
            # take_action only on its PHASE_POLICY branch (:1944) and drives
            # _step directly on the other, so the action counter is blind
            # whenever the task sits outside that phase -- while _step advances
            # step_count on both branches.
            if (getattr(_TASK, "_accepted_result", None) is not None
                    or bool(getattr(_TASK, "eval_success", False))):
                ended = (f"episode ended at iteration {index} of {steps} "
                         f"(accepted={getattr(_TASK, '_accepted_result', None)})")
                break
            aborted = (f"servo is a no-op: step_count stayed at {steps_before} "
                       f"(take_action_cnt "
                       f"{getattr(_TASK, 'take_action_cnt', None)}, step_lim "
                       f"{getattr(_TASK.cfg, 'step_lim', None)})")
            break
        if getattr(_TASK, "failure", None):
            aborted = f"task failure: {_TASK.failure}"
            break
        if (not holding and (index % _FEED_FRAME_EVERY == 0
                             or index == steps - 1)):
            # Sampled after the IK step, so the frame at steps - 1 is the
            # fully commanded block angle -- the pose the trace row recorded
            # just below was measured at.  Skipped on the breaks above, and
            # in a hold: a frame costs a forced render, and a constant pose
            # is not worth one.
            _record_frame("rotate:feed")
        if seating or index % _FEED_SAMPLE_EVERY == 0 or index == steps - 1:
            row = _TASK._measure()
            seat_n = float(row.get("seat_force_N", 0.0))
            torque_nm = float(row.get("torque_Nm", 0.0))
            pad = max((row.get("pad_normal_force_N") or {}).values(), default=0.0)
            trace.append({
                "step": int(_TASK.step_count),
                "angle_deg": float(np.rad2deg(angle)),
                "pad_max_N": float(pad),
                **_jsonable({k: row[k] for k in _FEED_TRACE_KEYS if k in row}),
            })
            if not seating and (torque_nm > target_torque / 3.0 or seat_n > 2.0):
                seating = True
                scale = _FEED_SEATING_SCALE
            # Bottomed: the part has taken the whole thread's travel and is
            # loaded on its seat.  The travel comes from the task's own thread
            # spec, so this is the same quantity ``check_success`` scores --
            # read a millimetre early so that stopping here settles the
            # scorer's two-millimetre tolerance with margin.  A loosening run
            # cannot get here: it moves the part the other way, so the advance
            # stays below the initial one.  Measured on seed 1: the advance
            # passes the travel a millimetre before the seat is even touched
            # (0.02 N at travel - 1 mm), and the seat itself only loads at
            # travel + 0.3 mm, so the seat term is what dates the event.
            if (not holding and seat_n > 3.0
                    and float(row.get("formal_advance_m", 0.0)) >= travel - 0.001):
                # No numbers in the message on purpose: the thread's travel is
                # a hidden task parameter, like the lead, and ``reached`` is
                # not the place to hand it over.
                reached = (f"the bulb has bottomed and is loaded on its seat "
                           f"({seat_n:.2f} N)")
                break
            if pad >= _FEED_PAD_ABORT_N:
                aborted = (f"pad force {pad:.1f} N reached the "
                           f"{_FEED_PAD_ABORT_N:.0f} N guard")
                break
            if torque_nm > over_torque:
                aborted = (f"torque {torque_nm:.3f} Nm over the "
                           f"{over_torque:.3f} Nm over-torque limit")
                break
            if seat_n > 180.0:
                aborted = (f"seat force {seat_n:.1f} N over "
                           f"the 180 N axial limit")
                break
            if (float(row.get("center_drift_m", 0.0)) > 0.004
                    or float(row.get("tilt_deg", 0.0)) > 10.0):
                aborted = "grasp or alignment lost"
                break

    return {
        "success": aborted is None,
        "plan_success": bool(getattr(_TASK, "plan_success", False)),
        "message": (f"Helix seated after {completed}/{steps} iterations: {reached}"
                    if reached else
                    f"Helix stopped after {completed}/{steps} iterations: {ended}"
                    if ended else
                    "OK" if aborted is None else
                    f"Helix stopped after {completed}/{steps} iterations: {aborted}"),
        "step": int(getattr(_TASK, "step_count", step_count)),
        "tactile": _sim_get_tactile_data(),
        "feed_lead_m": float(lead_m),
        "requested": int(steps),
        "completed": int(completed),
        "grip_released": bool(getattr(_TASK, "_reset_grip_released", False)),
        "aborted": aborted,
        "reached": reached,
        "trace": trace,
    }


def _sim_rotate_gripper(object_name: str, total_angle_rad: float,
                       steps: int = 4, feed_lead_m: float | None = None) -> dict:
    """Rotate the gripper holding an object, mimicking expert gripper_rotate.

    When ``feed_lead_m`` is given the call takes the lead-coupled helix path
    (``_sim_gripper_feed``) instead, and ``steps`` counts servo iterations
    rather than planner sub-moves.

    Captures the object's orientation before and after the rotation sequence
    and verifies that at least 30 % of the requested angular change was
    realised.  When the object is not firmly gripped the expert
    ``gripper_rotate`` may complete without errors yet fail to rotate the
    object — the orientation check catches this.
    """
    global _TASK
    if feed_lead_m is not None:
        return _sim_gripper_feed(object_name, total_angle_rad, steps, feed_lead_m)

    actor = _find_actor(object_name)
    if actor is None:
        return {"success": False, "plan_success": False,
                "message": f"Object '{object_name}' not found",
                "tactile": _sim_get_tactile_data()}

    # --- capture pre-rotation orientation ------------------------------------
    quat_before = np.asarray(actor.get_pose().q, dtype=np.float64)

    # --- execute rotation -----------------------------------------------------
    angle_per_step = total_angle_rad / steps
    for _ in range(steps):
        _TASK.gripper_rotate(actor, angle_per_step, steps=1, is_save=False)
        _record_frame("rotate:step")

    # --- verify object actually rotated ---------------------------------------
    quat_after = np.asarray(actor.get_pose().q, dtype=np.float64)
    # Angular difference between two quaternions:
    #   θ = 2 * acos(|q1·q2|)  (clamped to [-1, 1] for float drift)
    dot = float(np.clip(np.abs(np.dot(quat_before, quat_after)), -1.0, 1.0))
    actual_angle = 2.0 * np.arccos(dot)

    # Allow ~10° tolerance for noise / settling, but flag if < 30 % of
    # requested angle was achieved (object barely moved = not gripped).
    min_expected = max(abs(total_angle_rad) * 0.3, 0.05)  # at least 3°
    rotated = actual_angle >= min_expected

    success = bool(_TASK.plan_success) and rotated
    if not success:
        _TASK.plan_success = True  # unblock subsequent actions
        if not rotated:
            return {
                "success": False,
                "plan_success": _TASK.plan_success,
                "message": (
                    f"Object '{object_name}' rotated only {np.rad2deg(actual_angle):.1f}° "
                    f"(requested {np.rad2deg(total_angle_rad):.1f}°).  "
                    f"The object may not be firmly gripped — try closing the "
                    f"gripper again with a higher force_threshold, or re-grasp."
                ),
                "step": _TASK.step_count,
                "tactile": _sim_get_tactile_data(),
            }
        return {"success": False,
                "plan_success": _TASK.plan_success,
                "message": "Rotation failed",
                "step": _TASK.step_count,
                "tactile": _sim_get_tactile_data()}

    return {"success": True,
            "plan_success": _TASK.plan_success,
            "message": (
                f"Rotated '{object_name}' by {np.rad2deg(actual_angle):.1f}° "
                f"(requested {np.rad2deg(total_angle_rad):.1f}°)"
            ),
            "step": _TASK.step_count,
            "tactile": _sim_get_tactile_data()}


def _sim_get_grasp_pose(object_name: str, allow_ground_truth: bool = False) -> dict:
    """Return a grasp pose (position + quaternion WXYZ) for an object on the table.

    ``allow_ground_truth`` marks a privileged caller (one whose object poses
    already come from the simulator).  When it is set and the task declares a
    rule in ``_GT_GRASP_RULES`` for this object, the pose is built from the
    object's own pose plus the task's grasp rule and the EE-relative cache below
    is bypassed — the only case where the compromise in the NOTE applies.

    On the FIRST call for an object — while the EE still sits at the valid
    pre-grasp pose left by ``pre_move`` — we cache the object-relative offset
    ``ee_pose - object_pose`` together with the grasp quaternion.  Subsequent
    calls (a re-grasp after the object slipped out) return ``object_pose +
    offset`` so the pose tracks the object wherever it now lies, instead of
    returning the EE's current (possibly far-from-object) pose.

    NOTE: the cache is keyed by object name but seeded by the EE pose at the
    first call — only the object that ``pre_move`` anchored (EE directly
    above it) is reliable.  For any other object the cached offset is
    ``EE_now - object`` and returns a pose at the EE's current location
    (e.g. above a previously filled slot), which is why the API docs tell
    agents to construct such grasps from a measured offset instead.

    Returns ``{"position": None, ...}`` when the object cannot be found.
    """
    global _TASK

    # Verify the object exists in the scene
    obj_data = _sim_get_object_pose(object_name, return_bbox_extent=False)
    if not obj_data["found"]:
        return {"position": None, "quaternion_wxyz": None}

    object_pos = np.asarray(obj_data["position"], dtype=np.float64)

    if allow_ground_truth:
        # The task's own module — the one its class was defined in, so the
        # constant read here cannot come from a different fork of the env.
        task_module = sys.modules.get(type(_TASK).__module__)
        local_z_name = _GT_GRASP_RULES.get(_TASK_NAME, {}).get(object_name)
        local_z = (
            getattr(task_module, local_z_name, None)
            if task_module is not None and local_z_name else None
        )
        if local_z_name is not None and local_z is None:
            # A rule that names a constant the task does not have would
            # otherwise degrade silently to the EE-relative pose.
            logger.warning(
                "Ground-truth grasp rule for '%s' names '%s', which %s does "
                "not define — falling back to the EE-relative pose",
                object_name, local_z_name, type(_TASK).__module__,
            )
        if local_z is not None:
            from envs.utils.transforms import Pose
            # Grasp centre straight above the object origin, wrist orientation
            # kept as it stands — the task's own approach does exactly this and
            # warns against a constructed top-down quaternion, which puts the
            # GelSight wrist near a singularity and makes the planner refuse.
            # Rebuilt from the current object pose on every call, so it tracks
            # the object after a re-grasp.
            centre = Pose(
                object_pos + np.array([0.0, 0.0, float(local_z)]),
                _TASK._robot_manager.get_gripper_center_pose().q,
            )
            ee_pose = _TASK._robot_manager.gripper_center_to_ee(centre)
            logger.info(
                "Grasp pose for '%s': ground truth %s + local z %.4f -> grasp %s",
                object_name, object_pos, float(local_z), np.asarray(ee_pose.p),
            )
            return {
                "position": np.asarray(ee_pose.p, dtype=np.float64).tolist(),
                "quaternion_wxyz": np.asarray(ee_pose.q, dtype=np.float64).tolist(),
            }

    if object_name in _GRASP_OFFSET:
        offset, grasp_quat_wxyz = _GRASP_OFFSET[object_name]
        grasp_pos = object_pos + offset
        logger.info(
            "Grasp pose for '%s': cached offset %s, object at %s -> grasp %s",
            object_name, offset, object_pos, grasp_pos,
        )
    else:
        ee_pose = _TASK._robot_manager.get_ee_pose()
        grasp_pos = np.asarray(ee_pose.p, dtype=np.float64)
        grasp_quat_wxyz = np.asarray(ee_pose.q, dtype=np.float64)
        offset = grasp_pos - object_pos
        _GRASP_OFFSET[object_name] = (offset, grasp_quat_wxyz)
        logger.info(
            "Grasp pose for '%s': caching offset %s (post pre_move), "
            "object at %s -> grasp %s",
            object_name, offset, object_pos, grasp_pos,
        )

    return {
        "position": grasp_pos.tolist(),
        "quaternion_wxyz": grasp_quat_wxyz.tolist(),
    }


# Workspace centre – used to disambiguate which actor the user wants
# when several share a name prefix (e.g. can_d4 / can_d5 / can_d6).
_WORKSPACE_CENTER = np.array([0.7, 0.0, 0.05])


def _find_actor(object_name: str):
    """Return the actor whose name starts with *object_name*.

    When multiple actors match (e.g. ``can_d4``, ``can_d5``, ``can_d6``
    for ``"can"``), the one closest to the workspace centre is returned —
    this is the one that was moved into the robot's reach during reset.
    """
    global _TASK
    actors = _TASK._actor_manager.actors
    actor = actors.get(object_name)
    if actor is not None:
        return actor
    # Collect candidates
    candidates = []
    for name, a in actors.items():
        if name.startswith(object_name) or object_name in name:
            candidates.append(a)
    if not candidates:
        # Task-defined role fallback (e.g. tactile_memory_match): its actors
        # are per-class variants ("candidate_variant_0"..) chosen randomly
        # each episode, so a role query like "candidate_left" can never match
        # an actor name.  Tasks expose role -> actor via _resolve_public_
        # object_name + public_actor_map; use them when plain name matching
        # fails so role names stay addressable.
        return _find_public_role_actor(object_name)
    if len(candidates) == 1:
        return candidates[0]
    # Pick the actor closest to the workspace centre
    best = min(candidates, key=lambda a: np.linalg.norm(a.get_pose().p - _WORKSPACE_CENTER))
    return best


def _find_public_role_actor(object_name: str):
    """Resolve a task-defined role name to its physical actor, if any.

    Tasks that re-role per-class actors (random variant each episode)
    expose ``_resolve_public_object_name`` (alias folding) and a
    ``public_actor_map``; a query that matched no actor name falls back to
    those maps so roles like "reference_object" / "candidate_left" work
    exactly like actor names.  Returns None when the task has no such map.
    """
    global _TASK
    resolve = getattr(_TASK, "_resolve_public_object_name", None)
    if resolve is None:
        return None
    key = resolve(object_name)
    if key is None:
        return None
    public_actors = getattr(_TASK, "public_actor_map", {}) or {}
    return public_actors.get(key)


def _sim_get_object_pose(object_name: str, return_bbox_extent: bool = False) -> dict:
    """Get the pose of an actor (object) in the simulator.

    Supports prefix matching: ``"can"`` matches ``"can_d4"``, ``"can_d5"``, etc.
    When multiple matches exist the one nearest the workspace is returned.
    """
    global _TASK
    try:
        actor = _find_actor(object_name)
        if actor is None:
            return {"found": False, "position": None, "quaternion_wxyz": None, "bbox_extent": None}
        pose = actor.get_pose()
        result = {
            "found": True,
            "position": pose.p.tolist(),
            "quaternion_wxyz": pose.q.tolist(),
            "bbox_extent": None,
        }
        if return_bbox_extent:
            bbox = _try_get_bbox_extent(actor)
            result["bbox_extent"] = bbox
        return result
    except Exception as exc:
        logger.warning("get_object_pose('%s') failed: %s",
                       object_name, traceback.format_exc())
        action_log.log_query_failure("object_pose", f"get_object_pose('{object_name}') failed: {exc}",
                                     args={"object_name": object_name,
                                           "return_bbox_extent": return_bbox_extent})
        return {"found": False, "position": None, "quaternion_wxyz": None, "bbox_extent": None}


def _try_get_bbox_extent(actor) -> list[float] | None:
    """Try to compute the 3D bounding-box extent of an actor.

    Uses the Isaac Sim / USD API to query the world-space bounding box of the
    actor's underlying prim.  Returns ``[width, depth, height]`` in metres or
    ``None`` when the bounding box cannot be determined.

    Supports multiple actor backends:
    - UniVTAC ``UipcObject`` / ``Actor``  (has ``_prim_view``, ``cfg.prim_path``)
    - Isaac-Sim ``RigidObject``           (has ``prim``, ``prim_path``)
    - Generic prim wrappers               (has ``_prim``, ``root_prim``)
    """
    import re

    # --- strategy 1: _prim_view (UniVTAC UipcObject / Actor) ------------------
    prim_view = getattr(actor, "_prim_view", None)
    if prim_view is not None:
        try:
            prims = getattr(prim_view, "prims", None)
            if prims is not None and len(prims) > 0:
                # usdrt prim → get path → pxr lookup for BBoxCache compatibility
                prim_path = str(prims[0].GetPath())
                extent = _extent_from_prim_path(prim_path)
                if extent is not None:
                    return extent
        except Exception:
            pass

    # --- strategy 2: cfg.prim_path (UniVTAC ActorCfg) --------------------------
    cfg = getattr(actor, "cfg", None)
    if cfg is not None:
        prim_path_expr = getattr(cfg, "prim_path", None)
        if prim_path_expr is not None:
            # Resolve wildcard env pattern: ".../env_.*/..." → ".../env_0/..."
            resolved = re.sub(r"\.\*", "env_0", str(prim_path_expr))
            extent = _extent_from_prim_path(resolved)
            if extent is not None:
                return extent

    # --- strategy 3: cfg.extents (direct size hint, last resort) --------------
    if cfg is not None:
        extents = getattr(cfg, "extents", None)
        if extents is not None:
            try:
                vals = [float(x) for x in extents]
                if all(v > 0 for v in vals) and len(vals) == 3:
                    return vals
            except (TypeError, ValueError):
                pass

    # --- strategy 4: actor wraps a USD prim directly ---------------------------
    for attr_name in ("prim", "_prim", "root_prim"):
        prim = getattr(actor, attr_name, None)
        if prim is None:
            continue
        extent = _extent_from_prim(prim)
        if extent is not None:
            return extent

    # --- strategy 5: actor exposes a prim path string --------------------------
    for attr_name in ("prim_path", "_prim_path"):
        path_val = getattr(actor, attr_name, None)
        if path_val is None:
            continue
        paths = path_val if isinstance(path_val, (list, tuple)) else [path_val]
        for path_str in paths:
            extent = _extent_from_prim_path(str(path_str))
            if extent is not None:
                return extent

    return None


def _extent_from_prim(prim) -> list[float] | None:
    """Compute world-space extent from a USD prim object."""
    try:
        from pxr import Usd, UsdGeom
        bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                                        ['default', 'render', 'proxy'])
        bound = bbox_cache.ComputeWorldBound(prim)
        extent = bound.GetRange().GetSize()
        return [float(extent[i]) for i in range(3)]
    except Exception:
        return None


def _extent_from_prim_path(prim_path: str) -> list[float] | None:
    """Compute world-space extent from a prim path string."""
    try:
        import omni.usd
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            return None
        return _extent_from_prim(prim)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------


@app.post("/reset", response_model=ResetResponse)
def reset(req: ResetRequest):
    """Reset the task environment with a random seed."""
    if _TASK is None:
        action_log.log_action("reset", {"seed": req.seed}, ok=False,
                              msg="Task not initialized")
        raise HTTPException(status_code=503, detail="Task not initialized")
    # 600 s, not 300: a clean bulb_tightening reset measures ~251 s and the
    # client waits the same 300 s, so the two timers used to race on every
    # trial.  Which one wins decides the failure mode -- the client's read
    # timeout is retried by runner.py:338, but this endpoint's own 500 is an
    # HTTPError, which is not a Timeout, so it re-raises out of
    # _run_trial_with_retries and takes the whole batch with it.  A wound-up
    # arm from a partial screw-in is exactly the state that overruns, and it
    # is this task's normal failure mode.
    result = _run_action("reset", {"seed": req.seed}, 600.0, _sim_reset,
                         req.seed, detail_prefix="Reset failed")
    return ResetResponse(**result)


@app.get("/ee_pose", response_model=PoseResponse)
def get_ee_pose():
    """Get current end-effector pose."""
    if _TASK is None:
        action_log.log_query_failure("ee_pose", "Task not initialized")
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(_sim_get_ee_pose, timeout=30.0)
        return PoseResponse(**result)
    except TimeoutError:
        action_log.log_query_failure("ee_pose", "Simulation busy — retry in a few seconds")
        raise HTTPException(status_code=503, detail="Simulation busy — retry in a few seconds")


@app.get("/gripper_state")
def get_gripper_state():
    """Get current gripper state."""
    if _TASK is None:
        action_log.log_query_failure("gripper_state", "Task not initialized")
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        return _run_on_main(_sim_get_gripper_state, timeout=30.0)
    except TimeoutError:
        action_log.log_query_failure("gripper_state", "Simulation busy — retry in a few seconds")
        raise HTTPException(status_code=503, detail="Simulation busy — retry in a few seconds")


@app.post("/move", response_model=ActionResponse)
def move(req: MoveRequest):
    """Move end-effector to target pose, optionally with gripper action."""
    if _TASK is None:
        action_log.log_action("move", req.model_dump(), ok=False,
                              msg="Task not initialized")
        raise HTTPException(status_code=503, detail="Task not initialized")
    result = _run_action(
        "move", req.model_dump(), _MOTION_TIMEOUT,
        _sim_move, req.position, req.quaternion_wxyz, req.z_approach,
        req.gripper_action, req.gripper_pos, req.tag,
        detail_prefix="Move failed",
    )
    return ActionResponse(**result)


@app.post("/move_delta", response_model=ActionResponse)
def move_delta(req: MoveDeltaRequest):
    """Move end-effector by a small delta (for fine-grained contact adjustments)."""
    if _TASK is None:
        action_log.log_action("move_delta", req.model_dump(), ok=False,
                              msg="Task not initialized")
        raise HTTPException(status_code=503, detail="Task not initialized")
    result = _run_action(
        "move_delta", req.model_dump(), _MOTION_TIMEOUT,
        _sim_move_delta, req.dx, req.dy, req.dz, req.drx, req.dry, req.drz,
        req.coord, req.tag,
        detail_prefix="Move delta failed",
    )
    return ActionResponse(**result)


@app.post("/move_reflexive", response_model=MoveReflexiveResponse)
def move_reflexive(req: MoveReflexiveRequest):
    """Move by a small delta, stopping early on the first tactile event.

    Fast loop: while the motion runs, a per-step callback evaluates the
    tactile events (contact / hard_depth / shear_spike / slip / plateau /
    asymmetry — see tactile_reflex.py); the first enabled one freezes the
    arm and the response carries the event digest.  ``stop_on=None`` runs
    the motion to completion and reports the digest only.
    """
    if _TASK is None:
        action_log.log_action("move_reflexive", req.model_dump(), ok=False,
                              msg="Task not initialized")
        raise HTTPException(status_code=503, detail="Task not initialized")
    result = _run_action(
        "move_reflexive", req.model_dump(), _MOTION_TIMEOUT,
        _sim_move_reflexive, req.dx, req.dy, req.dz, req.drx, req.dry, req.drz,
        req.coord, req.tag,
        req.stop_on.model_dump() if req.stop_on else None,
        detail_prefix="Move reflexive failed",
    )
    return MoveReflexiveResponse(**result)


@app.post("/gripper", response_model=ActionResponse)
def gripper(req: GripperRequest):
    """Open or close the gripper."""
    if _TASK is None:
        action_log.log_action("gripper", req.model_dump(), ok=False,
                              msg="Task not initialized")
        raise HTTPException(status_code=503, detail="Task not initialized")
    result = _run_action(
        "gripper", req.model_dump(), _MOTION_TIMEOUT,
        _sim_gripper, req.action, req.force_threshold, req.use_adaptive_grasp,
        req.position,
        detail_prefix="Gripper action failed",
    )
    return ActionResponse(**result)


@app.get("/tactile", response_model=TactileResponse)
def get_tactile():
    """Get current tactile sensor readings from both fingertips."""
    if _TASK is None:
        action_log.log_query_failure("tactile", "Task not initialized")
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(_sim_get_tactile_data, timeout=30.0)
        return TactileResponse(**result)
    except TimeoutError:
        action_log.log_query_failure("tactile", "Simulation busy — retry in a few seconds")
        raise HTTPException(status_code=503, detail="Simulation busy — retry in a few seconds")


@app.get("/observation", response_model=ObservationResponse)
def get_observation():
    """Get full observation: cameras, tactile, and robot state."""
    if _TASK is None:
        action_log.log_query_failure("observation", "Task not initialized")
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(_sim_get_observation, timeout=30.0)
        return ObservationResponse(**result)
    except TimeoutError:
        action_log.log_query_failure("observation", "Simulation busy — retry in a few seconds")
        raise HTTPException(status_code=503, detail="Simulation busy — retry in a few seconds")


@app.get("/check_success", response_model=SuccessResponse)
def check_success():
    """Check if the current task is successfully completed."""
    if _TASK is None:
        action_log.log_query_failure("check_success", "Task not initialized")
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(_sim_check_success, timeout=30.0)
        return SuccessResponse(**result)
    except TimeoutError:
        # Main thread is busy (e.g. long-running motion).  Report as
        # "not yet complete" rather than crashing — the LLM can retry.
        action_log.log_query_failure(
            "check_success",
            "Simulation busy — returned completed=False without checking",
        )
        return SuccessResponse(completed=False, check_early_stop=False)


# The two endpoints below are the force-task port's contract, kept deliberately
# (decided 2026-09-26 after the feasibility spike).  ViTaForge's force tasks keep
# their physical progress in ``self.measured`` and their verdict in
# ``check_success()``, so neither ``/check_success`` nor any motion endpoint can
# show that progress without them.  They are additive and touch no existing path.
# (Until 2026-10-09 a submission gate kept ``/check_success`` answered False
# until ``/submit_episode`` was called; the gate went with the torque band, so
# ``/check_success`` now answers from the physical state alone.)
@app.get("/force_state")
def force_state():
    """Force-task acceptance state (see _sim_force_state)."""
    if _TASK is None:
        action_log.log_query_failure("force_state", "Task not initialized")
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        return _run_on_main(_sim_force_state, timeout=30.0)
    except TimeoutError:
        action_log.log_query_failure("force_state",
                                     "Simulation busy — retry in a few seconds")
        raise HTTPException(status_code=503,
                            detail="Simulation busy — retry in a few seconds")


@app.post("/submit_episode")
def submit_episode():
    """Submit a force-task episode for scoring (see _sim_submit_episode)."""
    if _TASK is None:
        action_log.log_action("submit_episode", {}, ok=False,
                              msg="Task not initialized")
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        # 550 s, not 60 s: _finish_bulb_episode ends in a terminal review of 120
        # steps when rejected, 180 when fractured, and **300 steps plus 150 IK
        # solves on the accepted branch** (bulb_tightening.py:1781/1786/1793 vs
        # :1795) — the longest of the three, and the one that decides the port.
        # A measured rejected review took 38.8 s, so 60 s covers only the
        # shortest case and would strand a success.  Matches this file's own
        # _MOTION_TIMEOUT convention and its client read timeout
        # (univtac_api._READ_TIMEOUT, 1800 s).
        # NOTE: a 503 here is recoverable anyway — _finish_bulb_episode sets
        # _accepted_result before the review and returns early on re-entry, so a
        # retry after the review lands returns the verdict immediately.  That
        # early return is read from source, not measured: the one submission run
        # so far answered 200 on its first attempt and never exercised the retry.
        return _run_on_main(_sim_submit_episode, timeout=_MOTION_TIMEOUT)
    except TimeoutError:
        action_log.log_action("submit_episode", {}, ok=False,
                              msg="Simulation busy — retry in a few seconds")
        raise HTTPException(status_code=503,
                            detail="Simulation busy — retry in a few seconds")


# NOTE: operator/internal-only — ground-truth prim pose.  The agent line
# (univtac_skill/robo.py) never calls this endpoint: its get_object_pose
# goes through UniVTACPerceptionApi (SAM3 segmentation + depth
# back-projection, non-privileged by design).  Kept for: (1) the internal
# _sim_get_grasp_pose call, (2) privileged eval configs, (3) operator
# debugging.  Do not wire it back into any agent-facing client.
@app.get("/object_pose", response_model=ObjectPoseResponse)
def get_object_pose(object_name: str, return_bbox_extent: bool = False):
    """Get the current pose of a named object in the simulator (operator-only)."""
    _object_pose_args = {"object_name": object_name,
                         "return_bbox_extent": return_bbox_extent}
    if _TASK is None:
        action_log.log_query_failure("object_pose", "Task not initialized",
                                     args=_object_pose_args)
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(_sim_get_object_pose, object_name, return_bbox_extent, timeout=30.0)
    except TimeoutError:
        action_log.log_query_failure("object_pose", "Simulation busy — retry in a few seconds",
                                     args=_object_pose_args)
        raise HTTPException(status_code=503, detail="Simulation busy — retry in a few seconds")
    return ObjectPoseResponse(**result)


@app.get("/health")
def health():
    """Health check."""
    return {
        "status": "ok",
        "task": _TASK_NAME,
        "step": _TASK.step_count if _TASK else 0,
        "plan_success": _TASK.plan_success if _TASK else True,
    }


@app.get("/grasp_pose", response_model=PoseResponse)
def get_grasp_pose(object_name: str, allow_ground_truth: bool = False):
    """Return a kinematically-feasible grasp pose for a named object.

    ``allow_ground_truth`` is set by privileged clients only (the ones whose
    get_object_pose already reads /object_pose); it lets the server build the
    pose from ground truth for tasks whose pre_move does not anchor the EE over
    the object.  Defaults to false, so the non-privileged perception line keeps
    exactly the pose it got before.
    """
    _grasp_pose_args = {"object_name": object_name,
                        "allow_ground_truth": allow_ground_truth}
    if _TASK is None:
        action_log.log_query_failure("grasp_pose", "Task not initialized",
                                     args=_grasp_pose_args)
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(_sim_get_grasp_pose, object_name,
                              allow_ground_truth, timeout=30.0)
    except TimeoutError:
        action_log.log_query_failure("grasp_pose", "Simulation busy — retry in a few seconds",
                                     args=_grasp_pose_args)
        raise HTTPException(status_code=503, detail="Simulation busy — retry in a few seconds")
    if result.get("position") is None:
        action_log.log_query_failure("grasp_pose", f"Object '{object_name}' not found",
                                     args=_grasp_pose_args)
        raise HTTPException(status_code=404, detail=f"Object '{object_name}' not found")
    return PoseResponse(**result)


@app.post("/rotate_gripper", response_model=RotateGripperResponse)
def rotate_gripper(req: RotateGripperRequest):
    """Rotate the gripper while holding an object (expert gripper_rotate).

    With ``feed_lead_m`` set, twists along a lead-coupled helix instead --
    see ``_sim_gripper_feed``.
    """
    if _TASK is None:
        action_log.log_action("rotate_gripper", req.model_dump(), ok=False,
                              msg="Task not initialized")
        raise HTTPException(status_code=503, detail="Task not initialized")
    result = _run_action(
        "rotate_gripper", req.model_dump(), _MOTION_TIMEOUT,
        _sim_rotate_gripper, req.object_name, req.angle_rad, req.steps,
        req.feed_lead_m,
        detail_prefix="Rotate failed",
    )
    return RotateGripperResponse(**result)


@app.get("/episode_video")
def get_episode_video():
    """Finalize the current episode's recording and return info about it.

    Calling this **assembles ``episode.mp4`` right now** (idempotent — a
    no-op if there is no active episode or it is already finalized), so the
    returned ``mp4`` exists immediately instead of waiting for the next
    ``/reset`` or server shutdown.  The episode ends here: frames captured
    after this call belong to a future episode (none until the next reset),
    so the agent calls it once, at the very end.

    ``mp4_ready`` is True once the MP4 has been assembled (this call, the
    next reset, or server shutdown).  If the server was killed before
    finalizing (e.g. SIGKILL), the scratch PNG frames are still on disk
    (see ``frames_dir``, under /tmp) — assemble them with
    ``scripts/assemble_univtac_video.py <dir>``.
    """
    _RECORDER.finalize()  # assemble the video now (no-op if none active)
    info = _RECORDER.info()
    if info is None:
        return {"recording": False, "mp4": None, "mp4_ready": False,
                "frames": 0, "dir": None, "index_json": None}
    mp4 = info.get("mp4")
    return {
        "recording": True,
        "task": info.get("task"),
        "seed": info.get("seed"),
        "started": info.get("started"),
        "dir": info.get("dir"),
        "frames_dir": info.get("frames_dir"),
        "mp4": mp4,
        "mp4_ready": bool(mp4) and Path(mp4).exists(),
        "index_json": info.get("index_json"),
        "frames": info.get("frames", 0),
    }


@app.get("/video_frames")
def get_video_frames(clear: bool = True):
    """Return the frames recorded since the last call, base64-PNG encoded.

    The eval harness (``capx/envs/launch.py``) calls this once per trial,
    after the last step, and assembles the trial video itself into its own
    output directory (``outputs/<model>/univtac_<task>/``).  Each frame is
    returned at most once (``clear=True`` semantics, always draining).

    The first call also switches the recorder to harness mode: from then
    on ``finalize()`` stops writing ``episode.mp4``/``index.json`` into the
    agent recording root (``outputs/agent``), so eval runs don't pollute
    the agent output directory.  The agent path never calls this endpoint
    and keeps its self-finalized episodes as before.
    """
    if clear:
        _RECORDER.set_harness_mode()
    frames = _RECORDER.drain_frames()
    return {"frames": [base64.b64encode(f).decode("ascii") for f in frames]}


@app.post("/recording/mode")
def set_recording_mode(req: RecordingModeRequest):
    """Switch the server into harness mode (the eval harness announces
    itself before its first reset, so even the startup episode is not
    self-finalized into the agent recording root).

    In harness mode the recorder stops assembling episode.mp4 into the
    agent recording root and the action log stops writing entirely —
    ``outputs/agent`` stays untouched during evals.  The agent path never
    calls this endpoint and keeps both behaviors.
    """
    if req.mode == "harness":
        _RECORDER.set_harness_mode()
        action_log.set_harness_mode()
    return {"mode": req.mode, "harness_mode": _RECORDER.harness_mode}


@app.get("/tasks")
def list_tasks():
    """List available tasks."""
    return {"tasks": AVAILABLE_TASKS}


# ---------------------------------------------------------------------------
# Task initialization (requires Isaac Sim app)
# ---------------------------------------------------------------------------


def _activate_backing_root(root: Path) -> None:
    """Make ``root`` the first place ``import envs.*`` resolves from.

    UniVTAC and ViTaForge each ship a top-level ``envs/`` with no
    ``__init__.py``, so both are PEP 420 namespace *portions* of the same
    ``envs`` package whenever both roots are on sys.path.  ``import
    envs.weight_classify`` then succeeds, but its ``from ._base_task import *``
    silently binds UniVTAC's ``_base_task`` instead — yielding a chimera with
    the wrong ``ASSETS_ROOT`` that only fails much later, deep inside
    ``add_from_usd_file``, looking like a missing asset.  Import *order*
    (sys.path position) is what decides, so pin the wanted root at the front
    and drop the cached modules that were resolved against the old one.

    The root is left on sys.path permanently: ``_sim_move`` imports
    ``envs.utils.atom`` lazily on first use, long after this returns, and a
    scoped try/finally would let that import fall back to UniVTAC.
    """
    import importlib

    roots = {UNIVTAC_ROOT.resolve(), VITAFORGE_ROOT.resolve()}
    sys.path[:] = [p for p in sys.path if Path(p).resolve() not in roots]
    sys.path.insert(0, str(root.resolve()))

    for name in [m for m in sys.modules if m == "envs" or m.startswith("envs.")]:
        del sys.modules[name]
    # /mnt/nas is NFS: FileFinder caches directory listings, so a stale cache
    # can resurrect the portion we just removed.
    importlib.invalidate_caches()
    logger.info("Backing root active: %s", root.resolve())


# The four packages ViTaForge forks under third_party/TacEx/source.  ``uipc``
# itself is NOT among them: it is the PyPI ``pyuipc`` wheel, shared by both
# repos, so only the Python side differs.
_TACEX_PACKAGES = ("tacex", "tacex_assets", "tacex_tasks", "tacex_uipc")


def _prefer_vitforge_tacex(root: Path) -> None:
    """Run ViTaForge's ``envs/`` against ViTaForge's TacEx fork.

    ViTaForge ships its own TacEx and its install docs are explicit that the
    public one must not be used — "some internal APIs have been adapted for
    ViTaForge's tactile sensor pipeline" (docs/Installation.md).  Both repos
    install *editable* into the same conda env under the same four package
    names, so the mapping can only ever point at one of them; here it points at
    UniVTAC's.  ViTaForge's ``envs/`` would therefore run on UniVTAC's TacEx —
    the same kind of chimera ``_activate_backing_root`` already guards against,
    one level deeper.

    It stays invisible until a force task measures anything: the missing piece
    is the ``vertex_positions`` property that ViTaForge's ``UipcObject`` adds
    and UniVTAC's lacks, which ``envs/utils/actor.Actor`` inherits and
    ``bulb_tightening._measure`` calls.  The symptom is an AttributeError deep
    inside ``_measure``, not an import error.

    An editable install is a finder *appended* to ``sys.meta_path``, i.e. it
    runs behind ``PathFinder``, so a ``sys.path`` entry takes precedence without
    reinstalling anything — which matters because one environment has to serve
    both repos' tasks.

    Scoped by the caller: to the force-task profiles, which have never been
    runnable under this server, and to the backings that ask for it by name —
    so this stays purely additive.  Tasks that already run keep their measured
    behaviour until that is decided separately, which is why the two
    classification profiles (gelsight.yml, no ``task_parameters`` block) are
    left on the environment's fork even though they, too, are ViTaForge code.
    """
    import importlib

    source = root.resolve() / "third_party" / "TacEx" / "source"
    staged = [source / name for name in _TACEX_PACKAGES
              if (source / name / name / "__init__.py").is_file()]
    if not staged:
        logger.warning("No ViTaForge TacEx fork under %s — leaving the "
                       "environment's editable install in place", source)
        return

    staged_paths = {str(p) for p in staged}
    sys.path[:] = [p for p in sys.path if p not in staged_paths]
    sys.path[:0] = [str(p) for p in staged]

    # Drop only modules that resolved against the *other* fork; re-importing a
    # package that is already correctly bound would create duplicate classes and
    # break the isinstance checks in envs/.
    for name in [m for m in sys.modules
                 if m.split(".")[0] in _TACEX_PACKAGES]:
        path = getattr(sys.modules[name], "__file__", None) or ""
        if not any(path.startswith(str(p)) for p in staged):
            del sys.modules[name]
    importlib.invalidate_caches()
    logger.info("TacEx fork active: %s", ", ".join(str(p) for p in staged))


def _apply_cfg_override(env_cfg: Any, spec: str) -> None:
    """Apply one ``KEY=VALUE`` override to ``env_cfg`` (VALUE parsed as YAML).

    Unknown keys are fatal.  A typo that silently no-ops would be diagnosed
    only after a ~330 s reset cycle, so failing at startup is much cheaper.
    """
    import yaml

    key, sep, raw = spec.partition("=")
    key = key.strip()
    if not sep:
        raise SystemExit(f"--cfg-override expects KEY=VALUE, got {spec!r}")
    if not hasattr(env_cfg, key):
        raise SystemExit(
            f"--cfg-override: TaskCfg has no field {key!r} "
            f"(available: {sorted(env_cfg.__dataclass_fields__)})"
        )
    value = yaml.safe_load(raw)
    setattr(env_cfg, key, value)
    logger.info("cfg override: %s = %r", key, value)


def _apply_task_config(env_cfg: Any, task_config: dict) -> None:
    """Copy task_config.yaml values into ``env_cfg``.

    This is a deliberate *whitelist*, not a passthrough.  Fields the YAML sets
    but serving must not honor:

    * ``save_frequency`` / ``video_frequency`` — forced to 0 below.  The
      VideoHandler opens an ffmpeg pipe per reset that is never closed, so any
      nonzero value leaks orphaned subprocesses; the server captures its own
      frames instead (see ``_record_frame``).  ViTaForge's configs and cfg
      defaults both say 2, so letting the YAML win would reintroduce the leak.
    * ``skip_pre_move`` / ``save_pre_move`` (ViTaForge's gelsight.yml) — the
      former would skip the pre_move hook that positions the arm, the latter
      just pickles every episode to disk.
    """
    env_cfg.decimation = task_config.get("decimation", env_cfg.decimation)
    env_cfg.render_frequency = task_config.get("render_frequency", 0)
    env_cfg.obs_data_type = task_config.get("observations", {})
    env_cfg.random_texture = task_config.get("random_texture", False)


def _log_actor_densities() -> None:
    """Log the mass density requested for each actor.

    Nothing downstream can read an object's mass back out (UIPC exposes no mass
    accessor and the PhysX view is unused for these bodies — the
    ``modify_mass_properties`` warnings at spawn are expected), so this log line
    is the only place the per-actor density is visible.  It matters for the
    weight-sensing tasks: "the sensor cannot resolve the difference" and "the
    density override never took effect" look identical from outside.

    Reports the density from the actor's own cfg, not from the UIPC geometry:
    ``AffineBodyConstitution.apply_to(mesh, k, mass_density=...)`` does not
    leave a read-back attribute behind, so ``mesh.instances()`` has nothing to
    find.  This line therefore proves the value *reaches* the constitution call,
    not that the solver honoured it.
    """
    try:
        actors = _TASK._actor_manager.actors
        parts = []
        for name, actor in actors.items():
            mesh = getattr(actor, "uipc_meshes", None) or [None]
            parts.append(f"{name}={actor.cfg.mass_density} (meshes={len(mesh)})")
        logger.info("Actor mass densities: %s", ", ".join(parts))
    except Exception as exc:  # diagnostic only — never block startup
        logger.warning("Could not read actor densities: %s", exc)


def _build_ball_task(task_config: dict, device: str, seed: int,
                     cfg_overrides: list[str] | None):
    """Construct the standalone touch-only ball task (``blind_pick_balls``).

    Everything the generic path in ``init_task`` does to a BaseTask is wrong for
    this environment, and the mismatches are structural rather than cosmetic:

    * ``TaskCfg`` has no ``step_lim`` / ``robot`` / ``use_adaptive_grasp`` /
      ``adaptive_grasp_depth_threshold``.  The endpoints read all four off
      ``_TASK.cfg``; the adapter sets them on the *live* config, because
      ``Task.__init__`` copies TaskCfg with ``dataclasses.replace`` and would
      drop anything written beforehand.
    * ``_apply_task_config`` reads ``decimation`` / ``render_frequency`` /
      ``obs_data_type`` / ``random_texture``.  None of those exist on this
      TaskCfg, and none of them mean anything to a task that renders nothing
      per control step.
    * The constructor is ``Task(cfg, render_mode=...)``.  There is no
      ``mode="collect"`` and no per-step ``_update_render`` to avoid.
    * ``workspace`` is a UIPC dump directory, and the dataclass default is
      relative to the CWD -- which is this repo when serving, so the scene
      dumps and the generated tet assets would land in it.  It goes under the
      server's temp root, keyed by seed: two seeds served by two processes
      must not share one world directory.

    Returns the adapter, which is what every endpoint means by ``_TASK``.
    """
    import importlib

    # env/tasks/, not envs/: this task ships in its own package (the upstream
    # runner imports it as ``env.tasks.run_blind_pick_balls``), and ``env`` is
    # a namespace package -- it has no __init__.py -- so the dotted path works
    # once ViTaForge is on sys.path.  ``envs.blind_pick_balls`` does not exist.
    task_module = importlib.import_module("env.tasks.blind_pick_balls")
    cfg = task_module.TaskCfg(device=device or "cuda:0")

    fields = set(cfg.__dataclass_fields__)
    unknown = sorted(set(task_config) - fields)
    if unknown:
        raise SystemExit(
            f"task_config/blind_pick_balls.yml: not a TaskCfg field of "
            f"blind_pick_balls: {unknown} (accepted: {sorted(fields)})")
    for key, value in task_config.items():
        setattr(cfg, key, tuple(value) if key == "material_profiles" else value)

    # The materials are drawn from cfg.seed in __init__ and never redrawn;
    # reset(seed) only resamples the cells.  One process therefore serves
    # exactly one seed's material set, which is what the cap-x configs'
    # "trial id == server --seed" rule assumes.
    cfg.seed = int(seed)
    cfg.workspace = str(
        Path(tempfile.gettempdir()) / "univtac_server" / "blind_pick_balls"
        / f"seed_{int(seed)}" / "world")
    # The observer camera is this task's only scene view -- its policy
    # observation carries no image at all -- and both /record/mode and
    # get_frame_shot need it.  Here record_video does nothing else: the video
    # writer it also gates in ViTaForge is a BaseTask feature this task lacks.
    cfg.record_video = True

    for spec in cfg_overrides or ():
        _apply_cfg_override(cfg, spec)

    logger.info("Blind-ball workspace: %s", cfg.workspace)
    task = task_module.Task(cfg, render_mode="rgb_array")

    from capx.serving.univtac_ball_adapter import BallTaskAdapter

    return BallTaskAdapter(task)


def init_task(task_name: str, device: str, sensor_type: str = "gsmini",
              seed: int = 0, cfg_overrides: list[str] | None = None) -> None:
    """Initialize the UniVTAC task. Must be called after Isaac Sim app is ready."""
    global _TASK, _TASK_NAME

    import importlib

    import yaml

    backing = _backing_for(task_name)
    # Defensive: the authoritative call is in main(), before AppLauncher.  This
    # one covers any other entry point into init_task.
    _activate_backing_root(backing.root)

    # Load task config
    config_path = backing.root / "task_config" / backing.config_file
    with open(config_path) as f:
        task_config = yaml.safe_load(f)

    # Must happen before ``import envs.*`` — once envs.utils.actor is bound, its
    # UipcObject base class is fixed for the life of the process.  Two ways in:
    # a backing that asks for the fork (blind_pick, whose first step reads
    # ``object.vertices`` — see the TASK_BACKING entry), or the profile marker
    # the force tasks carry.  Keyed on the config rather than the task name, so
    # that the second test is a property of the profile: a ``task_parameters``
    # block is what marks a force-task config, and ViTaForge's other profiles
    # (gelsight.yml) do not have one.
    if backing.vitforge_tacex or "task_parameters" in task_config:
        _prefer_vitforge_tacex(backing.root)

    # blind_pick_balls is not a BaseTask: every step from here down -- the
    # TaskCfg() build, _apply_task_config's field whitelist, mode="collect",
    # the force-task binder -- assumes a contract it does not implement (see
    # _build_ball_task and the TASK_BACKING entry).  Branch before any of it
    # and leave through the same startup sequence as the generic path.
    if task_name == "blind_pick_balls":
        _TASK = _build_ball_task(task_config, device, seed, cfg_overrides)
        _TASK_NAME = task_name
        logger.info("Task '%s' initialized successfully.", task_name)
        _sim_reset(seed=seed, log=False)
        logger.info("Startup reset complete (seed=%s).", seed)
        _log_actor_densities()
        return

    # Import task module
    task_module = importlib.import_module(f"envs.{task_name}")
    # Which repo's envs/ actually won.  B2's failure mode is silent for ~330 s
    # and then surfaces as a bogus "missing asset", so print it every startup.
    logger.info("envs package resolved: %s -> %s",
                task_name, sys.modules["envs._base_task"].__file__)

    # Build env config
    env_cfg = task_module.TaskCfg()
    env_cfg.tactile_sensor_type = sensor_type
    # UIPC dumps its scene (systems.json / dump/ / sanity_check/) into
    # uipc_sim.workspace (= save_root/"scene") on every reset, even with
    # save_frequency=0.  Serving never replays those dumps (the process
    # keeps first_frame in memory), so keep them out of the repo.
    env_cfg.save_dir = Path(tempfile.gettempdir()) / "univtac_server" / task_name
    _apply_task_config(env_cfg, task_config)
    # ViTaForge's four force tasks (envs/_force_task_utils.py) read their
    # parameters through task_parameters(), which returns {} unless either
    # ``cfg.force_task_parameters`` exists or VITAFORGE_TASK_CONFIG is
    # exported — neither holds for serving.  Bind them here, with the same
    # helper collect_data.py uses, from the backing's own config file.
    #
    # Exporting VITAFORGE_TASK_CONFIG is not a substitute: task_parameters()
    # forwards only the YAML's task_parameters block plus physics_seed, while
    # ``workspace`` is *derived* inside the binder from save_dir and cannot be
    # written into a YAML.  Task.__init__ reads it unconditionally, so that
    # route dies with KeyError: 'workspace'.  Binding here also drops the
    # dependency on an env var the operator can silently forget — in which
    # case the task runs on code defaults instead of the shipped profile.
    try:
        from envs._force_task_utils import FINAL_TASKS, prepare_force_task_config
    except ImportError:  # UniVTAC-backed task: no force-task contract
        pass
    else:
        if task_name in FINAL_TASKS:
            # skip_pre_move stays serving's decision, not the YAML's — see
            # _apply_task_config.  It is false in every force-task profile
            # today, so this only preserves the documented invariant.
            binder_config = {k: v for k, v in task_config.items() if k != "skip_pre_move"}
            prepare_force_task_config(env_cfg, binder_config, config_path, seed=seed)
            logger.info(
                "Bound force-task parameters from %s (physics_seed=%s, workspace=%s)",
                config_path, seed, env_cfg.force_task_parameters["workspace"])
    # Never write UniVTAC's own video/save outputs during serving.  The
    # VideoHandler opens an ffmpeg pipe per reset that is never closed (an
    # orphaned-subprocess leak), and the server already captures its own
    # frames (see _record_frame).  Force both to 0 regardless of task_config.
    env_cfg.save_frequency = 0
    env_cfg.video_frequency = 0
    env_cfg.scene.num_envs = 1
    # First reset in a fresh process runs the full scene init (UIPC build,
    # render-target creation) and has been observed at ~330 s on a loaded
    # machine (2026-08-25); the per-segment limit is applied to every reset
    # phase, so keep a wide margin.  Subsequent resets replay first_frame
    # and are fast.
    env_cfg.reset_time_limit = 600.0
    env_cfg.debug_vis = False
    env_cfg.logger_level = "error"

    # Route the physics simulation to the requested GPU.  TaskCfg.sim.device
    # defaults to "cuda:0" (SimulationCfg default), and the AppLauncher
    # --device flag only selects the *rendering* device — without this line
    # the physics would always land on GPU 0 regardless of --device cuda:N.
    # Same pattern as UniVTAC's scripts/eval_policy.py.
    if device:
        env_cfg.sim.device = device

    # Registry overrides first, then the command line, so an explicit
    # --cfg-override always wins.  Both must land before Task(...) below:
    # Task.__init__ validates these fields and bakes friction into the sim.
    for key, value in backing.cfg_overrides:
        setattr(env_cfg, key, value)
        logger.info("task backing override: %s = %r", key, value)
    for spec in cfg_overrides or ():
        _apply_cfg_override(env_cfg, spec)

    logger.info("Creating task '%s' (sensor=%s, device=%s)...", task_name, sensor_type, device)
    # Use "collect" mode — avoids per-step _update_render() overhead
    # that makes "eval" mode unusably slow (~2 FPS vs ~25 FPS).
    _TASK = task_module.Task(env_cfg, mode="collect")
    _TASK_NAME = task_name
    logger.info("Task '%s' initialized successfully.", task_name)

    # Startup reset.  Agents have no reset() (see robo.py), so the scene must
    # leave its raw loaded state on its own.  reset() runs the task's pre_move
    # hook — for insert_HDMI that grasps the prism above the slot and sets
    # ``target_pose``, which check_success() reads, and it initialises the
    # render targets that collect mode needs (previously the eval framework's
    # first POST /reset did this; that is no longer possible).
    _sim_reset(seed=seed, log=False)  # no action-log episode yet (see _sim_reset)
    logger.info("Startup reset complete (seed=%s).", seed)

    # Diagnostic, after the reset so the actors are guaranteed to exist.
    _log_actor_densities()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse args, launch Isaac Sim app, start FastAPI server.

    The Isaac Sim AppLauncher must be the first thing called (it parses
    its own CLI arguments).  We follow the same pattern as UniVTAC's
    scripts/collect_data.py and scripts/eval_policy.py.
    """
    # -------------------------------------------------------------------
    # Step 1: Build parser with BOTH server args AND AppLauncher args
    # -------------------------------------------------------------------
    from isaaclab.app import AppLauncher

    parser = argparse.ArgumentParser(description="UniVTAC Tactile API Server")
    parser.add_argument("--task", type=str, default="insert_hole",
                        choices=AVAILABLE_TASKS,
                        help="Task name to serve")
    parser.add_argument("--port", type=int, default=8120,
                        help="Server port")
    parser.add_argument("--host", type=str, default="127.0.0.1",
                        help="Server host")
    parser.add_argument("--sensor-type", type=str, default="gsmini",
                        choices=["gsmini", "gf225", "xensews"],
                        help="Tactile sensor type")
    parser.add_argument("--watchdog-grace", type=float, default=120.0,
                        help="seconds of /health silence before the freeze watchdog SIGKILLs this server "
                             "(120 << the client's 1800 s read timeout so a freeze is always killed, "
                             "never a silently-recovering half-dead server).  /health does not run on "
                             "the main thread, so a long _MOTION_TIMEOUT op does not trip this.")
    parser.add_argument("--no-watchdog", action="store_true",
                        help="disable the external freeze watchdog")
    parser.add_argument("--seed", type=int, default=0,
                        help="Scene-randomization seed for the startup reset "
                             "(drives pre_move noise, e.g. grasp offset)")
    parser.add_argument("--cfg-override", action="append", default=[], metavar="KEY=VALUE",
                        help="override a TaskCfg field for this run (repeatable; "
                             "VALUE is parsed as YAML).  Unknown keys abort startup.")
    # Let AppLauncher add its own args (--headless, --livestream, --device, etc.)
    AppLauncher.add_app_launcher_args(parser)

    # Parse combined args
    cli = parser.parse_args()

    # Claim the port before the expensive Isaac Sim startup. Passing this
    # socket to Uvicorn below keeps the check race-free: no other server can
    # claim the address between the check and Uvicorn's listen call.
    try:
        listener_socket = _reserve_listener(cli.host, cli.port)
    except OSError as exc:
        logger.error(
            "Cannot start UniVTAC server: %s:%d is already in use (%s)",
            cli.host,
            cli.port,
            exc,
        )
        sys.exit(1)

    # Pin the task's backing repo before anything imports envs.*: AppLauncher
    # below pulls in isaaclab, and init_task imports the task module.
    _activate_backing_root(_backing_for(cli.task).root)

    # Set defaults that UniVTAC tasks expect — must match scripts/eval_policy.py
    cli.enable_cameras = True
    cli.num_envs = 1
    cli.livestream = 2  # headless rendering mode (required for GelSight to init)

    # NOTE: GPU selection must happen via CUDA_VISIBLE_DEVICES (set by
    # scripts/launch_univtac_server.sh), NOT via --device cuda:N.  Isaac
    # Sim's usdrt scenegraph only supports cuda:0 ("GPUs other than cuda:0
    # are not currently supported"), so the chosen GPU is hidden behind
    # cuda:0 — same pattern as UniVTAC's scripts/collect_data.py.

    # -------------------------------------------------------------------
    # Step 2: Launch Isaac Sim app (must happen before isaaclab imports)
    # -------------------------------------------------------------------
    app_launcher = AppLauncher(cli)
    simulation_app = app_launcher.app

    # ---- re-register signal handlers -------------------------------------------
    # Isaac Sim's AppLauncher installs its own SIGINT handler that swallows
    # Ctrl+C.  Override it so SIGINT / SIGTERM cleanly shuts down the server.
    _shutdown_flag = _threading.Event()

    def _on_shutdown_signal(signum, frame):
        logger.info("Received signal %s — shutting down...", signum)
        _shutdown_flag.set()

    signal.signal(signal.SIGINT, _on_shutdown_signal)
    signal.signal(signal.SIGTERM, _on_shutdown_signal)

    headless = getattr(cli, "headless", False)
    logger.info("Isaac Sim app launched (headless=%s, pid=%d)", headless, os.getpid())

    # -------------------------------------------------------------------
    # Step 3: Initialize the UniVTAC task
    # -------------------------------------------------------------------
    try:
        # Use AppLauncher's --device for GPU selection (already parsed as cli.device)
        device = getattr(cli, "device", "cuda:0")
        init_task(cli.task, device, cli.sensor_type, seed=cli.seed,
                  cfg_overrides=cli.cfg_override)
    except Exception:
        logger.error("Failed to initialize task: %s", traceback.format_exc())
        listener_socket.close()
        simulation_app.close()
        sys.exit(1)

    # -------------------------------------------------------------------
    # Step 4: Start FastAPI server
    # -------------------------------------------------------------------
    action_log.log_server_start()
    logger.info("Starting UniVTAC API server on %s:%d (task=%s)",
                cli.host, cli.port, cli.task)

    # -------------------------------------------------------------------
    # Start uvicorn in daemon thread; keep main thread for Isaac Sim.
    # -------------------------------------------------------------------
    config = uvicorn.Config(app, host=cli.host, port=cli.port, log_level="warning")
    server = uvicorn.Server(config)

    def _run_uvicorn() -> None:
        import asyncio as _asyncio
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve(sockets=[listener_socket]))

    uvicorn_thread = _threading.Thread(target=_run_uvicorn, daemon=True)
    uvicorn_thread.start()

    # ``Server.started`` becomes true only after Uvicorn has adopted the
    # pre-bound socket and finished application startup. Do not report ready
    # or spawn a watchdog until then: otherwise a bind/start failure leaves a
    # healthy-looking main process that the watchdog kills only after grace.
    startup_deadline = _time.monotonic() + 15.0
    while not server.started and uvicorn_thread.is_alive() and _time.monotonic() < startup_deadline:
        _time.sleep(0.05)
    if not server.started:
        logger.error("Uvicorn failed to start on %s:%d", cli.host, cli.port)
        server.should_exit = True
        listener_socket.close()
        simulation_app.close()
        sys.exit(1)

    logger.info("UniVTAC API server ready on %s:%d", cli.host, cli.port)

    # Spawn an external freeze watchdog (a separate stdlib-only process).  A
    # cuRobo hang can hold the GIL inside C++/CUDA so that no Python thread —
    # not even /health — can run, which makes an in-process watchdog useless.
    # The watchdog polls /health and SIGKILLs this process if it stops
    # answering, so scripts/launch_univtac_server.sh can respawn it.
    if not getattr(cli, "no_watchdog", False):
        import subprocess as _subprocess

        watchdog_path = Path(__file__).with_name("univtac_watchdog.py")
        watchdog_proc = _subprocess.Popen(
            [
                sys.executable,
                str(watchdog_path),
                "--pid", str(os.getpid()),
                "--host", str(cli.host),
                "--port", str(cli.port),
                "--grace", str(getattr(cli, "watchdog_grace", 120.0)),
            ]
        )
        logger.info("Freeze watchdog spawned (pid=%d, grace=%.0fs)",
                    watchdog_proc.pid, getattr(cli, "watchdog_grace", 120.0))

    # Main-thread dispatch loop: poll _cmd_queue, execute sim operations
    # synchronously.  Exits cleanly on SIGINT (Ctrl+C) or SIGTERM.
    #
    # IMPORTANT: All simulation operations run directly on the main thread.
    # Isaac Sim physics / PhysX / cuRobo operations are NOT thread-safe at
    # the C++/CUDA level — running them in daemon threads causes deadlocks
    # that take down the entire process.  The dispatch loop serialises
    # operations through the queue; the main thread executes them one at a
    # time.  If an operation hangs (e.g. cuRobo IK on degenerate input),
    # the caller's HTTP-level timeout will fire, but the dispatch loop
    # remains blocked until the process is restarted.  This is an
    # acceptable trade-off: the physics engine works correctly, and cuRobo
    # hangs are rare in practice.
    while not _shutdown_flag.is_set():
        try:
            fn, args, kwargs, result_holder, done_event = _cmd_queue.get(timeout=0.1)
        except _queue.Empty:
            _time.sleep(0.01)
            continue

        # Consume internal kwargs (not passed through to the function).
        kwargs.pop("_op_timeout", _QUERY_TIMEOUT)

        # Per-op duration log: contact-heavy sim ops occasionally grind for
        # tens of seconds (once observed > 150 s with /health still alive —
        # the client outwaits them at 600 s).  Log any op over 2 s so the
        # server console shows how long each slow op actually took; that
        # distribution decides whether the 550/600 s ceilings are right.
        inner_result: dict = {}
        _op_t0 = _time.perf_counter()
        _exec_wrapper(fn, args, kwargs, inner_result)
        _op_dur = _time.perf_counter() - _op_t0
        if _op_dur >= 2.0:
            logger.warning("Sim op '%s' took %.1fs",
                           getattr(fn, "__name__", str(fn)), _op_dur)
        result_holder["result"] = inner_result.get("result")
        result_holder["error"] = inner_result.get("error")
        if result_holder["error"] is not None:
            err_text = str(result_holder["error"])
            logger.error("Sim operation failed: %s", result_holder["error"])
            # A CUDA illegal-memory-access poisons the whole CUDA context —
            # every later sim op fails with cascading errors (PhysX, render,
            # TorchScript) and the process cannot recover in place.  Die fast
            # so the supervisor respawns a fresh process instead of serving
            # errors from a dead GPU context.  (/health does not touch CUDA,
            # so the freeze watchdog cannot detect this.)
            if "CUDA error" in err_text or "illegal memory access" in err_text:
                logger.error("CUDA context poisoned (illegal memory access) — "
                             "exiting so the supervisor can respawn a fresh "
                             "process.")
                action_log.log_event(
                    "cuda_fatal",
                    a=getattr(fn, "__name__", str(fn)),
                    msg=err_text[:200],
                )
                os._exit(1)

        done_event.set()

    # Cleanup on exit
    logger.info("Shutting down...")
    server.should_exit = True
    uvicorn_thread.join(timeout=5.0)
    listener_socket.close()
    _RECORDER.finalize()  # assemble the final episode's video
    _TASK.close()
    simulation_app.close()


if __name__ == "__main__":
    main()
