"""Step recorder and camera helpers (task-agnostic).

Generalizes the ad-hoc ``LOG`` dicts and PIL saves the agent wrote by hand
in earlier episodes. The row format (``L_``/``R_`` per-fingertip fields)
matches what those logs already used, so old and new episodes compare.
``zoom`` / ``segmented_zoom`` generalize the hand-written crop-and-magnify
scripts: full views cannot resolve fine state, so crop the region of
interest and upscale it before judging.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

# Module-level ``import robo`` (NOT ``from robo import ...``): robo imports
# tools._config, which pulls this module in while robo is still initializing;
# binding the names here would fail on the partial module.  Access the
# attributes at call time instead (robo is fully loaded by then).
import robo
from ._config import SKILL_DIR

SCRATCH_DIR = SKILL_DIR / "scratch"


def _r2(v):
    return round(float(v), 2) if isinstance(v, (int, float)) else v


def _r3(v):
    return round(float(v), 3) if isinstance(v, (int, float)) else v


def tactile_row(td: dict | None) -> dict:
    """Extract the standard per-fingertip fields from a tactile dict."""
    if not td:
        return {}
    return {
        "L_contact": td.get("left_contact"),
        "R_contact": td.get("right_contact"),
        "L_depth_mm": _r2(td.get("left_depth_mm")),
        "R_depth_mm": _r2(td.get("right_depth_mm")),
        "L_disp_px": _r2(td.get("left_marker_displacement_px")),
        "R_disp_px": _r2(td.get("right_marker_displacement_px")),
        "L_coh": _r3(td.get("left_marker_coherence")),
        "R_coh": _r3(td.get("right_marker_coherence")),
    }


class StepRecorder:
    """Append-only per-step logger with a compact printed line.

    Usage::

        rec = StepRecorder("insert_probe", out_path="scratch/insert_probe_log.json")
        for i in range(1, 6):
            td = move_delta(dz=-0.005)
            rec.step(td, dz_m=-0.005, plug_z=z)   # tactile + your extra fields
        rec.save()

    ``out_path`` defaults to ``scratch/<name>_log.json``; set ``out_dir`` to
    keep the default name in another directory.
    """

    def __init__(self, name="run", out_path=None, out_dir=None):
        self.name = name
        self.rows: list[dict] = []
        if out_path is None:
            out_dir = SCRATCH_DIR if out_dir is None else Path(out_dir)
            out_path = out_dir / f"{name}_log.json"
        self.out_path = Path(out_path)
        self._i = 0

    def step(self, td=None, i=None, **extra):
        """Record one step row: tactile fields plus ``extra``. Returns the row."""
        i = i if i is not None else self._i + 1
        self._i = max(self._i, i)
        row = {"step": i}
        row.update(tactile_row(td))
        row.update({k: v for k, v in extra.items() if v is not None})
        self.rows.append(row)
        print(self._fmt(row))
        return row

    def add(self, key, value):
        """Attach a per-step field (e.g. ``start_mean_depth``) to the last row."""
        if self.rows:
            self.rows[-1][key] = value
        return value

    def save(self, out_path=None):
        """Write all rows as JSON. Returns the written path."""
        p = Path(out_path) if out_path else self.out_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.rows, indent=2), encoding="utf-8")
        print(f"log saved -> {p}")
        return p

    def _fmt(self, row):
        parts = [f"step {row.get('step', '?'):>2d}"]
        if row.get("dz_m") is not None:
            parts.append(f"dz={row['dz_m']:+.4f}")
        if row.get("L_depth_mm") is not None:
            parts.append(f"L c={row.get('L_contact')} d={row['L_depth_mm']:6.2f} "
                         f"disp={row.get('L_disp_px')} coh={row.get('L_coh')}")
        if row.get("R_depth_mm") is not None:
            parts.append(f"R c={row.get('R_contact')} d={row['R_depth_mm']:6.2f} "
                         f"disp={row.get('R_disp_px')} coh={row.get('R_coh')}")
        for k, v in row.items():
            if k in ("step", "dz_m", "L_contact", "R_contact", "L_depth_mm",
                     "R_depth_mm", "L_disp_px", "R_disp_px", "L_coh", "R_coh"):
                continue
            parts.append(f"{k}={v}")
        return " | ".join(parts)


def snapshot(name: str, out_dir=None) -> dict:
    """Save current head/wrist camera images; return {cam: path}.

    Images are written as ``<out_dir>/<name>_<cam>.png`` where ``out_dir``
    defaults to ``univtac_skill/scratch/``.
    """
    obs = robo.get_observation(include_depth=False)
    if out_dir is None:
        out_dir = SCRATCH_DIR
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for cam in ("head", "wrist"):
        rgb = obs.get(f"{cam}_rgb")
        if rgb is None:
            continue
        p = out_dir / f"{name}_{cam}.png"
        Image.fromarray(np.asarray(rgb)).save(p)
        paths[cam] = str(p)
    return paths


# ---------------------------------------------------------------------------
# zoom / segmented_zoom — make fine state readable (see SKILL.md
# "Resolution matching"). Client-side image processing of the observation
# the agent already has: the server passes camera tensors through untouched,
# so cropping client-side loses nothing.
# ---------------------------------------------------------------------------

def _save_zoom(name: str, cam: str, rgb, box, scale: int,
               out_dir=None) -> dict:
    """Crop *box* (x0, y0, x1, y1, clipped to the image) and save upscaled."""
    rgb = np.asarray(rgb)
    if out_dir is None:
        out_dir = SCRATCH_DIR
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = [int(v) for v in box]
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, w), min(y1, h)
    if x1 <= x0 or y1 <= y0:
        return {}
    crop = rgb[y0:y1, x0:x1]
    up = np.repeat(np.repeat(crop, scale, axis=0), scale, axis=1)
    p = out_dir / f"{name}_{cam}_zoom.png"
    Image.fromarray(up).save(p)
    return {cam: str(p)}


def zoom(name: str, cam="head", box=None, center=None, size=200, scale=3,
         out_dir=None) -> dict:
    """Save a magnified crop of one camera image; return {cam: path}.

    Full views cannot resolve fine state (alignment, gaps, seating are a
    few pixels across) — crop the region of interest and nearest-neighbor
    upscale it before judging.

    Args:
        name: label for the output filename (<name>_<cam>_zoom.png).
        cam: "head" or "wrist".
        box: pixel ROI as (x0, y0, x1, y1) in the camera image; mutually
            exclusive with center.
        center: (x, y) pixel center; the ROI is the size x size square
            around it.
        size: square ROI side in pixels, used with center.
        scale: integer nearest-neighbor upscale factor (>= 1).
        out_dir: defaults to univtac_skill/scratch/.

    Returns:
        {"cam": path}, or {} if the camera image is unavailable or the
        ROI does not intersect the image.
    """
    if box is None and center is None:
        raise ValueError("zoom: pass box=(x0, y0, x1, y1) or center=(x, y)")
    rgb = robo.get_observation(include_depth=False).get(f"{cam}_rgb")
    if rgb is None:
        return {}
    if box is not None:
        x0, y0, x1, y1 = [int(v) for v in box]
    else:
        cx, cy = int(center[0]), int(center[1])
        x0, y0 = cx - size // 2, cy - size // 2
        x1, y1 = x0 + size, y0 + size
    return _save_zoom(name, cam, rgb, (x0, y0, x1, y1), scale, out_dir)


_sam3_seg_fn = None


def _get_seg_fn():
    """Lazily build the SAM3 text-prompt segmenter client (same one the
    perception API uses for get_object_pose)."""
    global _sam3_seg_fn
    if _sam3_seg_fn is None:
        from capx.integrations.vision.sam3 import init_sam3
        _sam3_seg_fn = init_sam3()
    return _sam3_seg_fn


def segmented_zoom(name: str, prompt: str, cam="head", margin_px=40, scale=3,
                   out_dir=None) -> dict:
    """Save a magnified crop around a SAM3-prompted object's 2D mask.

    Runs the same text-prompt segmentation as get_object_pose but keeps
    the 2D pixel mask (more reliable than its 3D centroid) — "use vision
    to feed vision": no need to guess where the object is in the image.

    Args:
        name: label for the output filename (<name>_<cam>_zoom.png).
        prompt: SAM3 text prompt (use robo.SAM3_PROMPTS[...], not the
            bare semantic name).
        cam: "head" or "wrist".
        margin_px: extra pixels padded around the mask's bounding box.
        scale: integer nearest-neighbor upscale factor (>= 1).
        out_dir: defaults to univtac_skill/scratch/.

    Returns:
        {"cam": path}, or {} if SAM3 segmented nothing for the prompt or
        the camera image is unavailable.
    """
    rgb = robo.get_observation(include_depth=False).get(f"{cam}_rgb")
    if rgb is None:
        return {}
    results = _get_seg_fn()(rgb, text_prompt=prompt)
    if not results:
        print(f"segmented_zoom: no SAM3 result for prompt '{prompt}'")
        return {}
    best = max(results, key=lambda r: r.get("score", 0.0))
    x0, y0, x1, y1 = [int(v) for v in best["box"]]
    return _save_zoom(name, cam, rgb, (x0 - margin_px, y0 - margin_px,
                                       x1 + margin_px, y1 + margin_px),
                      scale, out_dir)
