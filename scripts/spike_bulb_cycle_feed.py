"""Probe: does the release/unwind/regrasp cycle let the servo helix keep going?

**Answered: yes.**  Two cycles ran clean at 12:20 on 2026-09-27 -- 368.63 deg
of bulb twist, 8.17 mm of descent, ``failure: null``, pad peak flat at 89.6 N.
Cycle 2 passed straight through 235 deg, the exact point that killed the
continuous run, because the unwind had just walked the wrist back to the
configuration it started the previous segment in.  ``cumulative_yaw`` tracks
the *bulb* (``_measure`` :1510), so unwinding the empty gripper costs nothing:
the thread stays where it was put and the wrist never leaves a one-segment
band.

That makes the full 608.6 deg of free travel reachable in ~180 deg segments,
with one release/unwind/regrasp between each (measured ~493 s, against 213 s
for the segment itself).

Three things this script does that the environment requires and that are easy
to miss:

* **The pre-flight wiggle.**  ``ReversibleThreadProbe`` (``_measure``
  :1532-1553) is fed purely from physics, and until *both* its legs register,
  ``formal_motion`` returns (0, 0) -- so ``formal_advance_m`` stays 0 and the
  success criterion's ``formal_advance_m >= free_travel - 2mm`` (:2267) is
  unsatisfiable however well the screw-in goes.  Each leg is an excursion
  peaking in [2, 6] deg that **returns home** (within 0.6 deg and 0.2 mm of
  the frozen origin); a single sweep through +4 and -4 sets both bounds at
  once and then *neither* leg passes, silently.  It must also come first: the
  angle reference freezes the moment the bulb leaves the home band (:441-443).

* **Release before unwinding.**  Turning while still holding unscrews the
  bulb, so the order is release, unwind the *empty* wrist, re-grasp -- the
  expert's own order (:2232-2240).

* **The hold is one-shot.**  ``take_action``'s Insert_USB END gesture fires at
  180 stationary steps (:1737) and ``_finish_bulb_episode`` sets
  ``_accepted_result`` permanently; ``check_success`` then sets
  ``failure='submission_outside_acceptance'`` if the criteria are not met
  (:1755).  So a stationary command issued before the bulb is seated and
  torqued kills the episode outright.  ``held_steps`` (:1684-1689) needs 132
  *consecutive* good steps inside that same 180-step window -- all seven of
  tightening-sign torque in band, seated, phase_error < 2 mm, and both speeds
  under 3 deg/s and 0.15 mm/s.

The segment now stops on the env's own ``reached`` (:2001): sensed torque at
``target_torque`` **and** the seat loaded.  Seat load alone would land with a
torque far below the 0.012 Nm band that ``held_steps`` scores.

This script's park is its own simplification of the expert's.  The expert
drives joint 7 to an absolute ``spin * 1.65`` rad (``_position_empty_wrist``
:1826) because it walks a pi segment from a known start; this script instead
just undoes the segment's own rotation while the gripper is empty, which needs
no joint feedback (the server exposes none).

Run with the proxy unset:

    env -u ALL_PROXY -u all_proxy ./.venv/bin/python scripts/spike_bulb_cycle_feed.py \
        --cycles 2 --no-grasp
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spike_bulb_feed import FeedSpike, _print_trace, _summary  # noqa: E402

# move_delta's per-call rotation cap is 0.5 rad, and its floor 0.02 rad.
TURN_SUB_RAD = 0.45
# The reversible-thread pre-flight.  ReversibleThreadProbe.update :462-485 only
# recognises a leg that leaves the origin and comes back: an excursion peaking
# in [2, 6] deg that returns within 0.6 deg and 0.2 mm of the frozen origin.
# Both legs must return home *separately* -- a single sweep through +4 and -4
# sets excursion_max and excursion_min at once, and then neither leg passes.
# Until both legs register, formal_motion returns (0, 0), so the success
# criterion's formal_advance_m >= free_travel - 2mm term (:2266) can never be
# met no matter how well the screw-in goes.
WIGGLE_DEG = 4.0
WIGGLE_ITER = 3
# The server now stops the helix on the env's own ``reached`` condition
# (_screw_segment :2001): sensed torque at target AND the seat loaded.  The
# seat half alone would land with a torque far below the band, and
# check_success wants |torque - 0.12| <= 0.012 (:2268).
SEAT_STOP_N = 3.0


class CycleFeed(FeedSpike):
    def finish(self) -> Path:
        """Persist this probe's own payload (see FeedSpike.finish)."""
        path = self.out_dir / "cycle.json"
        path.write_text(json.dumps(getattr(self, "result", self.log),
                                   indent=2, default=str))
        self.say(f"\nwrote {path}")
        return path

    def _turn(self, d_rad: float, tag: str) -> list[dict]:
        """Turn the EE in place by ``d_rad`` about world z, in capped sub-steps.

        ``coord='ee'`` leaves the position alone (transforms.py:134-136) but its
        rotation is pre-multiplied in world (both branches share the same
        ``euler2quat``), so drz turns about world +z whatever the tool is doing.
        """
        rows: list[dict] = []
        n = max(1, int(math.ceil(abs(d_rad) / TURN_SUB_RAD)))
        step = d_rad / n
        for k in range(n):
            t0 = time.time()
            res = self._post("/move_delta", {
                "drz": float(step), "coord": "ee", "tag": f"{tag}_{k}"})
            row = {"k": k, "cmd_deg": math.degrees(step),
                   "success": res.get("success"), "message": res.get("message", ""),
                   "seconds": round(time.time() - t0, 1)}
            rows.append(row)
            self.say(f"    {tag} {k}: drz={math.degrees(step):+.1f} deg "
                     f"-> success={row['success']} in {row['seconds']}s "
                     f"{'' if row['success'] else row['message'][:90]}")
            if not row["success"]:
                break
        return rows

    def _wiggle(self) -> list[dict]:
        """The reversible-thread pre-flight: +w, home, -w, home.

        Must run as the *first* angular motion after the grasp.  The probe
        freezes its angle reference the moment the bulb leaves the home band
        (:441-443), so a screw-in beforehand makes ``at_home`` unreachable
        without backing the bulb all the way out again.
        """
        rows: list[dict] = []
        for leg, sign in (("positive", +1.0), ("negative", -1.0)):
            for phase, s in ((f"{leg}_out", sign), (f"{leg}_home", -sign)):
                t0 = time.time()
                res = self._post("/rotate_gripper", {
                    "object_name": "light_bulb",
                    "angle_rad": float(s) * math.radians(WIGGLE_DEG),
                    "steps": int(WIGGLE_ITER),
                    "feed_lead_m": float(self._lead_m),
                }, timeout=600.0)
                row = {"leg": leg, "phase": phase,
                       "cmd_deg": s * WIGGLE_DEG,
                       "completed": res.get("completed"),
                       "requested": res.get("requested"),
                       "aborted": res.get("aborted"),
                       "seconds": round(time.time() - t0, 1)}
                rows.append(row)
                self.say(f"    wiggle {phase}: {s * WIGGLE_DEG:+.1f} deg -> "
                         f"{row['completed']}/{row['requested']} in "
                         f"{row['seconds']}s"
                         f"{'' if not row['aborted'] else '  ABORTED: ' + str(row['aborted'])}")
        return rows

    def _release(self) -> dict:
        t0 = time.time()
        res = self._post("/gripper", {"action": "open", "position": 1.0}, timeout=900.0)
        return {"success": res.get("success"), "message": res.get("message", ""),
                "seconds": round(time.time() - t0, 1)}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://127.0.0.1:8120")
    ap.add_argument("--output-dir", default="outputs/spike_bulb_cycle_feed")
    ap.add_argument("--cycles", type=int, default=2,
                    help="screw/release/unwind/regrasp rounds; the bulb needs "
                         "608.6 deg of free travel, i.e. 3.4 pi segments")
    ap.add_argument("--segment-deg", type=float, default=180.0)
    ap.add_argument("--iterations", type=int, default=120,
                    help="servo iterations per segment; 120 x 1.5 deg = 180 deg")
    ap.add_argument("--hold-iterations", type=int, default=150,
                    help="iterations of the constant-pose hold.  The END "
                         "gesture fires at 180 stationary steps = 90 "
                         "iterations, so anything past 90 is unreachable "
                         "*unless* the command's anchor resets early, which "
                         "restarts the count; 150 leaves room for one reset")
    ap.add_argument("--no-hold", action="store_true")
    ap.add_argument("--no-wiggle", action="store_true",
                    help="skip the reversible-thread pre-flight; it must run "
                         "first-thing after a fresh grasp, so this is only for "
                         "continuing a run whose probe is already complete")
    ap.add_argument("--do-grasp", action="store_true",
                    help="grasp first; off by default because the caller is "
                         "usually continuing a grip from a previous probe")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    spike = CycleFeed(args.base_url, Path(args.output_dir))
    out: dict = {"args": vars(args)}
    spike.result = out

    spike.say(f"health: {spike._get('/health')}")
    state = spike.force_state()
    if not state.get("available"):
        spike.say(f"force task not available: {state.get('message')}")
        spike.finish()
        return
    spec = state.get("thread_spec") or {}
    out["thread_spec"] = spec
    out["params"] = state.get("params") or {}
    lead_m = float(spec.get("lead_m", 0.0))
    yaw_sign = -int(spec.get("handedness", 1))
    spike._lead_m = lead_m
    out["lead_m"], out["yaw_sign"] = lead_m, yaw_sign
    free_travel_mm = (None if spec.get("nominal_free_travel_m") is None
                      else float(spec["nominal_free_travel_m"]) * 1000.0)
    out["free_travel_mm"] = free_travel_mm
    spike.say(f"lead {lead_m * 1000:.4f} mm, advancing yaw sign {yaw_sign:+d}, "
              f"free travel {free_travel_mm} mm")
    spike.say(f"before: {json.dumps(_summary(state), default=str)}")

    if args.do_grasp and not spike._grasp(out):
        spike.say("grasp failed -- stopping")
        spike.finish()
        return

    if args.no_wiggle:
        spike.say("skipping the reversible-thread pre-flight (--no-wiggle)")
    else:
        spike.say(f"\n=== reversible-thread pre-flight: +/-{WIGGLE_DEG} deg, home between legs ===")
        out["wiggle"] = spike._wiggle()
        st = spike.force_state()
        out["after_wiggle"] = _summary(st)
        # /force_state names this probe_physical_complete at the top level;
        # _summary already lifted probe_complete out of `measured`.  Reading
        # the wrong key here overwrote the real value with None.
        out["after_wiggle"]["probe_complete"] = st.get("probe_physical_complete")
        spike.say(f"  after wiggle: probe_complete={st.get('probe_physical_complete')} "
                  f"formal_adv {out['after_wiggle']['formal_advance_mm']} mm "
                  f"yaw {out['after_wiggle']['yaw_deg']} deg")

    out["cycles"] = []
    segment_rad = float(yaw_sign) * math.radians(args.segment_deg)
    for c in range(args.cycles):
        before = _summary(spike.force_state())
        spike.say(f"\n=== cycle {c + 1}/{args.cycles} "
                  f"(adv {before['advance_mm']} mm, seat {before['seat_N']} N, "
                  f"pad {before['pad_N']} N) ===")
        if before.get("failure"):
            spike.say(f"  task already failed: {before['failure']} -- stopping")
            break

        t0 = time.time()
        res = spike._post("/rotate_gripper", {
            "object_name": "light_bulb",
            "angle_rad": segment_rad,
            "steps": int(args.iterations),
            "feed_lead_m": lead_m,
        }, timeout=1800.0)
        elapsed = round(time.time() - t0, 1)
        after = _summary(spike.force_state())
        cyc = {"index": c, "screw_seconds": elapsed,
               "completed": res.get("completed"), "requested": res.get("requested"),
               "aborted": res.get("aborted"), "reached": res.get("reached"),
               "success": res.get("success"),
               "grip_released": res.get("grip_released"),
               "before": before, "after": after, "trace": res.get("trace")}
        out["cycles"].append(cyc)
        spike.say(f"  screw {res.get('completed')}/{res.get('requested')} iters "
                  f"in {elapsed}s success={res.get('success')}")
        if res.get("aborted"):
            spike.say(f"  ABORTED: {res['aborted']}")
        if res.get("reached"):
            spike.say(f"  REACHED: {res['reached']}")
        spike.say(f"  adv {before['advance_mm']} -> {after['advance_mm']} mm, "
                  f"seat {after['seat_N']} N, torque {after['torque']} Nm, "
                  f"pad {after['pad_N']} N, tilt {after['tilt_deg']}, "
                  f"held {after['held']}")
        _print_trace(res.get("trace") or [])
        if res.get("aborted") or after.get("failure"):
            break

        # The helix stopped on the env's own terminal condition -- torque at
        # target with the seat loaded.  This is the state held_steps scores,
        # so go straight to the hold rather than unwinding a seated thread.
        if res.get("reached"):
            out["reached_cycle"] = c
            break
        if (after["seat_N"] or 0.0) > SEAT_STOP_N:
            spike.say(f"  seat loaded ({after['seat_N']} N) without the torque "
                      f"reaching target -- stopping rather than driving into "
                      f"the 180 N axial limit")
            break

        if c + 1 >= args.cycles:
            break

        # Release before unwinding: turning while still holding unscrews the
        # bulb (the ordering the expert uses, :2232-2240).
        spike.say("  --- release + unwind the empty wrist + regrasp ---")
        cyc["release"] = spike._release()
        spike.say(f"  release -> success={cyc['release']['success']} "
                  f"in {cyc['release']['seconds']}s")
        cyc["unwind"] = spike._turn(-segment_rad, f"unwind_{c}")
        if not all(r["success"] for r in cyc["unwind"]):
            spike.say("  unwind refused -- stopping")
            break
        grasped: dict = {}
        ok = spike._grasp(grasped)
        cyc["regrasp"] = grasped
        cyc["regrasped"] = ok
        spike.say(f"  regrasp -> {ok}")
        if not ok:
            spike.say("  regrasp failed -- stopping")
            break

    out["final"] = _summary(spike.force_state())

    if not args.no_hold and out["cycles"]:
        spike.say(f"\n=== hold: constant pose for {args.hold_iterations} iterations "
                  f"({args.hold_iterations * 2} stationary steps) ===")
        t0 = time.time()
        try:
            res = spike._post("/rotate_gripper", {
                "object_name": "light_bulb", "angle_rad": 0.0,
                "steps": int(args.hold_iterations), "feed_lead_m": lead_m,
            }, timeout=1800.0)
            out["hold"] = {"seconds": round(time.time() - t0, 1),
                           "completed": res.get("completed"),
                           "requested": res.get("requested"),
                           "aborted": res.get("aborted"),
                           "success": res.get("success"),
                           "trace": res.get("trace")}
        except Exception as exc:  # noqa: BLE001 -- a submit inside take_action
            out["hold"] = {"error": str(exc)}  # can close the HTTP call early
            spike.say(f"  hold raised (often the END gesture submitting): {exc}")
        st = spike.force_state()
        out["after_hold"] = _summary(st)
        out["after_hold"]["submission_requested"] = st.get("submission_requested")
        out["after_hold"]["accepted"] = st.get("accepted_result")
        spike.say(f"  after hold: {json.dumps(out['after_hold'], default=str)}")

    out["final"] = _summary(spike.force_state())
    spike.say(f"\nfinal: {json.dumps(out['final'], default=str)}")
    try:
        out["verdict"] = spike._get("/check_success")
        spike.say(f"check_success: {json.dumps(out['verdict'], default=str)[:800]}")
    except Exception as exc:  # noqa: BLE001
        out["verdict"] = {"error": str(exc)}
        spike.say(f"check_success failed: {exc}")
    if args.submit:
        try:
            out["submit"] = spike._post("/submit_episode", {}, timeout=1800.0)
            spike.say(f"submit -> {json.dumps(out['submit'], default=str)[:600]}")
        except Exception as exc:  # noqa: BLE001
            out["submit"] = {"error": str(exc)}
            spike.say(f"submit failed: {exc}")

    spike.result = out
    spike.finish()


if __name__ == "__main__":
    main()
