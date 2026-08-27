#!/usr/bin/env python3
"""Scripted probe calibration for the insert_HDMI success discriminator.

Drives the live UniVTAC server through three ground-truth plug states —
entering (nose inside the socket mouth), seated (rel Z < 5 mm), and
rim-wedged (plug corner caught on the mouth edge, every motion shears) —
and in each state runs the same battery of probe motions: 2 mm descend,
2 mm lift, +/-3 mm lateral nudges.  For every motion it logs exactly the
fields detect_slip compares (per-motion marker-displacement deltas and
coherence) together with the ground-truth plug pose from /check_success,
so the numbers can be turned into thresholds for the eval prompt.

The server must already be running (scripts/launch_univtac_server.sh);
this script never restarts it.  If cuRobo hangs mid-move and the
external watchdog kills the server, the script waits for the supervisor
to respawn it, re-resets the task, and continues the current scenario.

Usage:
    .venv/bin/python scripts/probe_calibration.py
"""
import argparse
import csv
import sys
import time

import requests

BASE = "http://127.0.0.1:8120"
DEFAULT_LOG = "outputs/probe_calibration.log"

# Probe battery: (name, dx, dy, dz) in world frame, metres.
PROBES = [
    ("descend2", 0.0, 0.0, -0.002),
    ("lift2", 0.0, 0.0, +0.002),
    ("nudgeX+", +0.003, 0.0, 0.0),
    ("nudgeX-", -0.003, 0.0, 0.0),
    ("nudgeY+", 0.0, +0.003, 0.0),
    ("nudgeY-", 0.0, -0.003, 0.0),
]

# Ground-truth state classification from check_success detail (metres).
SEATED_REL_Z = 0.005    # success threshold
ENTERING_REL_Z = 0.014  # above this the nose is not yet past the mouth


