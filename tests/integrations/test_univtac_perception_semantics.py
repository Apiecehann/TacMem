"""Unit tests for UniVTACPerceptionApi.get_object_pose semantics (no GPU).

Verifies the non-privileged perception contract without any server or GPU:
SAM3 segmentation + depth back-projection → position = median point-cloud
centroid (NOT prim origin), quaternion always identity, bbox = np.ptp.
Also covers the empty-depth retry path, the mask/depth resolution mismatch
(NEAREST resize) branch, and the three plausibility gates a candidate mask
must clear before it is trusted: at least _MIN_POINTS back-projected points,
at most _MAX_POINTS, and not dominated by points within _NEAR_CAMERA_M of
the camera (the robot's own arm).

The masks here are deliberately larger than they look like they need to be:
a plausible object mask has to clear _MIN_POINTS = 30, so the 8x8 grids the
tests used to build would now be rejected outright.
"""

from __future__ import annotations

import base64
import io
from unittest import mock

import numpy as np
import pytest
from PIL import Image

from capx.integrations.tactile.univtac_perception_api import (
    UniVTACPerceptionApi,
)


def _obs_dict(depth: np.ndarray, intrinsics, pos_w, quat_w_ros) -> dict:
    """Build a /observation response dict for the head camera."""
    h, w = depth.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="PNG")
    return {
        "head_rgb_base64": base64.b64encode(buf.getvalue()).decode("ascii"),
        "head_depth_float32_base64": base64.b64encode(
            depth.astype(np.float32).tobytes()
        ).decode("ascii"),
        "head_image_shape": [h, w],
        "head_intrinsics": np.asarray(intrinsics, dtype=float).tolist(),
        "head_cam_pos_w": np.asarray(pos_w, dtype=float).tolist(),
        "head_cam_quat_w_ros": np.asarray(quat_w_ros, dtype=float).tolist(),
    }


def _make_api(obs: dict, seg_results, _get_calls=None):
    """Construct UniVTACPerceptionApi with SAM3 + HTTP mocked away.

    Returns (api, fake_seg_fn, get_mock). The observation dict is returned
    by _get every call; seg_results are returned by the fake segment_fn.
    """
    fake_seg = mock.Mock(return_value=seg_results)
    with mock.patch(
        "capx.integrations.tactile.univtac_perception_api.init_sam3",
        return_value=fake_seg,
    ):
        api = UniVTACPerceptionApi(object(), server_url="http://test:8120")
    get_mock = mock.Mock(return_value=obs)
    api._get = get_mock  # type: ignore[method-assign]
    return api, fake_seg, get_mock


def _world_points(obs: dict, mask: np.ndarray) -> np.ndarray:
    """Back-project a mask through the same math the API uses, for asserts."""
    depth = np.frombuffer(
        base64.b64decode(obs["head_depth_float32_base64"]), dtype=np.float32
    ).reshape(obs["head_image_shape"])
    intr = np.asarray(obs["head_intrinsics"])
    pos_w = np.asarray(obs["head_cam_pos_w"])
    quat = np.asarray(obs["head_cam_quat_w_ros"])
    fx, fy = intr[0, 0], intr[1, 1]
    cx, cy = intr[0, 2], intr[1, 2]
    ys, xs = np.nonzero(mask)
    z = depth[ys, xs]
    valid = np.isfinite(z) & (z > 0.0)
    ys, xs, z = ys[valid], xs[valid], z[valid]
    cam = np.stack([(xs - cx) * z / fx, (ys - cy) * z / fy, z], axis=-1)
    return cam + pos_w  # identity camera quat in these tests


def _flat_obs(h: int, w: int, z_plane: float) -> dict:
    """Observation at a constant-depth plane, identity camera, unit intrinsics."""
    depth = np.full((h, w), z_plane, dtype=np.float32)
    intr = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    return _obs_dict(depth, intr, [0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0])


