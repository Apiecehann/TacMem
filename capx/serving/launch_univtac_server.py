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
import io
import logging
import os
import signal
import sys
import traceback
from pathlib import Path
from typing import Any, Literal

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("univtac_server")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# cap-x repo root — this file lives at <repo>/capx/serving/launch_univtac_server.py
_REPO_ROOT = Path(__file__).resolve().parents[2]

# UniVTAC is cloned as a sibling of cap-x by default; override with UNIVTAC_ROOT.
UNIVTAC_ROOT = Path(os.environ.get("UNIVTAC_ROOT", str(_REPO_ROOT.parent / "UniVTAC")))
TASK_CONFIG_DIR = UNIVTAC_ROOT / "task_config"

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
]

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="UniVTAC Tactile API Server")

# Global state (initialized after Isaac Sim app starts)
_TASK: Any = None
_TASK_NAME: str = ""
_FRAME_BUFFER: list[str] = []  # base64 PNG frames captured during motion
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

# ---------------------------------------------------------------------------
# Thread-safe command queue — Isaac Sim APIs must be called from main thread.
# FastAPI handlers (uvicorn thread) post to _cmd_queue; the main-thread
# _dispatch_loop() executes them.
# ---------------------------------------------------------------------------
import queue as _queue
import threading as _threading

_cmd_queue: _queue.Queue = _queue.Queue()


# Timeout for main-thread operations.  Motion planning (cuRobo IK)
# can hang indefinitely on degenerate inputs, so every _run_on_main
# call must have a bounded wait.
_MOTION_TIMEOUT = 120.0  # goto_pose / move_delta / rotate_gripper
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


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _rgb_tensor_to_base64(tensor: torch.Tensor) -> str:
    """Convert RGB tensor (H, W, 3) to base64 PNG string."""
    if tensor.dtype != torch.uint8:
        tensor = (tensor.clamp(0, 1) * 255).to(torch.uint8)
    img = Image.fromarray(tensor.cpu().numpy())
    buf = io.BytesIO()
    img.save(buf, format="png")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


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


class GripperRequest(BaseModel):
    action: Literal["open", "close"]
    force_threshold: float | None = None
    use_adaptive_grasp: bool | None = None  # None → fall back to task config


class ActionResponse(BaseModel):
    success: bool = True
    plan_success: bool = True
    message: str = ""
    step: int = 0
    tactile: dict[str, Any] | None = None


class TactileResponse(BaseModel):
    left_contact: bool = False
    right_contact: bool = False
    left_depth_mm: float = 0.0
    right_depth_mm: float = 0.0
    left_marker_displacement_px: float = 0.0
    right_marker_displacement_px: float = 0.0
    left_marker_coherence: float = 0.0
    right_marker_coherence: float = 0.0
    left_marker_std_px: float = 0.0
    right_marker_std_px: float = 0.0
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