class Client:
    """Minimal HTTP client for the UniVTAC server endpoints."""

    def __init__(self, base: str):
        self.base = base

    def _get(self, path: str, timeout: float = 40.0) -> dict:
        resp = requests.get(self.base + path, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict, timeout: float = 150.0) -> dict:
        resp = requests.post(self.base + path, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def reset(self, seed: int) -> dict:
        return self._post("/reset", {"seed": seed}, timeout=300.0)

    def tactile(self) -> dict:
        return self._get("/tactile")

    def detail(self) -> dict:
        """Ground-truth check_success detail (prism_pos etc.)."""
        return self._get("/check_success")["detail"]

    def move(self, dx: float, dy: float, dz: float) -> dict:
        return self._post(
            "/move_delta",
            {"dx": dx, "dy": dy, "dz": dz, "coord": "world"},
            timeout=150.0,
        )

    def wait_healthy(self, timeout: float = 360.0) -> bool:
        """Poll /health after a presumed server kill; True when back up."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self._get("/health", timeout=5.0)
                return True
            except Exception:
                time.sleep(5.0)
        return False


class Calibrator:
    """Runs the probe battery per state and logs one CSV line per motion."""

    COLUMNS = [
        "time", "state", "probe", "dx", "dy", "dz",
        "preL", "preR", "postL", "postR", "dL", "dR",
        "cohL", "cohR", "depthL", "depthR",
        "relz_pre", "relz_post", "rely_post", "tilt_cos", "ee_z",
        "move_ok", "move_msg",
    ]

    def __init__(self, client: Client, log_path: str):
        self.cli = client
        self.log = open(log_path, "a", newline="")
        self.writer = csv.writer(self.log)
        self.writer.writerow(self.COLUMNS)
        self.log.flush()

    def close(self) -> None:
        self.log.close()

    def line(self, state: str, probe: str, dx: float, dy: float, dz: float,
             pre: dict, post: dict, d_pre: dict, d_post: dict,
             move_ok: bool, move_msg: str) -> None:
        """Write one CSV row for a single probe motion."""
        self.writer.writerow([
            time.strftime("%H:%M:%S"), state, probe, dx, dy, dz,
            pre.get("left_marker_displacement_px", 0.0),
            pre.get("right_marker_displacement_px", 0.0),
            post.get("left_marker_displacement_px", 0.0),
            post.get("right_marker_displacement_px", 0.0),
            round(post.get("left_marker_displacement_px", 0.0)
                  - pre.get("left_marker_displacement_px", 0.0), 3),
            round(post.get("right_marker_displacement_px", 0.0)
                  - pre.get("right_marker_displacement_px", 0.0), 3),
            post.get("left_marker_coherence", 0.0),
            post.get("right_marker_coherence", 0.0),
            post.get("left_depth_mm", 0.0),
            post.get("right_depth_mm", 0.0),
            round(d_pre["prism_pos"][2] * 1000, 2),
            round(d_post["prism_pos"][2] * 1000, 2),
            round(d_post["prism_pos"][1] * 1000, 2),
            d_post["tilt_cos"], d_post["ee_z"],
            move_ok, move_msg[:80],
        ])
        self.log.flush()
        print(f"[{state:8s}] {probe:12s} relZ {d_pre['prism_pos'][2]*1000:6.2f}"
              f" -> {d_post['prism_pos'][2]*1000:6.2f} mm")

    def probe(self, state: str, name: str, dx: float, dy: float,
              dz: float) -> None:
        """One probe motion: tactile before/after + ground truth after."""
        d_pre = self.cli.detail()
        pre = self.cli.tactile()
        move_ok, move_msg = self._move_guarded(dx, dy, dz)
        post = self.cli.tactile()
        d_post = self.cli.detail()
        self.line(state, name, dx, dy, dz, pre, post, d_pre, d_post,
                  move_ok, move_msg)

    def _move_guarded(self, dx: float, dy: float, dz: float):
        """move() with server-respawn recovery (cuRobo hang containment)."""
        try:
            resp = self.cli.move(dx, dy, dz)
            return bool(resp.get("success")), resp.get("message", "")
        except Exception as exc:
            print(f"  move failed ({exc}); waiting for server respawn...")
            if not self.cli.wait_healthy():
                raise RuntimeError("server did not come back")
            print("  server back; re-seeding current scenario via reset(0)")
            self.cli.reset(0)
            return False, f"recovered after respawn: {exc}"

    def rel_z(self) -> float:
        return self.cli.detail()["prism_pos"][2]

    def state_of(self, rel_z: float) -> str:
        if rel_z < SEATED_REL_Z:
            return "seated"
        if rel_z < ENTERING_REL_Z:
            return "entering"
        return "high"

    def descend_until(self, state: str, target_rel_z: float,
                      max_steps: int = 12) -> float:
        """Descend in 2 mm steps until target depth or 4 stalled steps."""
        stalls = 0
        prev_z = self.rel_z()
        for _ in range(max_steps):
            self.probe(state, "descend", 0.0, 0.0, -0.002)
            cur_z = self.rel_z()
            if prev_z - cur_z < 0.0008:
                stalls += 1
                if stalls >= 4:
                    print(f"  {state}: stalled at relZ={cur_z*1000:.1f} mm")
                    break
            else:
                stalls = 0
            prev_z = cur_z
            if cur_z <= target_rel_z:
                break
        return cur_z

    def reestablish(self, state: str) -> None:
        """Return the plug to the requested state after a probe displaced it."""
        rel_z = self.rel_z()
        for _ in range(8):
            if state == "seated" and rel_z < SEATED_REL_Z:
                return
            if state == "entering" and SEATED_REL_Z <= rel_z < ENTERING_REL_Z:
                return
            if state == "wedge":
                # Wedged = stalled descends at high rel Z; a free plug must be
                # re-wedged by pulling it back up and pressing again.
                if rel_z > ENTERING_REL_Z:
                    self.probe(state, "rewedge-lift", 0.0, 0.0, 0.002)
                    rel_z = self.rel_z()
                    continue
            if state in ("seated",) and rel_z >= SEATED_REL_Z:
                self.probe(state, "reseat", 0.0, 0.0, -0.002)
            elif state == "entering" and rel_z >= ENTERING_REL_Z:
                self.probe(state, "reenter", 0.0, 0.0, -0.002)
            elif state == "entering" and rel_z < SEATED_REL_Z:
                self.probe(state, "reenter-lift", 0.0, 0.0, 0.002)
            rel_z = self.rel_z()
        print(f"  reestablish({state}): gave up at relZ={rel_z*1000:.1f} mm")

    def battery(self, state: str) -> None:
        """Run all probes at one state, restoring and re-establishing after
        each so later probes see the same physical condition."""
        for name, dx, dy, dz in PROBES:
            self.probe(state, name, dx, dy, dz)
            # Restore the position; the restore motion is itself informative.
            self.probe(state, name + "_restore", -dx, -dy, -dz)
            self.reestablish(state)
        d = self.cli.detail()
        print(f"  {state} battery done, relZ={d['prism_pos'][2]*1000:.1f} mm, "
              f"tilt_cos={d['tilt_cos']:.3f}")


def scan_seeds(client: Client, cal: Calibrator, n_seeds: int):
    """Reset each seed once and record the reset-time plug offsets."""
    rows = []
    for seed in range(n_seeds):
        client.reset(seed)
        d = client.detail()
        p = d["prism_pos"]
        rows.append((seed, p[0] * 1000, p[1] * 1000, p[2] * 1000, d["tilt_cos"]))
        print(f"seed {seed:2d}: X={p[0]*1000:+5.1f} Y={p[1]*1000:+5.1f} "
              f"relZ={p[2]*1000:5.1f} mm  tilt_cos={d['tilt_cos']:.4f}")
    # Clean = small lateral offset AND upright; wedge = large offset or tilt.
    clean = min(rows, key=lambda r: abs(r[1]) + 0.3 * abs(r[2])
                + 200 * (1 - r[4]))
    wedge = max(rows, key=lambda r: abs(r[1]) + abs(r[2]) + 200 * (1 - r[4]))
    print(f"clean seed = {clean[0]}, wedge seed = {wedge[0]}")
    return clean[0], wedge[0]


def run_free(client: Client, cal: Calibrator, seed: int) -> None:
    """Baseline: plug dangling in the gripper, untouched."""
    client.reset(seed)
    cal.battery("free")


def run_clean(client: Client, cal: Calibrator, seed: int) -> None:
    """Clean insertion: descend to entering, probe, descend to seated, probe."""
    client.reset(seed)
    cal.descend_until("clean", ENTERING_REL_Z - 0.004)
    cal.battery("entering")
    cal.descend_until("clean", SEATED_REL_Z - 0.002)
    cal.battery("seated")


def run_wedge(client: Client, cal: Calibrator, seed: int) -> None:
    """Rim wedge: descend until progress stalls at high rel Z, then probe."""
    client.reset(seed)
    d = client.detail()
    sign_x = 1.0 if d["prism_pos"][0] >= 0 else -1.0
    sign_y = 1.0 if d["prism_pos"][1] >= 0 else -1.0
    stalls = 0
    prev_z = d["prism_pos"][2]
    for _ in range(12):
        cal.probe("wedge", "descend", 0.0, 0.0, -0.002)
        rel_z = cal.rel_z()
        if rel_z < ENTERING_REL_Z:
            # Too well aligned despite the bad seed: push back onto the rim
            # along the residual lateral offset, then keep pressing.
            cal.probe("wedge", "assist-nudge", 0.003 * sign_x,
                      0.003 * sign_y, 0.0)
            prev_z = cal.rel_z()
            continue
        if prev_z - rel_z < 0.0008:
            stalls += 1
            if stalls >= 4:
                print(f"  wedge stalled at relZ={rel_z*1000:.1f} mm")
                break
        else:
            stalls = 0
        prev_z = rel_z
    cal.battery("wedge")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--log", default=DEFAULT_LOG)
    parser.add_argument("--scan", type=int, default=15)
    parser.add_argument("--scenario", choices=["free", "clean", "wedge", "all"],
                        default="all")
    args = parser.parse_args()

    client = Client(args.base)
    if not client.wait_healthy(timeout=10.0):
        print("server not up at", args.base, "- start it first")
        sys.exit(1)
    cal = Calibrator(client, args.log)
    try:
        clean_seed, wedge_seed = scan_seeds(client, cal, args.scan)
        if args.scenario in ("free", "all"):
            run_free(client, cal, clean_seed)
        if args.scenario in ("clean", "all"):
            run_clean(client, cal, clean_seed)
        if args.scenario in ("wedge", "all"):
            run_wedge(client, cal, wedge_seed)
    finally:
        cal.close()
    print("done; log:", args.log)


if __name__ == "__main__":
    main()
