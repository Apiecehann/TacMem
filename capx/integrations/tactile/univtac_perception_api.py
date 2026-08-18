"""UniVTAC non-privileged perception API.

Same motion + tactile functions as :class:`UniVTACTactileApi`, but object poses
come from visual perception (SAM3 text-prompt segmentation + metric depth
back-projection) instead of the simulator's ground-truth ``/object_pose``
endpoint. This mirrors the original caP-X split between the non-privileged
``FrankaHandoverApi`` (SAM3) and the privileged ``FrankaHandoverPrivilegedApi``
(ground truth).

Only ``get_object_pose`` is overridden. Motion primitives, tactile sensing and
``check_success`` are inherited unchanged. ``sample_grasp_pose`` is still the
ground-truth grasp sampler (grasp perception is out of scope for this class).
"""

from __future__ import annotations

import base64
import io
from typing import Any

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation as SciRotation

from capx.envs.base import BaseEnv
from capx.integrations.tactile.univtac_api import UniVTACTactileApi
from capx.integrations.vision.sam3 import init_sam3

# Camera used for perception. The head camera sees the whole workspace; the
# wrist camera is close to the gripper (better for small objects / insertion).
_DEFAULT_CAMERA = "head"


class UniVTACPerceptionApi(UniVTACTactileApi):
    """UniVTAC tactile API where object poses come from SAM3 + depth."""

    def __init__(self, env: BaseEnv, server_url: str | None = None) -> None:
        super().__init__(env, server_url)
        print("init UniVTAC perception api (SAM3)")
        self._sam3_seg_fn = init_sam3()

    def get_object_pose(
        self,
        object_name: str,
        return_bbox_extent: bool = False,
        camera: str = _DEFAULT_CAMERA,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
        """Get an object's pose from the camera using SAM3 segmentation + depth.
        The object is localized by its text prompt and its pixels are
        back-projected into a world-frame point cloud. position is the median
        of those points (a robust centroid of the segmented object), not the
        prim origin returned by privileged mode, and it is noisy — do not
        over-correct from it. quaternion_wxyz is unreliable, so disregard it.

        Args:
            object_name: The natural-language text prompt describing the object.
            return_bbox_extent:  Whether to return the axis-aligned world-frame
                bbox size. Default is False.
            camera:  Which camera to perceive from, "head" (whole workspace) or
                "wrist" (near the gripper). Default is "head".

        Returns:
            position: (3,) XYZ in meters (median of the back-projected points).
            quaternion_wxyz: (4,) WXYZ unit quaternion (identity; unreliable).
            bbox_extent: (3,) XYZ in meters (full side length, not half-length
                extent). If return_bbox_extent is False, returns None.
        """
        points_world = self._perceive_once(object_name, camera)
        if points_world.shape[0] == 0:
            # The best mask back-projected to no valid depth (e.g. one bad
            # depth frame).  Transient — re-fetch /observation and re-segment
            # once before giving up.
            print(f"No valid depth points for '{object_name}' on attempt 1/2 — "
                  f"re-fetching /observation and re-segmenting")
            points_world = self._perceive_once(object_name, camera)
        if points_world.shape[0] == 0:
            raise RuntimeError(f"No valid depth points for '{object_name}'")

        position = np.median(points_world, axis=0)
        quaternion_wxyz = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
        print(f"Object '{object_name}' (perception): pos={np.round(position, 3)} "
              f"n_points={points_world.shape[0]}")

        bbox = None
        if return_bbox_extent:
            bbox = np.ptp(points_world, axis=0)  # full axis-aligned extent
        return position, quaternion_wxyz, bbox

    def _perceive_once(self, object_name: str, camera: str) -> np.ndarray:
        """One fetch-and-segment cycle.

        Pulls /observation, runs SAM3 on the text prompt, and back-projects
        the highest-scoring mask into world points.  Raises on camera
        calibration or empty results; returns a possibly-empty point cloud.
        """
        obs = self._get("/observation")

        rgb = self._decode_rgb(obs.get(f"{camera}_rgb_base64", ""))
        depth = self._decode_depth(
            obs.get(f"{camera}_depth_float32_base64", ""),
            obs.get(f"{camera}_image_shape"),
        )
        intrinsics = np.asarray(obs.get(f"{camera}_intrinsics"), dtype=np.float64)
        pos_w = np.asarray(obs.get(f"{camera}_cam_pos_w"), dtype=np.float64)
        quat_w_ros = np.asarray(obs.get(f"{camera}_cam_quat_w_ros"), dtype=np.float64)

        if intrinsics.shape != (3, 3) or pos_w.shape != (3,) or quat_w_ros.shape != (4,):
            raise RuntimeError(
                f"Camera '{camera}' calibration unavailable from server: "
                f"intrinsics={intrinsics.shape}, pos_w={pos_w.shape}, "
                f"quat_w_ros={quat_w_ros.shape}. Is the UniVTAC server recent "
                f"enough to expose metric camera data?"
            )

        results = self._sam3_seg_fn(rgb, text_prompt=object_name)
        if not results:
            raise RuntimeError(f"SAM3 found no object matching '{object_name}'")
        # Pick the highest-scoring mask that back-projects to at least one
        # valid depth point.  Raw top-score selection can land on tiny
        # background blobs whose pixels all have invalid depth (SAM3 scores
        # are close and flip nondeterministically), which used to surface as
        # "No valid depth points" even though a usable mask was ranked just
        # below.
        for r in sorted(results, key=lambda r: r.get("score", 0.0), reverse=True):
            mask = np.asarray(r["mask"], dtype=bool)
            points = self._mask_to_world_points(
                mask, depth, intrinsics, pos_w, quat_w_ros
            )
            if points.shape[0] > 0:
                return points
        return np.zeros((0, 3), dtype=np.float64)

    # ---- helpers ----

    @staticmethod
    def _decode_rgb(b64: str) -> np.ndarray:
        if not b64:
            raise RuntimeError("Server returned no RGB image for the requested camera")
        img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
        return np.asarray(img, dtype=np.uint8)

    @staticmethod
    def _decode_depth(b64: str, shape: Any) -> np.ndarray:
        if not b64 or not shape:
            raise RuntimeError("Server returned no metric depth for the requested camera")
        h, w = int(shape[0]), int(shape[1])
        raw = np.frombuffer(base64.b64decode(b64), dtype=np.float32)
        return raw.reshape(h, w)

    @staticmethod
    def _mask_to_world_points(
        mask: np.ndarray,
        depth: np.ndarray,
        intrinsics: np.ndarray,
        pos_w: np.ndarray,
        quat_w_ros: np.ndarray,
    ) -> np.ndarray:
        """Back-project a binary mask into a world-frame point cloud.

        Uses the standard pinhole back-projection (same formula as
        ``capx.utils.depth_utils.depth_to_pointcloud``)::

            x = (u - cx) * z / fx,  y = (v - cy) * z / fy,  z = depth

        ``quat_w_ros`` is the camera's world orientation in the ROS convention
        (camera frame has optical axis +Z), so it maps the camera-frame points
        directly into the world frame.
        """
        if mask.shape != depth.shape:
            # Defensive: SAM3 may return a mask at a different resolution.
            mask = np.asarray(
                Image.fromarray(mask.astype(np.uint8) * 255)
                .resize((depth.shape[1], depth.shape[0]), Image.NEAREST),
                dtype=bool,
            )
        fx, fy = intrinsics[0, 0], intrinsics[1, 1]
        cx, cy = intrinsics[0, 2], intrinsics[1, 2]
        ys, xs = np.nonzero(mask)
        z = depth[ys, xs]
        valid = np.isfinite(z) & (z > 0.0)
        ys, xs, z = ys[valid], xs[valid], z[valid]
        if z.size == 0:
            return np.zeros((0, 3), dtype=np.float64)
        cam = np.stack([(xs - cx) * z / fx, (ys - cy) * z / fy, z], axis=-1)
        rot = SciRotation.from_quat(
            [quat_w_ros[1], quat_w_ros[2], quat_w_ros[3], quat_w_ros[0]]
        )
        return rot.apply(cam) + pos_w
