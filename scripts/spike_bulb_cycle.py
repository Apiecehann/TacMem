#!/usr/bin/env python3
"""Spike 2: can the expert's segment/release/regrasp cycle seat the bulb?

Run 3 of ``spike_bulb_tightening.py`` concluded that the thread self-feeds
(74/89/88 % of the geometric ideal) but that rotation stalls at ~85 deg and the
glass then crushes, and blamed the wrist — "near a singularity" at this bulb
location.  Re-reading the sources says otherwise:

* ``_screw_segment`` (bulb_tightening.py:1968) never commands rotation alone.
  Its centre is ``start.p - [0, 0, lead*angle/2*pi + axial_correction]``: the
  descent is commanded *together with* the turn, so the gripper follows the
  thread down instead of fighting it.
* ``move_delta(coord="ee")`` is exactly that composition.  It runs
  ``add_bias([dx,dy,dz], coord="local")`` and *then*
  ``add_rotation(euler, coord="local")`` (launch_univtac_server.py:814-818).
  The local branch of ``add_rotation`` keeps ``new_p = self.p``
  (transforms.py:134-136), so ``dz`` travels along the **tool axis** (local +z).
  Its rotation, though, is *not* local: both branches compute
  ``new_q = qmult(new_rotation, self.q)`` from the same ``euler2quat``
  (transforms.py:135 and :139) — a pre-multiplied world-frame rotation, so
  ``drz`` always turns about **world +z** and ``coord`` only decides whether the
  position is left alone or orbited about the world origin.  For this bulb the
  tool is within ~1.8 deg of vertical, so the two axes nearly coincide, but the
  *sign* must not be derived from the tool axis: tightening means
  ``signed_progress_turns`` (:611, ``-handedness * yaw/360``) increasing, hence
  ``drz`` of sign ``-handedness`` — here ``+1`` — whatever the wrist is doing.
  A helical segment in one call.
* Run 3 never tested the coupled form: its coupled fallback is gated on
  ``not self_fed`` (:468), and ``self_fed`` came from a comparison against a
  *post-fracture* displacement, so the gate never opened.

So the ~85 deg wall is more likely axial than singular: holding the EE still
while the thread drags the bulb down ~1.9 mm per 90 deg accumulates a growing
mismatch until the glass gives.  This script tests that reading first, then
carries on into the cycle the expert actually runs:

    coupled segment -> release -> park the empty wrist -> re-grasp -> repeat

``held_steps`` needs 132 *consecutive* sim steps with the torque in band and the
motion stopped (``_step`` :1684-1689), which the expert gets from the tail of
``_screw_segment`` once the bulb seats and stops turning.  Here that tail is
driven explicitly: a minimal in-place turn, just above the API's 0.02 rad
minimum, issued over and over while the seated bulb refuses to move.

Reads the privileged thread spec from ``/force_state`` only.  Never starts or
stops a server — that stays the user's call.

Usage:
    env -u ALL_PROXY -u all_proxy python scripts/spike_bulb_cycle.py
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from spike_bulb_tightening import Spike, pad_max, quat_to_rot  # noqa: F401

# 0.40 rad = 22.9 deg: the step run 3 validated, and under move_delta's 0.5 rad
# per-call rotation cap.  ``--step-deg`` overrides it -- the expert walks 3 deg
# per IK target (60 targets per pi segment, :1979), so a run at 3 deg tests
# whether command granularity is what the coarse step was losing to.
STEP_RAD = 0.40
# The expert's segment is pi with efficient_wrist_motion (:1972), walked here as
# sub-steps so each call stays a small rigid delta rather than one big sweep.
SEGMENT_RAD = math.pi
# Just above move_delta's 0.02 rad floor, so the AND-gate opens on rotation
# alone while the axial feed stays under the 2 mm translation floor.
HOLD_STEP_RAD = 0.021
HOLD_MAX_OPS = 60
HOLD_TARGET = 132
MAX_SEGMENTS = 8
# The direction check is a *verification*, not a discovery: the sign follows from
# ``signed_progress_turns`` (see the module docstring), so a wrong verdict here
# should be impossible.  Keep the probe small anyway -- ``phase_error_m`` is
# cumulative, and a full 0.40 rad call spent the wrong way costs ~1 mm of its
# 2 mm budget permanently.
DIR_TEST_RAD = 0.10
# ``_position_empty_wrist`` (:1826-1847) drives joint 7 to
# ``controller_spin_sign * 1.65`` rad -- 94.5 deg -- while the gripper is empty.
# Parked there, the 180 deg segment that follows sweeps the wrist from +94.5 to
# -85.5 deg, inside the joint range; from a cold ~0 deg start the same segment
# would end hard against the +180 deg stop.  The expert does this once after the
# probe (``prepare_formal_wrist``, :2222-2228) and again between segments.
PARK_RAD = 1.65
# Each park sub-step is then 0.275 rad = 15.8 deg, well clear of move_delta's
# 0.02 rad floor, while keeping every call a small rigid delta.
PARK_SUBSTEPS = 6


class Cycle(Spike):
    """Drive the expert's regrouped screw cycle through the atom API."""

    #: Run the expert's ``prepare_formal_wrist`` block before segment 1.  On by
    #: default: it is the one expert action the 22.9 deg and 3.0 deg runs never
    #: reproduced, and it is where the measured grasp-quality gap lives.
    prepare_wrist = True

    def _state(self) -> dict:
        """One force-state read, flattened into the row we log per call."""
        st = self.force_state()
        m = st.get("measured") or {}
        row = self._brief(st)
        row.update({
            "step": st.get("step_count"),
            "raw_adv_mm": None if m.get("advance_m") is None
            else round(float(m["advance_m"]) * 1000.0, 4),
            "signed_torque": m.get("signed_torque_Nm"),
            "ang_speed": m.get("angular_speed_deg_s"),
            "axial_speed": m.get("axial_speed_m_s"),
            "drift_mm": None if m.get("center_drift_m") is None
            else round(float(m["center_drift_m"]) * 1000.0, 4),
        })
        return row

    def _tool_axis_sign(self) -> float:
        """+1 when the EE's local +z points *down* in world, else -1.

        ``coord="ee"`` takes both the translation and the rotation in the EE's
        local frame, so this single sign says which way ``dz`` descends and
        which way ``drz`` turns the tool about world z.
        """
        ee = self._get("/ee_pose")
        rot = quat_to_rot(ee["quaternion_wxyz"])
        return 1.0 if float(rot[2, 2]) < 0.0 else -1.0

    def _coupled(self, spin: float, sign: float, angle_rad: float, feed_m: float,
                 tag: str) -> dict:
        """One coupled helical step: descend ``feed_m`` while turning ``angle_rad``."""
        t0 = time.time()
        res = self._post("/move_delta", {
            "drz": spin * angle_rad,
            "dz": sign * feed_m,
            "coord": "ee",
            "tag": tag,
        })
        row = self._state()
        row.update({"tag": tag, "success": res.get("success"),
                    "message": res.get("message", ""),
                    "seconds": round(time.time() - t0, 1),
                    "cmd_drz_deg": round(math.degrees(spin * angle_rad), 3),
                    "cmd_dz_mm": round(sign * feed_m * 1000.0, 4)})
        return row

    def _turn(self, d_rad: float, tag: str) -> dict:
        """In-place turn only, no commanded descent (used to park the wrist)."""
        t0 = time.time()
        res = self._post("/move_delta", {"drz": d_rad, "coord": "ee", "tag": tag})
        row = self._state()
        row.update({"tag": tag, "success": res.get("success"),
                    "message": res.get("message", ""),
                    "seconds": round(time.time() - t0, 1),
                    "cmd_drz_deg": round(math.degrees(d_rad), 3)})
        return row

    # -- the cycle ---------------------------------------------------------

    def _regrasp(self, index: int, spin: float, n_sub: int) -> dict:
        """Release, park the empty wrist back, re-approach and close.

        Order matters and is the expert's (_perform_bulb_episode :2222-2226):
        the wrist return happens *after* the release, because turning while the
        bulb is still held simply unscrews what was just tightened.
        """
        out: dict = {}
        t0 = time.time()
        open_res = self._post("/gripper", {"action": "open", "position": 1.0}, timeout=900.0)
        out["open"] = {"success": open_res.get("success"),
                       "message": open_res.get("message", ""),
                       "seconds": round(time.time() - t0, 1),
                       "state": self._state()}
        self.say(f"  [{index}] release -> success={open_res.get('success')} "
                 f"in {out['open']['seconds']}s  "
                 f"depths={self._state().get('pad_N')}")

        out["unwind"] = []
        for k in range(n_sub):
            row = self._turn(-spin * STEP_RAD, f"cycle_unwind_{index}_{k}")
            out["unwind"].append(row)
            if not row["success"]:
                self.say(f"  [{index}] unwind refused: {row['message']}")
                break

        grasp_out: dict = {}
        ok = self._grasp(grasp_out)
        out["grasp"] = grasp_out
        out["regrasped"] = ok
        return out

    def _prepare_formal_wrist(self, index: int, spin: float) -> dict:
        """The expert's ``prepare_formal_wrist`` block (:2222-2228), in order.

        release -> park the *empty* wrist -> re-grasp centred on the bulb.  The
        park has to come after the release: turning the wrist while the bulb is
        still held simply unscrews what the probe just measured (same ordering
        argument as ``_regrasp``).  ``_grasp`` already *is* the ``force_recenter``
        branch -- ``_close_bulb(1, force_recenter=True)`` (:1877-1883) puts the
        gripper centre at ``bulb + [0, 0, .058]`` keeping the current
        orientation, which is exactly what ``GRASP_LOCAL_Z`` /
        ``GRIPPER_OFFSET_M`` reproduce -- so the new thing here is doing it a
        *second* time, after the probe, from a parked wrist.
        """
        out: dict = {}
        t0 = time.time()
        open_res = self._post("/gripper", {"action": "open", "position": 1.0},
                              timeout=900.0)
        out["open"] = {"success": open_res.get("success"),
                       "message": open_res.get("message", ""),
                       "seconds": round(time.time() - t0, 1)}
        self.say(f"  [prepare {index}] release -> success={open_res.get('success')} "
                 f"in {out['open']['seconds']}s")

        out["park"] = []
        for k in range(PARK_SUBSTEPS):
            row = self._turn(spin * PARK_RAD / PARK_SUBSTEPS,
                             f"cycle_park_{index}_{k}")
            out["park"].append(row)
            if not row["success"]:
                self.say(f"  [prepare {index}] park refused: {row['message']}")
                break
        parked = out["park"][-1] if out["park"] else {}
        self.say(f"  [prepare {index}] empty wrist parked at yaw "
                 f"{parked.get('yaw_deg')}, pad_N={parked.get('pad_N')}")

        grasp_out: dict = {}
        ok = self._grasp(grasp_out)
        out["grasp"] = grasp_out
        out["regrasped"] = ok
        return out

    def _hold(self, spin: float, sign: float, feed_per_rad: float, target: float,
              tol: float) -> list:
        """Drive the tail that accumulates ``held_steps`` once the bulb seats.

        The expert gets these from ``_screw_segment``'s remaining ticks: the
        commanded angle keeps growing, the seated bulb cannot follow, so the
        measured angular speed falls under 3 deg/s and every tick counts as
        held (:1684-1689).  Here each op is a minimal turn, well under the
        move_delta floor for translation, so the same stall is reproduced.
        """
        rows = []
        for i in range(HOLD_MAX_OPS):
            row = self._coupled(spin, sign, HOLD_STEP_RAD,
                                feed_per_rad * HOLD_STEP_RAD, f"cycle_hold_{i}")
            rows.append(row)
            self.say(f"    hold {i}: {row}")
            if row["fail"]:
                break
            if (row.get("held") or 0) >= HOLD_TARGET:
                self.say(f"    held_steps reached {row['held']} — band held long enough")
                break
            torque = row.get("torque")
            if torque is not None and abs(float(torque) - target) > tol:
                self.say(f"    hold stopped: torque {torque} left the band "
                         f"{target}+/-{tol}")
                break
        return rows

    def _direction_test(self, sign: float, spin: float, handedness: int,
                        feed_per_rad: float) -> tuple:
        """Confirm the turn direction off one small coupled call.

        ``signed_progress_turns`` is ``-handedness * world_yaw / 360`` (:611) and
        ``drz`` is a world-frame rotation, so tightening is ``drz`` of sign
        ``-handedness`` — derived, not guessed.  This call checks the sim agrees,
        and confirms which way ``dz`` descends.  It is deliberately short: the
        reversible probe above cannot see the sign because it scores both
        directions, and ``phase_error_m`` is cumulative, so a wrong guess at the
        full step size would eat half the acceptance budget.
        """
        before = self._state()
        ee_before = float(self._get("/ee_pose")["position"][2])
        row = self._coupled(spin, sign, DIR_TEST_RAD, feed_per_rad * DIR_TEST_RAD,
                            "cycle_dir_test")
        ee_after = float(self._get("/ee_pose")["position"][2])
        verdict: dict = {"call": row, "flipped_spin": False, "flipped_sign": False}
        if row["success"]:
            yaw0, yaw1 = before.get("yaw_deg"), row.get("yaw_deg")
            if yaw0 is not None and yaw1 is not None:
                progress = -handedness * (float(yaw1) - float(yaw0)) / 360.0
                verdict["progress_turns"] = round(progress, 5)
                if progress < 0.0:
                    spin = -spin
                    verdict["flipped_spin"] = True
            # Which way dz actually went must be read off the *end-effector*, not
            # the bulb.  A wrong spin unscrews the bulb, which raises it by the
            # thread alone -- reading bulb_z here would then flip ``sign`` on a
            # thread effect and command the EE to climb for the rest of the run.
            verdict["ee_dz_mm"] = round((ee_after - ee_before) * 1000.0, 4)
            if ee_after > ee_before:
                sign = -sign
                verdict["flipped_sign"] = True
        verdict["sign"], verdict["spin"] = sign, spin
        self.say(f"  direction test: {verdict}")
        return sign, spin, verdict

    def run_cycle(self) -> dict:
        self.say("\n=== cycle: coupled segments + release/park/regrasp ===")
        st = self.force_state()
        spec = st.get("thread_spec") or {}
        params = st.get("params") or {}
        handedness = int(spec.get("handedness", 0) or 0)
        lead = float(spec.get("lead_m", 0.0) or 0.0)
        travel = spec.get("nominal_free_travel_m")
        target = float(params.get("target_torque_Nm", 0.12))
        tol = float(params.get("torque_tolerance_Nm", 0.012))
        if handedness == 0 or lead <= 0.0:
            self.say("  no usable thread_spec — aborting")
            return {"skipped": "no thread_spec"}
        sign = self._tool_axis_sign()
        # drz is a world-frame rotation, so the tightening sign comes from the
        # thread alone (-handedness) and never from the tool axis; ``sign`` only
        # says which way dz descends.  Both are still checked empirically.
        spin = -float(handedness)
        feed_per_rad = lead / (2.0 * math.pi)
        n_sub = max(1, int(round(SEGMENT_RAD / STEP_RAD)))
        self.say(f"  thread_spec: handedness={handedness:+d} lead={lead*1000:.3f} mm/rev "
                 f"free_travel={None if travel is None else round(float(travel)*1000,3)} mm")
        self.say(f"  tool axis sign={sign:+.0f} (local +z points "
                 f"{'down' if sign > 0 else 'up'} in world) -> spin={spin:+.0f}")
        self.say(f"  step {math.degrees(STEP_RAD):.2f} deg, feed "
                 f"{feed_per_rad*STEP_RAD*1000:.4f} mm/call, {n_sub} calls per "
                 f"{math.degrees(SEGMENT_RAD):.0f} deg segment")

        result: dict = {"thread_spec": spec, "sign": sign, "spin": spin,
                        "feed_mm_per_call": feed_per_rad * STEP_RAD * 1000.0,
                        "params": {k: params.get(k) for k in
                                   ("target_torque_Nm", "torque_tolerance_Nm",
                                    "grip_depth_mm", "initial_depth_m")},
                        "segments": []}

        pre: dict = {}
        if not self._grasp(pre):
            self.say("  grasp failed — aborting")
            result["grasp"] = pre
            return result
        result["grasp"] = pre
        self._probe(result)

        # The expert's order is probe -> prepare_formal_wrist -> screw (:2216,
        # :2222-2228, :2232).  The direction check is this script's own
        # verification, so it slots in after the prepare rather than displacing
        # it -- a re-grasp does not change which way the thread runs.
        if self.prepare_wrist:
            self.say("\n  --- prepare_formal_wrist: release -> park empty wrist -> "
                     "recentre re-grasp ---")
            prep = self._prepare_formal_wrist(0, spin)
            result["prepare_formal_wrist"] = prep
            if not prep.get("regrasped"):
                self.say("  prepare_formal_wrist failed — stopping")
                result["final"] = self._state()
                return result

        sign, spin, test = self._direction_test(sign, spin, handedness, feed_per_rad)
        result["direction_test"] = test

        # One segment is 180 deg, well past the ~85 deg that killed run 3, so a
        # single-segment run already decides whether coupling removed that wall.
        for seg in range(1, self.max_segments + 1):
            self.say(f"\n  --- segment {seg}/{self.max_segments} ---")
            rows = []
            for k in range(n_sub):
                row = self._coupled(spin, sign, STEP_RAD, feed_per_rad * STEP_RAD,
                                    f"cycle_screw_{seg}_{k}")
                rows.append(row)
                self.say(f"    screw {seg}.{k}: {row}")
                if row["fail"]:
                    self.say(f"    stopping at segment {seg}: failure={row['fail']}")
                    result["segments"].append({"index": seg, "rows": rows,
                                               "stopped": row["fail"]})
                    result["final"] = self._state()
                    return result
                if not row["success"]:
                    self.say(f"    move refused: {row['message']}")
                    result["segments"].append({"index": seg, "rows": rows,
                                               "stopped": "refused"})
                    result["final"] = self._state()
                    return result
                torque = row.get("torque")
                if torque is not None and abs(float(torque) - target) <= tol:
                    self.say(f"    seated: torque {torque} inside {target}+/-{tol}")
                    result["segments"].append({"index": seg, "rows": rows,
                                               "stopped": "seated"})
                    result["hold"] = self._hold(spin, sign, feed_per_rad, target, tol)
                    result["final"] = self._state()
                    return result

            self.say(f"  segment {seg} done, regrasping")
            regrasp = self._regrasp(seg, spin, n_sub)
            result["segments"].append({"index": seg, "rows": rows, "regrasp": regrasp})
            if not regrasp.get("regrasped"):
                self.say(f"  segment {seg}: regrasp failed — stopping")
                result["final"] = self._state()
                return result

        result["final"] = self._state()
        return result

    def submit(self) -> dict:
        """Submit, retrying through the server's own request timeout.

        ``/submit_episode`` runs ``_finish_bulb_episode`` under the server's
        ``_MOTION_TIMEOUT`` (still 60 s on the running process), and the
        *accepted* branch is 90+150+60 steps -- longer than that.  A client-side
        timeout does not stop the work, and ``_finish_bulb_episode`` returns
        early once ``_accepted_result`` is set (:1750-1751), so a retry either
        picks up the finished verdict or queues behind the still-running one.
        """
        self.say("\n=== submit ===")
        t0 = time.time()
        attempts = []
        for attempt in range(3):
            a0 = time.time()
            try:
                body, status = self._post("/submit_episode", {}, timeout=2400.0), 200
            except RuntimeError as exc:
                body, status = {"error": str(exc)}, 0
            attempts.append({"attempt": attempt, "status": status,
                             "seconds": round(time.time() - a0, 1), "body": body})
            self.say(f"  /submit_episode attempt {attempt} -> HTTP {status} in "
                     f"{attempts[-1]['seconds']}s: {json.dumps(body)[:500]}")
            if status == 200:
                break
        after = self._state()
        self.say(f"  after: {after}")
        return {"attempts": attempts, "seconds": round(time.time() - t0, 1),
                "verdict": attempts[-1]["body"], "after": after}


