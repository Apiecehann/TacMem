"""UniVTAC tactile manipulation API for LLM code generation.

Functions in this class are called directly by LLM-generated Python code
during trial execution.  Each function's docstring appears verbatim in the
LLM prompt (via :meth:`ApiBase.combined_doc`), so write them clearly and
include practical guidance in the description.  Functions listed in
``_prompt_hidden`` stay callable at execution time but are excluded from
the prompt (see ``check_success``).

Paragraphs wrapped in ``[section:tag]`` / ``[/section]`` are optional: they are
delivered only to a task whose ``api_doc_sections`` lists that tag, so a task
that never measures weight does not read the weight recipe.  Unmarked text
always goes out.  See :data:`DOC_SECTION_TAGS` for the tags defined here.

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
# (weight), marker anisotropy (texture) — so a close or a lift carries its own
# reading instead of forcing a get_tactile_data() call straight afterwards.
# The images are the one thing withheld: those are what that call is for.
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
)


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


# Every `[section:...]` tag used in this module's docstrings.  A task opts into
# the ones its own reading needs (see the task classes' `api_doc_sections`):
# `position` and `servo` for how a close is commanded, `weight`, `hardness` and
# `texture` for the standalone reading recipes, `memory_match` for the
# task-specific integrated can protocol, and `screw` for twisting along a
# thread.  Tests assert this set and the tags actually present in the
# docstrings agree, so a typo cannot silently drop a section for every task at
# once.
DOC_SECTION_TAGS = (
    "position",
    "servo",
    "weight",
    "hardness",
    "texture",
    "memory_match",
    "screw",
)


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
            "detect_contact": self.detect_contact,
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
        Use z_approach=0.1 for grasping/placing, 0.0 for free-space motion.
        Reach a grasp pose in ONE call. A descent split into a second call
        (go to the pose 5 cm up, then go to the pose) arrives up to about a
        millimetre off target, and the close that follows then drags the
        grasped object sideways through the grip.

        This commands the END-EFFECTOR, not a held object — the two differ by
        the grasp offset.  So to place a held object at target T you would have
        to command T + offset, with offset = grasp_pos - object_pos (the same
        offset sample_grasp_pose describes).  Commanding T itself drives the
        held object down into the table or plate, and the planner refuses with
        "Motion planning failed".

        For moving a held object, prefer move_delta in world coordinates: the
        object travels with the hand, so one world-frame delta moves it by
        exactly that delta with no offset arithmetic — and, unlike a goto_pose,
        it never re-commands the wrist orientation, which while the object is
        touching down can twist it inside the grip.

        Args:
            position: (3,) XYZ in meters.
            quaternion_wxyz: (4,) WXYZ unit quaternion.
            z_approach: (float) Z-axis approach distance in meters. The
                end-effector first moves to position + z_approach above the
                target, then descends to the requested pose. Default is 0.0.

        Returns:
            dict with the scalar tactile readings after the move, per side:
            contact, depth_mm, marker_displacement_px, marker_shear_dx,
            marker_shear_dy, marker_coherence and marker_anisotropy.  The
            marker images are the one thing left to get_tactile_data().
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
              f"quat_w={quaternion_wxyz[0]:.3f} z_approach={z_approach}")
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
        EVERY axis cannot be executed and raises instead of moving nothing, so a
        closed-loop descent can never converge to a tolerance finer than 2 mm —
        step by at least 2 mm, and stop when the object is where you want it
        rather than commanding the last millimetre. The target Z must stay above
        the table (Z >= 0); violating these limits is rejected.

        Args:
            dx: (float) X translation in meters.
            dy: (float) Y translation in meters.
            dz: (float) Z translation in meters.
            drx: (float) X rotation in radians.
            dry: (float) Y rotation in radians.
            drz: (float) Z rotation in radians.
            coord: ("world" or "ee") Reference frame for the translation.

        Returns:
            dict with the scalar tactile readings after the move (same set as
            goto_pose: contact, depth, displacement, shear, coherence and
            anisotropy per side). Use it to stop on contact — e.g. repeatedly
            call move_delta(dz=-0.005) until a contact is reported.
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
              f"drx={drx:.3f} dry={dry:.3f} drz={drz:.3f} coord={coord}")
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
            coord: ("world" or "ee") Reference frame for the translation.
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

        Releasing a held object is a placement, not just an open: open only
        once the object rests on a surface.  Lower it in small (~5 mm) steps,
        watching the held object's own height - the moment that height stops
        decreasing the object is supported, so never press further.  Back the
        EE up ~5 mm first (one step, taking the fingers' weight off the
        object) and only then open: opening while the fingers still press the
        object into the surface tips and wedges it, and opening from a hover
        ~1 cm or more above rest drops it crooked.  After opening, keep
        executing small moves until the object has settled - a placement is
        counted only once it rests stably, so do not end while it is still
        falling.  If an open leaves it tipped or wedged, lift ~3 cm, re-grasp
        and re-place.

        Returns:
            dict with the scalar tactile readings after opening (same set as
            goto_pose), useful for checking that the grip has actually
            released.
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
        contact hardens (see get_tactile_data): 34 mm = no contact, ~27-28 mm
        = light grasp, ~20 mm = hard jam.  So LOWER values grip FIRMER.

        [section:position]
        position commands the finger separation directly, as a percentage of
        the gripper's travel (0.0 = fully closed, 1.0 = fully open), for
        objects rigid enough to pin the fingers: the squeeze then has to be
        set by how far the fingers are told to close, not by a depth the pads
        could never reach.  Just above the object's width is the lightest
        touch that still touches - where a heavy object slips out of the
        fingers and a light one does not.  The two are alternatives: with
        position set and the adaptive close still on, the fingers stop at
        whichever comes first.

        Measuring how much an object YIELDS needs position with
        adaptive=False.  A compliant object yields until the jaws bottom out
        at ~26.5 mm, so a firmer force_threshold is a target the servo cannot
        reach on it and the close FAILS outright rather than stopping short.
        With the adaptive close on, the servo keeps pushing until those jaws
        bottom out, the gel then reads a firm jam, and the sign of the
        reading reverses - a soft object coming out firmer than a hard one.
        A commanded travel stops where it is told and lets the object yield.
        It is also pure kinematics, so the same command from the same grasp
        pose returns the same depths: a close that read wrong comes out
        wrong again.  Change where you grasp, not how many times you close.
        [/section]

        [section:servo]
        The servo does not land exactly on the requested depth - the realized
        depth sits up to a few tenths of a millimetre off, and the offset
        drifts with the server session.  The reading follows the REALIZED
        depth, so read it back from the returned left_depth_mm /
        right_depth_mm (the deeper pad is min(left, right); the two
        pads sit a roughly constant 0.3 mm apart).  If it missed,
        open and close again at target - (realized - target): the servo
        repeats its landing error for the same target, so one correction
        lands it.  Re-closing at the light weight grip does not spoil a
        weight reading - only an object first squeezed at a firm grasp depth
        loses it.
        [/section]

        [section:weight]
        READING WEIGHT.  Two readings, both from one grasp; the second is the
        one to use when you have no reference object to compare against.

        Against a reference: close at force_threshold=27.7 and lift ~2 cm - a
        light object follows the hand almost the whole way, a heavy one slips
        further behind.  Compare how far each rises, not held-vs-dropped,
        because at this grip both stay near the hand.  The weight-sensitive
        band is narrow: a firmer grip hides the difference, and a much
        lighter one (> ~28) drops even a light object.  Judge the comparison
        within the run, never against a fixed number of millimetres - how far
        an object follows depends on where the close landed.

        From the gel: hang the object off a light grip and the load has to be
        carried by friction at the pads, which drags the marker pattern
        sideways (marker_shear_dx/dy).  Take the reading as a difference
        within the same grasp, because the squeeze that the close itself
        produced is much larger than the load's contribution and varies with
        where the grasp landed:
          1. close at force_threshold=27.7 (28.5 only for a rigid object of a
             few grams - see below),
          2. read the marker shear the close returned, while the object still
             RESTs on the table - nothing is hanging from the gel yet, so this
             is the baseline,
          3. lift in a few small steps (~1 cm each, ~3 cm total),
          4. read the shear the lift returned, while it hangs still.
        Check that the baseline of step 2 is really unloaded before trusting
        the difference.  The close can drag the object sideways as it seats
        into the grip, which shears the gel by about as much as a heavy load
        does.  In a valid baseline the two pads read within ~0.4 px of each
        other on shear_dx; if they are further apart the grip is already
        carrying the object, the lift of step 3 adds nothing to the reading,
        and a heavy object comes out looking light.  Recover by opening,
        re-approaching in a SINGLE goto_pose call, and closing again (see
        goto_pose) - the split descent is what makes the close drag the
        object.  Subtracting the larger baseline instead does not work: the
        shear is already as far as this grip will take it, not merely offset.
        The two fingertips are mounted mirrored, so the same slide shows up
        with OPPOSITE sign in their two images: subtract each pad's dx
        change, (left_hang - left_close) - (right_hang - right_close), and
        the squeeze cancels while the load adds.  A light object gives
        essentially 0 (|.| < 0.05 px); a heavy one gives 0.7-1.0 px.  Judge
        that magnitude - it stays near zero for light no matter where the
        grasp landed - and split the two at 0.2 px.  Do NOT read the weight
        from left_depth_mm or the gripper opening: a rigid object pins the
        fingers, so the servo lands on the same depth for both.

        The right grip is object-specific, because the object's mass decides
        how firmly the pads have to hold it.  On a RIGID object the shear
        signal survives at ~28.5 and collapses by ~26 - too firm a grip stops
        the gel shearing and hides the weight rather than holding it better.
        That ~28.5 is the figure for a LIGHT object (the 15 mm block): light
        enough to shear, firm enough that a few grams do not slip out of it.
        A rigid can of 300 g or more slips out at 28.5 instead - the gel
        barely loads (the marker displacement sits at ~0.02 px) and a 2 cm
        lift leaves the can on the table.  Close firmer for those, ~27.7.  A
        lift that leaves the object behind means the grip is too light: close
        firmer and lift again.  It is not a failed measurement - the same
        close performed twice returns the same reading, and only a firmer one
        changes the answer.
        [/section]

        [section:hardness]
        READING HARDNESS.  Squeeze a compliant object past the point where it
        yields and it stops resisting: the fingers keep travelling while the
        gel barely moves, because the object absorbs the extra travel instead
        of passing it on.  So read hardness as the gel's RESPONSE TO A CHANGE
        in grip force - two closes at different positions - not as the depth
        of one:
          1. close at the LOOSE position and read min(left_depth_mm,
             right_depth_mm) (see get_tactile_data),
          2. close again at the FIRM position and read it again.
        A hard object passes the extra squeeze straight through and the depth
        falls further; a soft one absorbs it and the depth barely moves.  Use
        this difference rather than an absolute depth: the difference cancels
        where the close happened to land, which the absolute reading does
        not.

        The two positions must BRACKET the object's width, so their values
        are object-specific rather than universal constants - and the loose
        end is the one you can verify in the reading itself:
          - LOOSE has to be wide enough that the fingers never touch the
            object.  A close that touches nothing reads the far plane: 34.0
            mm on both pads.  That reading IS the check - if the loose close
            reads anything else, the fingers were already squeezing, the two
            closes are bracketing nothing, and their difference means
            nothing.  Go wider and close again.
          - FIRM has to be a real squeeze, both depths clearly below the far
            plane, but not so firm that the channel bottoms out.  Gel depth
            clamps near 20 mm and never reads below it, so a FIRM past that
            point saturates and stops responding.
        Two calibrations, on objects of very different width:
          - a 4 cm cylinder (the memory-match cans): LOOSE 0.50, FIRM 0.25,
            split the difference at 10 mm.  0.50 does not touch the can (both
            pads read 34.0) and 0.25 lands at 21.27-21.34 mm on a rigid can
            against 26.62-26.66 mm on a compliant one - a difference of
            12.66-12.73 mm against 7.34-7.38 mm, no overlap and nothing near
            the split.  These same two closes carry the texture read as well,
            at no extra close - marker_anisotropy at each (see
            get_tactile_data).
          - a 15 mm block (the small-object classify tasks): LOOSE 0.10,
            FIRM 0.06, split at 1.2 mm (a hard block passes ~1.6 mm of the
            squeeze through to the gel, a soft one ~0.8 mm).
        Do not carry the block's numbers over to the can.  Both 0.10 and 0.06
        are already past a 4 cm can, so both closes simply bottom out on it
        and the difference collapses to ~0 for EITHER hardness - a blind
        reading that reads like a confident answer instead of failing.  The
        same bottoming-out is why a single close at the weight-sensing grip
        of 27.7 shows nothing at all, both hardnesses landing on the same
        depth.  Hardness therefore comes AFTER any weight measurement - step
        2 above is exactly the firm squeeze that destroys the weight reading.
        [/section]

        [section:memory_match]
        TACTILE MEMORY MATCH uses one integrated measurement grasp per can;
        do NOT prepend the standalone force_threshold weight recipe above.
        Approach the can once from a safe hover, then keep the fingers closed
        through this complete sequence:
          1. close at position=0.50 with adaptive=False and read the depths
             that close returned; both must be at the 34.0 mm no-contact far
             plane,
          2. without opening, close again at position=0.25 with
             adaptive=False and read the depths that close returned; real
             contact must now be present and the depths must be clearly below
             33.0 mm,
          3. record the can's own z, lift the hand 0.020 m, and record the
             can's z again; how much the CAN followed is a measurement, not a
             validity test,
          4. while still in that grasp, make a +0.002 m / -0.002 m vertical
             pair and use the spread of the can's z as the sag response,
          5. return the can to rest before opening.
        This one progressive squeeze supplies every feature: loose minus firm
        depth for hardness, firm minus loose marker_anisotropy for texture,
        and follow/sag under the lift for the remaining load response.  A rigid
        can can show firm bilateral gel contact yet remain on the table during
        the lift; that low follow is a real feature and MUST NOT invalidate the
        reading.  Do not require the pads' displacement or shear to be
        symmetric, and do not require every property channel to vote: validity
        comes only from the verified loose far-plane state followed by real
        firm contact.  Use a separate, firmer re-grasp later to transport the
        selected can.
        [/section]
        Lift the object clear of the table before carrying it anywhere: while
        it still rests there, a sideways move topples it instead of moving it.

        Args:
            force_threshold: (float, optional) Target gel deformation depth in
                mm. Lower values = firmer/deeper grip; 27.7 = calibrated light
                touch for weight sensing, 28.5 for a rigid object of a few
                grams. Not a way to set a firm squeeze on a compliant object -
                past ~26.5 the depth is one the servo can never reach on it and
                the close fails outright; use position with adaptive=False to
                squeeze one. None uses the task default (firm), with the
                historical landing behaviour (realized depth scatters,
                typically ~25.5 mm firm).
            position: (float, optional) Gripper opening percentage, 0.0 (fully
                closed) to 1.0 (fully open). Sets the grip by finger separation
                instead of by gel depth. None leaves the travel unconstrained
                (closes fully).
            adaptive: (bool) Whether the depth servo runs during the close.
                True (default) drives towards force_threshold. False commands
                the finger travel directly, so it goes exactly where it is told
                whatever the object does - needed when position is being used
                to measure how much an object yields.

        Returns:
            dict with the scalar tactile readings after closing (same set as
            goto_pose).  A close never needs a get_tactile_data() call after
            it to see its own contact, depth, shear or anisotropy — that call
            is for the marker images.
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
        """Read the GelSight Mini tactile sensors.

        Returns:
            dict with: left_contact, right_contact (bool); left_depth_mm,
            right_depth_mm (float, distance from the sensor to the contact
            surface — 34 mm is the far plane (no contact) and it DECREASES as
            contact hardens: ~27 mm is a light grasp, ~20 mm is a hard jam);
            left_marker_displacement_px, right_marker_displacement_px (float,
            mean absolute marker displacement since the no-contact baseline
            — rises with ANY gel deformation (contact, insertion) as well as
            with slip, so it is NOT a clean slip indicator on its own);
            left_marker_coherence, right_marker_coherence (float, 0-1
            uniformity of marker motion — high when markers move together
            (shear/slip), low when they move incoherently (compression/
            deformation or tracking loss); combine with displacement to detect
            slip);
            left_marker_shear_dx, left_marker_shear_dy, right_marker_shear_dx,
            right_marker_shear_dy (float, pixels, in the marker image's own
            frame: the mean marker displacement VECTOR since the no-contact
            baseline — the part of the marker motion that the whole patch
            shares).  Where marker_displacement_px throws the direction away
            and so cannot tell a slide from a squeeze, shear keeps it: a load
            hanging off the gel drags every marker one way (shear grows), while
            a uniform squeeze pushes them outward and its mean cancels.  The
            two sensors are mounted mirrored, so the SAME physical slide
            appears with OPPOSITE sign on the two pads.

        [section:texture]
            left_marker_anisotropy, right_marker_anisotropy (float, the
            row-to-row gradient of the marker image divided by its
            column-to-column gradient: 1.0 when the gel deforms evenly in
            every direction, higher when the deformation has horizontal
            structure — see the texture note below);
        [/section]

            Judge a "two-sided hold" by left_contact / right_contact and by
            BOTH depths being clearly below the far plane - NOT by the two
            pads showing similar marker displacement.  Displacement scales
            with local gel deformation and is commonly one-sided even on a
            real hold, so demanding symmetric displacement rejects almost
            every reading and leaves nothing left to compare.

        [section:weight]
            Weight shows up here (see close_gripper): hanging an object from a
            light grip shears the gel by an amount set by the object's weight,
            where the closing depth does not — a rigid object pins the fingers,
            so the depth servo lands on the same reading whatever it weighs.
            Hardness is the opposite case: a compliant object does not pin the
            fingers, so there the depth is the signal (see close_gripper).
        [/section]

        [section:texture]
            Surface texture shows up as marker_anisotropy, and in no other
            scalar: every other scalar above is an average over the whole gel
            image, and an average cannot see structure, so two surfaces that
            deform the gel by the same amount read alike on all of them.  (It
            comes back from the motion primitives too — a close reports its
            own anisotropy, so the two grips of the reading below are both
            readable without a separate call.)  Pressed against a
            rough surface the gel picks up the ridges as banding and
            marker_anisotropy moves; against a plain surface it stays near
            isotropic.  How far it moves depends on how hard the gel is
            pressed, so the reading is only meaningful at a COMMANDED grip
            (a default close lands wherever it lands — see close_gripper) and
            the robust form is the CHANGE across two grips.  Two
            calibrations, on objects of very different width:
              - a 15 mm block (the small-object classify tasks):
                  1. close_gripper(force_threshold=27.5) — a light grip — and
                     read,
                  2. close_gripper(force_threshold=20.5) — firm — and read
                     again,
                  3. the anisotropy rises by ~0.7-0.9 on a rough surface and
                     by ~0.1-0.2 on a plain one.  Per millimetre of gel the
                     rise is 0.10-0.13 rough against 0.01-0.03 plain; split
                     the two at 0.06.  The depth between the two reads is the
                     denominator: it is ~7 mm when the closes land where they
                     were asked.
                A single reading at force_threshold=24.0 separates too (rough
                ~1.65-1.67, plain ~1.10-1.11, split at 1.38), but it is only
                valid if the grasp landed where this calibration did; taking
                a difference cancels that assumption, and the difference is
                what does not drift.
              - a 4 cm cylinder (the memory-match cans): the two closes the
                hardness read already makes — position 0.50 (loose, reads the
                34 mm far plane) then position 0.25 (firm), both with
                adaptive=False — double as the texture read, so it costs no
                extra close: read the anisotropy at each and subtract the
                loose one from the firm one.  That change comes out negative
                on every can measured, about -0.06 for a rough can against
                -0.017 to -0.033 for a smooth one, and it is the DIFFERENCE
                between two cans that carries the texture: two cans of the
                same texture agree to within ~0.007, while a rough can and a
                smooth one sit ~0.025-0.038 apart.  Treat ~0.02 as the line
                between "same" and "different".  This is calibrated on RIGID
                cans, four of them across two weights, each texture class
                reproducing to 0.006-0.016.  On a COMPLIANT can the same
                contrast measured only 0.002-0.016 — at or under that line —
                so there a texture call made from the anisotropy change is a
                guess, not a reading.
            Do not carry the block's force_threshold pair over to the cans:
            20.5 mm is a depth the servo cannot reach on a compliant can, so
            there that close fails outright (see close_gripper) rather than
            stopping short and giving you a reading.
        [/section]
        """
        data = self._get("/tactile")
        # The server computes the marker anisotropy from the same uint8 array
        # its PNG payload carries.  Derive it here only when the response has
        # none, so an older server loses no reading over it.
        for side in ("left", "right"):
            key = f"{side}_marker_anisotropy"
            if data.get(key) is None:
                data[key] = _marker_anisotropy(data.get(f"{side}_rgb_base64", ""))
        # Print summary only (skip base64 images to keep logs readable)
        print(f"Tactile: L_contact={data['left_contact']} R_contact={data['right_contact']} "
              f"L_depth={data['left_depth_mm']:.2f}mm R_depth={data['right_depth_mm']:.2f}mm "
              f"L_disp={data['left_marker_displacement_px']:.2f}px "
              f"R_disp={data['right_marker_displacement_px']:.2f}px "
              f"L_coh={data['left_marker_coherence']:.2f} "
              f"R_coh={data['right_marker_coherence']:.2f}")
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

    def detect_contact(self) -> bool:
        """Check whether either fingertip gel is under pressure right now.

        "Contact" means the gel depth map already has pixels nearer than the
        far plane — the gel is physically pressed.  An open gripper
        descending next to an object reports False (nothing presses the gels
        yet), so this is NOT a proximity sensor: do not use it to stop a
        descent.  Drive descents by target position instead.  It only turns
        True once a close_gripper actually pinches something — use it after
        closing to confirm the grasp engaged before lifting.

        Returns:
            bool, True if either fingertip contacts an object.
        """
        data = self._get("/tactile")
        result = bool(data.get("left_contact", False) or data.get("right_contact", False))
        print(f"Contact detected: {result}")
        return result

    def detect_slip(
        self, threshold_px: float = 2.0, coherence_threshold: float = 0.7
    ) -> bool:
        """Check whether the grasped object is slipping.

        Slip is judged on how much the marker displacement has GROWN since the
        previous call: a real slip or jam shears the markers further in a
        uniform direction, so the displacement increases by more than
        threshold_px while coherence stays high.  A steady grasp — even one
        that already slipped earlier — does not re-report.  The FIRST call
        only establishes the reference reading (returns False), so call it
        once before the motion you want to test, or accept that the very
        first motion is never judged.

        NOTE: do NOT use this to detect "the object has reached the table"
        while lowering it into a slot.  Slow pressing (a few mm at a time)
        mostly compresses the tactile gel instead of shearing it, so
        displacement growth stays below the threshold and slip is NOT
        reported — the loop then keeps descending and the fingertips wedge
        under the object and push it out of the grip.  To place an object:
        descend in small steps and watch the object's own height with
        get_object_pose() instead; once it stops decreasing (the object is
        resting on the support), release — never keep pressing after that.

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
        """Sample a grasp pose for an object resting on the table. Do use the
        returned quaternion when approaching the grasp.

        Combine with goto_pose(..., z_approach=0.05) for a safe top-down
        approach.

        WARNING: This is only reliable for the object that the scene was
        anchored on (the one the EE starts directly above).  The grasp
        offset is cached from the EE pose on the FIRST call; once the EE
        has moved elsewhere (e.g. you already placed that first object),
        the cached offset no longer matches other objects and the returned
        pose can be far off target.  For any object the EE is not above
        right now, do NOT call this: instead construct the grasp pose
        yourself from a measured offset — the EE's position relative to a
        held object's centre is the same for every object, so reuse the
        offset from your first successful grasp:

            grasp_pos = object_pos + offset  # offset = grasp_pos - object_pos

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
        data = self._get("/grasp_pose", {"object_name": object_name})
        position = np.array(data["position"], dtype=np.float64)
        quat = np.array(data["quaternion_wxyz"], dtype=np.float64)
        print(f"Grasp pose for '{object_name}': pos={np.round(position, 3)} "
              f"quat_w={quat[0]:.3f}")
        return position, quat

    def rotate_gripper(
        self, object_name: str, angle_rad: float, steps: int = 4,
        feed_lead_m: float | None = None,
    ) -> dict:
        """Rotate the gripper while holding an object. Use for large rotations
        (>0.3 rad) instead of move_delta.

        [section:screw]
        TWISTING ALONG A THREAD.  Set feed_lead_m to twist about the world z
        axis while descending by that much per full turn.  The twist and the
        descent stay locked together - that coupling is the thread's own
        relation, and move_delta cannot express it, because a planned
        trajectory is resolved before contact exists.  In this mode steps
        counts servo iterations rather than planner sub-moves: one iteration
        is 1.5 deg of twist plus its matched feed, so 120 iterations is half
        a turn.

        THE PADS ARE THE INSTRUMENT.  A twist returns the same scalar tactile
        readings a motion does, taken at the end of the twist - and under a
        thread they carry something no pose reading can.  The fingertips are
        mounted mirrored, so one and the same slide of the part along the
        fingers shows up with OPPOSITE sign on the two pads: left_marker_shear_dx
        minus right_marker_shear_dx is that slide, doubled, with the squeeze
        the two pads share cancelled out.  Read the difference, not either pad.

        Its sign says which way the glass moved relative to the fingertips:
        positive means the part came down through them, negative that it
        backed up.  Its size in millimetres is that difference divided by the
        pad's gain - some tens of pixels per millimetre on this rig.  The
        reading is only valid while both contact flags are up; a pad that is
        not touching reports the 34 mm depth sentinel and a shear that means
        nothing.

        Two things follow, and the lead need not be known in advance.  The
        commanded descent and the part's real descent are two different
        numbers whose gap IS that slide, so the slide says which way the feed
        is wrong: below the lead the part rides along with the fingers and the
        slide stays near zero, while above it the thread will not let the part
        go faster than its own pitch and the slide turns negative.  Walk the
        feed up until the first twist the thread refuses to follow, and hold
        the one before it - a matched feed demands no slide at all, and the
        part then turns with the fingers instead of lagging them.  That is
        worth hunting for: an under-fed insertion costs half again as many
        turns for the same depth, and every extra twist is another release,
        another re-grasp, another chance to drop the part.

        And a part that has bottomed out cannot keep pace however well the
        feed is matched: the hand goes on down and the glass does not, so the
        slide collapses toward the whole commanded descent, running the other
        way, within a single twist.

        The slide is unambiguous only while it stays inside one marker-lattice
        pitch, roughly a millimetre and a half at that gain.  Past half of it
        the tracker settles on the neighbouring marker and comes back
        silently wrong, sign included, so keep each twist short enough that
        the part cannot travel further than that inside it.

        THE LEAD IS NOT GIVEN TO YOU, and it differs from one run to the next.
        With the part held, twist a few degrees one way and then the same
        angle back, each leg returning to where it started.  The hand descends
        on both legs alike, so the two differ in one respect only - which way
        the thread drives the part - and the pads separate them: the leg the
        pads read higher is the one that tightens.  Measure before tightening
        anything.  The part's angle reference freezes the moment it leaves its
        home position, so once it has been turned the measurement cannot be
        retaken without backing it all the way out again; and until both legs
        have registered, the formal advance reading stays at zero however well
        the tightening goes.

        A wrist runs out of range after roughly half a turn, so a full
        insertion is several segments with the hand re-set between them.
        Reset by releasing the part, twisting the EMPTY hand back to where the
        segment started, and re-grasping - turning back while still holding
        unscrews the part.

        A run ends one of three ways and they mean different things, so they
        are reported separately and none is simply "failed".  reached names
        the finished job: the thread is seated and the tightening torque has
        come up to its target, which is the state the task scores a hold
        from.  aborted names a physical overload or a lost grip.  With
        neither, the requested twist simply ran out - which for a lead-coupled
        helix is the normal end of a part-way segment, not a fault.

        A twist of exactly zero is a hold, not an approach: the arm stays on
        the pose it already holds and reached never fires.  Repeat that
        command to sit still - the environment ends the episode once a gripped
        command has stopped moving for long enough, and submitted names that
        ending.
        [/section]

        Args:
            object_name: The name of the object being held.
            angle_rad: (float) Total rotation angle in radians.
            steps: (int) Number of incremental steps to split the rotation into.
            feed_lead_m: (float) Axial advance per full turn, in metres.  When
                given, twists along that lead instead of rotating in place -
                pass the lead you measured, since a wrong one makes the
                commanded descent fight the real thread.

        Returns:
            dict with the scalar tactile readings after the rotation (same set
            as goto_pose; useful for detecting whether the object slipped
            during rotation).
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
            "submitted": data.get("submitted"),
            "trace": data.get("trace"),
        }
        print(f"Twisted '{object_name}' along lead {feed_lead_m * 1000:.3f} mm: "
              f"{data.get('completed')}/{data.get('requested')} iterations, "
              f"{'seated' if data.get('reached') else 'submitted' if data.get('submitted') else 'stopped: ' + str(data.get('aborted')) if data.get('aborted') else 'OK'}")
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
            Three values, always - the tuple is never shortened, so unpack
            three (or index with [0]/[1]/[2]):
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
