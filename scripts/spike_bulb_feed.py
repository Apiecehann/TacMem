"""Probe: does a lead-coupled servo helix advance the bulb through cap-x?

Drives the ``feed_lead_m`` path on ``/rotate_gripper`` -- one server-side op
that runs N iterations of the env's own ``_ik_to_center`` (one differential-IK
step plus two physics ticks each), twisting about world z while descending by
the thread's own lead.  That coupling is what ``bulb_tightening`` scores
against (``expected_advance``, bulb_tightening.py:614) and what cap-x's
planner-backed ops cannot express, because a cuRobo plan is resolved before
contact exists.

The question is narrow and has never been tested: with the twist and the
descent rigidly coupled and the servo re-aimed every iteration, does the bulb
advance -- and does the gel pad survive the trip?

The probe deliberately runs the *bare* helix.  The env's ``_screw_segment``
adds an image-shear servo on top (``axial_correction``), which needs the
expert's tactile reference frames; omitting it isolates the control question
from the image pipeline.

Run with the proxy unset -- a shell-level ALL_PROXY routes localhost through
socks, which makes httpx raise at construction:

    env -u ALL_PROXY -u all_proxy ./.venv/bin/python scripts/spike_bulb_feed.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spike_bulb_tightening import Spike, pad_max  # noqa: E402


class FeedSpike(Spike):
    def finish(self) -> Path:
        """Persist this probe's own payload.

        ``Spike.finish`` writes ``self.log``, which this script never fills
        (it does not call ``_record``) -- so it would drop everything.
        """
        path = self.out_dir / "feed.json"
        path.write_text(json.dumps(getattr(self, "result", self.log),
                                   indent=2, default=str))
        self.say(f"\nwrote {path}")
        return path


def _summary(state: dict) -> dict:
    """The physical fields that say whether the twist advanced the bulb."""
    m = state.get("measured") or {}
    return {
        "turns": m.get("formal_net_turns"),
        # The physical descent, not formal_advance_m: the formal channel stays
        # 0.000 until the env's own physical probe completes, so it is dark for
        # the whole first run and would silently disable the arrival check.
        "advance_mm": (None if m.get("advance_m") is None
                       else round(float(m["advance_m"]) * 1000, 4)),
        "formal_advance_mm": (None if m.get("formal_advance_m") is None
                              else round(float(m["formal_advance_m"]) * 1000, 4)),
        "yaw_deg": m.get("signed_world_rotation_deg"),
        # Gates formal_advance_m: until the reversible-thread pre-flight
        # registers, formal_motion returns (0, 0) and the success criterion's
        # formal_advance_m term is unsatisfiable.
        "probe_complete": m.get("probe_complete"),
        "phase_err_mm": (None if m.get("phase_error_m") is None
                         else round(float(m["phase_error_m"]) * 1000, 4)),
        "torque": m.get("torque_Nm"),
        "seat_N": m.get("seat_force_N"),
        "pad_N": pad_max(m),
        "peak_N": m.get("peak_pad_normal_force_N"),
        "bulb_z_mm": (None if m.get("bulb_z_m") is None
                      else round(float(m["bulb_z_m"]) * 1000, 3)),
        "tilt_deg": m.get("tilt_deg"),
        "drift_mm": (None if m.get("center_drift_m") is None
                     else round(float(m["center_drift_m"]) * 1000, 4)),
        "held": state.get("held_steps"),
        # Consecutive stationary steps toward the 180 the END gesture needs.
        # A hold that ends with this still low never moved the arm at all.
        "still": state.get("submission_stationary_steps"),
        "accepted": state.get("accepted_result"),
        "failure": state.get("failure"),
    }


def _print_trace(trace: list, limit: int = 14) -> None:
    if not trace:
        print("    (no trace returned)")
        return
    print(f"    {'angle':>7s} {'yaw':>8s} {'adv_mm':>8s} {'phase_mm':>9s}"
          f" {'torque':>8s} {'seat_N':>7s} {'pad_N':>7s} {'bulb_z':>8s}"
          f" {'still':>6s}")
    stride = max(1, len(trace) // limit)
    rows = trace[::stride]
    if rows[-1] is not trace[-1]:
        rows = rows + [trace[-1]]
    for r in rows:
        n = lambda k: r.get(k)                                        # noqa: E731
        adv = n("advance_m")
        ph = n("phase_error_m")
        bz = n("bulb_z_m")
        # pad_max_N is the per-sample max the server now derives from the
        # pad_normal_force_N dict (which stays out of the trace: it is a dict,
        # and reading it per iteration would break the env's 4-tick velocity
        # cadence).  peak_pad_normal_force_N is the env's own running max and
        # is monotone, which is what shows a pad climbing toward fracture.
        pad_n = n("pad_max_N")
        if pad_n is None:
            pad_n = n("peak_pad_normal_force_N")
        # `stationary` only climbs on a hold -- it is the count of consecutive
        # steps the gripped command has stopped moving, and the environment
        # submits the episode at 180 of them.
        print(f"    {n('angle_deg'):7.1f} {n('signed_world_rotation_deg'):8.2f}"
              f" {0.0 if adv is None else adv * 1000:8.3f}"
              f" {0.0 if ph is None else ph * 1000:9.3f}"
              f" {n('torque_Nm'):8.4f} {n('seat_force_N'):7.2f}"
              f" {pad_n:7.2f}"
              f" {0.0 if bz is None else bz * 1000:8.3f}"
              f" {n('stationary') if n('stationary') is not None else 0:6d}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://127.0.0.1:8120")
    ap.add_argument("--output-dir", default="outputs/spike_bulb_feed")
    ap.add_argument("--segments", type=int, default=4,
                    help="helix ops to run; free travel needs 608.6 deg / "
                         "180 = 3.38 at the reference profile")
    ap.add_argument("--segment-deg", type=float, default=180.0)
    ap.add_argument("--iterations", type=int, default=120,
                    help="servo iterations per segment; one is 1.5 deg at the "
                         "env's own granularity")
    ap.add_argument("--lead-mm", type=float, default=None,
                    help="override the thread lead the server reads")
    ap.add_argument("--arrive-margin-mm", type=float, default=0.5,
                    help="stop once the bulb has descended this close to the "
                         "end of free travel; without it the last segment "
                         "drives past seating and the 180 N guard ends the run")
    ap.add_argument("--no-grasp", action="store_true")
    ap.add_argument("--submit", action="store_true",
                    help="submit the episode for a verdict when the segments end")
    args = ap.parse_args()

    spike = FeedSpike(args.base_url, Path(args.output_dir))
    out: dict = {"args": vars(args)}
    spike.result = out

    spike.say(f"health: {spike._get('/health')}")
    state = spike.force_state()
    if not state.get("available"):
        spike.say(f"force task not available: {state.get('message')}")
        spike.result = out
        spike.finish()
        return
    spec = state.get("thread_spec") or {}
    out["thread_spec"] = spec
    out["params"] = state.get("params") or {}
    spike.say(f"thread_spec {spec}")

    lead_m = ((args.lead_mm / 1000.0) if args.lead_mm is not None
              else float(spec.get("lead_m", 0.0)))
    # signed_progress_turns(yaw) = -handedness * yaw / 360, so a world yaw of
    # this sign is the one that advances the bulb.
    yaw_sign = -int(spec.get("handedness", 1))
    free_travel_mm = (None if spec.get("nominal_free_travel_m") is None
                      else float(spec["nominal_free_travel_m"]) * 1000.0)
    out["lead_m"] = lead_m
    out["yaw_sign"] = yaw_sign
    out["free_travel_mm"] = free_travel_mm
    spike.say(f"lead {lead_m * 1000:.4f} mm, advancing yaw sign {yaw_sign:+d}, "
              f"free travel {free_travel_mm} mm")

    if args.no_grasp:
        spike.say("skipping grasp (--no-grasp)")
    elif not spike._grasp(out):
        spike.say("grasp failed -- stopping")
        spike.result = out
        spike.finish()
        return

    out["segments"] = []
    for i in range(args.segments):
        before = _summary(spike.force_state())
        spike.say(f"\n--- segment {i + 1}/{args.segments} "
                  f"(before: adv {before['advance_mm']} mm, "
                  f"bulb_z {before['bulb_z_mm']} mm, pad {before['pad_N']} N) ---")
        if before.get("failure"):
            spike.say(f"  task already failed: {before['failure']} -- stopping")
            break
        t0 = time.time()
        # One op: iterations IK steps, twist and descent locked at lead_m.
        res = spike._post("/rotate_gripper", {
            "object_name": "light_bulb",
            "angle_rad": float(yaw_sign) * np.deg2rad(args.segment_deg),
            "steps": int(args.iterations),
            "feed_lead_m": float(lead_m),
        }, timeout=900.0)
        elapsed = round(time.time() - t0, 1)
        after = _summary(spike.force_state())
        seg = {"index": i, "seconds": elapsed,
               "requested": res.get("requested"), "completed": res.get("completed"),
               "grip_released": res.get("grip_released"),
               "aborted": res.get("aborted"), "success": res.get("success"),
               "message": res.get("message"),
               "before": before, "after": after,
               "trace": res.get("trace")}
        out["segments"].append(seg)
        spike.say(f"  {res.get('completed')}/{res.get('requested')} iterations "
                  f"in {elapsed}s  success={res.get('success')}")
        if res.get("aborted"):
            spike.say(f"  ABORTED: {res['aborted']}")
        if res.get("grip_released") is False:
            spike.say("  WARNING: grip_released is False -- the bulb is still "
                      "animated, so this moves the arm without screwing anything")
        d_adv = (None if before["advance_mm"] is None or after["advance_mm"] is None
                 else round(after["advance_mm"] - before["advance_mm"], 4))
        spike.say(f"  advance {before['advance_mm']} -> {after['advance_mm']} mm "
                  f"(delta {d_adv}), bulb_z {before['bulb_z_mm']} -> "
                  f"{after['bulb_z_mm']} mm, phase_err {after['phase_err_mm']} mm, "
                  f"pad {after['pad_N']} N, seat {after['seat_N']} N, "
                  f"tilt {after['tilt_deg']}, held {after['held']}")
        _print_trace(res.get("trace") or [])
        if res.get("aborted") or after.get("failure"):
            break
        # Stop on arrival rather than on the guard: free travel is finite, and
        # a segment that starts past it compresses the seat at ~132 kN/m.
        if (free_travel_mm is not None and after["advance_mm"] is not None
                and after["advance_mm"] >= free_travel_mm - args.arrive_margin_mm):
            spike.say(f"  arrived: advance {after['advance_mm']} mm of "
                      f"{free_travel_mm} mm free travel -- stopping")
            out["arrived"] = True
            break

    out["final"] = _summary(spike.force_state())
    spike.say(f"\nfinal: {json.dumps(out['final'], default=str)}")
    if args.submit:
        spike.say("--- submit ---")
        try:
            out["submit"] = spike._post("/submit_episode", {}, timeout=900.0)
            spike.say(f"  submit -> {json.dumps(out['submit'], default=str)[:600]}")
        except Exception as exc:  # noqa: BLE001
            out["submit"] = {"error": str(exc)}
            spike.say(f"  submit failed: {exc}")

    spike.result = out
    spike.finish()


if __name__ == "__main__":
    main()
