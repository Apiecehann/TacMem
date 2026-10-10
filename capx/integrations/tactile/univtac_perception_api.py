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

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation as SciRotation

from capx.envs.base import BaseEnv
from capx.integrations.tactile.univtac_api import UniVTACTactileApi
from capx.integrations.vision.sam3 import init_sam3

# Camera used for perception. The head camera sees the whole workspace; the
# wrist camera is close to the gripper (better for small objects / insertion).
_DEFAULT_CAMERA = "head"

# Mask plausibility thresholds for _perceive_once.  A segmented mask must
# back-project to between this many valid depth points to be trusted:
# fewer is a sliver (background blob / part of the robot arm), more is a
# whole viewport region (camera pointed at a wall or its own arm) —
# neither is the queried object.  Back-projected points within
# _NEAR_CAMERA_M of the camera are the robot's own arm, not the object.
_MIN_POINTS = 30
_MAX_POINTS = 10000
_NEAR_CAMERA_M = 0.05


class UniVTACPerceptionApi(UniVTACTactileApi):
    """UniVTAC tactile API where object poses come from SAM3 + depth."""

    # This is the non-privileged line: it must not be handed a grasp pose
    # derived from the simulator's ground truth, which would give away the
    # object's true position.  It keeps the EE-relative pose, whose offset is
    # seeded from the EE pose rather than from the object.
    _ground_truth_grasp = False

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

        Perception is heuristic and can fail: a mask must back-project to
        between 30 and 10000 valid depth points, and must not be dominated
        by points within 5 cm of the camera (the robot's own arm), to be
        trusted.  Queries that pass nothing plausible raise RuntimeError
        after one automatic re-fetch — usually the object is out of view:
        move the gripper for a better vantage and re-query, or fall back
        to the fixed layout described in the prompt.

        Returns:
            position: (3,) XYZ in meters (median of the back-projected points).
            quaternion_wxyz: (4,) WXYZ unit quaternion (identity; unreliable).
            bbox_extent: (3,) XYZ in meters (full side length, not half-length
                extent). If return_bbox_extent is False, returns None.
        """
        points_world, rejections = self._perceive_once(object_name, camera)
        if points_world.shape[0] == 0:
            # Every candidate mask was rejected (bad depth frame, tiny
            # background sliver, or a giant self-occlusion blob).  Transient
            # frame problems clear on a re-fetch — re-observe once before
            # giving up.
            print(f"Perception for '{object_name}' from '{camera}' rejected "
                  f"on attempt 1/2 ({rejections or 'no valid depth'}) — "
                  f"re-fetching /observation and re-segmenting")
            points_world, rejections = self._perceive_once(object_name, camera)
        if points_world.shape[0] == 0:
            raise RuntimeError(
                f"Perception for '{object_name}' from camera '{camera}' "
                f"rejected on both attempts ({rejections or 'no valid depth'}). "
                f"The object is likely out of view, or the prompt matched the "
                f"robot arm / a large background region.  Move the gripper for "
                f"a better vantage and re-query, or fall back to the known "
                f"layout."
            )

        position = np.median(points_world, axis=0)
        quaternion_wxyz = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
        print(f"Object '{object_name}' (perception): pos={np.round(position, 3)} "
              f"n_points={points_world.shape[0]}")

        bbox = None
        if return_bbox_extent:
            bbox = np.ptp(points_world, axis=0)  # full axis-aligned extent
        return position, quaternion_wxyz, bbox

    def _perceive_once(self, object_name: str, camera: str) -> tuple[np.ndarray, str]:
        """One fetch-and-segment cycle.

        Pulls /observation (decoded by ``get_observation``), runs SAM3 on the
        text prompt, and back-projects the highest-scoring mask into world
        points.  Raises on camera calibration or empty results; returns a
        possibly-empty point cloud plus a short human-readable rejection
        reason ("" when a mask was accepted).
        """
        obs = self.get_observation(include_depth=True)

        rgb = obs[f"{camera}_rgb"]
        depth = obs[f"{camera}_depth"]
        if rgb is None:
            raise RuntimeError("Server returned no RGB image for the requested camera")
        if depth is None:
            raise RuntimeError("Server returned no metric depth for the requested camera")
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
        # Pick the highest-scoring mask that back-projects to a plausible
        # point cloud.  Raw top-score selection can land on tiny background
        # blobs whose pixels all have invalid depth (SAM3 scores are close
        # and flip nondeterministically), which used to surface as "No
        # valid depth points" even though a usable mask was ranked just
        # below.  Masks that back-project to implausible point counts are
        # rejected outright — too few (a sliver of background or of the
        # robot arm) or too many (a whole viewport region: the camera
        # looking at a wall or its own arm) — as are clouds dominated by
        # points within _NEAR_CAMERA_M of the camera (the arm, not the
        # queried object).
        rejections = []
        for r in sorted(results, key=lambda r: r.get("score", 0.0), reverse=True):
            mask = np.asarray(r["mask"], dtype=bool)
            points = self._mask_to_world_points(
                mask, depth, intrinsics, pos_w, quat_w_ros
            )
            n = points.shape[0]
            if n == 0:
                rejections.append("no valid depth")
                continue
            if n < _MIN_POINTS or n > _MAX_POINTS:
                rejections.append(f"n={n}")
                continue
            if (
                np.linalg.norm(points - pos_w[None, :], axis=1) < _NEAR_CAMERA_M
            ).mean() > 0.5:
                rejections.append(f"n={n} near-camera")
                continue
            return points, ""
        return np.zeros((0, 3), dtype=np.float64), "; ".join(rejections)

    # ---- helpers ----

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
