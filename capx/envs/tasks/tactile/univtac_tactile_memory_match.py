from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Task: tactile memory matching.  Three identical orange cans (lying flat on 
the table) sit in a line: one REFERENCE can at the back, two CANDIDATE cans 
in front of it (candidate_left on the left, candidate_right on the right).  
A green MATCH SLOT lies beside the row. Public pose names (get_object_pose; 
returns position, quaternion_wxyz, bbox_extent): reference_object, 
candidate_left, candidate_right and match_slot.

The cans look exactly the same but differ physically in three independent
ways: 2 surface textures x 2 weights x 2 hardnesses (one rigid, one
compliant).  One candidate is the reference's "twin" (identical in all
three); the other differs in at least one of them.  Your job:

  1. Measure all three properties of the reference from one progressive
     position-commanded grasp, following the task-specific API protocol.
  2. Probe both candidates with the exact same one-grasp protocol.
  3. Place on the MATCH SLOT the candidate whose feel is closest to the
     reference.  Vision cannot tell them apart — decide ONLY from tactile
     data.

Key rules:
- Every call can fail, time out, or be rejected.  Wrap each in try/except and
  treat a failure as "this attempt missed", never as the end of your program:
  a crashed trial scores 0 exactly like a wrong answer.
- Budget the episode, not the attempt: the whole run has to fit in roughly
  250 API calls, at most 3 grips per can plus one re-grasp.  Every call costs
  about the same ~10 s whether it moves 2 mm or 50 mm, so the count is what
  costs, not the distance.  Once a can's budget is spent, decide with the
  readings you have — two cans fully read beats three cans half-read.
- Travel between grasp sites at z ~0.28, and descend only over the
  destination: an open gripper swept near the table knocks cans away.  Grasp
  by the side (cans lie flat), and read the sensors only when still and at
  the same grip force.
- A reading is INVALID unless the loose state verifies the 34 mm no-contact
  far plane and the following firm state makes real contact.  Low follow on
  the protocol's small lift is a property reading, not a failed hold; a rigid
  can may stay on the table despite firm gel contact.  Do not add symmetry
  gates for displacement or shear.  On a missed approach, retreat to cruise
  height, re-query the can pose, and make one new approach; never retry by
  sweeping an open gripper near the table.
- Obtain valid protocol readings for the reference and both candidates before
  deciding.  Readability is not evidence of identity: never choose the only
  candidate that happened to read valid, and never pick a fallback side merely
  to make the script complete.
- Release only a can that already rests flat on the surface, and never end
  the episode while one is still falling.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

