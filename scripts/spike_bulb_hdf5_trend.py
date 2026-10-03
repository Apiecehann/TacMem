#!/usr/bin/env python
"""Offline: does any of our tactile quantities track the bulb's advance *within a grasp*?

Motivation. The earlier offline pass found exactly one thing with structure: the
differential y-shear ``asym = |Ldy - Rdy| / 2`` climbed 0.08 -> 0.90 inside each grasp
segment and fell back to ~0.01 at every hand change (713/894/1210/1748/2296/2856).  That
was filed as a *confound* ("privileged torque is flat at 0.0000 while asym climbs") --
but flat torque only rules out torque, not depth.  Inside one grasp the grip is constant,
so a monotone drift is a candidate depth signature.

The trap in testing that: within a segment the expert only ever screws *in*, so the
advance is monotone by construction and ANY smoothly-drifting quantity will "correlate".
Correlation is therefore not the test.  The tests that can fail are:

  (a) consistency -- does the quantity run the same distance over the same advance in
      every segment, or does it drift with something else?
  (b) reset -- does it really snap back at a regrasp, i.e. is it grasp-relative?
  (c) rate match -- the advance per segment is set by the yaw swept, so if the quantity
      is a depth proxy its per-segment swing should scale with that segment's yaw, not
      with wall-clock or step count.

Reads only the expert's recorded hdf5; no server needed.

    env -u ALL_PROXY -u all_proxy <env>/bin/python scripts/spike_bulb_hdf5_trend.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HDF5_DEFAULT = Path("/mnt/nas/shimufan/ViTaForge/data/bulb_tightening/bulb_tightening/hdf5/0.hdf5")

# A "hand change" is a discontinuity in the gripper->bulb relationship.  The earlier pass
# found them at these step indices; we re-detect rather than trust the list, but keep it
# as a cross-check.
KNOWN_HAND_CHANGES = [713, 894, 1210, 1748, 2296, 2856]


def _load(path: Path) -> dict:
    import h5py

    out: dict = {}
    with h5py.File(path, "r") as f:
        keys = []

        def walk(name, obj):
            if isinstance(obj, h5py.Dataset):
                keys.append(name)

        f.visititems(walk)

        def get(k):
            return np.asarray(f[k][()]) if k in f else None

        out["_keys"] = keys
        for side in ("left", "right"):
            for field in ("marker", "depth", "rgb_marker", "shear", "flow"):
                k = f"tactile/{side}_tactile/{field}"
                if k in f:
                    out[f"{side}_{field}"] = np.asarray(f[k][()])
        for k in ("actor/light_bulb", "actor/lamp_socket", "phase/name", "atom/tag"):
            if k in f:
                v = f[k][()]
                out[k.replace("/", "_")] = np.asarray(v)
    return out


def _finger_stats(marker: np.ndarray) -> dict:
    """marker: (T, 2, N, 2) = [reference, current] x N points x (x,y).

    Mirrors the server's ``_marker_stats``: displacement is measured against the stored
    reference (the unloaded frame), so the base cancels in any difference of two reads.
    """
    ref = marker[:, 0]           # (T, N, 2)
    cur = marker[:, 1]
    d = cur - ref                # (T, N, 2)
    return {
        "dx": d[..., 0],
        "dy": d[..., 1],
        "mag": np.linalg.norm(d, axis=-1),
    }


def _segments(bulb: np.ndarray, min_len: int = 40) -> list[tuple[int, int]]:
    """Split the trace on big single-step jumps in bulb xy -- a regrasp teleports the
    gripper's hold, so the bulb's world position jumps relative to the grasp frame."""
    p = bulb[:, :3]
    step = np.linalg.norm(np.diff(p, axis=0), axis=1)
    thresh = max(0.002, np.percentile(step, 99.5) * 3)
    cuts = np.where(step > thresh)[0] + 1
    bounds = [0, *cuts.tolist(), len(p)]
    segs = [(a, b) for a, b in zip(bounds[:-1], bounds[1:]) if b - a >= min_len]
    return segs


def _yaw(bulb: np.ndarray) -> np.ndarray:
    """Bulb world yaw in degrees from its quaternion column."""
    q = bulb[:, 3:7]  # wxyz, presumably
    if q.shape[1] != 4:
        return np.zeros(len(bulb))
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    return np.degrees(np.arctan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))


def _unwrap_deg(a: np.ndarray) -> np.ndarray:
    return np.degrees(np.unwrap(np.radians(a)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hdf5", type=Path, default=HDF5_DEFAULT)
    ap.add_argument("--out", type=Path, default=Path("/mnt/nas/shimufan/cap-x/outputs/spike_bulb_probe/trend"))
    args = ap.parse_args()

    if not args.hdf5.exists():
        print(f"missing {args.hdf5}", file=sys.stderr)
        return 2

    d = _load(args.hdf5)
    print("datasets:", len(d["_keys"]))
    for k in sorted(d["_keys"]):
        print("   ", k)

    L = _finger_stats(d["left_marker"])
    R = _finger_stats(d["right_marker"])
    bulb = d["actor_light_bulb"]
    T = len(bulb)
    print(f"\nT = {T}   bulb shape {bulb.shape}")

    bz = bulb[:, 2]
    yaw = _unwrap_deg(_yaw(bulb))

    # Our quantities, per finger, pooled over markers.  Median is the robust choice: the
    # pad's marker field is a rigid-ish patch, and a handful of LK outliers on the rim
    # would otherwise dominate the mean.
    q = {}
    for name, S in (("L", L), ("R", R)):
        q[f"{name}_dx"] = np.median(S["dx"], axis=1)
        q[f"{name}_dy"] = np.median(S["dy"], axis=1)
        q[f"{name}_mag"] = np.median(S["mag"], axis=1)
    q["asym_dy"] = np.abs(q["L_dy"] - q["R_dy"]) / 2.0
    q["sym_dy"] = np.abs(q["L_dy"] + q["R_dy"]) / 2.0
    q["asym_dx"] = np.abs(q["L_dx"] - q["R_dx"]) / 2.0
    q["sym_dx"] = np.abs(q["L_dx"] + q["R_dx"]) / 2.0

    segs = _segments(bulb)
    print(f"\nsegments (auto): {segs}")
    print(f"segments (known hand changes): {KNOWN_HAND_CHANGES}")

    rows = []
    print("\n=== per segment ===")
    hdr = f"{'seg':>14} {'steps':>6} {'dz_mm':>7} {'dyaw':>8} {'lead_mm':>8} | " + " ".join(
        f"{k:>9}" for k in ("asym_dy", "sym_dy", "L_dy", "R_dy", "L_dx", "R_dx")
    )
    print(hdr)
    for a, b in segs:
        dz = float(bz[a] - bz[b - 1]) * 1000.0
        dth = float(yaw[b - 1] - yaw[a])
        lead = abs(dz / (dth / 360.0)) if abs(dth) > 1e-6 else float("nan")
        vals = []
        for k in ("asym_dy", "sym_dy", "L_dy", "R_dy", "L_dx", "R_dx"):
            v = q[k][a:b]
            vals.append(f"{v[0]:+.3f}/{v[-1]:+.3f}")
        print(f"{a:>6}-{b:<7} {b - a:>6} {dz:>+7.3f} {dth:>+8.2f} {lead:>8.3f} | " + " ".join(f"{s:>9}" for s in vals))
        rows.append({"start": a, "end": b, "dz_mm": dz, "dyaw_deg": dth, "lead_mm": lead,
                     **{k: [float(q[k][a]), float(q[k][b - 1])] for k in
                        ("asym_dy", "sym_dy", "L_dy", "R_dy", "L_dx", "R_dx", "L_mag", "R_mag")}})

    # ---- (c) rate match: does the quantity's swing scale with the segment's yaw? -------
    print("\n=== rate match (swing vs swept yaw) ===")
    print(f"{'seg':>14} {'dyaw':>8} {'d_asym_dy':>10} {'d_sym_dy':>10} {'per_180_asym':>13} {'per_180_sym':>12}")
    for r in rows:
        dth = abs(r["dyaw_deg"])
        if dth < 1e-6:
            continue
        da = r["asym_dy"][1] - r["asym_dy"][0]
        ds = r["sym_dy"][1] - r["sym_dy"][0]
        print(f"{r['start']:>6}-{r['end']:<7} {dth:>8.2f} {da:>+10.3f} {ds:>+10.3f} "
              f"{da * 180.0 / dth:>13.3f} {ds * 180.0 / dth:>12.3f}")

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "trend.json").write_text(json.dumps(
        {"segments": rows, "hdf5": str(args.hdf5), "T": int(T)}, indent=2))
    np.savez_compressed(
        args.out / "quantities.npz",
        **{k: v.astype(np.float32) for k, v in q.items()},
        bulb_z=bz.astype(np.float32), yaw_deg=yaw.astype(np.float32))
    print(f"\nwrote {args.out}/trend.json + quantities.npz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
