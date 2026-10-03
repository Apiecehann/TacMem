#!/usr/bin/env python3
"""Run ViTaForge's reversible probe from cap-x, and check the readout both ways.

``probe_both_directions`` (bulb_tightening.py:516) is the env's own tactile
discriminator, and it is a *perturbation response*, not a static reading:

  1. command a known pure axial descent (``depth`` = 0.15 mm) and call the
     resulting marker-flow change the ``basis`` -- this both defines "axial"
     in flow space and calibrates the pixel->metre scale (``strength``);
  2. command a known rotation (+-4 deg) and project its flow onto ``basis``:
     ``axial_equivalent = depth * <response,basis> / <basis,basis>``.

``inferred_advance = rotation_descent - axial_equivalent`` is the thread travel
the tactile saw.  ``decision.signal_separation_m`` -- the gap between the two
rotation directions -- equals the lead under free travel and must collapse to
~0 once the bulb bottoms out.  That collapse is the judge this script exists to
measure.

Transport is ``/move`` (absolute pose), not ``/move_delta``.  ``_move_probe``
(:496) commands an absolute ``Pose``, and ``move_delta`` cannot express the
0.15 mm axial step anyway: its minimum gate rejects a call only when *every*
axis is under its floor (:799), so a pure 0.15 mm descent (dz < 2 mm, all
rotations 0) is refused outright.  ``/move`` has no such gate and no
``[1.0, 0.5, 0.25]`` silent scale-down (:851, which executes half or a quarter
of the command and still returns success: True).

Two readouts are computed on the same frames and compared:

* ``ours``  -- the four scalars /tactile already exposes (``shear_dx/dy`` per
  finger).  Offline this reproduced the env's 20-dim image features to 1.5%.
* ``image`` -- ViTaForge's own ``image_features`` run on the ``rgb_marker``
  base64 payload, i.e. the exact function the env calls.

Agreement between them on live data is the check that the four scalars are a
faithful stand-in and the oracle can skip image decoding entirely.

Never starts or stops a server -- that stays the user's call.

Usage:
    env -u ALL_PROXY -u all_proxy python scripts/spike_bulb_probe.py
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spike_bulb_tightening import (  # noqa: E402
    EXPECTED_PARAMS, GRASP_LOCAL_Z, GRASP_PRE_DISTANCE, GRIPPER_OFFSET_M,
    Spike, pad_max, quat_to_rot,
)

VITAFORGE_ROOT = Path("/mnt/nas/shimufan/ViTaForge")
_FORCE_UTILS = VITAFORGE_ROOT / "envs" / "_force_task_utils.py"


def _load_force_utils():
    """Load ViTaForge's ``_force_task_utils`` by path, not by package name.

    Both cap-x and ViTaForge ship an ``envs`` package on the same interpreter
    (see [[capx-vitforge-tacex-fork]]), so ``import envs._force_task_utils``
    resolves to whichever one won sys.path.  Going through the file avoids the
    coin flip, and skips ``envs/__init__`` (which pulls in Isaac).
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("_vit_forge_force_utils", _FORCE_UTILS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The env's probe constants (probe_both_directions :516-557).
PROBE_DEPTH_M = 0.00015        # `depth` / `probe_axial_compliance`
ROTATION_DESCENT_M = 0.00015   # `rotation_probe_descent_m`
PROBE_ANGLE_RAD = math.radians(4.0)


def _axangle2quat(axis, angle: float) -> np.ndarray:
    """t3d.quaternions.axangle2quat, wxyz, matching the env's convention."""
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    half = 0.5 * float(angle)
    return np.r_[math.cos(half), axis * math.sin(half)]


def _yaw_between(qa, qb) -> float:
    """Signed rotation about world z taking orientation ``qa`` to ``qb``.

    ``drz``/``_move_probe`` pre-multiply a world-frame z rotation, so the
    realised angle is the z component of ``qb * qa^-1`` (wxyz).
    """
    qa = np.asarray(qa, float); qb = np.asarray(qb, float)
    qa = qa / np.linalg.norm(qa); qb = qb / np.linalg.norm(qb)
    inv = np.array([qa[0], -qa[1], -qa[2], -qa[3]])
    d = _qmult(qb, inv)
    if d[0] < 0:
        d = -d
    return 2.0 * math.atan2(float(d[3]), float(d[0]))


def _qmult(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """t3d.quaternions.qmult, wxyz."""
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ])


