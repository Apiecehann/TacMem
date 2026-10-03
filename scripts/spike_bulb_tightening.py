#!/usr/bin/env python3
"""Spike: can ViTaForge's ``bulb_tightening`` be driven and scored through cap-x?

``bulb_tightening`` is built for ViTaForge's own per-seed worker pipeline
(``dispatch_force_task_seeds``: one subprocess per seed, ``VITAFORGE_TASK_CONFIG``
pointing at the task_config YAML, joint-space ``take_action`` contract).  cap-x's
server drives tasks a different way (one long-lived process, a per-trial
``/reset`` with a fresh seed, an atom-based motion API, no ``take_action``).
This script measures whether the two can meet, before anyone writes an oracle or
a prompt against assumptions.

It answers four questions and prints one ``[Qn]`` verdict line each:

  Q1  Does the task construct and reset at all (incl. runtime tetgen assets)?
  Q2  Does the per-seed assertion block a second ``/reset`` in one process?
  Q3  Do the YAML's ``task_parameters`` reach the task?
  Q4  Can the bulb be grasped, probed and screwed through this server's motion
      API, and does ``/submit_episode`` then produce a meaningful verdict?

Q4 is the decisive one: if the thread cannot be advanced by motions this API can
express, the port is not viable without new server primitives.

Geometry and motion constants below are read from the task source, not guessed:

* ``GRASP_LOCAL_Z = 0.058`` — envs/bulb_tightening.py:886, grasps this far above
  the bulb's own origin, in the bulb's local frame (the bulb's rotation is about
  z, so this is world +z).
* ``GRASP_PRE_DISTANCE = 0.050`` — :888, the pre-grasp waypoint above it.
* ``GRIPPER_ADAPTIVE_CLOSE_TARGET = 0.0`` — :890, close fully, let the tactile
  servo stop it.
* ``_close_bulb`` (:1068) closes with a depth threshold; the shipped YAML's
  ``grip_depth_mm: 27.2`` is the threshold this spike sends.
* ``gripper_offset = 0.131`` — envs/robot/robot_cfg.py (gsmini configs; the
  robotiq config's 0.1644 does not apply, the YAML asks for ``sensor_type:
  gsmini``).  ``gripper_center_to_ee`` (envs/robot/robot.py:285) applies it as a
  *local* offset, so the EE sits ``0.131`` along the gripper centre's local -z.
* ``probe_both_directions`` (:516) probes each direction separately and returns
  to the grasp origin between them, which is what makes the reversible probe
  register (see ``ReversibleThreadProbe.update``, :462).
* ``_screw_segment`` (:1968) couples rotation about world z to a descent of
  ``lead * angle / 2*pi``.  ``thread_spec.signed_progress_turns`` (:611) is
  ``-handedness * yaw / 360``, so tightening rotates *against* the handedness.
* **Rotations go through ``coord="ee"``, never ``coord="world"``.**  The two
  branches of ``Pose.add_rotation`` differ in more than frame: ``"local"`` keeps
  ``new_p = self.p`` (in place) while ``"world"`` sets ``new_p = R @ self.p``,
  orbiting the pose about the **world origin** (transforms.py:134-140).  At the
  bulb's x = 0.56 m a 4 deg world rotation is a 3.9 cm sideways drag, which is
  how an earlier version of this probe crushed the bulb while reporting four
  clean successes.

Runs against an already-running server; it never starts or stops one (that is the
user's call).  Plain HTTP, no LLM harness, no GPU work of its own.  Verified
against the *shipped* spec only in that it reads the hidden spec from the
privileged ``/force_state`` — this is a spike, not a policy.

Usage:
    python scripts/spike_bulb_tightening.py --base-url http://127.0.0.1:8120
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import requests

# Values the shipped task_config/bulb_tightening.yml sets.  Q3 is a check that
# these actually arrive, not just that construction succeeded.
EXPECTED_PARAMS = {
    "initial_depth_m": 0.014,
    "grip_depth_mm": 27.2,
    "target_torque_Nm": 0.12,
    "torque_tolerance_Nm": 0.012,
    "seat_top_m": 0.022,
    "thread_friction": 0.15,
    "policy_hold_steps": 180,
}

GRASP_LOCAL_Z = 0.058
GRASP_PRE_DISTANCE = 0.050
GRIPPER_OFFSET_M = 0.131

# Probe geometry from the env's own reversible probe (:516): +/-4 deg, each
# direction returning to the grasp origin before the next starts, because
# ReversibleThreadProbe.update only scores at home (:473).  The env also sinks
# the gripper centre 0.15 mm during the excursion (:525); that is an axial
# command, and the API's in-place rotation cannot carry one, so it is dropped
# here — the scored return-to-home is unaffected.
PROBE_ANGLE_RAD = math.radians(4.0)

# move_delta caps a single call at 0.5 rad / 0.10 m.  At 0.40 rad a call feeds
# lead * 0.40 / 2*pi = 0.49 mm for this seed, so 30 calls cover the 13 mm of
# free travel the spec reports — enough to finish the insertion, not just to
# detect whether it starts.
SCREW_STEP_RAD = 0.40
SCREW_MAX_CALLS = 30


def quat_to_rot(q) -> np.ndarray:
    """Rotation matrix from a wxyz quaternion (no scipy dependency)."""
    w, x, y, z = (float(v) for v in q)
    n = math.sqrt(w * w + x * x + y * y + z * z)
    if n == 0:
        raise ValueError("zero quaternion")
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def pad_max(measured: dict) -> float | None:
    """Largest pad normal force in a ``measured`` row.

    This is the number the glass guard reads (``bulb_tightening.py:1672-1676``:
    ``max(pad_normal_force_N) > glass_break_force_N``), so it is what says
    *why* a run died.  It is a diagnostic scaled by ``1/dt^2`` (:1520-1521),
    not newtons in the usual sense; only its ratio to the 135 N threshold is
    meaningful, and only within one run.
    """
    forces = (measured or {}).get("pad_normal_force_N") or {}
    return None if not forces else round(max(forces.values()), 3)


class Spike:
    def __init__(self, base_url: str, out_dir: Path, verbose: bool = True):
        self.base_url = base_url.rstrip("/")
        self.out_dir = out_dir
        self.verbose = verbose
        # trust_env=False so a shell-level ALL_PROXY/socks proxy cannot hijack
        # localhost calls (httpx raises ImportError on that; requests would
        # silently route through the proxy).
        self.http = requests.Session()
        self.http.trust_env = False
        self.log: dict = {"base_url": self.base_url, "events": []}
        out_dir.mkdir(parents=True, exist_ok=True)

    # -- plumbing ----------------------------------------------------------
    def _record(self, name: str, payload) -> None:
        self.log["events"].append({"t": time.time(), "name": name, "data": payload})

    @staticmethod
    def _raise_for_status(r, path: str) -> None:
        """Surface the response body, which is where the server puts the reason.

        Action endpoints raise ``HTTPException(500, f"{prefix}: {e}")`` (see
        ``_run_action``), so the body carries the task's own exception text —
        the only evidence a failing ``/reset`` leaves behind.  Plain
        ``raise_for_status()`` replaces it with "500 Server Error", which is
        how an earlier run of this spike turned a specific assertion into a
        bare status code.
        """
        if r.status_code < 400:
            return
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise RuntimeError(f"HTTP {r.status_code} on {path}: {detail}"[:1200])

    def _get(self, path: str, **params):
        r = self.http.get(f"{self.base_url}{path}", params=params or None, timeout=300)
        self._raise_for_status(r, path)
        return r.json()

    def _post(self, path: str, body: dict, timeout: float = 900.0):
        r = self.http.post(f"{self.base_url}{path}", json=body, timeout=timeout)
        self._raise_for_status(r, path)
        return r.json()

    def say(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    def force_state(self) -> dict:
        return self._get("/force_state")

    @staticmethod
    def _brief(state: dict) -> dict:
        """The few force-state fields worth printing on a loop line."""
        m = state.get("measured") or {}
        return {
            "turns": m.get("formal_net_turns"),
            "adv_mm": None if m.get("formal_advance_m") is None
            else round(float(m["formal_advance_m"]) * 1000, 4),
            "yaw_deg": m.get("signed_world_rotation_deg"),
            "torque": m.get("torque_Nm"),
            "seat_N": m.get("seat_force_N"),
            "phase_err_mm": None if m.get("phase_error_m") is None
            else round(float(m["phase_error_m"]) * 1000, 4),
            "tilt_deg": m.get("tilt_deg"),
            "bulb_z": m.get("bulb_z_m"),
            "pad_N": pad_max(m),
            "peak_N": m.get("peak_pad_normal_force_N"),
            "held": state.get("held_steps"),
            "probe": state.get("probe_physical_complete"),
            "fail": state.get("failure"),
        }

    # -- Q1 ----------------------------------------------------------------
    def q1_construct_and_reset(self) -> dict:
        self.say("\n=== Q1: construct + first reset ===")
        health = self._get("/health")
        self.say(f"  /health: {health}")
        t0 = time.time()
        try:
            reset = self._post("/reset", {"seed": 0}, timeout=7200.0)
            elapsed = time.time() - t0
            ok = bool(reset.get("success", True))
            self.say(f"  /reset(seed=0) -> {reset} in {elapsed:.1f}s")
        except Exception as exc:  # noqa: BLE001 - spike reports, never crashes
            elapsed = time.time() - t0
            reset, ok = {"error": str(exc)}, False
            self.say(f"  /reset(seed=0) FAILED after {elapsed:.1f}s: {exc}")
        result = {"health": health, "reset": reset, "seconds": round(elapsed, 1), "ok": ok}
        self._record("q1", result)
        self.say(f"[Q1] {'PASS' if ok else 'FAIL'} — first reset "
                 f"{'completed' if ok else 'did not complete'} in {elapsed:.1f}s")
        return result

    # -- Q3 (before Q2 and Q4: harmless, and Q4 needs the params) ----------
    def q3_params(self) -> dict:
        self.say("\n=== Q3: do YAML task_parameters reach the task? ===")
        state = self.force_state()
        if not state.get("available"):
            message = str(state.get("message", ""))
            self.say(f"  /force_state unavailable: {message}")
            self._record("q3", state)
            # The endpoint reports both "not a force task" and "the task's own
            # _measure() blew up" as available=False.  They are different
            # findings and only the first one is about Q3, so say which it is
            # rather than printing a verdict that reads as an answer.
            if message.startswith("force_state failed"):
                self.say("[Q3] ERROR — the task's own _measure() raised; the "
                         "params cannot be read this way (see the message above)")
            else:
                self.say("[Q3] FAIL — no force-task state to read")
            return state
        params = state.get("params", {})
        self.say(f"  params keys ({len(params)}): {sorted(params)}")
        missing = [k for k in EXPECTED_PARAMS if k not in params]
        wrong = {
            k: (params.get(k), v) for k, v in EXPECTED_PARAMS.items()
            if k in params and isinstance(params[k], (int, float))
            and abs(float(params[k]) - float(v)) > 1e-9
        }
        for k in sorted(EXPECTED_PARAMS):
            if k in params:
                self.say(f"    {k}: got {params[k]!r} want {EXPECTED_PARAMS[k]!r}")
        result = {"params": params, "missing": missing, "wrong": wrong,
                  "physics_seed": params.get("physics_seed")}
        self._record("q3", result)
        ok = not missing and not wrong
        self.say(f"[Q3] {'PASS' if ok else 'FAIL'} — "
                 f"{'all' if ok else f'{len(missing)} missing / {len(wrong)} wrong'} "
                 f"of {len(EXPECTED_PARAMS)} expected keys; "
                 f"physics_seed={params.get('physics_seed')!r}")
        return result

    # -- Q4 ----------------------------------------------------------------
    def _bulb_z(self) -> float | None:
        try:
            pose = self._get("/object_pose", object_name="light_bulb")
            return None if not pose.get("found") else float(pose["position"][2])
        except Exception:  # noqa: BLE001
            return None

    def _grasp(self, out: dict) -> bool:
        """Reproduce ``_approach_bulb`` + ``_close_bulb`` through the HTTP API."""
        bulb = self._get("/object_pose", object_name="light_bulb")
        if not bulb.get("found"):
            self.say("  light_bulb not found — cannot continue")
            return False
        ee = self._get("/ee_pose")
        q = ee["quaternion_wxyz"]

        bulb_p = np.asarray(bulb["position"], dtype=float)
        grasp_center = bulb_p + np.array([0.0, 0.0, GRASP_LOCAL_Z])
        # gripper_center_to_ee: local offset [0, 0, -gripper_offset], orientation
        # unchanged, which is why the env can reuse the wrist quaternion.
        ee_target = grasp_center + quat_to_rot(q) @ np.array([0.0, 0.0, -GRIPPER_OFFSET_M])
        pre_target = ee_target + np.array([0.0, 0.0, GRASP_PRE_DISTANCE])

        self.say(f"  bulb {np.round(bulb_p, 4).tolist()}  ee_now {np.round(ee['position'], 4).tolist()}")
        self.say(f"  grasp centre {np.round(grasp_center, 4).tolist()}  "
                 f"ee_target {np.round(ee_target, 4).tolist()}  pre {np.round(pre_target, 4).tolist()}")

        moves = {}
        for name, pos in (("pre", pre_target), ("grasp", ee_target)):
            t0 = time.time()
            res = self._post("/move", {
                "position": [float(v) for v in pos],
                "quaternion_wxyz": q,
                "z_approach": 0.0,
                "tag": f"spike_grasp_{name}",
            })
            moves[name] = {"success": res.get("success"), "seconds": round(time.time() - t0, 1),
                           "message": res.get("message", "")}
            self.say(f"  move[{name}] -> {moves[name]}")

        t0 = time.time()
        close = self._post("/gripper", {
            "action": "close",
            "force_threshold": float(EXPECTED_PARAMS["grip_depth_mm"]),
            "use_adaptive_grasp": True,
            "position": 0.0,
        }, timeout=900.0)
        # The env's _close_bulb also passes gripper_require_both_contacts, which
        # this API does not expose — a candidate reason a grip here could fail.
        out["grasp"] = {"moves": moves, "close_seconds": round(time.time() - t0, 1),
                        "close": close, "ee_target": ee_target.tolist(),
                        "bulb_z_after": self._bulb_z()}
        self.say(f"  close -> success={close.get('success')} in "
                 f"{out['grasp']['close_seconds']}s {close.get('message', '')}")
        st = self.force_state()
        m = st.get("measured") or {}
        out["after_grasp"] = {k: st.get(k) for k in
                              ("held_steps", "probe_physical_complete",
                               "reset_grip_released", "monitor_active", "failure")} | {
            "yaw_deg": m.get("signed_world_rotation_deg"),
            "progress_deg": (None if m.get("rotation_deg") is None
                             else round(float(m["rotation_deg"]), 4)),
            "bulb_z_m": m.get("bulb_z_m"),
            "pad_N": pad_max(m),
            "peak_N": m.get("peak_pad_normal_force_N"),
        }
        self.say(f"  after grasp: {out['after_grasp']}")
        return bool(out["after_grasp"]["reset_grip_released"])

    def _probe(self, out: dict) -> None:
        """The env's reversible probe, expressed in the motion API.

        The env's ``_move_probe`` (:496) spins the *gripper centre* about world
        z in place and interpolates over 48 ticks.  The gripper centre sits on
        the tool axis, so spinning the EE about its own local z leaves the
        centre's position fixed and rotates it — the same motion.  That is
        ``coord="ee"``, whose ``add_rotation`` branch keeps ``new_p = self.p``
        (transforms.py:134-136).

        ``coord="world"`` is the trap, and it is why an earlier run of this
        probe reported four clean successes and then a crushed bulb: its
        ``add_rotation`` sets ``new_p = R @ self.p`` (transforms.py:137-140),
        orbiting the EE about the *world origin*.  At the bulb's position
        (x = 0.56 m) a 4 deg "rotation" drags the pads 3.9 cm sideways through a
        bulb that is still threaded into the socket.

        ``ReversibleThreadProbe.update`` (:473-481) scores only at home and
        needs each direction separately to reach 2-6 deg and come back within
        0.6 deg / 0.2 mm, so this goes out and back per direction, in the env's
        order (negative first).
        """
        self.say("  probing in place about the tool axis (coord=ee), "
                 "each direction out and back ...")
        calls = []
        for label, drz in (("out_-4deg", -PROBE_ANGLE_RAD),
                           ("home", +PROBE_ANGLE_RAD),
                           ("out_+4deg", +PROBE_ANGLE_RAD),
                           ("home", -PROBE_ANGLE_RAD)):
            t0 = time.time()
            cmd = {"drz": drz, "coord": "ee", "tag": f"spike_probe_{label}"}
            try:
                res = self._post("/move_delta", cmd)
                calls.append({"label": label, "drz_deg": round(math.degrees(drz), 4),
                              "success": res.get("success"),
                              "message": res.get("message", ""),
                              "seconds": round(time.time() - t0, 1),
                              "state": self._brief(self.force_state())})
            except Exception as exc:  # noqa: BLE001
                calls.append({"label": label, "drz_deg": round(math.degrees(drz), 4),
                              "error": str(exc)})
            self.say(f"    probe {label}: {calls[-1]}")
        st = self.force_state()
        m = st.get("measured") or {}
        out["probe"] = {"calls": calls,
                        "probe_physical_complete": st.get("probe_physical_complete"),
                        "measured_probe_complete": m.get("probe_complete"),
                        "yaw_after_probe_deg": m.get("signed_world_rotation_deg"),
                        "advance_after_probe_mm": None if m.get("advance_m") is None
                        else round(float(m["advance_m"]) * 1000, 4),
                        "bulb_z_m": m.get("bulb_z_m"), "pad_N": pad_max(m)}
        self.say(f"  probe_physical_complete = {st.get('probe_physical_complete')} "
                 f"(measured.probe_complete={m.get('probe_complete')}, "
                 f"yaw after = {m.get('signed_world_rotation_deg')} deg, "
                 f"bulb_z = {m.get('bulb_z_m')}, pad_N = {pad_max(m)})")

    def _screw(self, out: dict, spec: dict) -> dict:
        """Turn the bulb in place, then ask whether the thread feeds itself.

        The env's expert ``_screw_segment`` (:1968-1986) rotates about world z
        *and* descends by ``lead * angle / 2*pi`` in one motion.  No single
        ``move_delta`` expresses that: ``coord="ee"`` rotates in place but
        translates in the tool frame, ``coord="world"`` translates in world but
        orbits the rotation about the world origin.  So this asks the cheaper
        question first — does the helix pull the bulb down on its own?  A real
        thread under a position-controlled arm usually does not, and the answer
        decides whether the port needs a coupled primitive or can be done with
        rotation alone.

        Direction is taken from ``signed_progress_turns = -handedness * yaw /
        360`` (:611), so tightening is positive progress.  ``formal_net_turns``
        cannot be the check for that: ``formal_motion`` returns 0 until the
        probe completes (:487-491), so it reads 0.0 for every pre-probe turn —
        which is why the earlier run's flip test never fired.  The physical
        ``signed_world_rotation_deg`` is the observable that exists at this
        point.
        """
        handedness = int(spec.get("handedness", 0) or 0)
        lead = float(spec.get("lead_m", 0.0) or 0.0)
        if handedness == 0 or lead <= 0:
            self.say("  no usable thread_spec — skipping the screw")
            return {"skipped": True}
        sign = -handedness          # positive progress = tightening
        step_rad = abs(SCREW_STEP_RAD)
        feed_mm = lead * step_rad / (2.0 * math.pi) * 1000.0
        trace, z0 = [], self._bulb_z()
        state0 = self.force_state()
        yaw0 = (state0.get("measured") or {}).get("signed_world_rotation_deg")
        self.say(f"  screw: {math.degrees(step_rad):.2f} deg/call, "
                 f"{feed_mm:.4f} mm of feed per call if the thread self-feeds")
        for i in range(SCREW_MAX_CALLS):
            t0 = time.time()
            try:
                res = self._post("/move_delta", {"drz": sign * step_rad,
                                                 "coord": "ee",
                                                 "tag": f"spike_screw_{i}"})
            except Exception as exc:  # noqa: BLE001
                trace.append({"i": i, "error": str(exc)})
                self.say(f"    screw {i}: RAISED {exc}")
                break
            z = self._bulb_z()
            row = {"i": i, "success": res.get("success"),
                   "seconds": round(time.time() - t0, 1),
                   "bulb_z": z, **self._brief(self.force_state())}
            if z is not None and z0 is not None:
                row["fed_mm"] = round((z0 - z) * 1000.0, 4)
            if row.get("yaw_deg") is not None and yaw0 is not None:
                progress = -handedness * (row["yaw_deg"] - yaw0) / 360.0
                row["progress_turns"] = round(progress, 5)
            trace.append(row)
            self.say(f"    screw {i}: {row}")
            # Flip once, on the first turn's evidence rather than on trust.
            if i == 0 and (row.get("progress_turns") or 0) < 0:
                self.say("    progress went negative — flipping direction")
                sign = -sign
                trace[0]["flipped"] = True
            if row["fail"]:
                self.say(f"    stopping: failure={row['fail']}")
                break
            if not row["success"]:
                self.say(f"    stopping: refused: {res.get('message', '')}")
                break
        fed = [r["fed_mm"] for r in trace if r.get("fed_mm") is not None]
        expected = len(fed) * feed_mm
        self_fed = bool(fed) and fed[-1] > 0.5 * expected
        self.say(f"  self-feed: {fed[-1] if fed else None} mm of {expected:.4f} mm "
                 f"expected over {len(fed)} turns -> {'YES' if self_fed else 'NO'}")

        # If the helix does not feed the bulb, the descent has to be commanded
        # in the same call.  Under coord="ee" that translation is taken in the
        # tool frame, so which sign of dz descends is a property of the wrist,
        # not of the thread — both are tried and the resulting z is reported.
        coupled: dict = {}
        if not self_fed and not any(r.get("fail") for r in trace) and not any(
                r.get("error") for r in trace):
            self.say("  no self-feed — testing a coupled descent in the tool frame")
            for label, dz in (("ee_plus_dz", +lead * step_rad / (2 * math.pi)),
                              ("ee_minus_dz", -lead * step_rad / (2 * math.pi))):
                z_before = self._bulb_z()
                t0 = time.time()
                try:
                    res = self._post("/move_delta",
                                     {"dz": dz, "drz": sign * step_rad, "coord": "ee",
                                      "tag": f"spike_coupled_{label}"})
                    row = {"dz_mm": round(dz * 1000, 4), "success": res.get("success"),
                           "seconds": round(time.time() - t0, 1),
                           "bulb_z_before": z_before, "bulb_z_after": self._bulb_z(),
                           **self._brief(self.force_state())}
                    if z_before is not None and row["bulb_z_after"] is not None:
                        row["fed_mm"] = round((z_before - row["bulb_z_after"]) * 1000, 4)
                    coupled[label] = row
                except Exception as exc:  # noqa: BLE001
                    coupled[label] = {"error": str(exc)}
                self.say(f"    coupled {label}: {coupled[label]}")
                if coupled[label].get("fail"):
                    break
        return {"trace": trace, "lead_m": lead, "handedness": handedness,
                "final_sign": sign, "feed_mm_per_call": round(feed_mm, 5),
                "self_fed": self_fed, "coupled_test": coupled,
                "bulb_z_before": z0, "bulb_z_after": self._bulb_z()}

    def q4_motion(self) -> dict:
        self.say("\n=== Q4: grasp + probe + screw through the motion API ===")
        state0 = self.force_state()
        spec = state0.get("thread_spec") or {}
        out: dict = {"thread_spec": spec}
        self.say(f"  thread_spec (privileged): {spec}")
        if not spec:
            self._record("q4", {"error": "no thread_spec"})
            self.say("[Q4] FAIL — task exposes no thread_spec")
            return out

        grasped = self._grasp(out)
        if grasped:
            self._probe(out)
            out["screw"] = self._screw(out, spec)

        self.say("  submitting ...")
        try:
            verdict = self._post("/submit_episode", {}, timeout=300.0)
        except Exception as exc:  # noqa: BLE001
            verdict = {"error": str(exc)}
        out["verdict"] = verdict
        self.say(f"  /submit_episode -> {verdict}")
        st = self.force_state()
        out["final_state"] = self._brief(st) | {
            "submission_requested": st.get("submission_requested"),
            "accepted_result": st.get("accepted_result")}
        self.say(f"  final: {out['final_state']}")

        self._record("q4", out)
        scored = bool(verdict.get("detail") is not None or verdict.get("completed") is not None) \
            and "has no force-task submission" not in str(verdict.get("message", "")) \
            and "failed" not in str(verdict.get("message", ""))
        screw = out.get("screw") or {}
        turns = screw.get("trace") or []
        advanced = any((r.get("turns") or 0) > 0.05 for r in turns)
        self_fed = bool(screw.get("self_fed"))
        probed = bool((out.get("probe") or {}).get("probe_physical_complete"))
        # The bulb turning is what shows the motion API reached the thread at
        # all; the thread advancing is what shows it can finish the task.  They
        # fail for different reasons and need different fixes, so report both.
        turned = any((r.get("progress_turns") or 0) > 0.01 for r in turns)
        verdict_word = ("PASS" if (scored and advanced) else
                        "PARTIAL" if (scored and turned) else "FAIL")
        self.say(f"[Q4] {verdict_word} — grasp {'ok' if grasped else 'FAILED'}; "
                 f"probe {'registered' if probed else 'did NOT register'}; "
                 f"bulb {'turned' if turned else 'did NOT turn'}; "
                 f"thread {'advanced' if advanced else 'did NOT advance'} "
                 f"(self-feed {'yes' if self_fed else 'no'}); "
                 f"submit {'scored' if scored else 'not possible'}")
        return out

    # -- Q2 (last: a failing reset can leave the task mid-reset) -----------
    def q2_reseed(self) -> dict:
        self.say("\n=== Q2: second /reset with a different seed (destructive) ===")
        try:
            reset = self._post("/reset", {"seed": 1}, timeout=7200.0)
            ok, err = True, None
            self.say(f"  /reset(seed=1) -> {reset}")
        except Exception as exc:  # noqa: BLE001
            reset, ok, err = None, False, str(exc)
            self.say(f"  /reset(seed=1) RAISED: {exc}")
        result = {"ok": ok, "error": err, "reset": reset}
        self._record("q2", result)
        self.say(f"[Q2] {'PASS (reseed works)' if ok else 'FAIL (per-seed assertion blocks it)'}")
        return result

    def finish(self) -> Path:
        path = self.out_dir / "spike_bulb_tightening.json"
        path.write_text(json.dumps(self.log, indent=2, default=str))
        self.say(f"\nwrote {path}")
        return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://127.0.0.1:8120")
    ap.add_argument("--output-dir", default="outputs/spike_bulb_tightening")
    ap.add_argument("--skip-motion", action="store_true",
                    help="only Q1/Q3/Q2 — no grasp, no screw (safe smoke test)")
    args = ap.parse_args()

    spike = Spike(args.base_url, Path(args.output_dir))
    try:
        spike.q1_construct_and_reset()
        spike.q3_params()
        if not args.skip_motion:
            spike.q4_motion()
        spike.q2_reseed()
    finally:
        spike.finish()


if __name__ == "__main__":
    main()
