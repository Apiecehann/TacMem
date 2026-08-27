"""Unit tests for UniVTACPerceptionApi.get_object_pose semantics (no GPU).

Verifies the non-privileged perception contract without any server or GPU:
SAM3 segmentation + depth back-projection → position = median point-cloud
centroid (NOT prim origin), quaternion always identity, bbox = np.ptp.
Also covers the empty-depth retry path and the mask/depth resolution
mismatch (NEAREST resize) branch.
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
    h, w = 8, 8
    obs = _flat_obs(h, w, z_plane=1.0)
    mask = np.zeros((h, w), dtype=bool)
    mask[2:6, 3:7] = True  # 4x4 block of pixels
    api, seg, _ = _make_api(obs, [{"mask": mask, "score": 0.9}])

    pos, quat, bbox = api.get_object_pose("black connector")

    expected = np.median(_world_points(obs, mask), axis=0)
    assert np.allclose(pos, expected)
    assert pos[2] == pytest.approx(1.0)


def test_quaternion_always_identity():
    """quaternion_wxyz must be [1, 0, 0, 0] regardless of anything."""
    h, w = 8, 8
    obs = _flat_obs(h, w, 1.0)
    mask = np.zeros((h, w), dtype=bool)
    mask[1:3, 1:3] = True
    api, _, _ = _make_api(obs, [{"mask": mask, "score": 0.9}])

    _, quat, _ = api.get_object_pose("x")

    assert quat.tolist() == [1.0, 0.0, 0.0, 0.0]


def test_bbox_is_ptp_of_points():
    """bbox extent must equal np.ptp of the back-projected points."""
    h, w = 8, 8
    obs = _flat_obs(h, w, 1.0)
    mask = np.zeros((h, w), dtype=bool)
    mask[2:6, 3:7] = True
    api, _, _ = _make_api(obs, [{"mask": mask, "score": 0.9}])

    _, _, bbox = api.get_object_pose("x", return_bbox_extent=True)

    assert np.allclose(bbox, np.ptp(_world_points(obs, mask), axis=0))


def test_empty_depth_retries_once_then_raises():
    """All-zero depth → empty points → one retry → still empty → RuntimeError."""
    h, w = 8, 8
    obs = _flat_obs(h, w, z_plane=0.0)  # z=0 is invalid depth (z > 0 required)
    mask = np.full((h, w), True, dtype=bool)
    api, seg, get_mock = _make_api(obs, [{"mask": mask, "score": 0.9}])

    with pytest.raises(RuntimeError, match="No valid depth points"):
        api.get_object_pose("x")

    # Retried once: two /observation fetches, two segmentations.
    assert get_mock.call_count == 2
    assert seg.call_count == 2


def test_best_mask_scoring_skips_invalid_depth():
    """A higher-scoring mask with no valid depth loses to a lower-scoring one."""
    h, w = 8, 8
    obs = _flat_obs(h, w, 1.0)
    # Punch NaN (invalid) depth into the region the high-score mask covers.
    depth = np.frombuffer(
        base64.b64decode(obs["head_depth_float32_base64"]), dtype=np.float32
    ).reshape(h, w).copy()
    depth[1:3, 1:3] = np.nan
    obs["head_depth_float32_base64"] = base64.b64encode(
        depth.astype(np.float32).tobytes()
    ).decode("ascii")

    bad_mask = np.zeros((h, w), dtype=bool)
    bad_mask[1:3, 1:3] = True  # score 0.95 but zero valid depth pixels
    good_mask = np.zeros((h, w), dtype=bool)
    good_mask[2:6, 3:7] = True  # score 0.70, valid depth
    api, _, _ = _make_api(
        obs,
        [{"mask": bad_mask, "score": 0.95}, {"mask": good_mask, "score": 0.70}],
    )

    pos, _, _ = api.get_object_pose("x")

    assert np.allclose(pos, np.median(_world_points(obs, good_mask), axis=0))


def test_mask_depth_resolution_mismatch_resizes_nearest():
    """Half-res mask must be NEAREST-resized to depth res before projection."""
    h, w = 8, 8
    obs = _flat_obs(h, w, 1.0)
    small_mask = np.zeros((h // 2, w // 2), dtype=bool)
    small_mask[1, 1] = True  # 1 pixel → 2x2 block after 2x NEAREST upscale
    api, _, _ = _make_api(obs, [{"mask": small_mask, "score": 0.9}])

    pos, _, bbox = api.get_object_pose("x", return_bbox_extent=True)

    # After 2x NEAREST upscale, pixel (1,1) becomes a 2x2 block at (2,3)x(2,3):
    # points at (2,2,1), (3,2,1), (2,3,1), (3,3,1) — median (2.5, 2.5, 1.0).
    assert np.allclose(pos, [2.5, 2.5, 1.0])
    # A 2x2 block at z=1.0 spans exactly 1 m in x and y with unit intrinsics.
    assert bbox[0] == pytest.approx(1.0)
    assert bbox[1] == pytest.approx(1.0)
    assert bbox[2] == pytest.approx(0.0)


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
