#!/usr/bin/env python3
"""Calibrate the tendon-strap task's gel reading against the physical tension.

Drives the live UniVTAC server (task ``tension_strap``) through one episode:
grasp the strap's tab, then raise the end effector in fixed increments while
logging, at every step,

  * the physical tension the task itself scores (``/force_state`` ->
    ``measured.tension_N``, the FEM elastic resultant), together with
    ``extension_m`` and ``maximum_stretch``;
  * every scalar the tactile API exposes (``/tactile``): depth, marker
    displacement, coherence, shear vector, anisotropy;
  * the commanded end-effector height (``/ee_pose``).

The point of the run is the mapping gel -> newtons.  The strap's modulus is
drawn per seed (0.25-0.40 MPa, ``envs/_force_task_utils.py:sample_physics``),
so the extension needed for a given tension is seed-dependent -- but the load
the pads carry is the tension itself, so the gel reading should not be.  Two
seeds are enough to tell whether that holds.

The server must already be running on the target seed (one process serves one
physics seed); this script never restarts it.

Usage:
    .venv/bin/python scripts/probe_tension_strap.py --seed 0 --port 8120
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

# --- Scene constants (envs/tension_strap.py) ---------------------------------
# The strap is added at base [0.55, 0, 0.005] and the tab -- the 40 mm cube at
# its top -- spans z in [0.154, 0.194] in the strap's own frame, so its centre
# is 0.174 above the base.
STRAP_BASE = (0.55, 0.0, 0.005)
TAB_CENTRE_Z = 0.174
# Flange -> gripper centre for a tool pointing down (envs/robot/robot.py).
GRIPPER_OFFSET_M = 0.131
# The task releases its initialization fixture once BOTH pads read at or under
# grasp_depth_mm + 0.1 (envs/tension_strap.py:_step).
GRASP_DEPTH_MM = 27.6
RELEASE_MM = GRASP_DEPTH_MM + 0.1
APPROACH_CLEAR_M = 0.05


class Client:
    def __init__(self, base: str):
        self.base = base

    def _get(self, path: str, timeout: float = 60.0) -> dict:
        resp = requests.get(self.base + path, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict, timeout: float = 1800.0) -> dict:
        resp = requests.post(self.base + path, json=payload, timeout=timeout)
        if not resp.ok:
            raise RuntimeError(f"{path} -> {resp.status_code}: {resp.text[:400]}")
        return resp.json()

    def health(self) -> dict:
        return self._get("/health")

    def reset(self, seed: int) -> dict:
        return self._post("/reset", {"seed": seed}, timeout=900.0)

    def ee_pose(self) -> dict:
        return self._get("/ee_pose")

    def tactile(self) -> dict:
        return self._get("/tactile", timeout=120.0)

    def force_state(self) -> dict:
        return self._get("/force_state", timeout=180.0)

    def check_success(self) -> dict:
        return self._get("/check_success", timeout=180.0)

    def move(self, position, quaternion, tag: str) -> dict:
        return self._post("/move", {
            "position": [float(v) for v in position],
            "quaternion_wxyz": [float(v) for v in quaternion],
            "tag": tag,
        })

    def gripper(self, action: str, **kwargs) -> dict:
        return self._post("/gripper", {"action": action, **kwargs})


TACTILE_KEYS = (
    "left_contact", "right_contact",
    "left_depth_mm", "right_depth_mm",
    "left_marker_displacement_px", "right_marker_displacement_px",
    "left_marker_coherence", "right_marker_coherence",
    "left_marker_shear_dx", "left_marker_shear_dy",
    "right_marker_shear_dx", "right_marker_shear_dy",
    "left_marker_anisotropy", "right_marker_anisotropy",
)


def tactile_row(data: dict) -> dict:
    return {k: data.get(k) for k in TACTILE_KEYS}


def measure_row(client: Client, note: str, commanded_z: float) -> dict:
    state = client.force_state()
    tactile = client.tactile()
    ee = client.ee_pose()
    measured = state.get("measured") or {}
    row = {
        "t": round(time.time(), 3),
        "note": note,
        "commanded_ee_z": commanded_z,
        "ee_z": float(ee["position"][2]),
        "phase": state.get("phase"),
        "failure": state.get("failure"),
        "plan_success": state.get("plan_success"),
        "grip_released": state.get("grip_released"),
        "step": state.get("step_count"),
        "tension_N": measured.get("tension_N"),
        "extension_m": measured.get("extension_m"),
        "maximum_stretch": measured.get("maximum_stretch"),
        "tab_in_hand_m": measured.get("tab_in_hand_m"),
        "tab_z_m": measured.get("tab_z_m"),
    }
    row.update(tactile_row(tactile))
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--port", type=int, default=8120)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--steps", type=int, default=8,
                        help="coarse-phase pull steps to attempt")
    parser.add_argument("--step-m", type=float, default=0.002)
    parser.add_argument("--coarse-until-n", type=float, default=10.0,
                        help="leave the coarse phase once the tension passes this")
    parser.add_argument("--fine-steps", type=int, default=60)
    parser.add_argument("--fine-m", type=float, default=0.0002,
                        help="fine-phase rise per move; 0 disables the phase")
    parser.add_argument("--fine-until-n", type=float, default=20.0)
    parser.add_argument("--hold-test", type=int, default=5,
                        help="no-op moves to time at the end of the ramp")
    parser.add_argument("--out", default="outputs/probe_tension_strap")
    args = parser.parse_args()

    client = Client(f"http://{args.host}:{args.port}")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"seed_{args.seed}.jsonl"

    print("health:", client.health())
    print("reset:", client.reset(args.seed))

    pose = client.ee_pose()
    quat = pose["quaternion_wxyz"]
    tab = (STRAP_BASE[0], STRAP_BASE[1], STRAP_BASE[2] + TAB_CENTRE_Z)
    grasp_ee = (tab[0], tab[1], tab[2] + GRIPPER_OFFSET_M)
    above_ee = (grasp_ee[0], grasp_ee[1], grasp_ee[2] + APPROACH_CLEAR_M)
    print("start ee:", [round(v, 4) for v in pose["position"]])
    print("grasp ee:", [round(v, 4) for v in grasp_ee])

    rows = []

    def record(note, commanded_z):
        row = measure_row(client, note, commanded_z)
        rows.append(row)
        print("  %-22s tension=%s ext=%s step=%s depths=(%s,%s) disp=(%s,%s) "
              "shear L=(%s,%s) R=(%s,%s)"
              % (note,
                 None if row["tension_N"] is None else round(row["tension_N"], 3),
                 None if row["extension_m"] is None else round(row["extension_m"] * 1000, 2),
                 row["step"],
                 row["left_depth_mm"], row["right_depth_mm"],
                 row["left_marker_displacement_px"], row["right_marker_displacement_px"],
                 row["left_marker_shear_dx"], row["left_marker_shear_dy"],
                 row["right_marker_shear_dx"], row["right_marker_shear_dy"]))
        return row

    record("after_reset", pose["position"][2])

    result = client.move(above_ee, quat, "above_tab")
    print("move above:", result["success"], result["message"])
    record("above_tab", above_ee[2])
    result = client.move(grasp_ee, quat, "at_tab")
    print("move at_tab:", result["success"], result["message"])
    record("at_tab", grasp_ee[2])

    # A close that never reaches the depth target comes back an HTTP error,
    # but the fingers stay where the tab stopped them and the reading is
    # still valid -- so record it either way instead of losing the whole run.
    try:
        closed = client.gripper("close", force_threshold=GRASP_DEPTH_MM,
                                use_adaptive_grasp=True)
        print("close:", closed.get("message"))
    except RuntimeError as exc:
        print("close raised (readings below are still the stopped fingers):", exc)
    time.sleep(0.5)
    record("after_close", grasp_ee[2])

    # The fixture releases only once both pads have acquired the tab; a strap
    # still held by it does not follow the hand.
    state = client.force_state()
    print("grip released:", (state.get("measured") or {}).get("tab_z_m"))

    # Two phases.  The coarse one finds out how steep this seed's strap is --
    # a few 2 mm rises map the whole 0-25 N range.  The fine one then walks
    # the last part of the range in tenths of a millimetre, because the oracle
    # has to land inside +-0.5 N and whether the arm can resolve that at all
    # is the question the fine phase answers.
    phases = [
        ("coarse", args.step_m, args.coarse_until_n, args.steps),
        ("fine", args.fine_m, args.fine_until_n, args.fine_steps),
    ]
    height = grasp_ee[2]
    index = 0
    stop = False
    for phase, step, until_n, budget in phases:
        if stop or step <= 0.0:
            continue
        for _ in range(budget):
            tension = rows[-1].get("tension_N")
            if tension is not None and tension >= until_n:
                print("  %s phase: %.2f N reached, stopping" % (phase, tension))
                break
            if rows[-1].get("failure"):
                stop = True
                break
            index += 1
            height += step
            result = client.move((grasp_ee[0], grasp_ee[1], height), quat,
                                 f"pull_{phase}_{index:03d}")
            row = record(f"pull_{index:03d}", height)
            row["phase_name"] = phase
            if not result["success"]:
                print("  move reported:", result["message"])
            if row.get("failure"):
                print("  physical failure:", row["failure"])
                stop = True
                break

    # --- Can the arm stand still while the clock runs? ----------------------
    # The two-stage verdict needs the tension held inside +-0.5 N for 3 s of
    # SIMULATED time, and nothing advances the physics but a motion call.  A
    # call to the pose the hand already holds is the cheapest candidate, so
    # measure what it costs: how many physics ticks it advances the episode
    # and whether the tension stays where it was.
    last = rows[-1] if rows else None
    if last is not None and last.get("failure") is None:
        height = last["commanded_ee_z"]
        for index in range(args.hold_test):
            before = client.force_state().get("step_count")
            result = client.move((grasp_ee[0], grasp_ee[1], height), quat,
                                 f"hold_{index:02d}")
            row = record(f"hold_{index:02d}", height)
            after = row["step"]
            row["hold_ticks"] = None if before is None else after - before
            row["hold_success"] = result["success"]
            print("  hold_%02d advanced %s ticks, success=%s"
                  % (index, row["hold_ticks"], result["success"]))

    with out_path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    print("wrote", out_path)
    print("check_success:", client.check_success())
    return 0


if __name__ == "__main__":
    sys.exit(main())