def test_position_is_median_centroid_not_origin():
    """position must equal the median of the back-projected points."""
    h, w = 16, 16
    obs = _flat_obs(h, w, z_plane=1.0)
    mask = np.zeros((h, w), dtype=bool)
    mask[4:12, 5:13] = True  # 8x8 block = 64 points, clears _MIN_POINTS
    api, seg, _ = _make_api(obs, [{"mask": mask, "score": 0.9}])

    pos, quat, bbox = api.get_object_pose("black connector")

    expected = np.median(_world_points(obs, mask), axis=0)
    assert np.allclose(pos, expected)
    assert pos[2] == pytest.approx(1.0)


def test_quaternion_always_identity():
    """quaternion_wxyz must be [1, 0, 0, 0] regardless of anything."""
    h, w = 16, 16
    obs = _flat_obs(h, w, 1.0)
    mask = np.zeros((h, w), dtype=bool)
    mask[4:10, 4:10] = True  # 6x6 block = 36 points, clears _MIN_POINTS
    api, _, _ = _make_api(obs, [{"mask": mask, "score": 0.9}])

    _, quat, _ = api.get_object_pose("x")

    assert quat.tolist() == [1.0, 0.0, 0.0, 0.0]


def test_bbox_is_ptp_of_points():
    """bbox extent must equal np.ptp of the back-projected points."""
    h, w = 16, 16
    obs = _flat_obs(h, w, 1.0)
    mask = np.zeros((h, w), dtype=bool)
    mask[4:12, 5:13] = True  # 8x8 block = 64 points, clears _MIN_POINTS
    api, _, _ = _make_api(obs, [{"mask": mask, "score": 0.9}])

    _, _, bbox = api.get_object_pose("x", return_bbox_extent=True)

    assert np.allclose(bbox, np.ptp(_world_points(obs, mask), axis=0))


def test_empty_depth_retries_once_then_raises():
    """All-zero depth → empty points → one retry → still empty → RuntimeError."""
    h, w = 16, 16
    obs = _flat_obs(h, w, z_plane=0.0)  # z=0 is invalid depth (z > 0 required)
    mask = np.full((h, w), True, dtype=bool)
    api, seg, get_mock = _make_api(obs, [{"mask": mask, "score": 0.9}])

    # The message names the camera and the rejection reason, and tells the
    # caller what to do next -- it reaches the LLM verbatim.
    with pytest.raises(RuntimeError, match="rejected on both attempts"):
        api.get_object_pose("x")

    # Retried once: two /observation fetches, two segmentations.
    assert get_mock.call_count == 2
    assert seg.call_count == 2


def test_best_mask_scoring_skips_invalid_depth():
    """A higher-scoring mask with no valid depth loses to a lower-scoring one."""
    h, w = 16, 16
    obs = _flat_obs(h, w, 1.0)
    # Punch NaN (invalid) depth into the region the high-score mask covers.
    depth = np.frombuffer(
        base64.b64decode(obs["head_depth_float32_base64"]), dtype=np.float32
    ).reshape(h, w).copy()
    depth[2:4, 2:4] = np.nan
    obs["head_depth_float32_base64"] = base64.b64encode(
        depth.astype(np.float32).tobytes()
    ).decode("ascii")

    bad_mask = np.zeros((h, w), dtype=bool)
    bad_mask[2:4, 2:4] = True  # score 0.95 but zero valid depth pixels
    good_mask = np.zeros((h, w), dtype=bool)
    good_mask[4:12, 4:12] = True  # score 0.70, 64 valid points
    api, _, _ = _make_api(
        obs,
        [{"mask": bad_mask, "score": 0.95}, {"mask": good_mask, "score": 0.70}],
    )

    pos, _, _ = api.get_object_pose("x")

    assert np.allclose(pos, np.median(_world_points(obs, good_mask), axis=0))


