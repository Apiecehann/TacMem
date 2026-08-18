"""UniVTAC tactile manipulation API for LLM code generation.

Functions in this class are called directly by LLM-generated Python code
during trial execution.  Each function's docstring appears verbatim in the
LLM prompt (via :meth:`ApiBase.combined_doc`), so write them clearly and
include practical guidance in the description.  Functions listed in
``_prompt_hidden`` stay callable at execution time but are excluded from
the prompt (see ``check_success``).

All calls are forwarded to the UniVTAC API server over HTTP.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import requests

from capx.integrations.base_api import ApiBase
from capx.envs.base import BaseEnv

# Default server URL — override via env var UNIVTAC_SERVER_URL
_DEFAULT_SERVER = "http://127.0.0.1:8120"

# Tactile fields returned by the motion primitives (goto_pose, move_delta,
# open/close_gripper, rotate_gripper) — the contact/slip signals relevant to
# motion.  Coherence is included so callers can tell shear (slip) apart from
# gel deformation.  The rest (marker std, base64 images) is only available via
# get_tactile_data().
_MOTION_TACTILE_FIELDS = (
    "left_contact",
    "right_contact",
    "left_depth_mm",
    "right_depth_mm",
    "left_marker_displacement_px",
    "right_marker_displacement_px",
    "left_marker_coherence",
    "right_marker_coherence",
)


def _trim_motion_tactile(data: dict) -> dict:
    """Keep only the motion-relevant fields from a full tactile dict."""
    return {k: data[k] for k in _MOTION_TACTILE_FIELDS if k in data}


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

    def _post(self, path: str, body: dict | None = None) -> dict:
        try:
            r = requests.post(
                f"{self._server}{path}",
                json=body or {},
                # Must exceed the server-side watchdog: _MOTION_TIMEOUT (120s)
                # plus the 5s dispatch grace.  Otherwise the client hangs up
                # before the server reports the motion failure.
                timeout=150,
            )
            r.raise_for_status()
        except requests.exceptions.ReadTimeout:
            raise RuntimeError(
                f"Server did not respond within 150s for POST {path}.\n"
                f"Request: {body}\n"
                f"This usually means the motion planner (cuRobo IK) is stuck.\n"
                f"Check that the target pose is physically reachable and not\n"
                f"inside solid geometry (table, object).  Try a smaller step\n"
                f"or a different approach direction."
            )
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Cannot connect to simulation server at {self._server}{path}.\n"
                f"The server may have crashed or not been started yet."
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
            raise RuntimeError(
                f"Server did not respond within 30s for GET {path}.\n"
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

        Args:
            position: (3,) XYZ in meters.
            quaternion_wxyz: (4,) WXYZ unit quaternion.
            z_approach: (float) Z-axis approach distance in meters. The
                end-effector first moves to position + z_approach above the
                target, then descends to the requested pose. Default is 0.0.

        Returns:
            dict with tactile feedback: left_contact, right_contact (bool);
            left_depth_mm, right_depth_mm (float); left_marker_displacement_px,
            right_marker_displacement_px (float); left_marker_coherence,
            right_marker_coherence (float).
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
        larger moves MUST be split into multiple calls. The target Z must stay
        above the table (Z >= 0); violating these limits is rejected.

        Args:
            dx: (float) X translation in meters.
            dy: (float) Y translation in meters.
            dz: (float) Z translation in meters.
            drx: (float) X rotation in radians.
            dry: (float) Y rotation in radians.
            drz: (float) Z rotation in radians.
            coord: ("world" or "ee") Reference frame for the translation.

        Returns:
            dict with tactile feedback (left_contact, right_contact,
            left_depth_mm, right_depth_mm, left_marker_displacement_px,
            right_marker_displacement_px, left_marker_coherence,
            right_marker_coherence). Use it to stop on contact — e.g.
            repeatedly call move_delta(dz=-0.005) until a contact is reported.
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

    def open_gripper(self) -> dict:
        """Open the gripper fully.

        Returns:
            dict with tactile feedback after opening.
        """
        data = self._post("/gripper", {"action": "open"})
        print("Gripper opened")
        return _trim_motion_tactile(data.get("tactile", {}))

    def close_gripper(self, force_threshold: float | None = None) -> dict:
        """Close the gripper to grasp an object. Uses tactile feedback to stop
        at the right gel deformation depth, ensuring a firm grip without
        crushing.

        Args:
            force_threshold: (float, optional) Target gel deformation depth in
                mm. None uses the task's default. Higher values = firmer grip.

        Returns:
            dict with tactile feedback after closing.
        """
        payload: dict = {"action": "close", "use_adaptive_grasp": True}
        if force_threshold is not None:
            payload["force_threshold"] = force_threshold
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
            mean absolute marker displacement since the no-contact reset
            baseline — rises with ANY gel deformation (contact, insertion) as
            well as with slip, so it is NOT a clean slip indicator on its own);
            left_marker_coherence, right_marker_coherence (float, 0-1
            uniformity of marker motion — high when markers move together
            (shear/slip), low when they move incoherently (compression/
            deformation or tracking loss); combine with displacement to detect
            slip);
            left_marker_std_px, right_marker_std_px (float, per-marker
            displacement spread).
        """
        data = self._get("/tactile")
        # Print summary only (skip base64 images to keep logs readable)
        print(f"Tactile: L_contact={data['left_contact']} R_contact={data['right_contact']} "
              f"L_depth={data['left_depth_mm']:.2f}mm R_depth={data['right_depth_mm']:.2f}mm "
              f"L_disp={data['left_marker_displacement_px']:.2f}px "
              f"R_disp={data['right_marker_displacement_px']:.2f}px "
              f"L_coh={data['left_marker_coherence']:.2f} "
              f"R_coh={data['right_marker_coherence']:.2f}")
        # Strip images before returning to avoid huge logs
        return {k: v for k, v in data.items() if "base64" not in k}

    def detect_contact(self) -> bool:
        """Check whether either fingertip is in contact with an object.

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

        Args:
            object_name: The name of the object to sample a grasp pose for.

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
        self, object_name: str, angle_rad: float, steps: int = 4
    ) -> dict:
        """Rotate the gripper while holding an object. Use for large rotations
        (>0.3 rad) instead of move_delta.

        Args:
            object_name: The name of the object being held.
            angle_rad: (float) Total rotation angle in radians.
            steps: (int) Number of incremental steps to split the rotation into.

        Returns:
            dict with tactile feedback after the rotation (useful for detecting
            whether the object slipped during rotation).
        """
        data = self._post(
            "/rotate_gripper",
            {"object_name": object_name, "angle_rad": angle_rad, "steps": steps},
        )
        print(f"Rotated gripper holding '{object_name}' by {angle_rad:.2f} rad "
              f"in {steps} steps")
        return _trim_motion_tactile(data.get("tactile", {}))

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
            return_bbox_extent: Whether to return the full 3D bounding-box size.
                Default is False.

        Returns:
            position: (3,) XYZ in meters (prim world origin, not the center).
            quaternion_wxyz: (4,) WXYZ unit quaternion.
            bbox_extent: (3,) XYZ in meters (full side length, not half-length
                extent). If return_bbox_extent is False, returns None.
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