class ProbeSpike(Spike):
    """Grasp the bulb, then run the probe with both readouts in lockstep."""

    settle: float = 0.0
    probe_angle: float = PROBE_ANGLE_RAD

    def finish(self) -> Path:
        """Persist the probe payload.

        ``Spike.finish`` writes ``self.log``, which this script never fills
        (it does not call ``_record``) — so it would drop everything.  The
        probe's own numbers are the whole point here.
        """
        path = self.out_dir / "probe.json"
        path.write_text(json.dumps(getattr(self, "result", self.log),
                                   indent=2, default=str))
        self.say(f"\nwrote {path}")
        return path

    def _tactile_raw(self) -> dict:
        return self._get("/tactile")

    @staticmethod
    def _ours(t: dict) -> np.ndarray:
        """The four scalars /tactile exposes: [L.dx, L.dy, R.dx, R.dy]."""
        return np.array([
            float(t["left_marker_shear_dx"]), float(t["left_marker_shear_dy"]),
            float(t["right_marker_shear_dx"]), float(t["right_marker_shear_dy"]),
        ])

    @staticmethod
    def _decode(t: dict) -> dict:
        import cv2
        out = {}
        for side, key in (("left_tactile", "left_rgb_base64"),
                          ("right_tactile", "right_rgb_base64")):
            blob = t.get(key) or ""
            if not blob:
                return {}
            buf = np.frombuffer(base64.b64decode(blob), np.uint8)
            bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if bgr is None:
                return {}
            out[side] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return out

    def _image(self, t: dict):
        """The env's own image_features, on the payload /tactile carries."""
        imgs = self._decode(t)
        if not imgs:
            return None, {"error": "no rgb base64 in /tactile"}
        try:
            feats, details = _load_force_utils().image_features(imgs, imgs)
        except ValueError as exc:
            return None, {"error": str(exc)}
        return feats, details

    def _sample(self, tag: str) -> dict:
        """One tactile frame: the four scalars, the images, and the diagnostics.

        The simulator steps continuously on its own (~9 steps/s measured), so
        the marker field keeps relaxing after a move returns.  The env waits
        ``delay(8)`` after ``_move_probe`` before reading; reading immediately
        catches a transient that is mostly unrelaxed elastic strain in the gel,
        which shows up as a large flow component orthogonal to the axial basis.
        """
        if self.settle > 0:
            time.sleep(self.settle)
        t = self._tactile_raw()
        return {
            "tag": tag, "ours": self._ours(t), "imgs": self._decode(t),
            "L_disp": t.get("left_marker_displacement_px"),
            "R_disp": t.get("right_marker_displacement_px"),
            "L_coh": t.get("left_marker_coherence"),
            "R_coh": t.get("right_marker_coherence"),
            "L_depth_mm": t.get("left_depth_mm"),
            "R_depth_mm": t.get("right_depth_mm"),
        }

    @staticmethod
    def _flow(a: dict, b: dict):
        """The env's image_features between two sampled frames, or None."""
        if not a.get("imgs") or not b.get("imgs"):
            return None, {"error": "no rgb base64 in /tactile"}
        try:
            return _load_force_utils().image_features(a["imgs"], b["imgs"])
        except ValueError as exc:
            return None, {"error": str(exc)}

    @staticmethod
    def _slim(s: dict) -> dict:
        """A sample without the images — what actually goes in the JSON."""
        return {k: v for k, v in s.items() if k != "imgs"}

    def _move_abs(self, pos, quat, tag: str) -> dict:
        t0 = time.time()
        res = self._post("/move", {
            "position": [float(v) for v in pos],
            "quaternion_wxyz": [float(v) for v in quat],
            "z_approach": 0.0,
            "tag": tag,
        })
        return {"success": res.get("success"), "message": res.get("message", ""),
                "seconds": round(time.time() - t0, 1)}

    def run_probe(self, out: dict) -> dict:
        out["truth_before"] = self.truth()
        ee0 = self._get("/ee_pose")
        p_start = np.asarray(ee0["position"], dtype=float)
        q_start = np.asarray(ee0["quaternion_wxyz"], dtype=float)
        self.say(f"  probe start EE {np.round(p_start, 5).tolist()}")

        # --- 1. the axial probe: pure descent, orientation untouched ----------
        s0 = self._sample("axial_ref")
        mv = self._move_abs(p_start + np.array([0.0, 0.0, -PROBE_DEPTH_M]),
                            q_start, "spike_probe_axial")
        ee1 = self._get("/ee_pose")
        s1 = self._sample("axial_cur")
        realized = float(p_start[2] - np.asarray(ee1["position"], dtype=float)[2])
        self.say(f"  axial probe: {mv} realised dz = {realized * 1e6:.1f} um")

        basis_img, det = self._flow(s0, s1)
        ax = {"commanded_m": PROBE_DEPTH_M, "realised_m": realized, "move": mv,
              "ref": self._slim(s0), "cur": self._slim(s1), "flow_details": det}
        # ours: /tactile's shears are absolute (vs the reset-time unloaded
        # frame), so the local reference cancels in the difference.
        b_ours = np.asarray(s1["ours"]) - np.asarray(s0["ours"])
        ax["ours"] = {"basis": b_ours.tolist(), "strength": float(b_ours @ b_ours)}
        ax["image"] = (None if basis_img is None else
                       {"basis": list(map(float, basis_img)),
                        "strength": float(basis_img @ basis_img)})
        out["axial"] = ax
        self.say(f"  basis strength: ours={round(ax['ours']['strength'], 4)} "
                 f"image={None if not ax['image'] else round(ax['image']['strength'], 4)}"
                 f"  {det}")
        if ax["ours"]["strength"] <= 0:
            self.say("  !! ours-basis is degenerate — /tactile shear may be all zeros")
            return out

        # --- 2. the two rotation probes, env order (negative first) -----------
        # Faithful to probe_both_directions (:535-556).  EACH rotation probe is
        # built from `start` and commands BOTH parts —
        #     p = start.p - [0,0,rotation_descent]     (the same 150 um descent)
        #     q = axangle2quat([0,0,1], yaw) * start.q
        # — with a return to `start` before each one.  Two things are easy to get
        # wrong here, and earlier this script got both:
        #   * the reference is taken at `start`, so the response *includes* the
        #     descent's own contribution, which is exactly `basis`.  Hence
        #     axeq = depth*(1 + cos*|rot|/|basis|) and `inferred_advance` comes
        #     out as minus the rotation's own axial contribution.  The env's own
        #     recorded pair (181.2 -> -31.2, 63.3 -> +86.7 um) is this identity.
        #   * without the backoff the second probe is measured over 2*angle, not
        #     angle, which doubles the +1 arm and corrupts the separation.
        # The 150 um offset cancels in the separation, the doubled angle does not.
        p_rot = p_start + np.array([0.0, 0.0, -ROTATION_DESCENT_M])
        probes = []
        for sign in (-1, +1):
            self._move_abs(p_start, q_start, f"spike_probe_backoff{sign:+d}")
            ee_a = self._get("/ee_pose")
            pa = np.asarray(ee_a["position"], dtype=float)
            qa = np.asarray(ee_a["quaternion_wxyz"], dtype=float)
            sa = self._sample(f"rot{sign:+d}_ref")
            q_t = _qmult(_axangle2quat([0.0, 0.0, 1.0], sign * self.probe_angle), q_start)
            mv = self._move_abs(p_rot, q_t, f"spike_probe_rot{sign:+d}")
            eb = self._get("/ee_pose")
            pb = np.asarray(eb["position"], dtype=float)
            qb = np.asarray(eb["quaternion_wxyz"], dtype=float)
            realized_r = float(pa[2] - pb[2])
            sb = self._sample(f"rot{sign:+d}_cur")
            resp_img, det_r = self._flow(sa, sb)
            row = {"sign": sign, "move": mv, "realised_dz_m": realized_r,
                   "realised_yaw_deg": math.degrees(_yaw_between(qa, qb)),
                   "commanded_yaw_deg": math.degrees(sign * self.probe_angle),
                   "ref": self._slim(sa), "cur": self._slim(sb),
                   "flow_details": det_r}
            r_ours = np.asarray(sb["ours"]) - np.asarray(sa["ours"])
            for name, b, r in (("ours", b_ours, r_ours),
                               ("image", None if not ax["image"] else
                                np.asarray(ax["image"]["basis"]),
                                None if resp_img is None else np.asarray(resp_img))):
                if b is None or r is None:
                    row[name] = None
                    continue
                # depth (the *axial* probe's) is the scale; rotation_descent is
                # the reference the env subtracts from.
                axeq = PROBE_DEPTH_M * float(r @ b) / float(b @ b)
                row[name] = {"axial_equivalent_m": axeq,
                             "inferred_advance_m": ROTATION_DESCENT_M - axeq,
                             "response": list(map(float, r)),
                             "residual_frac": float(np.linalg.norm(
                                 r - (float(r @ b) / float(b @ b)) * b)
                                 / (float(np.linalg.norm(r)) + 1e-30)),
                             "cos_with_basis": float(r @ b) / (
                                 float(np.linalg.norm(r)) * float(np.linalg.norm(b)) + 1e-30)}
            probes.append(row)
            # NOTE: row[name]["response"] is a list, so it cannot go through round().
            # Print only the scalar readouts.
            def _brief(d):
                if not d:
                    return "None"
                return (f"axeq={d['axial_equivalent_m'] * 1e6:+.1f}um "
                        f"inf={d['inferred_advance_m'] * 1e6:+.1f}um "
                        f"cos={d['cos_with_basis']:+.3f}")

            self.say(f"  rot{sign:+d}: {mv} realised dz={realized_r * 1e6:.1f} um  "
                     f"ours({_brief(row.get('ours'))}) image({_brief(row.get('image'))})"
                     f"  {det_r}")
        # the env's per-probe backoff also leaves the gripper at `start` at the
        # end of the pair; match it so a repeated probe starts from the same state
        self._move_abs(p_start, q_start, "spike_probe_backoff_end")
        out["probes"] = probes
        for name in ("ours", "image"):
            vals = [p[name] for p in probes if p.get(name)]
            if len(vals) == 2:
                inf = [v["inferred_advance_m"] for v in vals]
                out[f"separation_{name}_m"] = abs(inf[0] - inf[1])
        out["per_finger"] = self._per_finger(ax, probes)
        for name, d in out["per_finger"].items():
            out[f"separation_{name}_min_m"] = d["min_m"]
            out[f"separation_{name}_median_m"] = d["median_m"]
        out["truth_after"] = self.truth()
        return out

    @staticmethod
    def _per_finger(ax: dict, probes: list) -> dict:
        """Split each readout per finger and take min/median across fingers.

        Pooling all of a finger's dims is what the env does, and it is
        self-defeating: the four quadrant medians carry ~2% of the signal but
        four fifths of the response vector's norm, so they drag the projection
        cosine toward zero and the axial equivalent with it.  The whole-pad
        median alone keeps cos near 1.

        The per-finger split also gives the project's usual one-sided-blowup
        guard (see [[judge-from-the-carrying-modality]] and the memory_match
        weight axis): a single finger reading far above the other is an
        artifact, so ``min`` across fingers is the estimate and ``spread`` says
        how much to distrust it.
        """
        blocks = {
            "ours": [(0, 2), (2, 4)],                       # L, R: whole-pad
            "image_pad": [(0, 2), (10, 12)],                # L, R: whole-pad only
            "image": [(0, 10), (10, 20)],                   # L, R: pad + quadrants
        }
        out: dict = {}
        for name, spans in blocks.items():
            if not ax.get(name):
                continue
            b_all = np.asarray(ax[name]["basis"])
            per = []
            for lo, hi in spans:
                b = b_all[lo:hi]
                if not b.any():
                    per.append(None)
                    continue
                row = {}
                for p in probes:
                    r = p.get(name)
                    if not r:
                        row = None
                        break
                    rr = np.asarray(r["response"])[lo:hi]
                    row[p["sign"]] = PROBE_DEPTH_M * float(rr @ b) / float(b @ b)
                if row and -1 in row and 1 in row:
                    per.append({"minus_m": row[-1], "plus_m": row[1],
                                "delta_m": abs(row[1] - row[-1])})
                else:
                    per.append(None)
            deltas = [x["delta_m"] for x in per if x]
            if deltas:
                out[name] = {"fingers": per,
                             "min_m": min(deltas),
                             "median_m": float(np.median(deltas)),
                             "max_m": max(deltas),
                             "spread": max(deltas) / (min(deltas) + 1e-12)}
        return out

    def regrasp(self, back_off: float = 0.050) -> dict:
        """Release the bulb and take it again from a clear approach.

        Both probes measure a *small differential* flow on top of whatever
        shear the pads already carry, and the gel is not infinitely compliant:
        after the gripper has been lifted and re-lowered the absolute marker
        displacement reaches ~58 px (~5 mm at ~10.7 px/mm), near the end of the
        linear range.  Opening the gripper unloads the pads so the markers
        relax back, which is what the next probe's 150 um step needs.
        """
        res: dict = {}
        open_ = self._post("/gripper", {"action": "open", "position": 1.0}, timeout=900.0)
        res["open"] = open_.get("success")
        self.say(f"  open -> {open_.get('success')}")
        ee = self._get("/ee_pose")
        p = np.asarray(ee["position"], dtype=float)
        q = np.asarray(ee["quaternion_wxyz"], dtype=float)
        mv = self._move_abs(p + np.array([0.0, 0.0, back_off]), q, "spike_regrasp_clear")
        self.say(f"  back off {back_off * 1000:.0f} mm: {mv}")
        return res

    def report_per_finger(self, r: dict) -> None:
        pf = r.get("per_finger") or {}
        for name in ("ours", "image_pad", "image"):
            if name not in pf:
                continue
            d = pf[name]
            fingers = "  ".join(
                "n/a" if f is None else f"{f['delta_m'] * 1e6:7.1f}"
                for f in d["fingers"])
            self.say(f"  Δ {name:9s} L/R = {fingers} um | "
                     f"min {d['min_m'] * 1e6:7.1f} med {d['median_m'] * 1e6:7.1f} "
                     f"spread {d['spread']:.2f}x")

    def truth(self) -> dict:
        """Ground truth for the probe: bulb depth, world yaw, and pad load.

        Not an input to the judge — the judge must run on tactile alone — but
        every separation number is meaningless without knowing how far the
        bulb actually had left to travel.
        """
        st = self.force_state()
        m = st.get("measured") or {}
        return {"yaw_deg": m.get("signed_world_rotation_deg"),
                "bulb_z_m": m.get("bulb_z_m"), "pad_N": pad_max(m),
                "torque_Nm": m.get("torque_Nm"), "seat_N": m.get("seat_force_N"),
                "center_drift_m": m.get("center_drift_m"),
                "tilt_deg": m.get("tilt_deg"),
                "failure": st.get("failure")}

    def after(self, out: dict) -> None:
        st = self.force_state()
        out["after"] = {**self.truth(), "phase": st.get("phase"),
                        "held_steps": st.get("held_steps"),
                        "probe_physical_complete": st.get("probe_physical_complete")}
        self.say(f"  after: {out['after']}")

    def screw_to_seat(self, out: dict, step_deg: float = 10.0,
                      probe_every: int = 4, pad_abort: float = 118.0,
                      max_steps: int = 120) -> None:
        """Screw the bulb in along an absolute helix, probing as it goes.

        Every target is computed from the pose captured *once*, at the start:

            p_k = p0 - [0, 0, lead * theta_k / 2pi]
            q_k = axangle2quat([0,0,1], +theta_k) * q0

        This is the expert's construction (``center = start.p - [0,0,...]``,
        ``_ik_controller.compute`` from live state each tick), not the
        constant-relative-increment integrator the earlier spike used, where
        every step's shortfall accumulated.  It also means the mid-screw probes
        are self-correcting: a probe rotates the EE +-4 deg off the helix, and
        the next step simply commands the correct absolute point again.

        Stopping is decided by ground truth, not by counting degrees: a probe
        advances the bulb too, so "remaining travel / step size" drifts.  The
        loop stops once the bulb stops descending.

        Positive world yaw is the tightening direction here (``drz`` sign is
        ``-handedness`` = +1 for this task, and the measured run showed +7.97
        deg of yaw for a 0.161 mm descent), so the helix angle is +theta.
        """
        spec = out["thread_spec"]
        lead = float(spec["lead_m"])
        ee0 = self._get("/ee_pose")
        p0 = np.asarray(ee0["position"], dtype=float)
        q0 = np.asarray(ee0["quaternion_wxyz"], dtype=float)
        t0 = self.truth()
        self.say(f"  screw start: bulb_z {t0['bulb_z_m']:.6f} pad_N {t0['pad_N']} "
                 f"lead {lead * 1000:.4f} mm/rev, travel "
                 f"{float(spec['nominal_free_travel_m']) * 1000:.1f} mm")
        out["screw"] = {"p0": p0.tolist(), "q0": q0.tolist(), "lead_m": lead,
                        "step_deg": step_deg, "start": t0, "steps": [],
                        "probes": []}
        stall = 0
        for k in range(1, max_steps + 1):
            theta = math.radians(step_deg * k)
            p_t = p0 + np.array([0.0, 0.0, -lead * theta / (2 * math.pi)])
            q_t = _qmult(_axangle2quat([0.0, 0.0, 1.0], theta), q0)
            mv = self._move_abs(p_t, q_t, f"spike_screw_{k:03d}")
            tr = self.truth()
            prev_bz = out["screw"]["steps"][-1]["bulb_z_m"] if out["screw"]["steps"] else t0["bulb_z_m"]
            expected = lead * math.radians(step_deg) / (2 * math.pi)
            drop = prev_bz - tr["bulb_z_m"]
            out["screw"]["steps"].append({
                "k": k, "theta_deg": step_deg * k, "ok": mv["success"],
                "seconds": mv["seconds"], "bulb_z_m": tr["bulb_z_m"],
                "drop_m": drop, "expected_drop_m": expected,
                "pad_N": tr["pad_N"], "yaw_deg": tr["yaw_deg"],
                "torque_Nm": tr["torque_Nm"], "seat_N": tr["seat_N"],
                "failure": tr["failure"]})
            self.say(f"  screw {k:3d} {step_deg * k:6.1f}deg ok={mv['success']} "
                     f"z {tr['bulb_z_m']:.6f} drop {drop * 1e6:+7.1f}um "
                     f"(exp {expected * 1e6:+.1f}) pad {tr['pad_N']} "
                     f"torque {tr['torque_Nm']} fails={tr['failure']}")
            if tr["failure"]:
                self.say(f"  !! {tr['failure']} — bulb destroyed, stopping")
                out["screw"]["ended"] = f"failure:{tr['failure']}"
                return
            if tr["pad_N"] and tr["pad_N"] > pad_abort:
                self.say(f"  !! pad {tr['pad_N']} > {pad_abort} N — aborting before fracture")
                out["screw"]["ended"] = f"pad_abort:{tr['pad_N']}"
                return
            stall = stall + 1 if drop < 0.30 * expected else 0
            if stall >= 2:
                self.say("  bulb stopped descending — treating as seated")
                out["screw"]["ended"] = "seated"
                break
            if k % probe_every == 0:
                self.say(f"  --- probe at {step_deg * k:.0f} deg ---")
                out["screw"]["probes"].append(self.run_probe({}))
        else:
            out["screw"]["ended"] = "max_steps"
        self.say(f"  screw ended: {out['screw']['ended']}")
        self.say("  --- probe at seat ---")
        out["seat_probe"] = self.run_probe({})

    # --- the expert's real controller: a helix with tactile axial feedback ---
    # _screw_segment (:1968-2010) does NOT command a rigid helix.  Every 4 ticks
    # it measures the pad's axial shear and folds a correction into its own
    # descent:
    #     center = start.p - [0,0, segment_lead*angle/2pi + axial_correction]
    #     adjustment = clip(-shear*(4*dt/.15), -.00002, .00002)
    #     axial_correction = clip(axial_correction+adjustment, -.0015, .0015)
    # "Positive shear means the jaws lead the bulb downward" -- i.e. the pad is
    # being dragged because the bulb is not keeping up with the commanded
    # descent.  An open-loop helix therefore *builds* that shear until the gel
    # load fractures the bulb; this loop removes descent instead.  Measured on
    # the expert's own trace (metadata.json / axial_tactile_control): segments
    # end with corrections of -38, +89, -104 and -359 um, and the seating
    # segment's shear climbs monotonically +8.6 -> +78.5 um as travel runs out.
    SHEAR_GAIN = 1.5          # 0.444 per 4-tick record x ~3.4 records per 10 deg
    SHEAR_STEP_MAX = 60e-6    # the env's per-record clip (2e-5) x ~3 records
    SHEAR_MAX = 1.5e-3        # the env's axial_correction clip (:1999)

    def axial_calibrate(self, tag: str) -> dict:
        """One pure 150 um descent: the pad's axial basis + the shear reference.

        The axial half of probe_both_directions (:525-530).  The first frame is
        the reference every later shear reading is differenced against -- the
        env's ``_thread_shear_reference``, refreshed once per screw segment
        (:2230).
        """
        ee = self._get("/ee_pose")
        p = np.asarray(ee["position"], dtype=float)
        q = np.asarray(ee["quaternion_wxyz"], dtype=float)
        s0 = self._sample(f"shear_ref{tag}")
        self._move_abs(p + np.array([0.0, 0.0, -PROBE_DEPTH_M]), q,
                       f"spike_shear_basis{tag}")
        s1 = self._sample(f"shear_cur{tag}")
        self._move_abs(p, q, f"spike_shear_back{tag}")
        b_img, det = self._flow(s0, s1)
        b_ours = np.asarray(s1["ours"]) - np.asarray(s0["ours"])
        return {"p": p, "q": q, "ref": s0, "b_ours": b_ours, "b_img": b_img,
                "strength_ours": float(b_ours @ b_ours),
                "strength_img": None if b_img is None else float(b_img @ b_img),
                "flow_details": det}

    @staticmethod
    def axial_shear(cal: dict, cur: dict) -> dict:
        """The env's ``axial_image_displacement``: pad axial shear, in metres.

        Positive = the jaws lead the bulb downward = the bulb is not keeping up.
        """
        res: dict = {"ours": None, "image": None}
        # ours: the four absolute shears from /tactile, differenced against the
        # calibration frame (so the reset-time unloaded baseline cancels)
        b_ours, b_img = cal.get("b_ours"), cal.get("b_img")
        if b_ours is not None and cal.get("strength_ours"):
            r = np.asarray(cur["ours"]) - np.asarray(cal["ref"]["ours"])
            res["ours"] = PROBE_DEPTH_M * float(r @ b_ours) / cal["strength_ours"]
        if b_img is not None and cal.get("strength_img"):
            r, _ = ProbeSpike._flow(cal["ref"], cur)
            if r is not None:
                res["image"] = PROBE_DEPTH_M * float(r @ b_img) / cal["strength_img"]
        return res

    def screw_tactile(self, out: dict, step_deg: float = 10.0,
                      segment_deg: float = 180.0, pad_abort: float = 118.0,
                      gain: float | None = None,
                      seat_z: float | None = None,
                      max_segments: int = 8) -> None:
        """Screw to the seat the way the env does: helix + tactile shear feedback.

        Two things make this different from the open-loop helix that aborted
        after 30 deg with the pad at 120 N:

          * the descent is reduced whenever the pad's axial shear says the jaws
            are leading the bulb, so the gel load is bounded by the controller
            rather than by the fracture threshold;
          * `axial_correction` is reset per segment while `start` is re-read from
            the live pose -- so the accumulated correction is carried *in the
            pose*, and the per-segment shear-reference refresh starts from zero
            rather than from a stale baseline.
        """
        if gain is None:
            gain = self.SHEAR_GAIN
        spec = out["thread_spec"]
        lead = float(spec["lead_m"])
        out["screw"] = {"mode": "tactile_closed_loop", "lead_m": lead,
                        "step_deg": step_deg, "segment_deg": segment_deg,
                        "gain": gain, "segments": [], "ended": None}
        steps_per_segment = max(1, int(round(segment_deg / step_deg)))
        total_k = 0

        basis = None
        for seg in range(max_segments):
            # The env measures the axial basis ONCE (:580, in the pre-flight probe) and
            # refreshes only the shear *reference* per segment (:2230).  Re-measuring the
            # basis every segment injects the 150 um calibration descent as a real
            # displacement: a thread blocks pure axial motion, so the bulb does not
            # follow the jaws back up, and the reference is left ~150 um stale.  That
            # showed up in run tscrew1 as shear = -132.7 um on step 1 (the bulb "leading"
            # by exactly the calibration offset) driving corr the wrong way for ~3 steps.
            if basis is None:
                cal = dict(self.axial_calibrate("_s0"))
                basis = cal
            else:
                cal = dict(basis)
            ee = self._get("/ee_pose")
            cal["p"] = np.asarray(ee["position"], dtype=float)
            cal["q"] = np.asarray(ee["quaternion_wxyz"], dtype=float)
            cal["ref"] = self._sample(f"shear_ref_s{seg}")
            p0, q0 = cal["p"], cal["q"]
            t0 = self.truth()
            self.say(f"  seg {seg}: start bulb_z {t0['bulb_z_m']:.6f} pad {t0['pad_N']} "
                     f"yaw {t0['yaw_deg']:.1f} shear-basis "
                     f"ours={cal['strength_ours']:.2f} img="
                     f"{'None' if cal['strength_img'] is None else round(cal['strength_img'], 1)}")
            row = {"segment": seg, "start": t0, "corr_m": 0.0, "steps": []}
            out["screw"]["segments"].append(row)
            corr = 0.0
            stall = 0

            for k in range(1, steps_per_segment + 1):
                total_k += 1
                theta = math.radians(step_deg * k)
                p_t = np.array([p0[0], p0[1],
                                p0[2] - (lead * theta / (2 * math.pi) + corr)])
                q_t = _qmult(_axangle2quat([0.0, 0.0, 1.0], theta), q0)
                mv = self._move_abs(p_t, q_t, f"spike_tscrew_{seg}_{k:03d}")
                ee1 = self._get("/ee_pose")
                cur = self._sample(f"tscrew_{seg}_{k:03d}")
                sh = self.axial_shear(cal, cur)
                tr = self.truth()
                # EE tracking is the control: if the EE fails to reach its target the
                # bulb's shortfall is a motion/IK limit, not the thread blocking it.
                ee_z = float(np.asarray(ee1["position"], dtype=float)[2])
                ee_miss = float(p_t[2] - ee_z)  # >0 = EE fell short of the commanded z
                prev_bz = row["steps"][-1]["bulb_z_m"] if row["steps"] else t0["bulb_z_m"]
                expected = lead * math.radians(step_deg) / (2 * math.pi)
                drop = prev_bz - tr["bulb_z_m"]
                s = sh["ours"]
                row["steps"].append({
                    "k": total_k, "seg": seg, "theta_deg": step_deg * k, "ok": mv["success"],
                    "seconds": mv["seconds"], "bulb_z_m": tr["bulb_z_m"], "drop_m": drop,
                    "expected_drop_m": expected, "pad_N": tr["pad_N"], "yaw_deg": tr["yaw_deg"],
                    "ee_z_m": ee_z, "ee_miss_m": ee_miss, "ee_cmd_z_m": float(p_t[2]),
                    "torque_Nm": tr.get("torque_Nm"),
                    "corr_m": corr, "shear_ours_m": s, "shear_image_m": sh["image"],
                    "center_drift_m": tr["center_drift_m"], "tilt_deg": tr["tilt_deg"],
                    "failure": tr["failure"]})
                self.say(f"  seg{seg} {k:3d} {step_deg * k:6.1f}deg z {tr['bulb_z_m']:.6f} "
                         f"drop {drop * 1e6:+7.1f} (exp {expected * 1e6:+.1f}) "
                         f"miss {ee_miss * 1e6:+6.1f} pad {tr['pad_N']:.1f} "
                         f"shear {'None' if s is None else f'{s * 1e6:+7.2f}'}um "
                         f"corr {corr * 1e6:+7.1f}um fails={tr['failure']}")
                if tr["failure"]:
                    out["screw"]["ended"] = f"failure:{tr['failure']}"
                    self.say(f"  !! {tr['failure']} — bulb destroyed, stopping")
                    return
                if tr["pad_N"] and tr["pad_N"] > pad_abort:
                    out["screw"]["ended"] = f"pad_abort:{tr['pad_N']}"
                    self.say(f"  !! pad {tr['pad_N']} > {pad_abort} N — aborting before fracture")
                    return
                if tr["center_drift_m"] and tr["center_drift_m"] > 0.004:
                    out["screw"]["ended"] = f"drift:{tr['center_drift_m']}"
                    self.say("  !! centre drift > 4 mm — alignment lost, stopping")
                    return
                if tr["tilt_deg"] and tr["tilt_deg"] > 10.0:
                    out["screw"]["ended"] = f"tilt:{tr['tilt_deg']}"
                    self.say("  !! tilt > 10 deg — alignment lost, stopping")
                    return
                if s is not None:
                    adj = float(np.clip(-s * gain, -self.SHEAR_STEP_MAX, self.SHEAR_STEP_MAX))
                    corr = float(np.clip(corr + adj, -self.SHEAR_MAX, self.SHEAR_MAX))
                    row["corr_m"] = corr
                # The seat is a *position*, so test it as one.  An earlier version
                # inferred "seated" from the bulb merely failing to descend; in run
                # tscrew1 that fired at 40 deg with only 358 of 854 um advanced.  It
                # cannot tell "the thread blocks the bulb" from "I am not driving the
                # bulb", and separating those two is the entire point of this run.
                if seat_z is not None and tr["bulb_z_m"] is not None and tr["bulb_z_m"] <= seat_z:
                    out["screw"]["ended"] = f"seat_z:{tr['bulb_z_m']:.6f}"
                    self.say(f"  bulb_z {tr['bulb_z_m']:.6f} <= seat {seat_z:.6f} — SEATED")
                    break
                if drop < 0.30 * expected:
                    stall += 1
                    if stall >= 8:
                        out["screw"]["ended"] = "stall_not_driven"
                        self.say("  !! bulb not advancing over 8 steps and never reached "
                                 "the seat — reporting stall, NOT a seat")
                        return
                else:
                    stall = 0
            if out["screw"]["ended"]:
                break
        else:
            out["screw"]["ended"] = "max_segments"

        self.say(f"  screw ended: {out['screw']['ended']}")
        self.say("  --- probe at seat ---")
        out["seat_probe"] = self.run_probe({})

    def lift_control(self, out: dict, height: float = 0.030) -> None:
        """Probe again with the bulb lifted clear of the thread.

        The same two rotations, but with nothing to screw into, so the bulb
        cannot advance relative to the gripper and the differential axial
        response should collapse.  This is the cheap end of the judge's dynamic
        range: it costs one lift, and unlike the seated case it cannot fracture
        the bulb (no thread or seat load at all).  It is necessary, not
        sufficient — a seated bulb is still in thread contact — so it only
        shows that the signal tracks *advance*, not that it vanishes at the
        seat.
        """
        ee = self._get("/ee_pose")
        p = np.asarray(ee["position"], dtype=float)
        q = np.asarray(ee["quaternion_wxyz"], dtype=float)
        mv = self._move_abs(p + np.array([0.0, 0.0, height]), q, "spike_probe_lift")
        self.say(f"  lift clear {height * 1000:.0f} mm: {mv}")
        r = self.run_probe({"lifted": True, "height_m": height})
        out["lift_control"] = r
        for name in ("ours", "image"):
            if f"separation_{name}_m" in r:
                self.say(f"  lifted separation[{name}] = "
                         f"{r[f'separation_{name}_m']:.4e} m")
        mv = self._move_abs(p, q, "spike_probe_unlift")
        self.say(f"  lower back: {mv}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:8120")
    ap.add_argument("--out-dir", default="/mnt/nas/shimufan/cap-x/outputs/spike_bulb_probe")
    ap.add_argument("--tag", default="")
    ap.add_argument("--repeat", type=int, default=1,
                    help="run the probe this many times back to back")
    ap.add_argument("--lift-control", action="store_true",
                    help="after the repeats, probe again with the bulb lifted clear")
    ap.add_argument("--screw", action="store_true",
                    help="screw in along an absolute helix, probing on the way")
    ap.add_argument("--tactile-screw", action="store_true",
                    help="screw with the env's own controller: helix + tactile "
                         "axial-shear feedback (_screw_segment, :1968-2010).  "
                         "Supersedes --screw, which is the open-loop helix the "
                         "expert does NOT use.")
    ap.add_argument("--segment-deg", type=float, default=180.0,
                    help="rotation per tactile-feedback segment; the env uses "
                         "pi rad (180 deg) and re-reads start + the shear "
                         "reference at each boundary (:2230)")
    ap.add_argument("--shear-gain", type=float, default=1.5,
                    help="shear -> descent-correction gain per step")
    ap.add_argument("--step-deg", type=float, default=10.0,
                    help="rotation per command.  The env re-commands IK every 2 "
                         "ticks, 0.75deg/tick = 1.5deg per target, so large steps "
                         "sample the helix coarsely: a 10deg step departs from the "
                         "true helix by ~r*(1-cos5deg) ~= 76um mid-step if the motion "
                         "layer interpolates position linearly (r ~= 20mm), which is "
                         "comparable to the 213.6um step itself and forces the thread")
    ap.add_argument("--probe-every", type=int, default=4)
    ap.add_argument("--pad-abort", type=float, default=118.0)
    ap.add_argument("--seat-z", type=float, default=None,
                    help="bulb_z at or below which the bulb is at the seat (the "
                         "expert's seed-0 success reached 0.0245067).  None disables "
                         "the seat test, leaving only a stall verdict")
    ap.add_argument("--settle", type=float, default=0.0,
                    help="seconds to let the gel relax after each move before "
                         "reading tactile (the env waits delay(8) here)")
    ap.add_argument("--rot-deg", type=float, default=4.0,
                    help="rotation-probe half-angle in degrees (the env uses 4.0; "
                         "the axial slip is lead*deg/360, so this is the SNR lever)")
    ap.add_argument("--settle-cycle", default="",
                    help="comma list of settle values, cycled across --repeat "
                         "probe runs, to A/B the settle effect within one session")
    ap.add_argument("--no-grasp", action="store_true",
                    help="the bulb is already in the gripper; skip the grasp")
    ap.add_argument("--regrasp", action="store_true",
                    help="open, back off, and grasp again — unloads the gel so "
                         "the markers relax before the probe")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    stamp = args.tag or time.strftime("%Y%m%d-%H%M%S")
    spike = ProbeSpike(args.base_url, out_dir / stamp)
    spike.settle = args.settle
    spike.probe_angle = math.radians(args.rot_deg)
    out: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "settle_s": args.settle}

    out["params"] = (spike.force_state().get("params") or {})
    out["thread_spec"] = (spike.force_state().get("thread_spec") or {})
    spike.say(f"thread_spec {out['thread_spec']}")
    out["gripper_before"] = spike._get("/gripper_state")
    spike.say(f"gripper {out['gripper_before']}")
    if args.no_grasp:
        spike.say("skipping grasp (--no-grasp)")
        out["grasped"] = None
    else:
        if args.regrasp:
            spike.say("--- re-grasp: open, back off, grasp ---")
            out["regrasp"] = spike.regrasp()
        out["grasped"] = bool(spike._grasp(out))
        if not out["grasped"]:
            spike.say("grasp failed — stopping")
            spike.result = out
            spike.finish()
            return

    spike.result = out          # what finish() persists
    out["runs"] = []
    # --settle-cycle interleaves settle values inside one session.  The gel's
    # unloaded baseline drifts between sessions (and the markers creep), so a
    # settle=0 vs settle=3 comparison drawn from two different sessions would
    # confound the effect with that drift.  Cycling keeps both arms on the same
    # session; --settle stays the value used for --screw probes.
    cycle = [float(x) for x in args.settle_cycle.split(",")] if args.settle_cycle else []
    for i in range(args.repeat):
        if cycle:
            spike.settle = cycle[i % len(cycle)]
            out.setdefault("settle_cycle", cycle)
        spike.say(f"--- probe run {i + 1}/{args.repeat} (settle={spike.settle}s) ---")
        r = spike.run_probe({"settle_s": spike.settle, "run_index": i})
        out["runs"].append(r)
        for name in ("ours", "image"):
            if f"separation_{name}_m" in r:
                spike.say(f"  separation[{name}] = {r[f'separation_{name}_m']:.4e} m")
        spike.report_per_finger(r)
    if args.screw:
        spike.say("--- screw to seat (open-loop absolute helix) ---")
        spike.screw_to_seat(out, step_deg=args.step_deg,
                            probe_every=args.probe_every, pad_abort=args.pad_abort)
    if args.tactile_screw:
        spike.say("--- screw to seat (helix + tactile axial-shear feedback) ---")
        spike.screw_tactile(out, step_deg=args.step_deg,
                            segment_deg=args.segment_deg, pad_abort=args.pad_abort,
                            gain=args.shear_gain, seat_z=args.seat_z)
    if args.lift_control:
        spike.say("--- lift-clear control ---")
        spike.lift_control(out)
    spike.after(out)

    path = spike.finish()
    rows: list = [("free", x) for x in out["runs"]]
    if args.screw or args.tactile_screw:
        # the mid-screw probes exist only for the open-loop run; the closed loop
        # samples every step instead, so it has segments rather than probes
        for r in (out["screw"].get("probes") or []):
            if r.get("separation_ours_m"):
                rows.append((f"screw{r['theta_deg']:.0f}d", r))
        # both screw paths return early on failure/pad-abort, before they can run
        # the seat probe, so this key is legitimately absent then.
        # label the final probe by how the screw actually ended, so a stall is never
        # read as a seat: "seat_z" is the only verdict that means the bulb is seated
        ended = out["screw"].get("ended")
        if out.get("seat_probe"):
            rows += [(f"FINAL[{ended}]", out["seat_probe"])]
        elif ended:
            print(f"  (no final probe — screw ended: {ended})")
    if args.lift_control:
        rows.append(("lifted", out["lift_control"]))
    print("\n=== separation (um): pooled vs whole-pad-per-finger ===")
    print(f"  {'state':10s} {'env-20d':>9s} {'pad-min':>9s} {'pad-med':>9s}"
          f" {'ours-min':>9s} {'spread':>7s}  bulb_z")
    for label, r in rows:
        n = lambda k: r.get(k, float("nan")) * 1e6        # noqa: E731
        pad = (r.get("per_finger") or {}).get("image_pad") or {}
        print(f"  {label:10s} {n('separation_image_m'):9.1f}"
              f" {n('separation_image_pad_min_m'):9.1f}"
              f" {n('separation_image_pad_median_m'):9.1f}"
              f" {n('separation_ours_min_m'):9.1f}"
              f" {(pad.get('spread') or float('nan')):7.2f}"
              f"  {r.get('truth_before', {}).get('bulb_z_m')}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