def _sim_reset(seed: int) -> dict:
    """Reset the task environment."""
    global _TASK, _FRAME_BUFFER, _MARKER_REFERENCE
    _FRAME_BUFFER = []
    _GRASP_OFFSET.clear()
    _TASK.reset(seed=seed)
    _FRAME_BUFFER.append(_capture_frame())  # initial state
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
    _FRAME_BUFFER.append(_capture_frame())

    try:
        success = _TASK.move([action], tag=tag, delay=True, is_save=False)
    except Exception as exc:
        logger.error("_TASK.move() raised exception: %s", exc)
        success = False

    # --- fallback: retry without z-approach waypoint -------------------------
    # The pre_dis waypoint adds an intermediate IK query that cuRobo often
    # cannot solve (especially near workspace boundaries).  When the primary
    # plan fails and z_approach was used, retry with a direct move.
    if not success and z_approach > 0 and not gripper_action:
        action_no_app = Action(
            action="move",
            target_pose=target_pose,
            pre_dis=None,
        )
        try:
            success = _TASK.move([action_no_app], tag=tag + "_noapp", delay=True, is_save=False)
        except Exception as exc:
            logger.error("_TASK.move() fallback raised exception: %s", exc)
            success = False

    # If the motion planner failed, reset plan_success so that subsequent
    # gripper / move_delta calls are not blocked by a stale failure flag.
    if not success:
        _TASK.plan_success = True

    tactile_data = _sim_get_tactile_data()
    _FRAME_BUFFER.append(_capture_frame())

    return {
        "success": success,
        "plan_success": _TASK.plan_success,
        "message": "OK" if success else "Motion planning failed",
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
    from envs.utils.transforms import Pose

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

    # Skip trivially tiny deltas (below all minimums)
    if abs(dx) < MIN_TRANS and abs(dy) < MIN_TRANS and abs(dz) < MIN_TRANS \
       and abs(drx) < MIN_ROT and abs(dry) < MIN_ROT and abs(drz) < MIN_ROT:
        return {
            "success": True,
            "plan_success": _TASK.plan_success,
            "message": "OK (delta below minimum, skipped)",
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
    _FRAME_BUFFER.append(_capture_frame())

    # --- motion planning with fallback ----------------------------------------
    # cuRobo can transiently fail even for simple deltas (random seed, joint
    # limits, workspace boundary).  Retry with progressively smaller deltas
    # before reporting failure.
    success = False
    for scale in [1.0, 0.5, 0.25]:
        if scale < 1.0:
            logger.warning(
                "_sim_move_delta: retrying with scale=%.0f%% (was %s)",
                scale * 100, dict(dx=dx, dy=dy, dz=dz, drx=drx, dry=dry, drz=drz),
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

            if coord == "ee":
                scaled_pose = ee_pose.add_bias([scaled_dx, scaled_dy, scaled_dz],
                                                coord="local")
                if any([scaled_drx, scaled_dry, scaled_drz]):
                    scaled_pose = scaled_pose.add_rotation(
                        euler=[scaled_drx, scaled_dry, scaled_drz], coord="local")
            else:
                scaled_pose = ee_pose.add_bias([scaled_dx, scaled_dy, scaled_dz],
                                                coord="world")
                if any([scaled_drx, scaled_dry, scaled_drz]):
                    scaled_pose = scaled_pose.add_rotation(
                        euler=[scaled_drx, scaled_dry, scaled_drz], coord="world")

            scaled_target_z = float(scaled_pose.p[2])
            if scaled_target_z < 0.01:
                continue

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
        else:
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
            if success:
                break

    # If the delta move failed, reset plan_success so subsequent
    # actions are not blocked by a stale failure flag.
    if not success:
        _TASK.plan_success = True

    tactile_data = _sim_get_tactile_data()
    _FRAME_BUFFER.append(_capture_frame())

    return {
        "success": success,
        "plan_success": _TASK.plan_success,
        "message": "OK" if success else "Motion planning failed",
        "step": _TASK.step_count,
        "tactile": tactile_data,
    }


def _sim_gripper(action: str, force_threshold: float | None,
                 use_adaptive_grasp: bool | None = None) -> dict:
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
    even though both share the same task instance.
    """
    global _TASK

    from envs.utils.atom import Action

    if action == "open":
        target_pos = 1.0
        depth_threshold = None  # open never uses adaptive threshold
    else:
        target_pos = 0.0
        # Resolve adaptive-grasp threshold (matches atom.close_gripper logic)
        _use_adaptive = (
            use_adaptive_grasp
            if use_adaptive_grasp is not None
            else _TASK.cfg.use_adaptive_grasp
        )
        if _use_adaptive:
            depth_threshold = (
                force_threshold
                if force_threshold is not None
                else _TASK.cfg.adaptive_grasp_depth_threshold
            )
        else:
            depth_threshold = None

    # Capture frame before the gripper action
    _FRAME_BUFFER.append(_capture_frame())

    try:
        success = _TASK.move(
            [Action(action="gripper", target_gripper_pos=target_pos,
                    gripper_depth_threshold=depth_threshold)],
            tag="gripper",
            delay=True,
            is_save=False,
        )
    except Exception as exc:
        logger.error("_TASK.move() gripper raised exception: %s", exc)
        success = False

    # If the gripper action failed, reset plan_success so subsequent
    # actions are not blocked by a stale failure flag.
    if not success:
        _TASK.plan_success = True

    tactile_data = _sim_get_tactile_data()
    _FRAME_BUFFER.append(_capture_frame())

    return {
        "success": success,
        "plan_success": _TASK.plan_success,
        "message": "OK" if success else "Gripper action failed",
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
            """Per-marker displacement stats relative to a reference frame."""
            base = ref if ref is not None else marker[0]
            delta = (marker[1] - base).float()          # [M, 2]
            mag = delta.pow(2).sum(-1).sqrt()           # [M] per-marker L2
            mean_vec = delta.mean(dim=0)                # coherent (shear) part
            mean_mag = float(mag.mean())
            coherence = (
                float(torch.norm(mean_vec) / mean_mag) if mean_mag > 1e-6 else 0.0
            )
            return mean_mag, float(mag.max()), float(mag.std()), coherence

        left_displacement = 0.0
        right_displacement = 0.0
        left_coherence = 0.0
        right_coherence = 0.0
        left_std_px = 0.0
        right_std_px = 0.0
        if left_marker is not None:
            base = _ref("left")
            left_displacement = float(
                (left_marker[1] - (base if base is not None else left_marker[0]))
                .abs().mean()
            )
            _, _, left_std_px, left_coherence = _marker_stats(left_marker, base)
        if right_marker is not None:
            base = _ref("right")
            right_displacement = float(
                (right_marker[1] - (base if base is not None else right_marker[0]))
                .abs().mean()
            )
            _, _, right_std_px, right_coherence = _marker_stats(right_marker, base)

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
                    m, mx, sd, coh = _marker_stats(marker, _ref(key))
                    parts.append(
                        f"{side}_mean={m:.3f} {side}_max={mx:.3f} "
                        f"{side}_std={sd:.3f} {side}_coh={coh:.3f}"
                    )
            with open(TACTILE_CALIBRATION_LOG, "a") as _f:
                _f.write(" ".join(parts) + f" L_depth={ld:.1f} R_depth={rd:.1f}\n")
        except Exception:
            pass

        return {
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
            "left_marker_std_px": round(left_std_px, 4),
            "right_marker_std_px": round(right_std_px, 4),
            "left_rgb_base64": left_rgb_b64,
            "right_rgb_base64": right_rgb_b64,
            "left_depth_base64": left_depth_b64,
            "right_depth_base64": right_depth_b64,
        }
    except Exception:
        logger.warning("Tactile data unavailable: %s", traceback.format_exc())
        return {
            "left_contact": False,
            "right_contact": False,
            "left_depth_mm": 0.0,
            "right_depth_mm": 0.0,
            "left_marker_displacement_px": 0.0,
            "right_marker_displacement_px": 0.0,
            "left_marker_coherence": 0.0,
            "right_marker_coherence": 0.0,
            "left_marker_std_px": 0.0,
            "right_marker_std_px": 0.0,
            "left_rgb_base64": "",
            "right_rgb_base64": "",
            "left_depth_base64": "",
            "right_depth_base64": "",
        }


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
    except Exception:
        logger.warning("Observation unavailable: %s", traceback.format_exc())
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
    except Exception:
        logger.warning("check_success failed: %s", traceback.format_exc())
        return {"completed": False, "check_early_stop": False, "detail": None}


def _sim_rotate_gripper(object_name: str, total_angle_rad: float,
                       steps: int = 4) -> dict:
    """Rotate the gripper holding an object, mimicking expert gripper_rotate.

    Captures the object's orientation before and after the rotation sequence
    and verifies that at least 30 % of the requested angular change was
    realised.  When the object is not firmly gripped the expert
    ``gripper_rotate`` may complete without errors yet fail to rotate the
    object — the orientation check catches this.
    """
    global _TASK
    actor = _find_actor(object_name)
    if actor is None:
        return {"success": False, "plan_success": False,
                "message": f"Object '{object_name}' not found",
                "tactile": _sim_get_tactile_data()}

    # --- capture pre-rotation orientation ------------------------------------
    quat_before = np.asarray(actor.get_pose().q, dtype=np.float64)

    # --- execute rotation -----------------------------------------------------
    angle_per_step = total_angle_rad / steps
    for i in range(steps):
        _TASK.gripper_rotate(actor, angle_per_step, steps=1, is_save=False)
        _FRAME_BUFFER.append(_capture_frame())

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


def _sim_get_grasp_pose(object_name: str) -> dict:
    """Return a grasp pose (position + quaternion WXYZ) for an object on the table.

    On the FIRST call for an object — while the EE still sits at the valid
    pre-grasp pose left by ``pre_move`` — we cache the object-relative offset
    ``ee_pose - object_pose`` together with the grasp quaternion.  Subsequent
    calls (a re-grasp after the object slipped out) return ``object_pose +
    offset`` so the pose tracks the object wherever it now lies, instead of
    returning the EE's current (possibly far-from-object) pose.

    Returns ``{"position": None, ...}`` when the object cannot be found.
    """
    global _TASK

    # Verify the object exists in the scene
    obj_data = _sim_get_object_pose(object_name, return_bbox_extent=False)
    if not obj_data["found"]:
        return {"position": None, "quaternion_wxyz": None}

    object_pos = np.asarray(obj_data["position"], dtype=np.float64)

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
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Pick the actor closest to the workspace centre
    best = min(candidates, key=lambda a: np.linalg.norm(a.get_pose().p - _WORKSPACE_CENTER))
    return best


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
    except Exception:
        logger.warning("get_object_pose('%s') failed: %s",
                       object_name, traceback.format_exc())
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
        from pxr import UsdGeom, Usd
        boundable = UsdGeom.Boundable(prim)
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
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(_sim_reset, req.seed, timeout=300.0)
        return ResetResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reset failed: {e}")


@app.get("/ee_pose", response_model=PoseResponse)
def get_ee_pose():
    """Get current end-effector pose."""
    if _TASK is None:
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(_sim_get_ee_pose, timeout=30.0)
        return PoseResponse(**result)
    except TimeoutError:
        raise HTTPException(status_code=503, detail="Simulation busy — retry in a few seconds")


@app.get("/gripper_state")
def get_gripper_state():
    """Get current gripper state."""
    if _TASK is None:
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        return _run_on_main(_sim_get_gripper_state, timeout=30.0)
    except TimeoutError:
        raise HTTPException(status_code=503, detail="Simulation busy — retry in a few seconds")


@app.post("/move", response_model=ActionResponse)
def move(req: MoveRequest):
    """Move end-effector to target pose, optionally with gripper action."""
    if _TASK is None:
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(
            _sim_move,
            req.position, req.quaternion_wxyz, req.z_approach,
            req.gripper_action, req.gripper_pos, req.tag,
            timeout=_MOTION_TIMEOUT,
        )
        return ActionResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Move failed: {e}")


@app.post("/move_delta", response_model=ActionResponse)
def move_delta(req: MoveDeltaRequest):
    """Move end-effector by a small delta (for fine-grained contact adjustments)."""
    if _TASK is None:
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(
            _sim_move_delta,
            req.dx, req.dy, req.dz,
            req.drx, req.dry, req.drz,
            req.coord, req.tag,
            timeout=_MOTION_TIMEOUT,
        )
        return ActionResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Move delta failed: {e}")


@app.post("/gripper", response_model=ActionResponse)
def gripper(req: GripperRequest):
    """Open or close the gripper."""
    if _TASK is None:
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(_sim_gripper, req.action, req.force_threshold,
                               req.use_adaptive_grasp,
                               timeout=_MOTION_TIMEOUT)
        return ActionResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gripper action failed: {e}")


@app.get("/tactile", response_model=TactileResponse)
def get_tactile():
    """Get current tactile sensor readings from both fingertips."""
    if _TASK is None:
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(_sim_get_tactile_data, timeout=30.0)
        return TactileResponse(**result)
    except TimeoutError:
        raise HTTPException(status_code=503, detail="Simulation busy — retry in a few seconds")


@app.get("/observation", response_model=ObservationResponse)
def get_observation():
    """Get full observation: cameras, tactile, and robot state."""
    if _TASK is None:
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(_sim_get_observation, timeout=30.0)
        return ObservationResponse(**result)
    except TimeoutError:
        raise HTTPException(status_code=503, detail="Simulation busy — retry in a few seconds")


@app.get("/check_success", response_model=SuccessResponse)
def check_success():
    """Check if the current task is successfully completed."""
    if _TASK is None:
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(_sim_check_success, timeout=30.0)
        return SuccessResponse(**result)
    except TimeoutError:
        # Main thread is busy (e.g. long-running motion).  Report as
        # "not yet complete" rather than crashing — the LLM can retry.
        return SuccessResponse(completed=False, check_early_stop=False)


@app.get("/object_pose", response_model=ObjectPoseResponse)
def get_object_pose(object_name: str, return_bbox_extent: bool = False):
    """Get the current pose of a named object in the simulator."""
    if _TASK is None:
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(_sim_get_object_pose, object_name, return_bbox_extent, timeout=30.0)
    except TimeoutError:
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
def get_grasp_pose(object_name: str):
    """Return a kinematically-feasible grasp pose for a named object."""
    if _TASK is None:
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(_sim_get_grasp_pose, object_name, timeout=30.0)
    except TimeoutError:
        raise HTTPException(status_code=503, detail="Simulation busy — retry in a few seconds")
    if result.get("position") is None:
        raise HTTPException(status_code=404, detail=f"Object '{object_name}' not found")
    return PoseResponse(**result)


@app.post("/rotate_gripper", response_model=ActionResponse)
def rotate_gripper(req: RotateGripperRequest):
    """Rotate the gripper while holding an object (expert gripper_rotate)."""
    if _TASK is None:
        raise HTTPException(status_code=503, detail="Task not initialized")
    try:
        result = _run_on_main(
            _sim_rotate_gripper, req.object_name, req.angle_rad, req.steps,
            timeout=_MOTION_TIMEOUT,
        )
        return ActionResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Rotate failed: {e}")


@app.get("/video_frames")
def get_video_frames():
    """Return all captured frames (base64 PNGs) since last reset."""
    return {"frames": list(_FRAME_BUFFER), "count": len(_FRAME_BUFFER)}


@app.get("/tasks")
def list_tasks():
    """List available tasks."""
    return {"tasks": AVAILABLE_TASKS}


# ---------------------------------------------------------------------------
# Task initialization (requires Isaac Sim app)
# ---------------------------------------------------------------------------


def init_task(task_name: str, device: str, sensor_type: str = "gsmini") -> None:
    """Initialize the UniVTAC task. Must be called after Isaac Sim app is ready."""
    global _TASK, _TASK_NAME

    import importlib
    import yaml

    # Load task config
    config_path = TASK_CONFIG_DIR / "contact.yml"
    with open(config_path) as f:
        task_config = yaml.safe_load(f)

    # Import task module
    task_module = importlib.import_module(f"envs.{task_name}")

    # Build env config
    env_cfg = task_module.TaskCfg()
    env_cfg.tactile_sensor_type = sensor_type
    env_cfg.save_dir = Path("./eval_result") / "server" / task_name
    env_cfg.decimation = task_config.get("decimation", env_cfg.decimation)
    env_cfg.save_frequency = task_config.get("save_frequency", 0)  # Don't save during serving
    env_cfg.video_frequency = task_config.get("video_frequency", 0)
    env_cfg.render_frequency = task_config.get("render_frequency", 0)
    env_cfg.obs_data_type = task_config.get("observations", {})
    env_cfg.random_texture = task_config.get("random_texture", False)
    env_cfg.scene.num_envs = 1
    env_cfg.reset_time_limit = 300.0  # generous timeout for first reset
    env_cfg.debug_vis = False
    env_cfg.logger_level = "error"

    # Route the physics simulation to the requested GPU.  TaskCfg.sim.device
    # defaults to "cuda:0" (SimulationCfg default), and the AppLauncher
    # --device flag only selects the *rendering* device — without this line
    # the physics would always land on GPU 0 regardless of --device cuda:N.
    # Same pattern as UniVTAC's scripts/eval_policy.py.
    if device:
        env_cfg.sim.device = device

    logger.info("Creating task '%s' (sensor=%s, device=%s)...", task_name, sensor_type, device)
    # Use "collect" mode — avoids per-step _update_render() overhead
    # that makes "eval" mode unusably slow (~2 FPS vs ~25 FPS).
    _TASK = task_module.Task(env_cfg, mode="collect")
    _TASK_NAME = task_name
    logger.info("Task '%s' initialized successfully.", task_name)

    # No startup reset here.  The eval framework always POSTs /reset first
    # (capx.envs.trial), which is what initialises the render targets in collect
    # mode (per-step rendering is skipped otherwise).  Dev scripts that query
    # /observation directly reset first themselves.


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
    parser.add_argument("--watchdog-grace", type=float, default=180.0,
                        help="seconds of /health silence before the freeze watchdog SIGKILLs this server")
    parser.add_argument("--no-watchdog", action="store_true",
                        help="disable the external freeze watchdog")
    # Let AppLauncher add its own args (--headless, --livestream, --device, etc.)
    AppLauncher.add_app_launcher_args(parser)

    # Parse combined args
    cli = parser.parse_args()

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
        init_task(cli.task, device, cli.sensor_type)
    except Exception:
        logger.error("Failed to initialize task: %s", traceback.format_exc())
        simulation_app.close()
        sys.exit(1)

    # -------------------------------------------------------------------
    # Step 4: Start FastAPI server
    # -------------------------------------------------------------------
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
        loop.run_until_complete(server.serve())

    _threading.Thread(target=_run_uvicorn, daemon=True).start()

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
                "--grace", str(getattr(cli, "watchdog_grace", 180.0)),
            ]
        )
        logger.info("Freeze watchdog spawned (pid=%d, grace=%.0fs)",
                    watchdog_proc.pid, getattr(cli, "watchdog_grace", 180.0))

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
    import time as _time

    while not _shutdown_flag.is_set():
        try:
            fn, args, kwargs, result_holder, done_event = _cmd_queue.get(timeout=0.1)
        except _queue.Empty:
            _time.sleep(0.01)
            continue

        # Consume internal kwargs (not passed through to the function).
        kwargs.pop("_op_timeout", _QUERY_TIMEOUT)

        inner_result: dict = {}
        _exec_wrapper(fn, args, kwargs, inner_result)
        result_holder["result"] = inner_result.get("result")
        result_holder["error"] = inner_result.get("error")
        if result_holder["error"] is not None:
            logger.error("Sim operation failed: %s",
                         result_holder["error"])

        done_event.set()

    # Cleanup on exit
    logger.info("Shutting down...")
    _TASK.close()
    simulation_app.close()


if __name__ == "__main__":
    main()