ORACLE_CODE = """
import numpy as np

# ---- fixed layout priors (task design, same kind of prior as RELEASE_Z) ---
#   reference ~[0.68, 0.24];  candidate_left [0.60, -0.345];
#   candidate_right [0.60, -0.175];  match pad [0.42, 0.00] (top z 0.011)
# Can: 4 cm diameter, 8 cm long, LYING flat (long axis along x), origin at
# the centre.  Table rest: centre z = 0.021; top surface z = 0.041.
PAD_XY = np.array([0.42, 0.00])
CAN_REST_Z = 0.021        # centre z on the table
RELEASE_Z = 0.039         # centre z just above resting (pad 0.031 / table 0.021)
CRUISE_Z = 0.28           # inter-can travel height: above cans + open fingers
FAR_DEPTH_MM = 34.0       # tactile no-contact far plane
REF = "reference_object"
CANDIDATES = ["candidate_left", "candidate_right"]

# ---- the ONE grasp, and the two commanded states it reads -----------------
# Every can is grasped exactly once.  All three properties come out of that
# single closure:
#   state LOOSE -> state FIRM   the gel's response to a tighter POSITION
#                               command  -> hardness
#   the lift after it           the can's own z under load -> weight
#   the firm read               marker channels (texture; recorded, not scored)
# Both states are POSITION commands, which is what lets them share one grasp:
# position control is kinematic (the jaws go where they are told) and
# adaptive=False is a hard requirement, not a preference -- with the adaptive
# close on, the servo keeps pushing until the jaws bottom out, so a compliant
# can reads as a firm jam and the two hardnesses come out INVERTED.  The two
# commands descend, so the pair is one progressive squeeze sampled twice.
HARD_LOOSE_POS = 0.50     # fingers clear of the can: the no-contact sentinel
HARD_FIRM_POS = 0.25      # ~19.5 mm apart: a jam on a rigid can, yielding on
                          # a compliant one
LIFT_Z = 0.020            # the lift the weight axes are read from
JITTER_Z = 0.002          # smallest executable move; the pair cancels out

# Measured class constants (8 seeds x 3 cans; docs findings 21-24, 36).
HARD_SENTINEL_TOL_MM = 0.05   # how far the loose read may sit off the far plane
HARD_CONTACT_MM = 33.0        # a firm close reading shallower than this never
                              # touched a 34 mm can: the can was not there
HARD_SPREAD_MM = 0.13         # same-class spread of hard_mm
HARD_DEADBAND_MM = 0.50       # ~4x that spread; below it the axis votes "same"
DIM_CAP = 3.0                 # the most one dimension may cost a candidate

# Texture axis (docs finding 46, measured on the three seeds that followed the
# 2026-09-19 sync).  d_aniso is the change in the gel's marker anisotropy across
# THIS grasp's two commanded states, firm minus loose.  The loose state is the
# sentinel-gated no-contact state, so it is an unloaded baseline on the same
# pad in the same grasp -- the difference-of-two-command-states shape hard_mm
# uses, at no extra op.
#
# Measured: on RIGID cans a real texture contrast moves d_aniso by 0.0307
# (light) and 0.0263 (heavy), against a within-class spread of 0.0028 and
# 0.0022 -- 11x and 12x.  On COMPLIANT cans the same contrast moves it 0.0085,
# under the deadband: the soft can yields instead of imprinting the ridges, so
# the axis reads "same" there and the four soft texture-only pairs stay coin
# flips.  That is a measured boundary, not an omission.
#
# The deadband is 0.02 because that is the threshold finding 29 pre-registered
# for this channel before any of this data existed; it is not re-tuned here.
TEX_DEADBAND = 0.02
TEX_SPREAD = 0.0028           # the WIDER of the two measured same-class spreads
                              # (0.0028, 0.0022): the scale is deliberately not
                              # tightened toward the narrow one

# Where a can stops counting as "in the fingers".  `follow` is the
# measurement's OWN lift test -- the fraction of LIFT_Z the can rode -- and it
# splits by HARDNESS, not weight: the 0.25 close jams on a rigid can hard
# enough to read a firm gel contact but never actually grips it, so the can
# stays on the table (2026-09-18 queue, 6 trials x 3 cans: rigid 0.029 / 0.097
# / 0.141 / 0.161 / 0.314 / 0.330, compliant 0.942-1.052 -- cleanly split).
# This is what keeps the keep=True shortcut honest: a hold the lift says never
# happened is not a hold, however firm the gel reading was.
#
# 2026-09-19: the n=6 window quoted above was too narrow.  Over the full 11-trial
# queue the rigid class reaches higher -- 21 rigid readings span 0.000-0.612
# (0.612 is seed 44's candidate_right), against 12 compliant readings spanning
# 0.942-1.052.  So the true gap is (0.612, 0.942) and 0.60 sat 0.012 BELOW the
# highest rigid reading: a rigid can could still pass the gate, and seeds 0/1
# were exactly that failure.  Geometric mean of the window is 0.76.
KEEP_HOLD_MIN = 0.76


def _op(fn, *a, **k):
    \"\"\"Run one API call; any failure/timeout is returned as None instead of
    raising -- a jammed op becomes a miss to retry, never a crash.\"\"\"
    try:
        return fn(*a, **k)
    except Exception:
        return None


def read_tactile():
    \"\"\"One tactile read's per-finger scalars (the base64 images ignored).\"\"\"
    return get_tactile_data()


def side_mean(data, key):
    \"\"\"Mean of one per-finger key over the two pads (0.0 when unreadable).\"\"\"
    data = data or {}
    vals = [data.get(f"{s}_{key}") for s in ("left", "right")]
    vals = [v for v in vals if v is not None]
    return float(np.mean(vals)) if vals else 0.0


def side_mean_opt(data, key):
    \"\"\"Mean of one per-finger key over the two pads, or None when unreadable.

    Unlike side_mean this does NOT substitute 0.0.  The anisotropy channel is
    a gradient ratio centred on 1.0, so a fabricated 0.0 would read as a real
    -- and wildly anisotropic -- measurement instead of a missing one.
    \"\"\"
    data = data or {}
    vals = [data.get(f"{s}_{key}") for s in ("left", "right")]
    vals = [float(v) for v in vals if v is not None]
    return float(np.mean(vals)) if vals else None


def _fmt(v, nd=4):
    \"\"\"Format an optional scalar for the [measure] line.\"\"\"
    return "None" if v is None else f"{v:.{nd}f}"


def _delta_aniso(firm_data, loose_data):
    \"\"\"The loaded-minus-unloaded marker anisotropy for ONE grasp, or None
    when either state could not be read.

    None rather than a number on purpose: a fabricated 0.0 would look like a
    real reading far from the 1.0 isotropy point and would be scored as one.
    \"\"\"
    f = side_mean_opt(firm_data, "marker_anisotropy")
    l = side_mean_opt(loose_data, "marker_anisotropy")
    return None if (f is None or l is None) else float(f - l)


def _dev(cand, ref, key, scale):
    \"\"\"One axis' UNBANDED deviation between a candidate and the reference, or
    None when either side is missing.

    This is the diagnostic print only -- dimension_scores() is what the verdict
    uses, and it subtracts the deadband.  Both go through the same None check so
    a skipped axis is visible as a skipped axis rather than a fabricated number.
    \"\"\"
    cv, rv = cand.get(key), ref.get(key)
    if cv is None or rv is None:
        return None
    return abs(float(cv) - float(rv)) / scale


def min_depth_mm(data):
    \"\"\"The DEEPER pad's gel depth (mm; far plane 34, less = deeper
    contact).  Not the mean: the pads sit a roughly constant ~0.3 mm apart and
    the deeper one is the pad actually carrying the squeeze.  The 10.007 mm
    split was calibrated in these units, so the phase must read the same
    quantity the ladder measured.\"\"\"
    if not data:
        return FAR_DEPTH_MM
    vals = [float(data[f"{s}_depth_mm"]) for s in ("left", "right")
            if data.get(f"{s}_depth_mm") is not None]
    return min(vals) if vals else FAR_DEPTH_MM


def shear_px(data):
    \"\"\"Coherent marker shear magnitude at the firm grip (recorded only).\"\"\"
    vals = []
    for s in ("left", "right"):
        dx = data.get(f"{s}_marker_shear_dx")
        dy = data.get(f"{s}_marker_shear_dy")
        if dx is not None and dy is not None:
            vals.append(float(np.hypot(float(dx), float(dy))))
    return float(np.mean(vals)) if vals else 0.0


def retreat():
    \"\"\"Open clear of the can and back off.\"\"\"
    _op(open_gripper)
    _op(move_delta, dz=0.05)


def descend_to(can_name, target_z, step=None):
    \"\"\"Walk the HELD can down to target_z, watching the can's own height:
    the moment its height stops decreasing it is supported, so never press
    further.  Reading the pose is free and a 2 mm op costs the same as a
    50 mm one on this server, so with step=None each pass moves the whole
    remaining drop.  step is given in mm-sized increments only for the
    placement onto the pad, whose gentle ~5 mm stepping is the release
    protocol that many trials were tuned against.\"\"\"
    for _ in range(6 if step is None else 60):
        z = float(get_object_pose(can_name)[0][2])
        if z <= target_z + 0.003:
            break
        drop = z - target_z
        move_delta(dz=-min(drop, 0.10) if step is None else -min(drop, step))


def release(can_name, target_z=CAN_REST_Z, step=None):
    \"\"\"Set the held can down and let go.  Unload BEFORE opening: the fingers
    are still pressing the can into the surface, and opening under load tips
    and wedges it.  Backing off one 5 mm step takes the fingers' weight off
    (the can is already supported) and leaves it resting when they open.\"\"\"
    descend_to(can_name, target_z, step)
    move_delta(dz=0.005)
    _op(open_gripper)
    _op(move_delta, dz=0.08)


def measure(can_name, offset, quat, keep=False):
    \"\"\"Grasp this can ONCE and return every reading the match uses.

    One approach, two commanded position states and one lift.  Then the can
    goes back on the table -- or stays in the fingers when keep=True, so the
    caller can place it without grasping it a second time.  Returns the
    feature dict, or None when the can was not measurable: an invalid reading
    must never decide the match.\"\"\"
    pos, _, _ = get_object_pose(can_name)
    grasp_pos = np.asarray(pos[:3], dtype=float) + offset
    # Hover high first: the planner does not avoid the cans, and an open
    # gripper swept at table height scatters them.
    if _op(goto_pose, np.array([grasp_pos[0], grasp_pos[1], CRUISE_Z]),
           quat, z_approach=0.0) is None:
        return None
    if _op(goto_pose, grasp_pos, quat, z_approach=0.06) is None:
        return None
    try:
        # ---- state 1: fingers clear of the can.  The no-contact sentinel is
        # the gate for the WHOLE measurement: it says the fingers arrived from
        # outside the can, which is what makes state 2's depth mean anything.
        if _op(close_gripper, position=HARD_LOOSE_POS, adaptive=False) is None:
            retreat()
            return None
        loose_read = read_tactile()
        loose = min_depth_mm(loose_read)
        if abs(loose - FAR_DEPTH_MM) > HARD_SENTINEL_TOL_MM:
            print(f"[measure {can_name}] loose point read {loose:.2f} mm, not "
                  f"the {FAR_DEPTH_MM:.1f} mm no-contact sentinel -- fingers "
                  f"were not clear of the can, measurement void")
            retreat()
            return None
        # ---- state 2: the SAME grasp, commanded tighter.  No open in
        # between: the fingers travel monotonically inward.
        if _op(close_gripper, position=HARD_FIRM_POS, adaptive=False) is None:
            retreat()
            return None
        firm_read = read_tactile()
        firm = min_depth_mm(firm_read)
        # Mirror-image gate: a close to a ~19.5 mm gap that reads NO contact is
        # self-contradictory -- a 34 mm can cannot fit between the fingers --
        # so the can was not there to be squeezed.  Without this the phase
        # would report loose - firm = 0.0 and read SOFT: a silent wrong answer
        # rather than a missing one.
        if firm > HARD_CONTACT_MM:
            print(f"[measure {can_name}] firm point read {firm:.2f} mm, no "
                  f"contact at a {HARD_FIRM_POS} close -- the can was not "
                  f"between the fingers, measurement void")
            retreat()
            return None
        # ---- the lift: the can's OWN z, still in the same grasp.  follow is
        # how far it rode the commanded lift; sag is its stick-slip spread
        # while suspended.  A light can is held by friction and barely moves;
        # a heavy one creeps under its own weight (measured on firm sessions:
        # light 0.5-1.3 mm vs heavy 2.5-4.3 mm).
        z0 = float(get_object_pose(can_name)[0][2])
        move_delta(dz=LIFT_Z)
        z1 = float(get_object_pose(can_name)[0][2])
        zs = []
        for _ in range(3):
            move_delta(dz=JITTER_Z)
            move_delta(dz=-JITTER_Z)
            zs.append(float(get_object_pose(can_name)[0][2]))
        feats = {
            "hard_mm": loose - firm,
            "follow": min(max((z1 - z0) / LIFT_Z, 0.0), 1.5),
            "sag_mm": (max(zs) - min(zs)) * 1000.0,
            # recorded, NOT scored.  The client derives the anisotropy from
            # the marker images the /tactile response already carries, so both
            # readings below ride along on ops this grasp was already making:
            # no extra close, no second grasp.  aniso_loose is the sentinel-
            # gated NO-CONTACT state -- an unloaded gel -- so the pair is a
            # loaded-vs-unloaded change on one grasp, the same difference-of-
            # two-command-states shape as hard_mm.
            #
            # The old note here said the channel was flat because "texture" was
            # one mesh with a different friction scalar.  That premise died in
            # the 2026-09-19 sync, which shipped Can_d4cm_axial_ridges.usda: a
            # friction scalar has no direction, but real axial ridges do, so
            # whether this channel can see them is an OPEN measurement now.
            "shear_px": shear_px(firm_read),
            "coherence": side_mean(firm_read, "marker_coherence"),
            "aniso": side_mean_opt(firm_read, "marker_anisotropy"),
            "aniso_loose": side_mean_opt(loose_read, "marker_anisotropy"),
            # The scored form.  None (not 0.0) when either state was
            # unreadable, so the axis is SKIPPED rather than voting on a
            # fabricated reading.
            "d_aniso": _delta_aniso(firm_read, loose_read),
            "depth_loose_mm": loose,
            "depth_firm_mm": firm,
        }
        if not keep:
            release(can_name)
        return feats
    except Exception:
        # Any hiccup mid-measure -> discard this reading, never crash.
        try:
            retreat()
        except Exception:
            pass
        return None


# One entry per PHYSICAL dimension, each with its axes, the scale that axis'
# same-can spread has been measured in (so a deviation of 1.0 is "one
# class-spread apart", whichever axis it is), and the deadband below which a
# deviation counts as no difference at all.
#
# All three dimensions now have an axis.  Texture got one only after the
# 2026-09-19 sync put real axial ridges in the world; until then the two
# "textures" were one mesh with a different friction scalar and the channel was
# measurably flat (docs finding 38: contrast 0.000 over a 0.004 range).
DIMENSION_AXES = [
    ("weight", [("follow", 0.25, 0.0), ("sag_mm", 1.2, 0.0)]),
    ("hardness", [("hard_mm", HARD_SPREAD_MM, HARD_DEADBAND_MM)]),
    ("texture", [("d_aniso", TEX_SPREAD, TEX_DEADBAND)]),
]
ALL_AXES = [a for _dim, axes in DIMENSION_AXES for a in axes]


def dimension_scores(ref, cand):
    \"\"\"Per-dimension deviation between two measurements: each axis'
    deviation is measured from its deadband, divided by its own scale, then
    reduced by the MEDIAN within the dimension, so no single axis of a
    dimension can run away with it.  An axis either side reports as None is
    skipped; a dimension with no usable axis left is absent, not zero.\"\"\"
    out = {}
    for dim, axes in DIMENSION_AXES:
        devs = []
        for key, scale, band in axes:
            rv, cv = ref.get(key), cand.get(key)
            if rv is None or cv is None:
                continue
            devs.append(max(abs(float(cv) - float(rv)) - band, 0.0) / scale)
        if devs:
            out[dim] = float(np.median(devs))
    return out


def signature_distance(ref, cand):
    \"\"\"Sum over dimensions of each dimension's CAPPED deviation; the
    candidate with the smaller score is the twin.

    The env's match is defined structurally -- the twin is identical in ALL
    dimensions, the distractor differs in AT LEAST ONE -- so a sum over
    dimensions is the rule that fits, and a dimension both candidates are
    equally far on contributes the same to both and decides nothing.  The cap
    is what keeps a merely NOISY dimension (far on both candidates because the
    reference-vs-twin offset is wide, not because either really differs) from
    drowning out the dimension that carries the answer.  Capping alone is not
    enough to make an axis safe: a same-class reading under the cap still adds
    to the total.  What makes the hardness axis safe is its deadband -- a
    same-class pair then contributes exactly 0 and a different-class pair
    exactly the cap, so the axis is a same/different vote that can only help.\"\"\"
    return float(sum(min(v, DIM_CAP)
                     for v in dimension_scores(ref, cand).values()))


def grasp_offset_from_reference():
    \"\"\"EE-to-can-centre offset, measured on the reference can (the one
    sample_grasp_pose knows).  Candidates are identical lying cans, so the
    same offset with their own live pose grasps them correctly.\"\"\"
    grasp, quat = sample_grasp_pose(REF)
    pos, _, _ = get_object_pose(REF)
    return np.asarray(grasp, dtype=float) - np.asarray(pos[:3], dtype=float), quat


def carry_to_pad(can_name, offset, quat):
    \"\"\"Transport the held can over the match pad and release it there.\"\"\"
    hover = np.array([PAD_XY[0] + offset[0], PAD_XY[1] + offset[1],
                      RELEASE_Z + offset[2] + 0.08])
    if _op(goto_pose, hover, quat, z_approach=0.05) is None:
        return
    release(can_name, target_z=RELEASE_Z, step=0.005)


def grasp_for_transport(can_name, offset, quat):
    \"\"\"A plain close for a can that only has to be moved, not measured.
    An empty grab means the fingers caught an end cap, so shift along the can
    axis and retry.\"\"\"
    pos, _, _ = get_object_pose(can_name)
    base = np.asarray(pos[:3], dtype=float) + offset
    for dx in (0.0, 0.015, -0.015, 0.03, -0.03):
        p = base + np.array([dx, 0.0, 0.0])
        if _op(goto_pose, np.array([p[0], p[1], CRUISE_Z]), quat,
               z_approach=0.0) is None:
            continue
        if _op(goto_pose, p, quat, z_approach=0.06) is None:
            continue
        fb = _op(close_gripper)
        if (fb is not None and fb.get("left_contact")
                and fb.get("right_contact")):
            return True
        retreat()
    return False


def report(can_name, feats):
    \"\"\"One line per can, in the shape the analysis scripts read.\"\"\"
    if feats is None:
        print(f"[measure {can_name}] ok=False — no valid reading, score "
              f"excluded")
        return
    print(f"[measure {can_name}] ok=True hard_mm={feats['hard_mm']:.3f} "
          f"follow={feats['follow']:.3f} sag_mm={feats['sag_mm']:.3f} "
          f"loose={feats['depth_loose_mm']:.2f} firm={feats['depth_firm_mm']:.2f} "
          f"shear_px={feats['shear_px']:.3f} "
          f"coh={feats['coherence']:.3f} "
          f"aniso={_fmt(feats.get('aniso'))} "
          f"aniso_loose={_fmt(feats.get('aniso_loose'))}")


# ---- one grasp per can ----------------------------------------------------
offset, quat = grasp_offset_from_reference()
print(f"grasp offset = {np.round(offset, 3)}")

ref_feats = measure(REF, offset, quat)
report(REF, ref_feats)
if ref_feats is None:
    print("reference measurement void — cannot proceed")
    raise SystemExit(1)

feats = {}
# The FIRST candidate is measured and put back; the SECOND is measured with
# keep=True and stays in the fingers, so a decision that falls on it needs no
# second grasp.  Every can is still grasped exactly once to be measured.
feats[CANDIDATES[0]] = measure(CANDIDATES[0], offset, quat)
report(CANDIDATES[0], feats[CANDIDATES[0]])
feats[CANDIDATES[1]] = measure(CANDIDATES[1], offset, quat, keep=True)
report(CANDIDATES[1], feats[CANDIDATES[1]])

# Invalid (None) measurements must never decide the match: exclude them from
# selection with an infinite distance.
scores = {
    name: (signature_distance(ref_feats, feats[name])
           if feats[name] is not None else float("inf"))
    for name in CANDIDATES
}
for name in CANDIDATES:
    print(f"[score] {name}: {scores[name]:.4f}")
    if feats[name] is not None:
        print(f"[score-axes] {name}: " + " ".join(
            f"{key}={_fmt(_dev(feats[name], ref_feats, key, scale), 3)}"
            for key, scale, _band in ALL_AXES))
        print(f"[score-dims] {name}: " + " ".join(
            f"{dim}={v:.3f}" for dim, v in
            dimension_scores(ref_feats, feats[name]).items()))

picked = min(scores, key=scores.get)
margin = sorted(scores.values())[1] - scores[picked]
print(f"picked={picked} margin={margin:.4f}")

# ---- place the closest-feeling candidate on the match pad -----------------
LAST = CANDIDATES[1]
# Keep it only if the measurement's own lift test says it is really held.
# Assuming the keep instead sends an EMPTY gripper to the pad on a rigid can:
# every reading correct, the pick correct, and the trial fails anyway (seed 0,
# 2026-09-18).  The PROMPT already states this rule for the LLM -- a grip the
# can slides out of on the lift is not a real hold -- and the code was the one
# place not obeying it.
kept = feats[LAST] is not None and feats[LAST]["follow"] >= KEEP_HOLD_MIN
held = LAST if kept else None
if held is not None and picked != held:
    release(held)          # the pick was measured first: put this one back
    held = None
if held is None:
    if not kept:
        # Declining the keep leaves the jaws shut at the measuring gap (or
        # measure() already retreated) -- either way clear them first: the
        # transport grasp descends onto the can from above, and a shut gripper
        # would knock over the very can it came to move.
        open_gripper()
        move_delta(dz=0.08)
    ok = grasp_for_transport(picked, offset, quat)
    print(f"final grasp {picked} ok={ok}")
    held = picked if ok else None
if held is not None:
    move_delta(dz=0.10)    # clear of the table before travelling
    carry_to_pad(picked, offset, quat)

# The env only marks a placement after ~10 stable steps of the can resting on
# the pad -- keep the sim stepping with tiny moves until it is settled.
for _ in range(40):
    if check_success():
        break
    try:
        # 2 mm is the smallest executable move.  The pair cancels out, so this
        # only advances the sim.
        move_delta(dz=-0.002)
        move_delta(dz=0.002)
    except Exception:
        break

print(f"Success: {check_success()}")
"""


class UniVTACTactileMemoryMatchCodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE
    # Optional API-doc sections this task's LLM needs; the rest of the
    # shared UniVTACTactileApi doc is pruned.  See DOC_SECTION_TAGS.
    api_doc_sections = ("position", "hardness", "texture", "memory_match")
