from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: an open-topped box stands in front of the robot with one object
somewhere on its floor. The walls are opaque and the object sits below the rim,
so no camera or API reading tells you where it is: the search is by touch, and
the pads are what end it.

Getting the object out is the job, not touching it. Clear of the box means the
object's own body is out: its lowest point well above the rim, not just the
pads.

The box is fixed; enter over its rim. Its inside measures 0.296 m along x by
0.356 m along y, centred on x = 0.520, y = 0.000; the rim top is at z = 0.197 m
and the floor inside at z = 0.016 m. The object is called "hidden_object"; it
is a 0.05 m by 0.05 m block, 0.06 m tall, resting on that floor. Its centre is
always within 0.028 m of x = 0.520 and 0.052 m of y = 0.000.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

ORACLE_CODE = """
import math
import sys
import numpy as np

OBJECT = "hidden_object"

# =============================================================================
# blind_pick v13 -- find the lane, centre x, centre y by the side first-touch
# flip, then close only on a face pair.
#
# v10 tried four hand yaws 22.5 deg apart, closing on the first that looked
# right.  Seed 9 (2026-10-18) killed it, and the failure is informative:
#
#   * the first candidate fired BOTH pads at the same rung (28.04 / 28.50 --
#     indistinguishable from a face pair) and still lost the block on the first
#     lift step.  A symmetric CORNER pinch fools every depth test;
#   * each failed candidate shoved the block (x 0.541 -> 0.530, yaw 38.6 ->
#     43.0), so by the time the one good yaw (+45 deg, 9.4 deg of error) got its
#     turn the block was out of the pads' footprint.
#
# The controls show why, and it is not the yaw: THE HAND WAS NOT CENTRED.
#
#   * at the block's true x, a face-aligned position close holds (26.2 / 26.0
#     at +0 deg, 27.5 / 27.4 at +12 deg, 3.9 s each) and the block barely moves;
#   * at +24 deg the ladder is ONE-sided at rung 0.80 and the close shoves the
#     block 6 mm without lifting it;
#   * at the production x (0.527, seed 9's block at 0.5407) a face-aligned yaw
#     IS STILL ONE-SIDED (0.80, grip 27.94 / 34.00) -- the 14 mm offset makes
#     the two closing faces meet the block asymmetrically.
#
# v11 measured x (edges 0.5216..0.5639 -> centre 0.5428 against the block's true
# 0.5412, and the block did not move during the scan) and still lost every yaw
# of the sweep, always ONE-sided on the right pad.  The lane was the problem:
# -0.026 against the block's true y centre of -0.0214.  The pads close
# symmetrically about the hand, so their two first touches differ by twice the
# hand's offset along the closing axis, and one rung step (0.05) is only
# 2 x 39 x 0.05 = 3.9 mm of closure: a both-sided first touch needs the hand
# within about 1 mm of the centre on BOTH axes.
#
# v12 measured y on the block's TOP FACE: close the jaws to rung 0.55 (pads
# 21.5 mm either side of the lane), descend to the top face and bisect the union
# of the two pads' press regions, whose midpoint is the centre whatever the
# block's half-length.  The x scan works that way (0.5428).  The y scan does
# NOT, and the v13 run (seed 9) says why: its informative probes have to press
# the gel onto the block's top-face EDGES, and that shoves the block -- measured
# yaw 38.0 -> 28.8 and y -0.0215 -> -0.0290 during the scan.  Its own answer
# (-0.0187) was then 9 mm off the block it had just moved, so the sweep came out
# one-sided on every yaw again.
#
# v13 measures y on the block's SIDE faces instead, with the same flip the lane
# search uses: the pads close symmetrically about the hand, so at any lane the
# pad that touches first is the pad on the block's side, and sign(depth_R -
# depth_L) is a continuous direction signal.  The lane where it swaps IS the y
# centre whenever the pads' x strip is centred on the block, because the block's
# y extent over that strip is then symmetric about yc (central symmetry of a
# square) -- whatever the yaw or the block's half-length.  Off-centre strips
# bias it (2.1 mm at the spawn x), which is exactly why the lane search there is
# only a hint and the flip runs at the measured x.
#
# The block spawns uniformly in x within +-28 mm of 0.520 and in y within
# +-52 mm of 0.  Stages:
#
#   L  LANE   bisect y at x = 0.520.  The mouth gate (a descent to the block's
#             top face with the jaws open) reports a pad that would land on the
#             block; otherwise the rung ladder reports which pad fires first.
#             A both-sided lane ends the search, but it need not fire: at the
#             spawn x the block's y-slice can be shorter than the pads' closed
#             spacing, so the bracket midpoint is the estimate.
#   X  CENTRE the full x scan at the hint lane: bisect both edges of the union
#             of the pads' pressed top-face intervals, which is symmetric about
#             xc.
#   Y  CENTRE the side flip at the measured x: probe the hint, widen 5 then
#             10 mm in the direction its sign points until the sign swaps, then
#             bisect four times.  6-8 probes, each meeting a SIDE face at
#             mid-block height, each starting from fully open so the first
#             squeeze is as shallow as the instrument allows.
#   S  SWEEP  four hand yaws 22.5 deg apart (the block's plan is a 50 mm square,
#             so its yaw repeats every 90 deg and one of the four is within
#             11.25 deg of a face pair -- inside the measured 12 deg).  Each
#             candidate: mouth gate, ladder, and CLOSE NOTHING unless the first
#             touch is at rung 0.70 or wider-open (a face; a pad landing on a
#             corner touches at 0.75 and up).  A corner is skipped without
#             touching the block, so a bad candidate cannot spoil the next one.
#   G  GRIP   position close to (first touch - 0.05), then keep closing one
#             rung at a time (at most 3) until both pads read at least 26.5 mm
#             deep: the harness counts a hold only while both depths are under
#             28.0 mm, and the block settles a little in the grip during the
#             lift.  A pad left on the far plane rejects the candidate before
#             lifting.
#   T  TEST   a 120 mm lift in two steps.  A lost block reads the far plane.
#   F  FINAL  four steps to gripper-centre z 0.319 (EE ~0.45).
#
# Nothing here reads the block's pose.  Every number below is a pad depth, and
# the only geometry is the box's, which the task states.
# =============================================================================

# --- the box, the block, the hand (same numbers as ORACLE_CODE) --------------
FLOOR_Z = 0.016
RIM_Z = 0.197
BLOCK_TOP_Z = FLOOR_Z + 0.060
GRIPPER_OFFSET_M = 0.131       # EE -> gripper centre, along the tool axis
PAD_HALF_GAP_M = 0.039         # jaw faces at +-39 mm when fully open

# --- the depth channel, in gel mm --------------------------------------------
FAR_MM = 34.0                  # nothing under the footprint
FACE_MM = 28.3                 # touching the gel, no indentation
TOUCH_MM = 31.0                # "this pad is on something": 2.7 mm short of the
                               # face, well outside the channel's scatter

# --- heights, all GRIPPER-CENTRE z ------------------------------------------
# The gel's lowest point sits only ~2.5 mm below the gripper centre (measured
# 2026-10-18: stepping the centre down 2 mm at a lane with a pad over the block,
# seed 9 lane 0, read 34.00 at 0.080 and 26.75 at 0.078).  The mouth is the
# block's own top face: a pad that is over the block presses ~2.5 mm into it and
# reads ~26, a pad in the channel reads 34.00, and the block is never pushed
# down 20 mm by an open-loop descent the planner cannot see.
MOUTH_CZ = BLOCK_TOP_Z
TRAVEL_CZ = 0.169              # turn height: pads clear the block's top
PROBE_CZ = FLOOR_Z + 0.030     # 0.046 -- the block's own middle
TEST_CZ = PROBE_CZ + 0.120     # a lost block reads the far plane by here
LIFT_CZ = 0.319                # EE ~0.45; the task line is the block's lowest
                               # VERTEX above RIM_Z + 0.015 = 0.212, and at
                               # EE 0.377 the bottom face sat at 0.2152 and the
                               # check still said no

# --- the rung ladder and the sweep ------------------------------------------
# Fractions of the opening.  Consecutive rungs are 3.9 mm of face travel apart,
# inside the gel's 4.3 mm of compliance; the deepest puts the faces 31.2 mm
# apart, so anything the block presents here is met by a pad.
RUNGS = (0.90, 0.85, 0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50, 0.45)
GRIP_BELOW_RUNG = 0.05         # the grip closes a hair past the first touch
GRIP_TARGET_MM = 26.5          # ... then one rung at a time until BOTH pads
GRIP_EXTRA_RUNGS = 3           # read at least this deep (at most this many)
                               # The harness counts a hold only while both pad
                               # depths are under 28.0 mm, and the block
                               # settles a little in the grip during the lift
                               # (measured: grip 27.55 / 27.39 -> 28.26 / 27.95
                               # at the top, so the left pad fell out of it).
SWEEP_DEG = (0.0, 22.5, 45.0, 67.5)
RUNG_MAX = 0.70                # a first touch wider than this means a pad
                               # lands on a CORNER, not on a face pair
                               # (measured at the sweep's worst-case yaw error:
                               # the two face-ish yaws touch at 0.65, the two
                               # where a pad reaches a corner at 0.75)

# --- where to look ------------------------------------------------------------
# The block spawns uniformly in x within +-28 mm of 0.520 and in y within
# +-52 mm of 0.  The lane search runs at the spawn centre; the x scan then
# measures where the block actually is.
X0_M = 0.520
SPAWN_Y_M = 0.052              # the block's y never leaves +-52 mm
LANE_LIMIT_M = 0.070           # beyond this the outer pad nears the wall
SIDE_STEP_M = 0.020            # a lane that reports nothing steps sideways
MIN_LANE_GAP_M = 0.0015        # do not re-probe a bisection point
MAX_LANES = 8
X_SCAN_RUNG = 0.55             # pads at +-21.5 mm either side of the lane
X_SCAN_STEP_M = 0.026          # coarse spacing across the spawn x range
X_SCAN_ITERS = 3               # 13 mm -> 6.5 -> 3.3 -> 1.6 mm

# --- the y flip (side first touch) --------------------------------------------
# The block is nearly as wide as the jaw's clear opening, so the FIRST rung of
# the probe already decides how hard it squeezes: start fully open and step in.
FLIP_RUNGS = (1.00, 0.95) + RUNGS
FLIP_STEPS_M = (0.005, 0.010)  # widening search for the flip bracket
FLIP_ITERS = 4                 # 5 mm bracket -> 0.3 mm, 10 mm -> 0.6 mm
FLIP_EPS_MM = 0.2              # any reading off the far plane is a touch

CALLS = [0]
LANES = [0]
HAND_X = [X0_M]
HAND_Y = [0.0]
HAND_Z = [TRAVEL_CZ]


# ---------------------------------------------------------------------------
# Small helpers.
# ---------------------------------------------------------------------------
def quat_to_rot(q):
    w, x, y, z = [float(v) for v in q]
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def floor_axis(rot, index):
    axis = np.asarray(rot, dtype=float)[:, index].copy()
    axis[2] = 0.0
    return axis / np.linalg.norm(axis)


def roll_about_z(q, angle):
    half = angle / 2.0
    c, s = math.cos(half), math.sin(half)
    return [c * q[0] - s * q[3], c * q[1] - s * q[2],
            c * q[2] + s * q[1], c * q[3] + s * q[0]]


def pads(fb):
    if not fb:
        return None
    left, right = fb.get("left_depth_mm"), fb.get("right_depth_mm")
    if left is None or right is None:
        return None
    if float(left) <= 0.0 or float(right) <= 0.0:
        return None        # a missing pad reads 0.0, not a zero pad
    return float(left), float(right)


def fmt(d):
    return "none" if d is None else "L %.2f R %.2f" % d


def say(text):
    print(text)
    sys.stdout.flush()


def read_now(note):
    CALLS[0] += 1
    try:
        return get_tactile_data()
    except Exception as exc:
        say("   %-22s re-read FAILED: %s" % (note, str(exc).splitlines()[-1]))
        return None


def open_jaws(note):
    CALLS[0] += 1
    try:
        return open_gripper()
    except Exception as exc:
        say("   %-22s open FAILED: %s" % (note, str(exc).splitlines()[-1]))
        return None


# ---------------------------------------------------------------------------
# 0. The hand, taken before it moves.
# ---------------------------------------------------------------------------
# sample_grasp_pose returns the hand's parked pose; only its quaternion is used,
# and every motion is that quaternion rolled about the vertical axis.
_, q0 = sample_grasp_pose(OBJECT)
R0 = quat_to_rot(q0)
u0 = floor_axis(R0, 1)
say("hand tool axis %s | jaw axis on the floor %s"
    % (np.round(R0[:, 2], 3), np.round(u0, 3)))
# The LEFT pad is the one at +u0 and the RIGHT pad at -u0 (v9 had this the other
# way round; the seed-1 run pinned it).  At the parked pose u0 is world -y, so
# the right pad is the +y one.
PAD_DIR = {"right": -u0, "left": u0}
SIDE_SIGN = dict((k, 1.0 if v[1] >= 0.0 else -1.0) for k, v in PAD_DIR.items())
say("pad sides: R %s (walk %+.0f in y) | L %s (walk %+.0f in y)"
    % (np.round(PAD_DIR["right"], 3), SIDE_SIGN["right"],
       np.round(PAD_DIR["left"], 3), SIDE_SIGN["left"]))


def side_of(l, r):
    # +1: the right pad (the +y one) fired, so the block sits toward +y.
    return 1.0 if r < l else -1.0


def ee_for(centre, quat):
    return np.asarray(centre, dtype=float) - GRIPPER_OFFSET_M * quat_to_rot(quat)[:, 2]


def move(centre, quat, tag, z_approach=0.0):
    CALLS[0] += 1
    try:
        fb = goto_pose(ee_for(centre, quat), quat, z_approach)
    except Exception as exc:
        say("   %-22s FAILED: %s" % (tag, str(exc).splitlines()[-1]))
        return None
    HAND_X[0] = float(centre[0])
    HAND_Y[0] = float(centre[1])
    HAND_Z[0] = float(centre[2])
    return fb


def move_to(tag, quat, lane, cz, z_approach=0.0, x=None):
    xx = HAND_X[0] if x is None else x
    return move([xx, lane, cz], quat, tag, z_approach)


def rise_here(quat, lane, tag="rise"):
    \"\"\"Come straight up at the current x before translating, so a pad that is
    over the block never drags across its top face.\"\"\"
    if abs(HAND_Z[0] - TRAVEL_CZ) < 1e-9:
        return True
    return move_to(tag, quat, lane, TRAVEL_CZ) is not None


# The gripper's state at reset is not part of any contract, and every close
# below assumes a fully open start.
open_jaws("start")


# ---------------------------------------------------------------------------
# L -- one lane: mouth gate, descend, then the ladder up to the first touch.
# ---------------------------------------------------------------------------
def probe_close(rung, note):
    CALLS[0] += 1
    try:
        fb = close_gripper(position=rung, adaptive=False)
    except Exception as exc:
        say("   %-22s rung %+.2f: %s"
            % (note, rung, str(exc).splitlines()[-1]))
        fb = read_now(note)
    return pads(fb)


def ladder(note):
    \"\"\"Close rung by rung to the first touch.

    Returns (rung, sides, l, r); the firing reading travels with the rung,
    because a re-read after open_gripper() would show 34.00 on both pads and
    lose the side the bisection needs.
    \"\"\"
    for rung in RUNGS:
        d = probe_close(rung, note)
        if d is None:
            continue
        l, r = d
        if min(l, r) < TOUCH_MM:
            sides = 2 if max(l, r) < TOUCH_MM else 1
            say("   %-22s rung %.2f: %s  (%s)"
                % (note, rung, fmt(d), "both" if sides == 2 else "ONE"))
            return rung, sides, l, r
    say("   %-22s no touch on the whole ladder" % note)
    return None, 0, None, None


def find_lane(quat):
    \"\"\"Bisect y on the side of the first touch; the bracket midpoint is the
    estimate.

    A both-sided first touch ends the search early, but it does NOT have to
    fire: at the spawn x the block's y-slice can be shorter than the pads'
    closed spacing, so a one-sided touch is all a lane probe can ever give
    there.  The bisection's own bracket is then the best estimate -- it
    converges to the lane where the contact changes sides, and unlike the
    deepest-first-touch sample it does not sit a whole lane step off centre.
    \"\"\"
    lo, hi = -SPAWN_Y_M, SPAWN_Y_M
    queue = [0.0]
    tried = []
    best = None      # (rung, lane)
    for _ in range(MAX_LANES):
        lane = None
        while queue:
            c = max(-LANE_LIMIT_M, min(LANE_LIMIT_M, queue.pop(0)))
            if all(abs(c - t) > MIN_LANE_GAP_M for t in tried):
                lane = c
                break
        if lane is None:
            break
        tried.append(lane)
        LANES[0] += 1
        say("LANE %+.3f" % lane)
        open_jaws("lane rise")
        if move_to("rise", quat, lane, TRAVEL_CZ, x=X0_M) is None:
            return None
        if move_to("mouth", quat, lane, MOUTH_CZ,
                   z_approach=TRAVEL_CZ - MOUTH_CZ, x=X0_M) is None:
            return None
        d = pads(read_now("mouth"))
        if d is None:
            return None
        if min(d) < TOUCH_MM:
            sign = side_of(*d)
            if sign > 0.0:
                lo = max(lo, lane)
            else:
                hi = min(hi, lane)
            say("   a pad is over the block here, bracket [%+.3f, %+.3f]"
                % (lo, hi))
            open_jaws("dirty lane")
            move_to("rise", quat, lane, TRAVEL_CZ, x=X0_M)
            queue.insert(0, 0.5 * (lo + hi))
            continue
        if move_to("descend", quat, lane, PROBE_CZ, x=X0_M) is None:
            return None
        rung, sides, lf, rf = ladder("lane %+.3f" % lane)
        open_jaws("lane done")
        if rung is None:
            move_to("rise", quat, lane, TRAVEL_CZ, x=X0_M)
            queue.insert(0, lane - SIDE_STEP_M)
            queue.insert(1, lane + SIDE_STEP_M)
            continue
        if sides == 2:
            return lane
        if best is None or rung < best[0]:
            best = (rung, lane)
        if side_of(lf, rf) > 0.0:
            lo = max(lo, lane)
        else:
            hi = min(hi, lane)
        say("   one-sided, bracket [%+.3f, %+.3f]" % (lo, hi))
        move_to("rise", quat, lane, TRAVEL_CZ, x=X0_M)
        queue.insert(0, 0.5 * (lo + hi))
    mid = 0.5 * (lo + hi)
    if best is not None:
        say("   no both-sided lane; bracket [%+.3f, %+.3f] -> %+.3f (deepest"
            " first touch was rung %.2f at %+.3f)"
            % (lo, hi, mid, best[0], best[1]))
    else:
        say("   no lane fired at all -- the y scan starts from %+.3f" % mid)
    return mid


# ---------------------------------------------------------------------------
# X -- centre x at the block's top face.
# ---------------------------------------------------------------------------
def x_pressed(x, lane, quat):
    \"\"\"Descend at MOUTH with the jaws closed to X_SCAN_RUNG and say whether a
    pad landed on the block's top face.  Both pads sit at the same x, so this
    is one signal for the pair.  Rise first, translate second.\"\"\"
    if not rise_here(quat, lane, "x rise"):
        return None
    if move_to("x move", quat, lane, TRAVEL_CZ, x=x) is None:
        return None
    if move_to("x mouth", quat, lane, MOUTH_CZ,
               z_approach=TRAVEL_CZ - MOUTH_CZ, x=x) is None:
        return None
    d = pads(read_now("x %.3f" % x))
    if d is None:
        return None
    return min(d) < TOUCH_MM


def bisect_edge(press_x, out_x, lane, quat):
    lo, hi = out_x, press_x
    for _ in range(X_SCAN_ITERS):
        mid = 0.5 * (lo + hi)
        got = x_pressed(mid, lane, quat)
        if got is None:
            break
        if got:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def centre_x(quat, lane):
    \"\"\"Coarse-scan x, then bisect both edges of the pressed interval.

    The union of the two pads' pressed intervals is symmetric about the block's
    x centre: the pads cover y bands mirrored through the centre of a square, so
    the midpoint survives an unknown yaw.  The block's x is uniform within
    +-28 mm of X0, so the interval (at least 28 mm wide plus the pad footprint)
    always contains one of five samples 26 mm apart.
    \"\"\"
    open_jaws("x scan")
    rise_here(quat, lane, "xc rise")
    probe_close(X_SCAN_RUNG, "xscan close")
    xs = [X0_M, X0_M - X_SCAN_STEP_M, X0_M + X_SCAN_STEP_M,
          X0_M - 2 * X_SCAN_STEP_M, X0_M + 2 * X_SCAN_STEP_M]
    res = {}
    for x in xs:
        res[x] = x_pressed(x, lane, quat)
    pressed = [x for x in xs if res[x]]
    if not pressed:
        say("   no press anywhere in the x scan -- x scan skipped")
        open_jaws("x scan end")
        rise_here(quat, lane, "xc rise")
        return X0_M
    lo_press, hi_press = min(pressed), max(pressed)
    left_out = max([x for x in xs if x < lo_press],
                   default=lo_press - X_SCAN_STEP_M)
    right_out = min([x for x in xs if x > hi_press],
                    default=hi_press + X_SCAN_STEP_M)
    for _ in range(2):
        if x_pressed(left_out, lane, quat) is not True:
            break
        left_out -= X_SCAN_STEP_M
        say("   left bracket extended to %.4f" % left_out)
    for _ in range(2):
        if x_pressed(right_out, lane, quat) is not True:
            break
        right_out += X_SCAN_STEP_M
        say("   right bracket extended to %.4f" % right_out)
    left = bisect_edge(lo_press, left_out, lane, quat)
    right = bisect_edge(hi_press, right_out, lane, quat)
    xc = 0.5 * (left + right)
    say("   x edges %.4f .. %.4f -> centre %.4f (width %.1f mm)"
        % (left, right, xc, 1000.0 * (right - left)))
    open_jaws("x scan end")
    rise_here(quat, lane, "xc rise")
    return xc


# ---------------------------------------------------------------------------
# Y -- centre y at the block's top face.
# ---------------------------------------------------------------------------
# Y -- centre y on the block's SIDE faces, by the first-touch flip.
# ---------------------------------------------------------------------------
def flip_ladder(note):
    \"\"\"Close from fully open to the first touch and return the firing
    (rung, l, r).

    Starting at rung 1.00 keeps the first squeeze as shallow as the instrument
    can make it -- the block is nearly as wide as the jaw's clear opening, so a
    deep first touch would shove it.  The ladder's 3.9 mm step then bounds the
    first-touch indentation to one step whatever the lane.
    \"\"\"
    for rung in FLIP_RUNGS:
        d = probe_close(rung, note)
        if d is None:
            continue
        l, r = d
        if min(l, r) < FAR_MM - FLIP_EPS_MM:
            say("   %-22s rung %.2f: %s  (dL-dR %+.2f)"
                % (note, rung, fmt(d), r - l))
            return rung, l, r
    say("   %-22s nothing off the far plane" % note)
    return None, None, None


def flip_at(quat, lane, x):
    \"\"\"Which side of the block's y centre is the hand on?  +1 if the block
    lies toward +y (the right pad touches first), -1 toward -y.

    The pads close symmetrically about the hand, so the pad that touches first
    is the one on the block's side.  At mid-block height the probe only ever
    meets the block's SIDE faces -- a top-down scan has to press the top-face
    edges, and in the v13 run that shoved the block 7.5 mm and 9 deg.
    \"\"\"
    open_jaws("y flip")
    if not rise_here(quat, HAND_Y[0], "y rise"):
        return None
    if move_to("y move", quat, lane, TRAVEL_CZ, x=x) is None:
        return None
    if move_to("y descend", quat, lane, PROBE_CZ, x=x) is None:
        return None
    rung, l, r = flip_ladder("flip %+.4f" % lane)
    open_jaws("y flip end")
    move_to("y rise", quat, lane, TRAVEL_CZ, x=x)
    if rung is None:
        return None
    return side_of(l, r)


def centre_y(quat, x, hint):
    \"\"\"Bisect the lane where the two pads' first touches swap sides.

    At x = xc the pads' footprint strip is centred on the block, and the
    block's y extent over that strip is symmetric about yc (central symmetry of
    a square), so the swap is exactly yc whatever the yaw or the block's
    half-length.  An off-centre strip biases it -- measured 2.1 mm at the spawn
    x, which is why the lane search there is only a hint.

    The hint is probed FIRST: it is the lane nearest the flip, so its squeeze
    is the gentlest, and its sign says which way the bracket has to widen.
    \"\"\"
    f0 = flip_at(quat, hint, x)
    if f0 is None:
        say("   y flip: no reading at the hint -- keeping %+.4f" % hint)
        return hint
    lo = hi = None
    flo = f0
    for step in FLIP_STEPS_M:
        other = hint + step if f0 > 0.0 else hint - step
        f1 = flip_at(quat, other, x)
        if f1 is None or f1 == f0:
            say("   y flip: %+.4f still says %+.0f -- widening" % (other, f0))
            continue
        lo, hi = (hint, other) if other > hint else (other, hint)
        flo = f0 if lo == hint else f1
        break
    if lo is None:
        say("   y flip: not bracketed within %.0f mm -- keeping %+.4f"
            % (1000.0 * FLIP_STEPS_M[-1], hint))
        return hint
    for _ in range(FLIP_ITERS):
        mid = 0.5 * (lo + hi)
        f = flip_at(quat, mid, x)
        if f is None:
            break
        if f == flo:
            lo = mid
        else:
            hi = mid
        say("   y bracket [%+.4f, %+.4f]" % (lo, hi))
    yc = 0.5 * (lo + hi)
    say("   y flip %+.4f (bracket %+.4f .. %+.4f, %.1f mm)"
        % (yc, lo, hi, 1000.0 * (hi - lo)))
    return yc


# ---------------------------------------------------------------------------
# G, T, F -- grip, test lift, finish.
# ---------------------------------------------------------------------------
def grip(rung):
    CALLS[0] += 1
    try:
        fb = close_gripper(position=rung, adaptive=False)
    except Exception as exc:
        say("   grip %.3f FAILED: %s" % (rung, str(exc).splitlines()[-1]))
        fb = read_now("grip")
    d = pads(fb)
    say("   grip %.3f: %s" % (rung, fmt(d)))
    return d


def lift_to(quat, lane, target_cz, steps, tag, x):
    z0 = HAND_Z[0]
    out = []
    for i in range(steps):
        cz = z0 + (target_cz - z0) * (i + 1) / steps
        fb = move_to("%s %d" % (tag, i), quat, lane, cz, x=x)
        if fb is None:
            break
        d = pads(fb)
        if d is None:
            d = pads(read_now(tag))
        if d is None:
            break
        out.append(d)
        say("   %-10s cz %.3f: %s" % (tag, cz, fmt(d)))
    return out


# ---------------------------------------------------------------------------
# The search: lane, then x, then four yaws.
# ---------------------------------------------------------------------------
lane = find_lane(q0)
grasped = False
if lane is None:
    say("")
    say("no lane found -- the search ends here and the harness scores it")
else:
    xc = centre_x(q0, lane)
    yc = centre_y(q0, xc, lane)
    lane = yc
    say("")
    say("centre (%.4f, %+.3f) -- four yaws 22.5 deg apart" % (xc, lane))
    open_jaws("sweep start")
    for k, off in enumerate(SWEEP_DEG):
        quat = roll_about_z(q0, math.radians(off))
        say("")
        say("SWEEP %+.1f deg" % off)
        if move_to("turn", quat, lane, TRAVEL_CZ, x=xc) is None:
            break
        if move_to("mouth", quat, lane, MOUTH_CZ,
                   z_approach=TRAVEL_CZ - MOUTH_CZ, x=xc) is None:
            break
        d = pads(read_now("mouth"))
        if d is None:
            break
        if min(d) < TOUCH_MM:
            say("   a pad would land on the block at this yaw -- skipped")
            move_to("rise", quat, lane, TRAVEL_CZ, x=xc)
            continue
        if move_to("descend", quat, lane, PROBE_CZ, x=xc) is None:
            break
        rung, sides, lf, rf = ladder("sweep %+.1f" % off)
        # The rung alone separates a face from a corner, and ONE side is fine:
        # with the hand half a millimetre off in y the two pads touch one rung
        # apart (measured 0.65 / ONE at the two yaws nearest a face pair, 0.75
        # / both where a pad lands on a corner).  The grip below is what
        # proves both pads ended up on the block.
        if rung is None or rung > RUNG_MAX:
            say("   first touch %s at the rung -- a pad lands on a corner; this"
                " yaw is skipped without closing"
                % ("none" if rung is None else "%.2f" % rung))
            open_jaws("corner skip")
            move_to("rise", quat, lane, TRAVEL_CZ, x=xc)
            continue
        grip_rung = max(0.0, round(rung - GRIP_BELOW_RUNG, 3))
        d = grip(grip_rung)
        for _ in range(GRIP_EXTRA_RUNGS):
            if d is None or max(d) <= GRIP_TARGET_MM or min(d) > TOUCH_MM:
                break
            grip_rung = max(0.0, round(grip_rung - 0.05, 3))
            d = grip(grip_rung)
        if d is None:
            say("   the grip returned no reading -- next yaw")
            open_jaws("no reading")
            move_to("rise", quat, lane, TRAVEL_CZ, x=xc)
            continue
        if max(d) > TOUCH_MM:
            say("   ONE-sided pinch at this yaw -- rotate instead of lifting")
            open_jaws("one-sided")
            move_to("rise", quat, lane, TRAVEL_CZ, x=xc)
            continue
        seen = lift_to(quat, lane, TEST_CZ, 2, "test", xc)
        if not seen:
            say("   the test lift did not return a reading -- next yaw")
            open_jaws("test failed")
            move_to("rise", quat, lane, TRAVEL_CZ, x=xc)
            continue
        if max(seen[-1]) > TOUCH_MM:
            say("   LOST on the test lift (%s) -- opening and rising"
                % fmt(seen[-1]))
            open_jaws("lost")
            move_to("rise", quat, lane, TRAVEL_CZ, x=xc)
            continue
        say("   HELD through the test lift -- finishing to the retrieval height")
        lift_to(quat, lane, LIFT_CZ, 4, "final", xc)
        grasped = True
        break

say("")
say("SUMMARY")
say("  motions/reads issued: %d" % CALLS[0])
say("  lanes probed: %d, lane used: %s"
    % (LANES[0], "none" if lane is None else "%+.3f" % lane))
say("  result: %s" % ("grasped and lifted" if grasped else "no grasp"))
say("  check_success: %s" % check_success())
say("blind_pick v13 finished; the harness scores it")

"""


class UniVTACBlindPickCodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE
