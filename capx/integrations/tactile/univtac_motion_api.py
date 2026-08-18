"""UniVTAC motion-only API (no tactile) — ablation baseline.

Same motion+perception functions as UniVTACTactileApi, but without
tactile sensing (get_tactile_data, detect_contact, detect_slip,
sample_grasp_pose).  Used for ablation experiments comparing
vision+tactile vs motion-only LLM code generation.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import requests

from capx.integrations.base_api import ApiBase
from capx.envs.base import BaseEnv

_DEFAULT_SERVER = "http://127.0.0.1:8120"


class UniVTACMotionApi(ApiBase):
    """Robot control API for a Franka arm (motion only, no tactile).

    Provides goto_pose, move_delta, gripper control, object pose queries,
    and success checking.  Tactile sensing functions are intentionally
    excluded for ablation experiments.
    """

    def __init__(self, env: BaseEnv, server_url: str | None = None) -> None:
        super().__init__(env)
        self._server = server_url or os.environ.get(
            "UNIVTAC_SERVER_URL", _DEFAULT_SERVER
        )

    def functions(self) -> dict[str, Any]:
        return {
            "goto_pose": self.goto_pose,
            "move_delta": self.move_delta,
            "open_gripper": self.open_gripper,
            "close_gripper": self.close_gripper,
            "rotate_gripper": self.rotate_gripper,
            "get_object_pose": self.get_object_pose,
            "sample_grasp_pose": self.sample_grasp_pose,
            "check_success": self.check_success,
        }

    # ---- HTTP helpers ----

    def _post(self, path: str, body: dict | None = None) -> dict:
        try:
            r = requests.post(
                f"{self._server}{path}",
                json=body or {},
                timeout=150,  # must exceed server-side _MOTION_TIMEOUT (120s)
            )
            r.raise_for_status()
        except requests.exceptions.ReadTimeout:
            raise RuntimeError(
                f"Server did not respond within 90s for POST {path}.\n"
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
            raise RuntimeError(f"Motion failed: {data.get('message', 'unknown')}")
        return data

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            r = requests.get(
                f"{self._server}{path}",
                params=params or {},
                timeout=30,
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

    # ---- Motion API ----

    def goto_pose(
        self, position: np.ndarray, quaternion_wxyz: np.ndarray,
        z_approach: float = 0.0,
    ) -> None:
        """Move the end-effector to a target pose via inverse kinematics.
        Use z_approach=0.1 for grasping/placing, 0.0 for free-space motion.

        Args:
            position: (3,) XYZ in meters.
            quaternion_wxyz: (4,) WXYZ unit quaternion.
            z_approach: (float) Z-axis approach distance in meters. The
                end-effector first moves to position + z_approach above the
                target, then descends to the requested pose. Default is 0.0.

        Returns:
            None
        """
        self._post("/move", {
            "position": np.asarray(position, dtype=float).tolist(),
            "quaternion_wxyz": np.asarray(quaternion_wxyz, dtype=float).tolist(),
            "z_approach": float(z_approach),
            "tag": "goto_pose",
        })
        print(f"Moved to pos={np.round(position, 3)} quat_w={quaternion_wxyz[0]:.3f}")

    def move_delta(
        self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0,
        drx: float = 0.0, dry: float = 0.0, drz: float = 0.0,
        coord: str = "world",
    ) -> None:
        """Move the end-effector by a small delta.

        Limits per call: min 0.002 m, max 0.10 m translation per axis, max
        0.5 rad rotation; larger moves MUST be split into multiple calls. The
        target Z must stay above the table (Z >= 0); violating these limits is
        rejected.

        Args:
            dx: (float) X translation in meters.
            dy: (float) Y translation in meters.
            dz: (float) Z translation in meters.
            drx: (float) X rotation in radians.
            dry: (float) Y rotation in radians.
            drz: (float) Z rotation in radians.
            coord: ("world" or "ee") Reference frame for the translation.

        Returns:
            None
        """
        self._post("/move_delta", {
            "dx": float(dx), "dy": float(dy), "dz": float(dz),
            "drx": float(drx), "dry": float(dry), "drz": float(drz),
            "coord": coord, "tag": "delta",
        })
        print(f"Delta: dx={dx:.3f} dy={dy:.3f} dz={dz:.3f} dry={dry:.3f}")

    def open_gripper(self) -> None:
        """Open the gripper fully.

        Returns:
            None
        """
        self._post("/gripper", {"action": "open"})
        print("Gripper opened")

    def close_gripper(self, force_threshold: float | None = None) -> None:
        """Close the gripper to grasp an object (simple position close).

        Args:
            force_threshold: (float, optional) Not used in the motion-only API
                (adaptive grasp is disabled).

        Returns:
            None
        """
        payload: dict = {"action": "close", "use_adaptive_grasp": False}
        if force_threshold is not None:
            payload["force_threshold"] = force_threshold
        self._post("/gripper", payload)
        print("Gripper closed")

    # ----

    def rotate_gripper(
        self, object_name: str, angle_rad: float, steps: int = 4
    ) -> None:
        """Rotate the gripper while holding an object. Use for large rotations
        instead of move_delta.

        Args:
            object_name: The name of the object being held.
            angle_rad: (float) Total rotation angle in radians.
            steps: (int) Number of incremental steps to split the rotation into.

        Returns:
            None
        """
        self._post("/rotate_gripper", {
            "object_name": object_name, "angle_rad": angle_rad, "steps": steps,
        })
        print(f"Rotated '{object_name}' by {angle_rad:.2f} rad in {steps} steps")

    def get_object_pose(
        self, object_name: str, return_bbox_extent: bool = False
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
        """Get the pose of an object in the scene. position is the prim's world
        origin, not necessarily its center: in grasp_classify the prism and
        pads have their origin at the bottom face (resting z ~0.001), so to
        rest an origin-at-bottom object on a pad, target
        position[2] = pad_pos[2] + pad_ext[2].

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
        print(f"Object '{object_name}': pos={np.round(position, 3)} quat_w={quat[0]:.3f}")
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