def main() -> None:
    global STEP_RAD
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://127.0.0.1:8120")
    ap.add_argument("--output-dir", default="outputs/spike_bulb_tightening")
    ap.add_argument("--no-reset", action="store_true",
                    help="skip /reset (only safe if the process is already clean)")
    ap.add_argument("--submit", action="store_true", help="POST /submit_episode at the end")
    ap.add_argument("--max-segments", type=int, default=MAX_SEGMENTS,
                    help=f"segments before stopping (default {MAX_SEGMENTS}); "
                         "1 is enough to test whether coupling removes the ~85 deg wall")
    ap.add_argument("--step-deg", type=float, default=math.degrees(STEP_RAD),
                    help=f"rotation per move_delta call, degrees (default "
                         f"{math.degrees(STEP_RAD):.1f}). The expert walks 3.0 deg per "
                         "IK target; 22.9 is the coarse step that crushed at ~91 deg. "
                         "Must stay above move_delta's MIN_ROT floor of 1.15 deg.")
    ap.add_argument("--no-prepare-wrist", action="store_true",
                    help="skip the expert's prepare_formal_wrist block. It is ON by "
                         "default because it is the one expert step the 22.9/3.0 deg "
                         "runs never reproduced; turn it off only to A/B it.")
    args = ap.parse_args()

    if args.step_deg < 1.15:
        ap.error(f"--step-deg {args.step_deg} is at or below move_delta's MIN_ROT "
                 "floor (0.02 rad = 1.15 deg); the call would not rotate at all")
    STEP_RAD = math.radians(args.step_deg)

    out_dir = Path(args.output_dir)
    cyc = Cycle(args.base_url, out_dir)
    cyc.max_segments = max(1, args.max_segments)
    cyc.prepare_wrist = not args.no_prepare_wrist
    try:
        cyc.say(f"  /health: {cyc._get('/health')}")
        if not args.no_reset:
            t0 = time.time()
            try:
                cyc._post("/reset", {"seed": 0}, timeout=7200.0)
            except RuntimeError as exc:
                cyc.say(f"  RESET FAILED after {round(time.time()-t0,1)}s: {exc} — stopping")
                return
            cyc.say(f"  /reset(seed=0) -> ok in {round(time.time()-t0,1)}s")
        result = {"reset": None if args.no_reset else "ok"}
        result["cycle"] = cyc.run_cycle()
        if args.submit:
            result["submit"] = cyc.submit()
        cyc.log["cycle_run"] = result
    finally:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "spike_bulb_cycle.json"
        path.write_text(json.dumps(cyc.log, indent=2, default=str) + "\n")
        cyc.say(f"\nwrote {path}")


if __name__ == "__main__":
    main()
