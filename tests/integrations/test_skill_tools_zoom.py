"""Unit tests for univtac_skill/tools zoom + segmented_zoom.

No GPU, no server: get_observation and the SAM3 segmenter are mocked.
Verifies the primitive contracts: box/center crops are pixel-exact and
nearest-neighbor upscaled, out-of-bounds ROIs clip, a missing camera or
empty SAM3 result returns {}, and segmented_zoom crops the best-score
mask's box plus margin.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "univtac_skill"))

from tools import segmented_zoom, zoom  # noqa: E402


def _fake_image(h=480, w=320):
    """Grayscale-ish (H, W, 3) image whose pixel value equals its x index —
    lets tests assert exact crop content from the first channel."""
    x = np.tile(np.arange(w, dtype=np.uint8), (h, 1))
    return np.stack([x, x, x], axis=-1)


def _fake_obs(image, cam="head"):
    return {f"{cam}_rgb": image}


def test_zoom_box_crop_pixel_exact_and_upscaled(tmp_path):
    img = _fake_image()
    with mock.patch("tools.record.robo.get_observation", return_value=_fake_obs(img)):
        res = zoom("t", cam="head", box=(100, 50, 150, 80), scale=3,
                   out_dir=tmp_path)

    p = tmp_path / "t_head_zoom.png"
    assert res == {"head": str(p)}
    assert p.exists()
    out = np.asarray(__import__("PIL").Image.open(p))
    assert out.shape == ((80 - 50) * 3, (150 - 100) * 3, 3)
    # Top-left pixel of the crop = original pixel (x=100, y=50).
    assert out[0, 0, 0] == 100
    # Nearest-neighbor: the 3x3 block repeats the same original pixel.
    assert out[:3, :3, 0].tolist() == [[100] * 3] * 3


def test_zoom_center_uses_size_square(tmp_path):
    img = _fake_image()
    with mock.patch("tools.record.robo.get_observation", return_value=_fake_obs(img)):
        res = zoom("t", cam="head", center=(160, 240), size=40, scale=2,
                   out_dir=tmp_path)

    p = tmp_path / "t_head_zoom.png"
    assert res == {"head": str(p)}
    out = np.asarray(__import__("PIL").Image.open(p))
    assert out.shape == (80, 80, 3)
    assert out[0, 0, 0] == 140  # x0 = 160 - 20


def test_zoom_requires_box_or_center():
    with mock.patch("tools.record.robo.get_observation",
                    return_value=_fake_obs(_fake_image())):
        with pytest.raises(ValueError):
            zoom("t")


def test_zoom_missing_camera_returns_empty(tmp_path):
    with mock.patch("tools.record.robo.get_observation",
                    return_value=_fake_obs(_fake_image(), cam="head")):
        res = zoom("t", cam="wrist", box=(0, 0, 50, 50), out_dir=tmp_path)

    assert res == {}


def test_zoom_out_of_bounds_clips(tmp_path):
    img = _fake_image()
    with mock.patch("tools.record.robo.get_observation", return_value=_fake_obs(img)):
        res = zoom("t", cam="head", box=(-20, -10, 30, 40), scale=1,
                   out_dir=tmp_path)

    assert res  # intersects the image -> saved, clipped
    out = np.asarray(__import__("PIL").Image.open(tmp_path / "t_head_zoom.png"))
    # After clipping negatives: x 0→30, y 0→40; shape is (H, W, 3).
    assert out.shape == (40, 30, 3)


def test_zoom_fully_outside_returns_empty(tmp_path):
    img = _fake_image()
    with mock.patch("tools.record.robo.get_observation", return_value=_fake_obs(img)):
        res = zoom("t", cam="head", box=(400, 400, 500, 500), out_dir=tmp_path)

    assert res == {}


def test_segmented_zoom_crops_mask_box_plus_margin(tmp_path):
    img = _fake_image()
    seg = mock.Mock(return_value=[
        {"mask": None, "box": [120, 60, 140, 90], "score": 0.9, "label": "o"},
    ])
    with mock.patch("tools.record.robo.get_observation", return_value=_fake_obs(img)), \
         mock.patch("tools.record._get_seg_fn", return_value=seg):
        res = segmented_zoom("t", "some object", cam="head", margin_px=10,
                             scale=2, out_dir=tmp_path)

    p = tmp_path / "t_head_zoom.png"
    assert res == {"head": str(p)}
    out = np.asarray(__import__("PIL").Image.open(p))
    # box + margin: (120-10, 60-10, 140+10, 90+10) = (110, 50, 150, 100).
    assert out.shape == ((100 - 50) * 2, (150 - 110) * 2, 3)
    assert out[0, 0, 0] == 110
    # The segmenter saw the SAME image the crop was made from.
    seg.assert_called_once()
    args, kwargs = seg.call_args
    assert np.array_equal(args[0], img)
    assert kwargs["text_prompt"] == "some object"


def test_segmented_zoom_picks_best_score(tmp_path):
    img = _fake_image()
    seg = mock.Mock(return_value=[
        {"mask": None, "box": [10, 10, 20, 20], "score": 0.5, "label": "bg"},
        {"mask": None, "box": [200, 100, 220, 120], "score": 0.95, "label": "o"},
    ])
    with mock.patch("tools.record.robo.get_observation", return_value=_fake_obs(img)), \
         mock.patch("tools.record._get_seg_fn", return_value=seg):
        res = segmented_zoom("t", "o", margin_px=0, scale=1, out_dir=tmp_path)

    assert res
    out = np.asarray(__import__("PIL").Image.open(tmp_path / "t_head_zoom.png"))
    assert out.shape == (20, 20, 3)
    assert out[0, 0, 0] == 200  # high-score box, not the background blob


def test_segmented_zoom_no_result_returns_empty(tmp_path):
    img = _fake_image()
    seg = mock.Mock(return_value=[])
    with mock.patch("tools.record.robo.get_observation", return_value=_fake_obs(img)), \
         mock.patch("tools.record._get_seg_fn", return_value=seg):
        res = segmented_zoom("t", "nothing", out_dir=tmp_path)

    assert res == {}


def test_segmented_zoom_missing_camera_returns_empty(tmp_path):
    seg = mock.Mock(return_value=[{"mask": None, "box": [0, 0, 5, 5],
                                   "score": 1.0, "label": "o"}])
    with mock.patch("tools.record.robo.get_observation",
                    return_value=_fake_obs(_fake_image(), cam="wrist")), \
         mock.patch("tools.record._get_seg_fn", return_value=seg):
        res = segmented_zoom("t", "o", cam="head", out_dir=tmp_path)

    assert res == {}
    seg.assert_not_called()
