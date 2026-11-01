"""Adapter serving ViTaForge's standalone touch-only ball task.

``ViTaForge/env/tasks/blind_pick_balls.py`` is not a ViTaForge ``BaseTask``.
It is a standalone Gymnasium environment: it owns its PhysX/UIPC world, its own
differential-IK controller and its own scorer, and it has none of the manager
objects (``_robot_manager`` / ``_tactile_manager`` / ``_actor_manager``) that
every endpoint in ``launch_univtac_server.py`` reads.  Its constructor is
``Task(cfg, render_mode=...)`` and its ``TaskCfg`` has no ``step_lim``,
``robot``, ``use_adaptive_grasp`` or ``adaptive_grasp_depth_threshold``.

This module supplies exactly that missing surface, in the terms the endpoints
already speak:

* Poses cross the boundary as **end-effector (hand-link) poses**, matching
  every other task served here.  The ball task commands its own **TCP**, the
  centre of the two gel sensing surfaces, which sits ``task.tcp_offset`` along
  the hand's z axis (about 0.118 m on this assembly, re-measured from the gel
  vertices at every reset; the 0.131 m the shared tactile text quotes belongs
  to the other scenes).  Both directions are converted here, so an oracle
  written against ``goto_pose`` never has to know the difference.
* ``get_min_depth()`` returns the **raw gel height-map minimum in mm**, the
  same quantity every other gsmini task reports: about 28.3 mm with the pad
  unloaded, falling as the gel indents.  ``tactile_far_plane`` is 34.0 mm for
  the same reason, which also carries that reader's known quirk here: the
  ``*_contact`` flag in ``/tactile`` compares against far_plane - 0.1, so it
  reads True even with nothing touching a pad.
* Nothing here reads or writes the task's scorer.  ``check_success`` and
  ``check_success_detail`` are the task's own; ``plan_success`` is this
  adapter's and means only "the last motion executed".  The task's own
  ``failure`` (``wrong_material``, ``unverified_grasp``, ``timeout``,
  ``rigid_interpenetration``) is surfaced read-only.
* ``_actor_manager.actors`` is empty on purpose.  The balls are UIPC FEM bodies
  with no ``get_pose``, and leaving them out keeps the served line touch-only,
  which is the task.  ``get_object_pose`` / ``sample_grasp_pose`` /
  ``rotate_gripper`` are withdrawn from the API for the same reason.

Motion is executed by stepping the task's own controller (``Task.step``) until
its TCP settles on the commanded pose, which is what the task's expert does.
There is no separate planner to ask, and none is needed: this arm's IK is the
task's own and the episode budget is the task's own.
* ``physical_trace.jsonl``, next to the seed's UIPC workspace, is a diagnostic
  record rather than part of the served line: one row per control step with
  both pads' true front-face force, the gel depths, the finger opening, the
  contact patch on each pad and, per ball, the two numbers the scorer's held
  test turns on.  The scorer only credits a grasp after 0.15 s of that test
  holding; when a run ends in ``unverified_grasp`` this file says which of its
  conditions broke and when.  Nothing reads it back and no endpoint exposes it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as torch_F
import transforms3d as t3d

logger = logging.getLogger(__name__)

# Full-open per-jaw travel of this gripper.  ViTaForge's other Franka profile
# declares 0.039 (envs/robot/robot_cfg.py:87) and maps percent through that;
# this task's own action space runs to 0.04 and its reset pose is 0.04, so a
# "fully open" command must mean 0.04 here or open_gripper() would leave the
# fingers a millimetre short of where the episode started them.
FULL_OPEN_Q = 0.04
# This task's action-space floor, per jaw (Task.__init__), = a 16 mm face gap.
CLOSED_Q = 0.008

# One control step moves the TCP at most travel_speed / 60 = 2.67 mm, so 900
# steps is >2 m of travel: more than any single move in this task.  The real
# bound on a blocked move is the stall detector below, not this number.
MOVE_STEP_LIMIT = 900
# Opening slew is 0.03 * dt per control step (Task.step), i.e. 0.5 mm here, so
# a full-range close is 64 steps.  160 leaves room for the settle.
GRIPPER_STEP_LIMIT = 160
# Control steps held after a gripper command lands.  Task.step's slew limit is
# on the *command*; a compliant ball needs a few steps more before the finger
# joint and the gel reach equilibrium, and the depth the endpoint verifies is
# the settled one.
SETTLE_STEPS = 4

# Diagnostic record written beside the seed's UIPC workspace, one row per
# control step.  Same file name the bulb task gives its physical trace, because
# it answers the same kind of question: a scorer that ends a run for a physical
# reason the operator cannot see in the call log.
TRACE_FILE = "physical_trace.jsonl"

# A commanded move that physics blocks (a ball between the pads) or that the
# arm cannot hold (a reach floor) must end the call, not burn the whole step
# budget: 10 steps without 0.15 mm of progress means the hand has stopped.
# The call then reports what the exhausted budget would have reported -- the
# pose was not reached -- in a fraction of the time.
STALL_WINDOW = 10
STALL_TOL_M = 0.00015

# Reached tolerance and dwell, matching the task's own _go() (error < .002 m,
# angle < .03 rad, 3 stable steps), less 0.5 mm.  It has to sit BELOW the
# 2 mm minimum delta move_delta accepts: the smallest correction a caller can
# ask for is 2 mm, and a tolerance at or above that answers "already there"
# before the hand moves, so the call reports success on a hand that did not
# move and the caller reads its own still pose as a lost grasp.  The 2.5 mm
# this used to be did exactly that on 2026-10-25 (seed 1): the last 2 mm of a
# 0.23 m lift went unexecuted, the program put a gripped dotted ball back and
# ended with it in the fingers.  Loaded poses still fit -- a ball pressed
# against the table settles about 1.2 mm off its target.
REACHED_M = 0.0015
REACHED_RAD = 0.03
STABLE_STEPS = 3

# What a move that stops short may still be short of its goal and count as
# executed.  REACHED_M alone cannot catch these: a hand carrying a gripped
# 60 mm ball drifts a couple of millimetres sideways over the last few
# centimetres of a descent, so the arrival branch never fires and the stall
# detector, which is right about the hand being stuck, calls a move that did
# almost everything a failure.  On 2026-10-26 (seed 2) that cost the whole
# episode: the last 9 cm at the bowl stopped 2.5-3.4 mm off its goal, the
# program's catch-all read the failure as "the delivery did not happen", flew
# the ball back to the box and dropped it there -- three times, until 240 s
# ran out.  The run that scored 1.0 the day before hit the same 3 mm
# near-miss and only scored because the next loop iteration happened to open
# the gripper while still over the bowl.
#
# 5 mm is under twice the largest residual measured, and well inside the
# distances callers check against afterwards (the bowl-centre test is 18 mm).
# It cannot be folded into REACHED_M: a tolerance at or above the 2 mm
# minimum delta answers "already there" before the hand moves (see above).
# This branch is safe where that one is not only because it requires the hand
# to have covered part of the distance it was asked for (STILL_FRACTION): on
# its own it answers "OK" to a hand that never moved, whenever the caller's
# own step is smaller than this tolerance.
SETTLE_M = 0.005

# How much of the commanded distance a move that stops inside SETTLE_M has to
# have covered before it counts as executed rather than as not executed at
# all.  SETTLE_M compares the residual with the goal and never with the
# distance that was asked for, so a caller correcting in 2-4 mm steps -- every
# step smaller than the tolerance -- is told "OK: the hand stopped 4.8 mm
# short" by a hand that did not move: the shortfall is just the request.
# 2026-10-27 (blind balls, seed 3) that ran a converge loop 65 times over
# 24.5 s of a 100 s episode, each call buying about 0.05 mm, every one of them
# a success, so the loop had nothing to stop on until its own 2 mm exit.  A
# tenth separates that (2% of the request) from every descent measured at the
# bowl, where the hand covers 40-94% and the settle branch should still fire.
STILL_FRACTION = 0.10

# Once the task latches ``_done`` (Task._score: a settled ball in the bowl, a
# failure, or the episode clock) its own ``step`` refuses to advance, so every
# later motion or gripper call is a no-op.  Those calls used to answer
# ``check_success()`` -- a plain success -- and a program could not tell a
# frozen world from a working one: 2026-10-26 trial_01 scored the moment the
# ball landed and then commanded the arm through 44 more stations (88 calls,
# 176 of the video's 293 frames) without moving it once.  Report the refusal
# instead; the client turns ``success: False`` into a RuntimeError, which is
# the only channel that can stop a program mid-run (the whole program is one
# harness step, so nothing outside it can interrupt).
EPISODE_OVER_MESSAGE = (
    "The episode is over: the task has already ended, the simulation no "
    "longer advances and the arm did not move. Every later motion or gripper "
    "call returns this same error."
)

# cfg.step_lim is reported by /reset and read by the bulb helix servo; nothing
# on the serving path consults it.  Set it to the task's own episode budget --
# episode_seconds / physics_dt -- so the number an operator reads in the reset
# response is the clock that actually ends this task (Task._score: 'timeout').
STEP_LIM = 28800

# Same two values every other ViTaForge gsmini task declares
# (envs/robot/robot_cfg.py: create_franka_gsmini_gripper), restated here
# because this TaskCfg has no ``robot`` block to carry them.  The endpoints
# read both off ``_TASK.cfg``.
TACTILE_FAR_PLANE_MM = 34.0
ADAPTIVE_DEPTH_MM = 27.5


def _quat_matrix(quat_wxyz) -> np.ndarray:
    """Rotation matrix from a wxyz quaternion (numpy or torch)."""
    q = np.asarray(quat_wxyz, dtype=np.float64).reshape(4)
    q = q / max(np.linalg.norm(q), 1e-12)
    return t3d.quaternions.quat2mat(q)


def _quat_angle(q_a, q_b) -> float:
    """Absolute angle between two wxyz quaternions, radians."""
    a = np.asarray(q_a, dtype=np.float64).reshape(4)
    b = np.asarray(q_b, dtype=np.float64).reshape(4)
    a = a / max(np.linalg.norm(a), 1e-12)
    b = b / max(np.linalg.norm(b), 1e-12)
    return float(2.0 * np.arccos(np.clip(abs(float(np.dot(a, b))), 0.0, 1.0)))


class _RobotManagerShim:
    """The RobotManager surface the endpoints read, backed by the ball task."""

    def __init__(self, adapter: "BallTaskAdapter") -> None:
        self._adapter = adapter
        self.robot = adapter.task.robot
        self.device = torch.device(adapter.task.cfg.device)
        # adaptive_set_gripper() sizes its per-step gripper command from this;
        # this adapter does not call it, but a future caller would read it.
        self._gripper_ids = adapter.task.finger_ids

    def get_ee_pose(self):
        return self._adapter.ee_pose()

    def get_gripper_center_pose(self):
        return self._adapter.gripper_center_pose()

    def gripper_center_to_ee(self, centre):
        return self._adapter.gripper_center_to_ee(centre)

    def get_gripper_qpos(self) -> float:
        return self._adapter.gripper_qpos()

    def gripper_percent2qpos(self, percent: float) -> float:
        return FULL_OPEN_Q * float(percent)

    def is_gripper_opening(self, target_qpos: float) -> bool:
        return float(target_qpos) > self.get_gripper_qpos()


class _TactileManagerShim:
    """``get_min_depth()`` over the task's two gel sensors, in millimetres."""

    def __init__(self, adapter: "BallTaskAdapter") -> None:
        self._adapter = adapter

    def get_min_depth(self) -> torch.Tensor:
        return self._adapter.get_min_depth()