def test_mask_depth_resolution_mismatch_resizes_nearest():
    """Half-res mask must be NEAREST-resized to depth res before projection."""
    h, w = 16, 16
    obs = _flat_obs(h, w, 1.0)
    small_mask = np.zeros((h // 2, w // 2), dtype=bool)
    small_mask[2:5, 2:5] = True  # 3x3 → 6x6 block after 2x NEAREST upscale
    api, _, _ = _make_api(obs, [{"mask": small_mask, "score": 0.9}])

    pos, _, bbox = api.get_object_pose("x", return_bbox_extent=True)

    # The 3x3 block at (2..4) upscales to rows/cols 4..9 — 36 points at z=1.0,
    # which is what clears _MIN_POINTS.  With unit intrinsics x = u and y = v,
    # so both medians are the middle of {4..9}, i.e. 6.5.
    assert np.allclose(pos, [6.5, 6.5, 1.0])
    # A 6x6 block at z=1.0 spans 5 m in x and y with unit intrinsics.
    assert bbox[0] == pytest.approx(5.0)
    assert bbox[1] == pytest.approx(5.0)
    assert bbox[2] == pytest.approx(0.0)


def test_sliver_mask_loses_to_a_lower_scoring_plausible_mask():
    """A sub-_MIN_POINTS sliver loses even when it holds the top score.

    SAM3 scores are close and flip nondeterministically between the object
    and a few-pixel background sliver, which back-projects to a handful of
    points.  Ranking on score alone would return that sliver.
    """
    h, w = 16, 16
    obs = _flat_obs(h, w, 1.0)
    sliver = np.zeros((h, w), dtype=bool)
    sliver[1:3, 1:3] = True  # 4 points, below _MIN_POINTS
    real = np.zeros((h, w), dtype=bool)
    real[4:12, 4:12] = True  # 64 points
    api, _, _ = _make_api(
        obs,
        [{"mask": sliver, "score": 0.99}, {"mask": real, "score": 0.60}],
    )

    pos, _, _ = api.get_object_pose("x")

    assert np.allclose(pos, np.median(_world_points(obs, real), axis=0))


def test_self_occlusion_cloud_loses_to_a_distant_mask():
    """A cloud dominated by points within _NEAR_CAMERA_M is the robot's arm.

    The camera looks at a plane 1 cm out, so the middle of the image
    back-projects to within a few centimetres of the lens — what a mask on
    the arm itself looks like.  A mask out at the corner must win instead.
    """
    h, w = 16, 16
    # With unit fx/fy one pixel spans one metre, so the plane has to sit
    # 1 cm out for the back-projected points to land in centimetre range.
    depth = np.full((h, w), 0.01, dtype=np.float32)
    intr = np.array([[1.0, 0.0, 8.0], [0.0, 1.0, 8.0], [0.0, 0.0, 1.0]])
    obs = _obs_dict(depth, intr, [0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0])
    arm = np.zeros((h, w), dtype=bool)
    arm[4:13, 4:13] = True  # 69 of 81 points within ~4.9 cm of the camera
    far = np.zeros((h, w), dtype=bool)
    far[0:6, 0:6] = True  # 36 points, all but one over 5 cm away
    api, _, _ = _make_api(
        obs,
        [{"mask": arm, "score": 0.95}, {"mask": far, "score": 0.60}],
    )

    pos, _, _ = api.get_object_pose("x")

    assert np.allclose(pos, np.median(_world_points(obs, far), axis=0))


def test_get_ee_pose_returns_server_payload_verbatim():
    """get_ee_pose is a read-only passthrough: /ee_pose, raw dict back.

    No masking, no privilege checks, and (like get_observation) it is NOT
    registered in functions() — the functions() list is hand-maintained and
    the method never appears in it. The payload shape mirrors the server:
    position [x, y, z] metres + quaternion_wxyz [w, x, y, z].
    """
    payload = {
        "position": [0.4, -0.1, 0.35],
        "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
    }
    api, _, get_mock = _make_api(payload, [])

    out = api.get_ee_pose()

    assert out == payload
    get_mock.assert_called_once_with("/ee_pose")
