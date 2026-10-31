#!/usr/bin/env python3
"""Turn a probe_tension_strap.py log into the numbers the oracle needs.

Reads the JSONL rows the probe writes and answers three questions:

  * How steep is the strap?  tension against commanded rise, which is what a
    distance-based plan would have to know and cannot, because the modulus is
    drawn per seed.
  * Does the gel carry the load?  tension against each tactile scalar, with
    the slope, the R^2 and the residual scatter.  The residual is the number
    that decides the task: an oracle that sits on the calibrated reading lands
    within +-0.5 N only if that scatter is well inside it.
  * Does a no-op move advance the clock, and does the tension survive it?

Usage:
    .venv/bin/python scripts/analyze_tension_probe.py outputs/probe_tension_strap/seed_0.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

CANDIDATES = (
    # The one the oracle uses: the two pads are mounted mirrored, so the same
    # physical slip has opposite sign on them and the difference is the load.
    ("gel_shear_diff_px", ("left_marker_shear_dx", "right_marker_shear_dx")),
    ("gel_level_px", ("left_marker_displacement_px", "right_marker_displacement_px")),
    ("gel_shear_mag_px", ("left_marker_shear_dx", "left_marker_shear_dy",
                          "right_marker_shear_dx", "right_marker_shear_dy")),
    ("gel_depth_mm", ("left_depth_mm", "right_depth_mm")),
)


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def series(rows: list[dict], name: str, keys: tuple[str, ...]) -> np.ndarray:
    out = []
    for row in rows:
        values = [row.get(key) for key in keys]
        if any(value is None for value in values):
            out.append(np.nan)
            continue
        if name == "gel_shear_diff_px":
            out.append(values[0] - values[1])
        elif name == "gel_shear_mag_px":
            # The two pads are mounted mirrored, so the same physical slide
            # shows up with opposite sign; the magnitude is sign-free.
            left = float(np.hypot(values[0], values[1]))
            right = float(np.hypot(values[2], values[3]))
            out.append(0.5 * (left + right))
        else:
            out.append(float(np.mean(values)))
    return np.asarray(out, dtype=float)


def fit(x: np.ndarray, y: np.ndarray) -> dict:
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 3:
        return {"n": int(mask.sum())}
    slope, intercept = np.polyfit(x[mask], y[mask], 1)
    residual = y[mask] - (slope * x[mask] + intercept)
    total = y[mask] - y[mask].mean()
    r2 = 1.0 - float((residual ** 2).sum() / max((total ** 2).sum(), 1e-12))
    return {
        "n": int(mask.sum()),
        "slope": float(slope),
        "intercept": float(intercept),
        "r2": r2,
        "residual_std_N": float(residual.std()),
        "residual_max_N": float(np.abs(residual).max()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()

    rows = load(args.path)
    print(f"{len(rows)} rows from {args.path}")
    for row in rows[:6]:
        print("  %-20s tension=%s step=%s" % (row["note"], row["tension_N"], row.get("step")))

    ramp = [r for r in rows if r["note"].startswith("pull_")]
    tension = np.array([np.nan if r["tension_N"] is None else r["tension_N"] for r in ramp])
    rise = np.array([np.nan if r["commanded_ee_z"] is None else
                     r["commanded_ee_z"] for r in ramp])
    if len(ramp):
        rise = rise - rise[0]

    print("\n--- strap stiffness (the seed-dependent number) ---")
    if len(ramp) >= 3:
        print("  tension ramp_N:", np.round(tension, 2).tolist())
        print("  rise_mm      :", np.round(rise * 1000.0, 1).tolist())
        stats = fit(rise, tension)
        print("  N per mm     : %.3f  (r2 %.4f)" % (stats["slope"] * 1000.0, stats["r2"]))

    print("\n--- does the gel carry the load? ---")
    for name, keys in CANDIDATES:
        x = series(ramp, name, keys)
        stats = fit(x, tension)
        print("  %-16s %s" % (name, {k: (round(v, 4) if isinstance(v, float) else v)
                                     for k, v in stats.items()}))
        if "slope" in stats and stats["slope"]:
            px_per_n = 1.0 / stats["slope"]
            print("  %-16s px per N = %.3f   (+-0.5 N = %.2f of the reading)"
                  % ("", px_per_n, 0.5 * abs(stats["slope"])))

    print("\n--- hold cost ---")
    holds = [r for r in rows if r["note"].startswith("hold_")]
    for row in holds:
        print("  %-8s ticks=%s tension=%s success=%s"
              % (row["note"], row.get("hold_ticks"), row["tension_N"],
                 row.get("hold_success")))
    ticks = [r.get("hold_ticks") for r in holds if r.get("hold_ticks")]
    if ticks:
        print("  ticks per no-op move: median %.0f  (a 3 s hold = 360 ticks "
              "= ~%.0f moves)" % (np.median(ticks), 360.0 / np.median(ticks)))
    if holds:
        values = [r["tension_N"] for r in holds if r["tension_N"] is not None]
        if len(values) > 1:
            print("  tension drift over the hold: %.3f N" % (max(values) - min(values)))

    steps = [r.get("step") for r in rows if r.get("step") is not None]
    if len(steps) > 1:
        print("\nsteps consumed: %d -> %d (%d ticks of the 7200-tick budget)"
              % (steps[0], steps[-1], steps[-1] - steps[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