class _ActorManagerShim:
    """No actors: the balls are UIPC FEM bodies with no pose query.

    Empty on purpose -- see the module docstring.  ``/object_pose`` therefore
    answers "not found" and ``_log_actor_densities`` logs an empty list.
    """

    actors: dict = {}


class BallTaskAdapter:
    """BaseTask-shaped view of ``env.tasks.blind_pick_balls.Task``."""

    def __init__(self, task, *, record_trace: bool = True) -> None:
        self.task = task
        self.plan_success = True
        # Why the last motion call failed, when the reason is not a planning
        # failure.  The endpoints read it off ``_TASK`` and put it in the
        # response's ``message``; cleared at the entry of every motion call so
        # a stale reason cannot ride along on an unrelated failure.
        self.last_motion_error = ""
        # What a call that reports success still owes the caller: how far short
        # of its goal a stalled move settled (SETTLE_M).  Same lifecycle as the
        # error above, so a note cannot ride along on a later call.
        self.last_motion_note = ""
        self._last_frame: torch.Tensor | None = None
        self._robot_manager = _RobotManagerShim(self)
        self._tactile_manager = _TactileManagerShim(self)
        self._actor_manager = _ActorManagerShim()
        # The endpoint's non-privileged perception path degrades to Nones when
        # this is absent, which is right: the task exposes no camera to the
        # policy (the observer camera is for recording only).
        self._camera_manager = None

        cfg = task.cfg
        # ``Task.__init__`` copies TaskCfg with dataclasses.replace, so a field
        # added before construction is dropped.  The serving profile carries
        # none of these four; set them on the live config instead.
        cfg.step_lim = STEP_LIM
        cfg.robot = _RobotNamespace(tactile_far_plane=TACTILE_FAR_PLANE_MM)
        cfg.use_adaptive_grasp = True
        cfg.adaptive_grasp_depth_threshold = ADAPTIVE_DEPTH_MM

        self._aim_observer_camera()

        self._trace_stream = None
        if record_trace:
            self._open_trace()

        logger.info(
            "Ball task served: %d balls %s, target=%s, episode=%.0fs",
            len(task.materials),
            [m.name for m in task.materials],
            cfg.target_class,
            cfg.episode_seconds,
        )

    def _aim_observer_camera(self) -> None:
        """Put the recording camera where the upstream runner puts it.

        ``Task.__init__``'s ``sim.set_camera_view`` only primes the viewport;
        the observer camera keeps whatever pose its cfg spawned with until
        someone sets it (env/tasks/run_blind_pick_balls.py does this before its
        first reset).  The eye and target are that file's single-ball values.
        """
        if self.task.camera is None:
            return
        self.task.camera.set_world_poses_from_view(
            torch.tensor([[0.88, -0.43, 0.90]], device=self.task.cfg.device),
            torch.tensor([[0.50, -0.08, 0.09]], device=self.task.cfg.device),
        )

    # ------------------------------------------------------------------
    # Physical trace (diagnostics only -- nothing here changes the task)
    # ------------------------------------------------------------------

    def _open_trace(self) -> None:
        """Truncate and open this process's diagnostic record.

        The file sits beside the seed's UIPC workspace, which is already keyed
        by seed, so two seeds served by two processes never share one record.
        """
        path = Path(str(self.task.cfg.workspace)).parent / TRACE_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
            # Line buffered: a SIGKILLed server still leaves every step it ran.
            self._trace_stream = path.open("a", buffering=1)
        except OSError:
            logger.exception("Ball adapter: cannot write %s; tracing disabled",
                             path)
            self._trace_stream = None
        else:
            logger.info("Ball physical trace: %s", path)

    def _close_trace(self) -> None:
        stream, self._trace_stream = self._trace_stream, None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass

    def _trace(self, event: str | None = None, **extra: object) -> None:
        """Append one row holding everything the scorer's held test reads.

        The held test is ``loaded.all()`` on the two pads' true front-face
        force, the ball's lowest vertex above the rim, and its centroid within
        45 mm of the TCP, sustained for 0.15 s; ``grasp_verified`` latches when
        that happens and the delivery demands the latch.  The oracle can only
        see the gel depths, which are already known to decouple from the face
        force, so a run that ends in ``unverified_grasp`` cannot be explained
        from its own log.  This row carries all of it per control step.

        ``event`` names a lifecycle row (``reset``) rather than a step, and
        ``extra`` adds fields to it.  Never raises: a diagnostics failure must
        not change what the task does, so the first error disables tracing.
        """
        if self._trace_stream is None:
            return
        try:
            task = self.task
            tcp = task._tcp()[0][0].cpu().numpy()
            patches = []
            for patch in getattr(task.physics, "patches", (None, None)):
                patches.append(None if patch is None else
                               [round(float(v), 4)
                                for v in np.asarray(patch, float) - tcp])
            balls = []
            for i in range(len(task.materials)):
                points = task.physics.ball_points(i)
                balls.append([
                    round(float(points[:, 2].min()), 4),
                    round(float(np.linalg.norm(points.mean(axis=0) - tcp)), 4)])
            row = {
                "step": int(task.step_count),
                "q_cmd": round(float(task._opening), 5),
                "q_act": round(float(task.robot.data.joint_pos[
                    0, task.finger_ids].mean()), 5),
                "tcp": [round(float(v), 4) for v in tcp],
                "loads": [round(float(v), 3) for v in np.linalg.norm(
                    task.physics.force, axis=1)],
                # The same number get_min_depth() serves: the deepest point of
                # the gel's height map.  task.physics.depths is the indentation
                # map in metres, and its own minimum is 0 everywhere the gel is
                # at rest, so the statistic wanted there is the maximum.
                "depths": [round(float(v), 3)
                           for v in self.get_min_depth().cpu().numpy()],
                "patches": patches,
                "balls": balls,
                "held": {str(i): int(n) for i, n in enumerate(task._held) if n},
                "verified": [i for i, v in enumerate(task._grasp_verified) if v],
                "failure": task.failure,
            }
            row.update(extra)
            if event is not None:
                row["event"] = event
            self._trace_stream.write(json.dumps(row) + "\n")
        except Exception:  # noqa: BLE001 - see the docstring
            logger.exception("Ball adapter: physical trace failed; disabled")
            self._close_trace()

    # ------------------------------------------------------------------
    # Interface the endpoints read
    # ------------------------------------------------------------------

    @property
    def cfg(self):
        return self.task.cfg

    @property
    def step_count(self) -> int:
        # Physics steps, as on every other task (Task._physics_step).
        return int(self.task.step_count)

    @property
    def choice(self):
        """What /reset logs as the episode's ground truth line.

        This task's public goal is the target class, not one ball's material
        (there are four textured balls among the twelve), so that is what the
        calibration log records.
        """
        return self.task.cfg.target_class

    @property
    def failure(self) -> str:
        return self.task.failure

    # ------------------------------------------------------------------
    # Poses: hand link (what the API speaks) <-> TCP (what the task commands)
    # ------------------------------------------------------------------

    def ee_pose(self):
        """Current hand-link pose in the robot base frame (== world here)."""
        from envs.utils.transforms import Pose
        from isaaclab.utils.math import subtract_frame_transforms

        task = self.task
        hand_p = task.robot.data.body_link_pos_w[0, task.hand_body]
        hand_q = task.robot.data.body_link_quat_w[0, task.hand_body]
        root_p = task.robot.data.root_link_pos_w[0]
        root_q = task.robot.data.root_link_quat_w[0]
        # The Franka root is authored at the world origin in this task, so the
        # two frames coincide; the subtraction keeps that an observation
        # rather than an assumption.
        p_b, q_b = subtract_frame_transforms(root_p, root_q, hand_p, hand_q)
        return Pose(p_b.cpu().numpy(), q_b.cpu().numpy())

    def gripper_center_pose(self):
        """The task's TCP -- the centre of the two sensing surfaces."""
        from envs.utils.transforms import Pose

        tcp, quat, _ = self.task._tcp()
        return Pose(tcp[0].cpu().numpy(), quat[0].cpu().numpy())

    def gripper_center_to_ee(self, centre):
        from envs.utils.transforms import Pose

        p = np.asarray(centre.p, dtype=np.float64) - _quat_matrix(centre.q) @ self._tcp_offset()
        return Pose(p, np.asarray(centre.q, dtype=np.float64))

    def _tcp_offset(self) -> np.ndarray:
        """Hand-frame offset to the sensing-surface centre, from the last reset."""
        return np.asarray(self.task.tcp_offset, dtype=np.float64).reshape(3)

    def _tcp_from_ee(self, p_ee, q_ee) -> np.ndarray:
        return np.asarray(p_ee, dtype=np.float64).reshape(3) + _quat_matrix(q_ee) @ self._tcp_offset()

    def _ee_from_tcp(self, p_tcp, q) -> np.ndarray:
        return np.asarray(p_tcp, dtype=np.float64).reshape(3) - _quat_matrix(q) @ self._tcp_offset()

    # ------------------------------------------------------------------
    # Gripper
    # ------------------------------------------------------------------

    def gripper_qpos(self) -> float:
        return float(self.task.robot.data.joint_pos[0, self.task.finger_ids].mean())

    def get_min_depth(self) -> torch.Tensor:
        """Per-pad minimum of the raw gel height map, millimetres.

        Read straight out of the sensor buffers: ``Task.step`` ends with an
        observation, which forces both gels to re-render, so the value is the
        one the step just produced.  Order is (left, right).
        """
        depths = [
            float(sensor.sensor.data.output["height_map"].min())
            for sensor in self.task.physics.sensors
        ]
        return torch.tensor(depths, dtype=torch.float32, device=self.task.cfg.device)

    def height_roughness_mm(self) -> torch.Tensor:
        """Per-pad fine relief of the gel indentation map, millimetres.

        The indentation map is the gel's own compression, and this is the RMS
        residual left after a smooth quadratic contact envelope is fitted to
        it and removed: the imprint of a sphere is a clean cap whatever its
        stiffness, while a raised surface leaves its bumps behind.  It is the
        statistic the task's own expert classifies on.  Read straight out of
        the buffers the last observation produced, like ``get_min_depth``.
        Order is (left, right).
        """
        from env.tasks._blind_ball_models import surface_roughness

        return torch.tensor(
            [1000.0 * surface_roughness(height) for height in self.task.physics.depths],
            dtype=torch.float32,
            device=self.task.cfg.device,
        )

    def _opening_ramp(self) -> float:
        """The per-control-step opening slew Task.step will allow."""
        return 0.03 * self.task.cfg.physics_dt * self.task.cfg.action_repeat

    def _refuse_if_over(self) -> bool:
        """True once the episode has ended, leaving the reason for the endpoint.

        Checked inside the motion loops rather than at the entry of the public
        methods because a call can straddle the end of the episode: the step
        that settles the ball into the bowl both moves the arm and latches
        ``_done``, and everything after that step must report that it did not
        run.
        """
        if not self.task._done:
            return False
        self.last_motion_error = EPISODE_OVER_MESSAGE
        return True

    def set_gripper(self, percent: float, depth_threshold: float | None = None) -> bool:
        """Drive the finger opening to ``percent`` of full open.

        With ``depth_threshold`` (an adaptive close) the ramp stops as soon as
        **both** pads read below it, which is what
        ``_base_task.adaptive_set_gripper`` does for every other task.  The
        task's own slew limit is the landing resolution: the command can only
        move 0.5 mm per control step, so there is no jump to servo out of and
        ``grasp_servo``'s precise-landing branch has nothing to add here.
        """
        self.last_motion_error = ""
        self.last_motion_note = ""
        target = float(np.clip(FULL_OPEN_Q * float(percent), CLOSED_Q, FULL_OPEN_Q))
        closing = target < float(self.task._opening) - 1e-9
        stop = None
        if closing and depth_threshold is not None:
            threshold = float(depth_threshold)

            def stop() -> bool:
                return bool((self.get_min_depth() < threshold).all())

        landed = self._drive_gripper(target, stop=stop)
        if landed:
            self._hold(SETTLE_STEPS)
        return landed

    def _drive_gripper(self, target: float, stop=None, limit: int = GRIPPER_STEP_LIMIT) -> bool:
        task = self.task
        opening = float(task._opening)
        for _ in range(limit):
            if self._refuse_if_over():
                return False
            if stop is not None and stop():
                return True
            if abs(opening - target) <= 1e-9:
                return True
            opening = (min(opening + self._opening_ramp(), target) if target > opening
                       else max(opening - self._opening_ramp(), target))
            self._command(task._goal, opening)
        return False

    # ------------------------------------------------------------------
    # Motion
    # ------------------------------------------------------------------

    def _command(self, tcp_goal, opening: float | None = None) -> None:
        """One control step: hold the TCP goal, hold (or set) the opening."""
        q = float(self.task._opening) if opening is None else float(opening)
        self.task.step(np.r_[np.asarray(tcp_goal, dtype=np.float64).reshape(3),
                             float(np.clip(q, CLOSED_Q, FULL_OPEN_Q))])
        self._trace()

    def _hold(self, steps: int) -> None:
        for _ in range(int(steps)):
            if self.task._done:
                return
            self._command(self.task._goal)

    def move(self, actions, tag: str = "move", is_save: bool = True,
             delay: bool = True, **_: object) -> bool:
        """Execute a list of ``envs.utils.atom.Action`` objects.

        Mirrors ``_base_task.move``'s contract for the endpoints: return False
        and latch ``plan_success`` when an action cannot be executed.  The
        server resets ``plan_success`` itself after a failure, so nothing here
        stays latched across calls.
        """
        self.last_motion_error = ""
        self.last_motion_note = ""
        if not self.plan_success:
            return False
        for action in actions:
            kind = getattr(action, "action", None)
            if kind in ("move", "all"):
                pre = float(action.args.get("pre_dis") or 0.0)
                pose = action.target_pose
                if not self.goto(pose.p, pose.q, pre_dis=pre):
                    logger.warning("Ball adapter: motion to %s failed (tag=%s)",
                                   np.round(np.asarray(pose.p), 3).tolist(), tag)
                    self.plan_success = False
                    return False
            if kind in ("gripper", "all"):
                depth = action.args.get("gripper_depth_threshold")
                if not self.set_gripper(float(action.target_gripper_pos), depth):
                    logger.warning("Ball adapter: gripper action failed (tag=%s)", tag)
                    self.plan_success = False
                    return False
        self._update_render()
        return True

    def goto(self, p_ee, q_ee, pre_dis: float = 0.0,
             limit: int = MOVE_STEP_LIMIT) -> bool:
        """Move the hand to ``(p_ee, q_ee)``, optionally via a world +z rise.

        ``pre_dis`` keeps the API docstring's meaning -- "first moves to
        position + z_approach above the target, then descends" -- and is the
        only approach geometry used here, since the task has no planner to
        hand an approach axis to.
        """
        p_ee = np.asarray(p_ee, dtype=np.float64).reshape(3)
        q_ee = np.asarray(q_ee, dtype=np.float64).reshape(4)
        q_ee = q_ee / max(np.linalg.norm(q_ee), 1e-12)
        if pre_dis > 1e-6:
            if not self._goto_segment(p_ee + np.array([0.0, 0.0, pre_dis]), q_ee, limit):
                return False
        return self._goto_segment(p_ee, q_ee, limit)

    def _goto_segment(self, p_ee, q_ee, limit: int) -> bool:
        task = self.task
        # Each segment is judged on its own goal: a note from a previous
        # segment ("stopped 3 mm short") must not describe this one.
        self.last_motion_note = ""
        goal = self._tcp_from_ee(p_ee, q_ee)
        low = np.asarray(task.action_space.low[:3], dtype=np.float64)
        high = np.asarray(task.action_space.high[:3], dtype=np.float64)
        clipped = np.clip(goal, low, high)
        if float(np.linalg.norm(clipped - goal)) > 1e-6:
            logger.warning(
                "Ball adapter: TCP goal %s clipped to the task workspace %s",
                np.round(goal, 3).tolist(), np.round(clipped, 3).tolist())
        goal = clipped

        task._orientation = torch.as_tensor(
            q_ee, dtype=torch.float32, device=task.cfg.device)[None]

        # Where the hand is when the call arrives -- before the first command,
        # so a fast first step cannot shrink what was asked for.  Both the
        # travel reported to the caller and the share of the request a settled
        # move has to cover (STILL_FRACTION) are measured from here.
        start = task._tcp()[0][0].cpu().numpy()
        requested = float(np.linalg.norm(goal - start))
        stable = 0
        history: list[float] = []
        for _ in range(limit):
            if self._refuse_if_over():
                return False
            self._command(goal)
            actual, quat, _ = task._tcp()
            p_now = actual[0].cpu().numpy()
            err = float(np.linalg.norm(p_now - goal))
            angle = _quat_angle(quat[0].cpu().numpy(), q_ee)
            if err < REACHED_M and angle < REACHED_RAD:
                stable += 1
                if stable >= STABLE_STEPS:
                    return True
            elif angle < REACHED_RAD * 2.0:
                # Blocked, or a pose the arm cannot hold: a ball between the
                # pads, a wall, a joint limit folding the hand away from the
                # goal.  Stop as soon as the TCP stops making progress
                # instead of spending the whole budget in place -- the
                # window has to be accumulated here, which is why the three
                # counters are separate (a 2026-10-24 descent that stalled
                # 16 mm short burned all 900 steps with the window cleared
                # on every sample it was meant to fill).
                stable = 0
                history.append(err)
                if len(history) > STALL_WINDOW:
                    recent = history[-STALL_WINDOW:]
                    if max(recent) - min(recent) < STALL_TOL_M:
                        progress = float(np.linalg.norm(p_now - start))
                        covered = progress / requested if requested > 1e-9 else 1.0
                        if err <= SETTLE_M and covered >= STILL_FRACTION:
                            # The hand came to rest a few millimetres from the
                            # goal having covered a real part of the distance it
                            # was asked for: the motion happened, and the
                            # caller's own pose check is what decides the rest
                            # (SETTLE_M, STILL_FRACTION).  It is told where the
                            # hand stopped regardless.
                            logger.info(
                                "Ball adapter: motion settled %.1f mm from the "
                                "goal after %.1f mm of travel",
                                err * 1000.0, progress * 1000.0)
                            self.last_motion_note = (
                                f"OK: the hand stopped {err * 1000.0:.1f} mm "
                                "short of the commanded point and would not go "
                                "closer.")
                            return True
                        if err <= SETTLE_M:
                            # A goal this close, and the hand covered next to
                            # none of it: nothing was executed.  Answering OK
                            # here is what keeps a caller's converge loop
                            # running forever on a hand that is not moving,
                            # because the shortfall it reports is the caller's
                            # own step (STILL_FRACTION).
                            logger.warning(
                                "Ball adapter: no motion -- %.1f mm covered of "
                                "%.1f mm commanded, %.1f mm from the goal",
                                progress * 1000.0, requested * 1000.0,
                                err * 1000.0)
                            self.last_motion_error = (
                                f"The hand did not move: {requested * 1000.0:.1f} "
                                f"mm was commanded, it covered "
                                f"{progress * 1000.0:.1f} mm of that and stayed "
                                f"{err * 1000.0:.1f} mm from the point.  A move "
                                "this small is not executed at this pose -- read "
                                "the pose as it stands, or command a larger "
                                "move.")
                            return False
                        logger.warning(
                            "Ball adapter: motion stalled %.1f mm from the "
                            "goal after %.1f mm of travel",
                            err * 1000.0, progress * 1000.0)
                        # Same verdict the exhausted budget would give -- the
                        # pose was not reached -- only reached in seconds.
                        self.last_motion_error = (
                            f"The hand travelled {progress * 1000.0:.0f} mm "
                            f"and stopped {err * 1000.0:.1f} mm short of the "
                            "commanded point, and did not move again while the "
                            "goal was held.")
                        return False
            else:
                # Still turning toward the commanded orientation: not stuck.
                stable = 0
                history.clear()
        return False

    # ------------------------------------------------------------------
    # Observation / rendering
    # ------------------------------------------------------------------

    def _get_observations(self) -> dict:
        """Tactile observations in the shape every endpoint parses."""
        # Cheap when the cache is warm (it is after every Task.step); this is
        # what forces both gels to re-render when it is not.
        self.task.physics.observation()
        tactile = {}
        for sensor in self.task.physics.sensors:
            tactile[f"{sensor.name}_tactile"] = sensor.get_observations(
                ["rgb_marker", "depth", "marker"])
        return {"tactile": tactile}

    def _update_render(self) -> None:
        """Refresh the recording camera and cache the frame."""
        if self.task.camera is None:
            self._last_frame = None
            return
        self._last_frame = torch.as_tensor(self.task.render(), dtype=torch.uint8)

    def get_frame_shot(self, obs: dict) -> torch.Tensor:
        """Head view (320x480) plus the two gel images, 160x160 each.

        Same layout as ``_base_task.get_frame_shot`` with the wrist column
        dropped: this task has no wrist camera, and its policy observation
        carries no scene image at all.
        """
        scene = self._last_frame
        if scene is None:
            scene = torch.zeros((320, 480, 3), dtype=torch.uint8)
        canvas = torch.zeros((320, 640, 3), dtype=torch.uint8)
        canvas[:, :480] = _resize_hwc(scene, 320, 480)
        tactile = obs.get("tactile", {})
        canvas[:160, 480:] = _resize_hwc(
            tactile.get("left_tactile", {}).get("rgb_marker"), 160, 160)
        canvas[160:, 480:] = _resize_hwc(
            tactile.get("right_tactile", {}).get("rgb_marker"), 160, 160)
        return canvas

    # ------------------------------------------------------------------
    # Episode
    # ------------------------------------------------------------------

    def reset(self, seed: int | None = None, options: dict | None = None):
        self.plan_success = True
        self._last_frame = None
        observation, info = self.task.reset(seed=seed, options=options)
        self._aim_observer_camera()
        self._trace(
            "reset",
            seed=seed,
            target_class=self.task.cfg.target_class,
            materials=[dict(name=m.name, kind=m.kind)
                       for m in self.task.materials],
            tcp_offset=[round(float(v), 5) for v in self._tcp_offset()])
        logger.info(
            "Ball task reset: seed=%s target=%s TCP offset=%s",
            seed, self.task.cfg.target_class,
            np.round(self._tcp_offset(), 4).tolist())
        return observation, info

    def check_success(self) -> bool:
        return bool(self.task.check_success())

    def check_early_stop(self) -> bool:
        return False

    def check_success_detail(self) -> dict:
        return self.task._info()

    def close(self) -> None:
        self._close_trace()
        self.task.close()


class _RobotNamespace:
    """Stand-in for ``cfg.robot``, which this TaskCfg does not have."""

    __slots__ = ("tactile_far_plane",)

    def __init__(self, **kwargs: object) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


def _resize_hwc(image, height: int, width: int) -> torch.Tensor:
    """Bilinear resize of an HWC image tensor to (height, width), uint8."""
    if image is None:
        return torch.zeros((height, width, 3), dtype=torch.uint8)
    x = torch.as_tensor(image).detach()
    if x.dim() == 2:
        x = x.unsqueeze(-1)
    if x.shape[-1] == 1:
        x = x.expand(-1, -1, 3)
    x = x[..., :3].to(torch.float32).permute(2, 0, 1).unsqueeze(0)
    x = torch_F.interpolate(x, size=(height, width), mode="bilinear", align_corners=False)
    return x[0].permute(1, 2, 0).clamp(0, 255).to(torch.uint8)
