"""UniVTAC tactile manipulation API for LLM code generation.

Functions in this class are called directly by LLM-generated Python code
during trial execution.  Each function's docstring appears verbatim in the
LLM prompt (via :meth:`ApiBase.combined_doc`), so it describes what the
function and its arguments ARE: ranges, units, hard limits, and the fields
the return carries.  How to USE a function on a task -- which reading to
take, what to compare it against, which trap the obvious version falls
into -- is not written here.  That lives in the tactile skill library
(``capx.tactile_skills``), which is delivered to the task alongside this doc,
so each rule has one owner instead of a copy in every prompt.  Functions
listed in ``_prompt_hidden`` stay callable at execution time but are excluded
from the prompt (see ``check_success``).

All calls are forwarded to the UniVTAC API server over HTTP.
"""

from __future__ import annotations

import base64
import io
import os
import time
from typing import Any

import numpy as np
import requests
from PIL import Image

from capx.envs.base import BaseEnv
from capx.integrations.base_api import ApiBase
from capx.integrations.tactile import trace as tactile_trace

# Default server URL — override via env var UNIVTAC_SERVER_URL
_DEFAULT_SERVER = "http://127.0.0.1:8120"

# Read timeout for motion POSTs.  Must exceed the server-side _MOTION_TIMEOUT
# (1500 s, launch_univtac_server.py) plus its dispatch grace, so a merely-slow
# physics op returns its real response rather than being cut off here.  Raising
# only the server side would turn a slow op into a client-side ReadTimeout,
# which runner.py treats as a retryable timeout and restarts the whole trial
# from depth 0.  Raise both or neither.
_READ_TIMEOUT = 1800.0

# Scalar tactile fields returned by the motion primitives (goto_pose,
# move_delta, open/close_gripper, rotate_gripper).  Every reading this API
# documents is built from these — contact and depth (hardness), marker shear
# (weight), marker anisotropy and imprint relief (texture) — so a close or a
# lift carries its own reading instead of forcing a get_tactile_data() call
# straight afterwards.  The images are the one thing withheld: those are what
# that call is for.
_MOTION_TACTILE_FIELDS = (
    "left_contact",
    "right_contact",
    "left_depth_mm",
    "right_depth_mm",
    "left_marker_displacement_px",
    "right_marker_displacement_px",
    "left_marker_coherence",
    "right_marker_coherence",
    "left_marker_shear_dx",
    "left_marker_shear_dy",
    "right_marker_shear_dx",
    "right_marker_shear_dy",
    "left_height_roughness_mm",
    "right_height_roughness_mm",
)


def _settled_note(data: dict) -> str:
    """The server's success note, ready to append to a motion tool's print.

    A motion that succeeds normally answers "OK".  The blind-ball adapter
    also reports a move that came to rest a few millimetres short of its goal
    as executed, and says where the hand actually stopped; that sentence is a
    fact about the world the caller acts on, so it goes out with the tool's
    own line instead of staying in the server log.
    """
    note = (data or {}).get("message") or ""
    return f" - {note}" if note and note != "OK" else ""


def _trim_motion_tactile(data: dict) -> dict:
    """Keep the scalar tactile readings from a full tactile dict.

    The marker images and depth images are dropped.  Anisotropy rides along
    because the server now computes it; a response that predates that still
    gets it derived from the marker image, which comes back with every motion,
    so the return carries every scalar either way.
    """
    out = {k: data[k] for k in _MOTION_TACTILE_FIELDS if k in data}
    for side in ("left", "right"):
        key = f"{side}_marker_anisotropy"
        if key in data:
            out[key] = data[key]
            continue
        try:
            value = _marker_anisotropy(data.get(f"{side}_rgb_base64", ""))
        except Exception:
            # A motion call must never fail over a derived scalar.
            value = None
        if value is not None:
            out[key] = value
    return out


def _fmt(value: float | None, spec: str = ".2f") -> str:
    """One log-line number, with the missing case spelled out."""
    return "n/a" if value is None else format(value, spec)


def _marker_anisotropy(b64: str) -> float | None:
    """Row-to-row over column-to-column gradient of a marker image.

    Every scalar the API exposes for the gel is an average over the image,
    and an average cannot see spatial structure — which is exactly where
    surface roughness shows up: pressed against a rough surface the gel is
    imprinted with horizontal banding, which raises the row gradient while
    leaving the column gradient alone.  A plain surface deforms the gel
    evenly and the image stays near isotropic.  Returns 1.0 for an
    isotropic (unloaded or evenly loaded) gel, and None when the image is
    unavailable.

    The server computes this same reading from the same image and sends it
    with every tactile payload; this is the fallback for a response that
    carries no value of its own.  Both paths quantize identically, so the
    number does not depend on which one produced it.
    """
    if not b64:
        return None
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    g = np.asarray(img.convert("RGB"), dtype=np.float64).mean(axis=2)
    row = float(np.abs(np.diff(g, axis=0)).mean())
    col = float(np.abs(np.diff(g, axis=1)).mean())
    return row / col if col > 1e-9 else None


class UniVTACTactileApi(ApiBase):
    """Robot control API for a Franka arm with GelSight Mini tactile sensors.

    Provides motion, tactile sensing, and perception primitives for
    contact-rich manipulation tasks (insertion, grasping, placement).
    The robot is controlled via Cartesian end-effector poses solved
    through inverse kinematics.  Tactile feedback from the left and
    right GelSight Mini fingertips is available after every motion.
    """

    # Hidden from the LLM prompt: check_success is a ground-truth oracle.
    # The framework appends a final check_success() call after code generation
    # (CodeExecEnvConfig.append_final_check) and computes the reward itself
    # after each step, so the agent neither sees nor needs this function.
    _prompt_hidden = {"check_success"}

    # This class is the privileged line: get_object_pose reads /object_pose,
    # the simulator's ground truth.  It may therefore ask the server for a
    # grasp pose built from that ground truth, which is the only thing that
    # works on a task whose pre_move does not leave the EE above the object.
    # UniVTACPerceptionApi, the SAM3 line, keeps the EE-relative pose instead.
    _ground_truth_grasp = True

    def __init__(self, env: BaseEnv, server_url: str | None = None) -> None:
        super().__init__(env)
        self._server = server_url or os.environ.get(
            "UNIVTAC_SERVER_URL", _DEFAULT_SERVER
        )
        # Reference (L, R) marker displacements for delta-based slip detection.
        # None until the first detect_slip call establishes it.
        self._slip_baseline: tuple[float, float] | None = None

    # ------------------------------------------------------------------
    # ApiBase interface
    # ------------------------------------------------------------------

    def functions(self) -> dict[str, Any]:
        return {
            "goto_pose": self.goto_pose,
            "move_delta": self.move_delta,
            "open_gripper": self.open_gripper,
            "close_gripper": self.close_gripper,
            "get_tactile_data": self.get_tactile_data,
            "detect_slip": self.detect_slip,
            "get_object_pose": self.get_object_pose,
            "sample_grasp_pose": self.sample_grasp_pose,
            "rotate_gripper": self.rotate_gripper,
            "check_success": self.check_success,
        }

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _server_health(self, timeout: float = 3.0) -> bool:
        """Best-effort GET /health probe — True only on an HTTP 200."""
        try:
            r = requests.get(f"{self._server}/health", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False

    def _post(self, path: str, body: dict | None = None) -> dict:
        started = time.monotonic()
        try:
            r = requests.post(
                f"{self._server}{path}",
                json=body or {},
                # Must exceed the server-side _MOTION_TIMEOUT (1500 s) plus its
                # 5 s dispatch grace, so a merely-slow op surfaces as a clean
                # HTTP 5xx instead of a client-side hang.  A true C++ freeze
                # (GIL held — even /health cannot answer) is killed by the
                # external watchdog after its 120 s grace, which lands well
                # inside this timeout, so the client sees a fast connection
                # reset rather than the full 1800 s wait.
                timeout=_READ_TIMEOUT,
            )
            r.raise_for_status()
        except requests.exceptions.ReadTimeout:
            # A ReadTimeout does NOT imply the full read timeout elapsed.
            # urllib3 catches socket.timeout, and on Python 3.10+ socket.timeout
            # IS TimeoutError — so the harness's own wall-clock alarm
            # (runner.py raises TimeoutError from its SIGALRM handler) is
            # converted into this exception the moment it fires, however few
            # seconds the request had been waiting.  Measure before blaming
            # the server: 2026-10-02 a bulb trial was reported as "did not
            # respond within 600s" when the request had been out for 42 s and
            # the alarm had simply gone off.
            waited = time.monotonic() - started
            if waited < _READ_TIMEOUT * 0.9:
                raise RuntimeError(
                    f"POST {path} was abandoned after {waited:.0f}s, far short\n"
                    f"of the {_READ_TIMEOUT:.0f}s read timeout — an interrupted\n"
                    f"wait, not a slow server.  The usual cause is the harness's\n"
                    f"own wall-clock alarm (SIGALRM -> TimeoutError -> urllib3's\n"
                    f"socket.timeout -> ReadTimeout).  The sim op is still\n"
                    f"running on the server's main thread and will still write\n"
                    f"its rows to physical_trace.jsonl; do not assume it did\n"
                    f"nothing, and do not retry it blind.\n"
                    f"Request: {body}"
                )
            if self._server_health():
                raise RuntimeError(
                    f"Server did not respond within {_READ_TIMEOUT:.0f}s for "
                    f"POST {path}, but\n"
                    f"/health now answers — the sim op was extremely slow or\n"
                    f"stuck-and-recovered; its effect on the scene is unknown.\n"
                    f"Request: {body}\n"
                    f"This usually means the motion planner (cuRobo IK) took a\n"
                    f"degenerate query.  Re-query the object pose before going\n"
                    f"on; do not assume the motion did or did not happen."
                )
            raise RuntimeError(
                f"Server did not respond within {_READ_TIMEOUT:.0f}s for POST "
                f"{path} and is\n"
                f"FROZEN (/health silent) — a C++/CUDA hang holding the GIL.\n"
                f"Request: {body}\n"
                f"The external watchdog (120 s grace) will SIGKILL it and the\n"
                f"supervisor respawns it; Isaac cold-start takes minutes.\n"
                f"Treat this trial as lost and do NOT retry the motion blindly:\n"
                f"the sim state at the freeze is undefined."
            )
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Cannot connect to simulation server at {self._server}{path}.\n"
                f"The server may not be started, may have crashed, or the freeze\n"
                f"watchdog may have just SIGKILLed it (the supervisor respawns it\n"
                f"automatically, but Isaac cold-start takes minutes) — wait for\n"
                f"/health to answer before retrying."
            )
        except requests.exceptions.HTTPError as e:
            # Server returned HTTP 4xx/5xx — extract detail if available
            detail = ""
            try:
                detail = e.response.text[:500]
            except Exception:
                pass
            raise RuntimeError(
                f"Server returned HTTP {e.response.status_code} for POST {path}.\n"
                f"Request: {body}\n"
                f"Server response: {detail}"
            )
        data = r.json()
        # Recorded before the success check: a motion the server refused
        # still carries the tactile state that made it refuse, and that
        # state is usually the half of the row worth keeping.
        tactile = data.get("tactile")
        if isinstance(tactile, dict):
            tactile_trace.record(path, tactile, body)
        if not data.get("success", True):
            msg = data.get("message", "Unknown motion error")
            raise RuntimeError(f"Motion failed: {msg}")
        return data

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            r = requests.get(
                f"{self._server}{path}",
                params=params or {},
                # Query watchdog server-side is 30s + 5s dispatch grace; the
                # client must outwait it to see the clean 503 response.
                timeout=40,
            )
            r.raise_for_status()
        except requests.exceptions.ReadTimeout:
            state = ("now answers (a slow op recovered)" if self._server_health()
                     else "FROZEN (/health silent — the watchdog will SIGKILL "
                          "and the supervisor respawn it)")
            raise RuntimeError(
                f"Server did not respond within 30s for GET {path}; server "
                f"{state}.\n"
                f"The simulation may be stuck — try resetting the environment."
            )
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Cannot connect to simulation server at {self._server}{path}.\n"
                f"The server may have crashed or not been started yet."
            )
        except requests.exceptions.HTTPError as e:
            detail = ""
            try:
                detail = e.response.text[:500]
            except Exception:
                pass
            raise RuntimeError(
                f"Server returned HTTP {e.response.status_code} for GET {path}.\n"
                f"Server response: {detail}"
            )
        return r.json()

    # ------------------------------------------------------------------
    # Motion API
    # ------------------------------------------------------------------

    def goto_pose(
        self,
        position: np.ndarray,
        quaternion_wxyz: np.ndarray,
        z_approach: float = 0.0,
    ) -> dict:
        """Move the end-effector to a target pose via inverse kinematics.

        This commands the END-EFFECTOR, not a held object: the two differ by
        the grasp offset, so a target for a held object is not a target for
        this call.  The position is the wrist flange; the gripper centre lies
        0.131 m from it along the hand's own z axis, toward the fingers, and
        the pads reach about 2.5 mm further along that axis.  With the fingers
        pointing down, the lowest point of the pads is therefore about 0.134 m
        below the commanded position.  When the commanded pose would drive a
        held object into the table, the planner refuses with
        "Motion planning failed".

        Args:
            position: (3,) XYZ in meters.
            quaternion_wxyz: (4,) WXYZ unit quaternion.
            z_approach: (float) Z-axis approach distance in meters. The
                end-effector first moves to position + z_approach above the
                target, then descends to the requested pose. Default is 0.0.

        Returns:
            dict with the scalar tactile readings after the move, per side:
            contact, depth_mm, marker_displacement_px, marker_shear_dx,
            marker_shear_dy, marker_coherence and marker_anisotropy.
        """
        data = self._post(
            "/move",
            {
                "position": np.asarray(position, dtype=float).tolist(),
                "quaternion_wxyz": np.asarray(quaternion_wxyz, dtype=float).tolist(),
                "z_approach": float(z_approach),
                "tag": "goto_pose",
            },
        )
        print(f"Moved to position={np.round(position, 3)} "
              f"quat_w={quaternion_wxyz[0]:.3f} z_approach={z_approach}"
              + _settled_note(data))
        return _trim_motion_tactile(data.get("tactile", {}))

    def move_delta(
        self,
        dx: float = 0.0, dy: float = 0.0, dz: float = 0.0,
        drx: float = 0.0, dry: float = 0.0, drz: float = 0.0,
        coord: str = "world",
    ) -> dict:
        """Move the end-effector by a small delta. Primary primitive for
        contact-rich tasks (insertion, alignment).

        Limits per call: max 0.10 m translation per axis, max 0.5 rad rotation;
        larger moves MUST be split into multiple calls. There is also a MINIMUM
        of 0.002 m (2 mm) translation and 0.02 rad per call: a delta below it on
        EVERY axis cannot be executed and raises instead of moving nothing, so
        no closed-loop descent can converge to a tolerance finer than 2 mm. The
        target Z must stay above the table (Z >= 0); violating these limits is
        rejected.

        Args:
            dx: (float) X translation in meters.
            dy: (float) Y translation in meters.
            dz: (float) Z translation in meters.
            drx: (float) X rotation in radians.
            dry: (float) Y rotation in radians.
            drz: (float) Z rotation in radians.
            coord: ("world" or "ee") Reference frame for the whole delta,
                rotation included. Under "world" a rotation turns the
                end-effector's position about the world origin as well as its
                orientation, so the hand sweeps an arc whose radius is its
                distance from that origin; under "ee" the same rotation is
                about the end-effector itself and its position is left alone.

        Returns:
            dict with the scalar tactile readings after the move (same set as
            goto_pose: contact, depth, displacement, shear, coherence and
            anisotropy per side).
        """
        data = self._post(
            "/move_delta",
            {
                "dx": float(dx),
                "dy": float(dy),
                "dz": float(dz),
                "drx": float(drx),
                "dry": float(dry),
                "drz": float(drz),
                "coord": coord,
                "tag": "move_delta",
            },
        )
        print(f"Moved delta: dx={dx:.3f} dy={dy:.3f} dz={dz:.3f} "
              f"drx={drx:.3f} dry={dry:.3f} drz={drz:.3f} coord={coord}"
              + _settled_note(data))
        return _trim_motion_tactile(data.get("tactile", {}))

    def move_reflexive(
        self,
        dx: float = 0.0, dy: float = 0.0, dz: float = 0.0,
        drx: float = 0.0, dry: float = 0.0, drz: float = 0.0,
        coord: str = "world",
        stop_on: dict | None = None,
    ) -> dict:
        """Move by a small delta, stopping early when a tactile event fires.

        The fast loop runs INSIDE the motion: the server evaluates tactile
        events on every simulation step and freezes the arm at the first
        enabled one, so you do not need to poll between small steps.  Use it
        instead of a slow while-loop of move_delta calls for contact-
        critical motions: approach until first touch, insert until seated,
        press until jammed, hold-and-check for slip.

        Limits per call are the same as move_delta (max 0.10 m translation
        per axis, max 0.5 rad rotation, target Z >= 0).  Each step needs a
        full render (~2 FPS with the reflex active), so keep deltas <=
        0.05 m per call and expect the call to take longer than move_delta.

        stop_on keys (all ORed; a key absent from the dict stays disabled):
            contact=True         stop when either fingertip touches
                                 (depth < ~34 mm far plane)
            hard_depth=<mm>      stop when either depth <= threshold
                                 (20.0 default = hard jam)
            shear_spike=<px>     stop when per-step marker displacement
                                 GROWS >= threshold (2.0 default) with
                                 coherence >= 0.7 (friction event)
            slip=<deg>           stop when the shear direction turns >= deg
                                 between steps (30.0 default; direction
                                 aware — the improvement over detect_slip)
            asymmetry=<mm>       stop when |L_depth - R_depth| >= mm
                                 (3.0 default; yaw misalignment)
            plateau={"mm":0.5,"steps":5}  stop when contact is flat
                                 (depth change < mm over N steps; jammed or
                                 seated signal)
            max_steps=<n>        safety net: stop after n evaluations

        Success means "reached the target OR stopped on a condition" —
        always check terminated_by in the return to know which.  The motion
        never retries after a reflex trigger.

        Args:
            dx: (float) X translation in meters.
            dy: (float) Y translation in meters.
            dz: (float) Z translation in meters.
            drx: (float) X rotation in radians.
            dry: (float) Y rotation in radians.
            drz: (float) Z rotation in radians.
            coord: ("world" or "ee") Reference frame for the whole delta,
                rotation included; see move_delta.
            stop_on: (dict) Conditions to stop on (see above).

        Returns:
            dict with tactile (the scalar readings per side: contact, depth,
            displacement, shear, coherence, anisotropy),
            terminated_by (the event
            that stopped the motion, or None if it completed) and event
            (the full digest: max_shear_px, shear_dir_deg, asymmetry_mm,
            min_depth_mm, contact_step, events, steps, callback_error).
            The same 2 mm / 0.02 rad per-call minimum as move_delta applies:
            a delta below it on every axis cannot be executed and raises.
        """
        data = self._post(
            "/move_reflexive",
            {
                "dx": float(dx),
                "dy": float(dy),
                "dz": float(dz),
                "drx": float(drx),
                "dry": float(dry),
                "drz": float(drz),
                "coord": coord,
                "tag": "move_reflexive",
                "stop_on": stop_on,
            },
        )
        event = data.get("event") or {}
        term = event.get("terminated_by")
        print(f"Reflexive move: dx={dx:.3f} dy={dy:.3f} dz={dz:.3f} "
              f"terminated_by={term}")
        return {
            "tactile": _trim_motion_tactile(data.get("tactile", {})),
            "terminated_by": term,
            "event": event,
        }

    def open_gripper(self) -> dict:
        """Open the gripper fully.

        Returns:
            dict with the scalar tactile readings after opening (same set as
            goto_pose).
        """
        data = self._post("/gripper", {"action": "open"})
        print("Gripper opened")
        return _trim_motion_tactile(data.get("tactile", {}))

    def close_gripper(self, force_threshold: float | None = None,
                      position: float | None = None,
                      adaptive: bool = True) -> dict:
        """Close the gripper to grasp an object. Uses tactile feedback to stop
        at the requested gel deformation depth.

        force_threshold is a gel-depth target in mm, where depth DECREASES as
        contact hardens (see get_tactile_data).  Lower values grip firmer.

        With adaptive=True (the default) the close is judged by the gel depth
        it reached, so a close that meets nothing never reaches the target and
        raises "Gripper action failed" instead of returning a far-plane
        reading.  adaptive=False commands the finger travel directly: a close
        that meets nothing returns the far-plane reading, and one an object
        stops short of the commanded opening raises, the message carrying the
        opening it landed on.  A raise is not a lost reading: the fingers stay
        where the object stopped them, and get_tactile_data returns what the
        pads see at that stopping point.

        Args:
            force_threshold: (float, optional) Target gel deformation depth in
                mm. Lower values = firmer/deeper grip. Below ~26.5 mm the depth
                is one the servo can never reach on a compliant object, and the
                close fails outright rather than stopping short. An explicit
                value switches the close to a servoed landing: the fingers
                micro-step onto the target and the released depth comes back
                close to it — not exactly: 26.23, 26.32 and 26.68 were
                measured around a 26.5 target — so check the reading against a
                narrow band rather than one exact value. It gives up on an
                asymmetric or wedged grasp, where the fingers stop wherever
                they jammed. None uses the task default (firm), which is not
                servoed, lands near 25.5 mm and varies from one close to the
                next.
            position: (float, optional) Gripper opening percentage, 0.0 (fully
                closed) to 1.0 (fully open). Sets the grip by finger separation
                instead of by gel depth, which fixes the grip only where the
                object is rigid enough for the fingers to pin it. None leaves
                the travel unconstrained (closes fully).
            adaptive: (bool) Whether the depth servo runs during the close.
                True (default) drives towards force_threshold. False commands
                the finger travel directly, so it goes exactly where it is told
                whatever the object does.

        Returns:
            dict with the scalar tactile readings after closing (same set as
            goto_pose).
        """
        payload: dict = {"action": "close", "use_adaptive_grasp": adaptive}
        if force_threshold is not None:
            payload["force_threshold"] = force_threshold
        if position is not None:
            payload["position"] = position
        data = self._post("/gripper", payload)
        print("Gripper closed")
        return _trim_motion_tactile(data.get("tactile", {}))

    # ------------------------------------------------------------------
    # Tactile sensing API
    # ------------------------------------------------------------------

    def get_tactile_data(self) -> dict:
        """Read the GelSight Mini tactile sensors without moving.

        The readings are the same scalar set every motion primitive returns —
        this is the read that costs no motion.  The marker images are not
        returned by this call, only the scalars listed below.

        Every numeric field below falls back to 0.0 when its sensor did not
        report.  0.0 is out of range for all of them, so a 0.0 means the
        reading is MISSING, not that it is zero — and a missing pad is not a
        zero pad: a dead left gel returns depth 0.0, which is past the hard
        stop and would read as the firmest grip on the scale if taken at face
        value.  Pair it with the contact flag, which is False on that same
        pad.  (marker_anisotropy is the exception: it comes back as None when
        there is no image to read.)

        Returns:
            dict with: left_contact, right_contact (bool — a RANGE flag, not
            a press: true as soon as any surface is inside the sensor's range,
            which happens while the depth is below the far plane and the
            surface can still be up to ~5.6 mm short of the gel face.  Both
            up does not by itself mean the pads are touching the object);
            left_depth_mm, right_depth_mm (float, distance from the sensor to
            the nearest surface — 34 mm is the far plane (nothing in range);
            the gel's own face sits at ~28.3 mm undeformed, so a reading above
            that is a surface that has NOT reached the pad and one below it is
            pressed into the gel.  The reading DECREASES as contact hardens:
            ~27.8-29 mm is the gel-face band, where a light grasp and a
            close that met nothing both read — the contact flags are up in
            either case, and only the marker displacement separates them
            (under 0.01 px for the empty close; a real grasp moves them
            1.5 px or more.  A pad that has touched nothing still reports a
            few thousandths of a pixel, so test a threshold — anything
            above ~1 px is contact — and never "nonzero") — ~24 mm is the
            sensor's floor (the camera's window ends there), and the reading
            saturates there.  Returning to 34 mm
            means the pads are
            open, which is the same reading whether the object was never held,
            was set down, or was dropped — the field answers whether something
            is inside the sensor's range, never where it went);
            left_marker_displacement_px, right_marker_displacement_px (float,
            mean absolute marker displacement since the no-contact baseline —
            a magnitude, so it responds to any gel deformation, not only slip);
            left_marker_coherence, right_marker_coherence (float, 0-1
            uniformity of marker motion — high when markers move together
            (shear/slip), low when they move incoherently (compression/
            deformation or tracking loss).  It is a ratio of common motion
            to motion overall, not a measure of how far the markers went:
            0.0 to 0.8 is ordinary on a pad that barely moved, whether at
            rest right after a close or on a turn that slid it almost
            nothing, and grasps that go on to work have read it there; a
            pad dragged a long way through the gel has come back at 0.9 or
            more);
            left_marker_shear_dx, left_marker_shear_dy, right_marker_shear_dx,
            right_marker_shear_dy (float, pixels, in the marker image's own
            frame: the mean marker displacement VECTOR since the no-contact
            baseline — the part of the marker motion that the whole patch
            shares, so the direction is kept: a load hanging off the gel drags
            every marker one way (shear grows), while a uniform squeeze pushes
            them outward and its mean cancels.  The two sensors are mounted
            mirrored, so the SAME physical slide appears with OPPOSITE sign on
            the two pads);
            left_marker_anisotropy, right_marker_anisotropy (float, the
            row-to-row gradient of the marker image divided by its
            column-to-column gradient: 1.0 when the gel deforms evenly in every
            direction, higher when the deformation has horizontal structure);
            left_height_roughness_mm, right_height_roughness_mm (float, mm,
            RMS fine relief of the gel's indentation map after a smooth
            quadratic contact envelope is fitted and removed: the imprint of a
            sphere is a clean cap, while a raised surface leaves its bumps in
            the gel.  Every field above is an average over the gel, so it
            grows with how hard the pad is pressed; this one mostly does not
            — seated on a ball's centre a smooth ball reads 0.02-0.04 mm and
            a dotted one 0.09-0.25 mm at the same squeeze — so it answers
            what the surface is shaped like rather than how hard it is
            pressed.  It stops answering that once the imprint stops being
            the surface: a close driven to a gel-depth target keeps pressing
            into a compliant ball — over 100 N a side on a 60 mm ball — and a
            smooth rubber ball then reads 0.15 mm, a dotted ball's number.
            Reported only where the fingertip exposes a full height map;
            absent from the response otherwise.)
        """
        data = self._get("/tactile")
        # The server computes the marker anisotropy from the same uint8 array
        # its PNG payload carries.  Derive it here only when the response has
        # none, so an older server loses no reading over it.
        for side in ("left", "right"):
            key = f"{side}_marker_anisotropy"
            if data.get(key) is None:
                data[key] = _marker_anisotropy(data.get(f"{side}_rgb_base64", ""))
        tactile_trace.record("/tactile", data)
        # Print summary only (skip base64 images to keep logs readable)
        relief = ""
        if data.get("left_height_roughness_mm") is not None:
            relief = (" L_relief=%.3fmm R_relief=%.3fmm"
                      % (data["left_height_roughness_mm"],
                         data["right_height_roughness_mm"]))
        print(f"Tactile: L_contact={data['left_contact']} R_contact={data['right_contact']} "
              f"L_depth={data['left_depth_mm']:.2f}mm R_depth={data['right_depth_mm']:.2f}mm "
              f"L_disp={data['left_marker_displacement_px']:.2f}px "
              f"R_disp={data['right_marker_displacement_px']:.2f}px "
              f"L_coh={data['left_marker_coherence']:.2f} "
              f"R_coh={data['right_marker_coherence']:.2f} "
              f"L_shear=({_fmt(data.get('left_marker_shear_dx'))},"
              f"{_fmt(data.get('left_marker_shear_dy'))})px "
              f"R_shear=({_fmt(data.get('right_marker_shear_dx'))},"
              f"{_fmt(data.get('right_marker_shear_dy'))})px "
              f"L_aniso={_fmt(data.get('left_marker_anisotropy'))} "
              f"R_aniso={_fmt(data.get('right_marker_anisotropy'))}{relief}")
        # Strip images before returning to avoid huge logs
        return {k: v for k, v in data.items() if "base64" not in k}

    def get_tactile_image(self, side: str = "left") -> np.ndarray | None:
        """Return the GelSight marker-view image of one fingertip as RGB.

        The image shows the marker grid printed on the gel — the raw visual
        signal behind the displacement/coherence scalars.  Call it only at
        DECISION POINTS: every call is a full render (~0.5 s), so never
        fetch it mid-motion.  Save it to scratch/ and inspect it like a
        camera image (crop and upscale regions of interest, as with zoom).

        Args:
            side: ("left" or "right") Which fingertip to read.

        Returns:
            (H, W, 3) uint8 array of the marker-view image, or None when
            the image is unavailable.
        """
        data = self._get("/tactile")
        b64 = data.get(f"{side}_rgb_base64", "")
        if not b64:
            return None
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
        print(f"Tactile image ({side}): {arr.shape}")
        return arr

    def detect_slip(
        self, threshold_px: float = 2.0, coherence_threshold: float = 0.7
    ) -> bool:
        """Check whether the grasped object is slipping.

        Slip is judged on how much the marker displacement has GROWN since the
        previous call: a real slip or jam shears the markers further in a
        uniform direction, so the displacement increases by more than
        threshold_px while coherence stays high.  A steady grasp — even one
        that already slipped earlier — does not re-report.  The FIRST call
        only establishes the reference reading (returns False), so the very
        first motion after it is never judged.

        Args:
            threshold_px: (float) Minimum displacement GROWTH in pixels since
                the previous call.  1.0 = sensitive, 5.0 = tolerant.
            coherence_threshold: (float) Minimum marker coherence (0-1) for a
                displacement to count as slip.  ~0.7 separates uniform shear
                (slip) from compression/deformation.

        Returns:
            bool, True if the object is slipping.
        """
        data = self._get("/tactile")
        left_disp = float(data.get("left_marker_displacement_px", 0.0))
        right_disp = float(data.get("right_marker_displacement_px", 0.0))
        left_coh = float(data.get("left_marker_coherence", 0.0))
        right_coh = float(data.get("right_marker_coherence", 0.0))
        if self._slip_baseline is None:
            # First call: only establish the reference reading.
            self._slip_baseline = (left_disp, right_disp)
            print(f"Slip baseline set: L={left_disp:.2f}px R={right_disp:.2f}px")
            return False
        last_left, last_right = self._slip_baseline
        d_left = left_disp - last_left
        d_right = right_disp - last_right
        self._slip_baseline = (left_disp, right_disp)
        left_slip = d_left > threshold_px and left_coh > coherence_threshold
        right_slip = d_right > threshold_px and right_coh > coherence_threshold
        result = left_slip or right_slip
        if result:
            print(
                f"Slip detected! (dL={d_left:+.2f}px/{left_coh:.2f} "
                f"dR={d_right:+.2f}px/{right_coh:.2f})"
            )
        return result

    # ------------------------------------------------------------------
    # Perception API
    # ------------------------------------------------------------------

    def sample_grasp_pose(
        self, object_name: str
    ) -> tuple[np.ndarray, np.ndarray]:
        """Sample a grasp pose for an object resting on the table.

        The pose is the object's pose in the simulator's ground-truth scene
        plus a hand-relative offset, both read on the FIRST call for that
        object and cached from then on.  A task that would rather not take the
        cached offset has its own rule, which the API applies instead: where a
        rule exists the pose is rebuilt from the object's pose on every call
        rather than cached, so it follows the object after it has been moved or
        re-grasped, and the FIRST-call caveat above does not apply.

        Args:
            object_name: The simulator PRIM name of the object (e.g.
                "object_a", "object_b") — NOT a perception phrase.  This
                queries the simulator's ground-truth scene, so a SAM3
                phrase like "orange can" returns a 404.  (Perception
                prompts only apply to get_object_pose.)

        Returns:
            position: (3,) XYZ in meters.
            quaternion_wxyz: (4,) WXYZ unit quaternion.
        """
        data = self._get("/grasp_pose", {
            "object_name": object_name,
            "allow_ground_truth": self._ground_truth_grasp,
        })
        position = np.array(data["position"], dtype=np.float64)
        quat = np.array(data["quaternion_wxyz"], dtype=np.float64)
        print(f"Grasp pose for '{object_name}': pos={np.round(position, 3)} "
              f"quat_w={quat[0]:.3f}")
        return position, quat

    def rotate_gripper(
        self, object_name: str, angle_rad: float, steps: int = 4,
        feed_lead_m: float | None = None,
    ) -> dict:
        """Rotate the gripper while holding an object, about the world z axis.

        feed_lead_m selects the implementation.  Omitted, the environment's
        expert rotate runs one planner sub-move per step and fails the call
        when the object turns by less than 30 % of the requested angle (or
        about 3 deg, whichever is larger).
        Given, zero included, each step is one differential-IK iteration that
        turns the object by angle_rad / steps and advances the hand by the
        matched feed; the twist and the advance stay locked together.  This
        servo path needs the task's own _ik_to_center servo — without it the
        call is refused.

        One servo iteration carries about 4.6 deg of joint motion, so a step
        count too coarse for the angle is raised rather than refused:
        "requested" can exceed the number passed.  An unreachable target (a
        wrist limit, say) refuses the call, and that refusal latches the
        task's failure flag for the rest of the episode.

        A twist the servo can carry but that contact stops raises as well: the
        call ends with an error when the pads reach 128 N of normal force, the
        torque passes the task's over-torque limit, the axial load on the
        part's stop passes 180 N, the grasp drifts past 4 mm or tilts past
        10 deg, or the task is already in a latched failure.  An errored call
        is not a no-op: the stop is noticed at the end of an iteration, after
        the step that ran into it, so that step's effect on the part stands
        however few turns the call reports.  Only a servo refusal is decided
        before its own iteration is stepped, and unlike those stops it latches
        nothing by itself.

        Args:
            object_name: The name of the object being held.
            angle_rad: (float) Total rotation angle in radians.
            steps: (int) Number of increments to split the rotation into —
                planner sub-moves without a lead, servo iterations with one,
                each carrying angle_rad / steps of the twist.
            feed_lead_m: (float) Axial advance per full turn, in metres.  Its
                sign does not follow angle_rad: a positive lead descends the
                hand, a negative one lifts it, whichever way the twist goes.
                Zero rolls the hand in place on the servo; omitted rotates it
                in place on the planner instead.

        Returns:
            dict with the scalar tactile readings after the rotation (same set
            as goto_pose).  With feed_lead_m given, a "helix" entry is added:
            completed / requested (servo iterations run, and the count run
            after any raise), feed_lead_m (the lead this call twisted along),
            reached (bool — the feed servo reached its terminal condition: the
            part has taken its full travel and is loaded on its stop),
            aborted (always None in a returned dict — a stopped call raises
            instead, with the stop in the message), grip_released (bool) and
            trace (the per-iteration record).
        """
        payload: dict[str, Any] = {
            "object_name": object_name, "angle_rad": angle_rad, "steps": steps,
        }
        if feed_lead_m is not None:
            payload["feed_lead_m"] = feed_lead_m
        data = self._post("/rotate_gripper", payload)
        if feed_lead_m is None:
            print(f"Rotated gripper holding '{object_name}' by {angle_rad:.2f} rad "
                  f"in {steps} steps")
            return _trim_motion_tactile(data.get("tactile", {}))
        readings = _trim_motion_tactile(data.get("tactile", {}))
        readings["helix"] = {
            "feed_lead_m": data.get("feed_lead_m"),
            "completed": data.get("completed"),
            "requested": data.get("requested"),
            "grip_released": data.get("grip_released"),
            "aborted": data.get("aborted"),
            "reached": data.get("reached"),
            "trace": data.get("trace"),
        }
        print(f"Twisted '{object_name}' along lead {feed_lead_m * 1000:.3f} mm: "
              f"{data.get('completed')}/{data.get('requested')} iterations, "
              f"{'seated' if data.get('reached') else 'stopped: ' + str(data.get('aborted')) if data.get('aborted') else 'OK'}")
        return readings

    def check_success(self) -> bool:
        """Check whether the task is complete. Call at the end of the code to
        verify.

        Returns:
            bool, True if the success criteria are met.
        """
        data = self._get("/check_success")
        result = bool(data.get("completed", False))
        if result:
            print("Task check: SUCCESS")
        else:
            detail = data.get("detail") or {}
            print("Task check: FAILED")
            for reason in detail.get("reasons", []):
                print(f"  - {reason}")
            if detail.get("ground_truth_texture"):
                print(f"  ground truth: {detail['ground_truth_texture']}, "
                      f"expected pad: {detail['expected_pad']}")
                print(f"  prism at {detail.get('prism_pos')}, "
                      f"target at {detail.get('target_pos')}")
        return result

    def get_object_pose(
        self, object_name: str, return_bbox_extent: bool = False
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
        """Get the pose of an object in the scene. position is the prim's world
        origin, not its geometric center. Task objects are authored with their
        origin at the bottom face (they rest on the table, so position[2] is
        the resting height above it). When return_bbox_extent=True, bbox_extent
        is the full 3D bounding-box size (width/depth/height in metres), not
        the half-extent.

        Args:
            object_name: The name of the object to get the pose of (prefix
                matches, e.g. "prism").
            return_bbox_extent: Whether to fill in bbox_extent, the third return
                value, with the full 3D bounding-box size. Default is False, in
                which case bbox_extent comes back as None.

        Returns:
            Three values, always - the tuple is never shortened:
            position: (3,) XYZ in meters (prim world origin, not the center).
            quaternion_wxyz: (4,) WXYZ unit quaternion.
            bbox_extent: (3,) XYZ in meters (full side length, not half-length
                extent), or None when return_bbox_extent is False.
        """
        params: dict = {"object_name": object_name}
        if return_bbox_extent:
            params["return_bbox_extent"] = "true"
        data = self._get("/object_pose", params)
        if not data.get("found", False):
            raise RuntimeError(f"Object '{object_name}' not found in scene")
        position = np.array(data["position"], dtype=np.float64)
        quat = np.array(data["quaternion_wxyz"], dtype=np.float64)
        print(f"Object '{object_name}': pos={np.round(position, 3)} "
              f"quat_w={quat[0]:.3f}")
        bbox = None
        if return_bbox_extent:
            raw = data.get("bbox_extent")
            if raw is not None:
                bbox = np.array(raw, dtype=np.float64)
            if bbox is None:
                raise NotImplementedError(
                    "bbox_extent is not available for this object. "
                    "Try calling get_object_pose() without return_bbox_extent."
                )
        return position, quat, bbox

    # ------------------------------------------------------------------
    # Agent-facing observation (NOT part of the cap-x LLM API)
    # ------------------------------------------------------------------

    def get_observation(self, include_depth: bool = True) -> dict:
        """Return the current head/wrist camera images for interactive agents.

        Agent-facing only: this method is intentionally NOT listed in
        ``functions()``, so it never appears in the generated LLM prompt and
        is not bound in the exec sandbox.  It exists for coding agents that
        drive the arm interactively (see ``univtac_skill/robo.py``).

        Args:
            include_depth: (bool) Also decode the metric depth maps (float32
                metres). Default True.

        Returns:
            dict with ``head_rgb`` / ``wrist_rgb``: (H, W, 3) uint8 numpy
            arrays (None if unavailable), and ``head_depth`` / ``wrist_depth``:
            (H, W) float32 metres (None if include_depth is False or the
            camera is unavailable).  ``{cam}_intrinsics`` /
            ``{cam}_cam_pos_w`` / ``{cam}_cam_quat_w_ros`` for both head and
            wrist: camera calibration passed through from the server (None if
            the server does not provide it) — needed by the perception
            back-projection.
        """
        data = self._get("/observation")
        result: dict = {}
        for cam in ("head", "wrist"):
            rgb_b64 = data.get(f"{cam}_rgb_base64", "")
            if rgb_b64:
                img = Image.open(io.BytesIO(base64.b64decode(rgb_b64)))
                result[f"{cam}_rgb"] = np.asarray(img.convert("RGB"), dtype=np.uint8)
            else:
                result[f"{cam}_rgb"] = None

            depth = None
            if include_depth:
                depth_b64 = data.get(f"{cam}_depth_float32_base64", "")
                shape = data.get(f"{cam}_image_shape")
                if depth_b64 and shape:
                    depth = np.frombuffer(
                        base64.b64decode(depth_b64), dtype=np.float32
                    ).reshape(shape[0], shape[1])
            result[f"{cam}_depth"] = depth

        # Camera calibration (needed for perception back-projection); passed
        # through from the server response, unchanged.  Both cameras — head
        # and wrist — so that get_object_pose(camera="wrist") can back-project
        # (previously only the head keys were parsed, and wrist queries failed
        # with "calibration unavailable").
        for cam in ("head", "wrist"):
            for key in (f"{cam}_intrinsics", f"{cam}_cam_pos_w", f"{cam}_cam_quat_w_ros"):
                result[key] = data.get(key)

        shapes = ", ".join(
            f"{cam}="
            + (str(result[f"{cam}_rgb"].shape) if result[f"{cam}_rgb"] is not None else "None")
            for cam in ("head", "wrist")
        )
        print(f"Observation: {shapes}")
        return result

    def get_ee_pose(self) -> dict:
        """Return the end-effector pose in the world frame.

        Agent-facing only: this method is intentionally NOT listed in
        ``functions()``, so it never appears in the generated LLM prompt and
        is not bound in the exec sandbox.  It exists for coding agents that
        drive the arm interactively (see ``univtac_skill/robo.py``).

        Diagnostic use only: verify that a commanded move_delta actually
        executed (execution state != world state), detect drift from a
        known-good pose, and re-anchor before a precise descent.  Do NOT use
        it for object alignment — ``goto_pose`` plans in the same frame this
        returns, and object perception (SAM3) is far noisier than the arm
        kinematics, so aligning to the perception through this adds no
        precision.

        Args:
            None.

        Returns:
            dict with ``position``: [x, y, z] metres and ``quaternion_wxyz``:
            [w, x, y, z], both in the world frame.
        """
        return self._get("/ee_pose")
