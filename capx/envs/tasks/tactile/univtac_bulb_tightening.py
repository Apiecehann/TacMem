from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: screw the light_bulb into its socket until it is all the way in. In the
scene the bulb is named "light_bulb" - use that exact name with the pose and
grasp helpers.

The bulb starts already threaded into its socket, and it is a thread: turning
is the only thing that takes it deeper. Pushing it down does not move it - the
push is taken by the glass and the pads instead, and it stays there.

The fingertips are the instrument, not a safety net. The bulb is smooth glass
and the pads are the only thing that touches it, so every question this task
asks is answerable from them, and that is where the answers should come from:

- Which way is in. Take the direction from separate legs; how long each leg
  must be, and how far apart they have to come before you believe them, are in
  the tactile skills at the end of this prompt.
- How fast to descend. The lead is not given either, and it varies with the
  bulb, so it has to be measured rather than assumed - and the pads are what
  measure it. The skills below give the method.
- Whether it is in. The seat is a reading, not a count of turns, and the skills
  below say when it can be believed.

The wrist runs out of range soon after a third of a turn, so a full insertion
is several segments with the hand re-set in between. Going past that range is
refused outright and is not recoverable within the episode, so keep the
segments well short of it; the skills below give the reset sequence and say
when to stop re-setting.

The job is done the moment the bulb is all the way in: there is nothing to
submit and nothing to hold at the end, so stop turning there and let the
episode be scored on the bulb.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

ORACLE_CODE = """
import math

# --- Where the bulb is (task source, envs/bulb_tightening.py:886-890) ---------
# Bulb origin is its bottom face; the grasp centre is GRASP_LOCAL_Z above that,
# the wrist a further GRIPPER_OFFSET along the gripper centre's own -z
# (gripper_center_to_ee, envs/robot/robot.py:285).
GRASP_LOCAL_Z = 0.058
GRIPPER_OFFSET_M = 0.131
GRASP_Z_APPROACH = 0.05
GRIP_DEPTH_MM = 27.2

BULB = "light_bulb"

# --- The block: the unit this oracle thinks in --------------------------------
# What the pads see is the SLIDE between gel and glass, and it is unambiguous
# only inside one marker-lattice pitch (~1.5 mm): past half of that the tracker
# settles on the neighbouring marker and returns a wrong number silently, sign
# included.  A 30 deg block slides 0.66 mm at the longest admissible lead (its
# whole commanded descent, if the thread holds the bulb back), so 30 is the
# longest twist whose own reading can be believed.
BLOCK_DEG = 30.0
# 180 was tried and refused.  Seed 1, feed 4.271 against a true lead of 4.3623,
# carried four blocks then refused 15 deg into the fifth -- 135 deg from the
# grab -- and a refusal latches _TASK.failure until reset, so the episode is
# over.  An earlier 60 deg generation refused at the same cumulative 135 deg;
# v11 at 120 turned 82 blocks over twenty grasps with none.  A latched refusal
# cannot be walked back, so 120 is what is known to clear it.
DEG_PER_GRASP = 120.0
# Degrees per servo iteration on the wind back.  See unwind() for why it is
# coarser than a block's 1.5 and why it may not go much coarser.
UNWIND_DEG_PER_STEP = 2.5
# The wind back's fallback, for the part rotate_gripper refuses: move_delta's
# 0.5 rad per-call cap, and the 0.02 rad below which it refuses as a no-op.
UNWIND_CHUNK_DEG = 25.0
UNWIND_MIN_DEG = 2.0
# Free travel is 0.0065 m -- the seat was raised on 2026-10-10, halving it from
# the 0.0123 m it had been.  The lead is 0.0123 / U(1.12, 2.88), so the turns an
# insertion needs are travel / lead, worst at the shortest admissible lead:
# 6.5 / 4.271 = 1.52 turns (548 deg).  That, plus the probe and the seat's own
# overshoot, is 25 blocks; 60 leaves slack for re-tried blocks and for a bulb
# turning more than nominal.
MAX_BLOCKS = 60

# --- Reading the lead off one twist -------------------------------------------
#
# THE POINT OF THE WHOLE EXERCISE.  The pads slide BACKWARD by (descent -
# advance) when the hand outruns the thread and FORWARD when the thread carries
# the bulb ahead, so above the lead -- where the thread will not let the bulb
# outrun its own pitch -- one twist at an over-lead feed MEASURES the lead:
#
#     lead = feed + slide * 360 / deg
#
# 11.1 mm/turn is past the sampler's 4.271-7.935, so every seed is over-lead on
# the first block.  The reading is the pads' worst, though (seed 1: 0.632 mm read
# for a true 0.561 mm slide, giving 3.51 against a 4.3623 pitch), so the grasp
# slope below is what actually settles the feed.
LEAD_PROBE_MM = 11.1
# lead = 0.0123 / nominal_turns, and the sampler re-draws until nominal is at
# least 1.55 turns (envs/bulb_tightening.py:728-745), so the support is
# [0.0123/2.88, 0.0123/1.55] -- the sampler's own range, not a guess at the
# answer.  A correction that lands on one of these has failed, and says so.
LEAD_TRUE_MIN_MM = 4.271
LEAD_TRUE_MAX_MM = 7.935

# --- Bringing the feed onto the pitch -----------------------------------------
#
# Correcting a block at a time was v11's mistake: the per-block slide scatters
# with sigma ~0.03 mm = 0.12 mm/turn at 30 deg, nearly three per cent of a pitch,
# and a damped servo chasing that walks.  Seed 1's walked -- 2.1 per cent under
# the pitch, then 9.9 over, 16 of its 41 blocks pinned on LEAD_TRUE_MIN_MM.
#
# The pitch is a property of the grasp, so it is read per grasp, cumulatively:
# inside one 120 deg grasp the hand is never re-set, so nothing but the thread
# moves the glass and the grasp's whole gain over its whole descent IS the pitch.
#
#     lead = (sum of gain) * 360 / (sum of degrees)
#
# 90 deg of sum instead of 30 cuts the scatter by about three, so one grasp gives
# ~5 per cent and nine give ~1.7 -- nothing for a damping factor to do.  Seed 1
# settles inside 1.5 per cent of its 4.3623 and stays there.
#
# The grasp's first block is left out: every re-grasp drags the glass back through
# the pads a little (seed 1's nine re-grasps averaged -0.044 mm there against
# +0.006, -0.001, -0.003 for the other three positions).  That costs ~1.4 per
# cent low, the safe side to be wrong on -- a feed UNDER the pitch lets the thread
# lead the hand, while a feed OVER it holds the glass back by a fixed amount every
# block, and that standing gap is what the seat test looks for.
#
# Deliberately NOT gated on coherence: confidence collapses for a grasp or two
# after every re-grasp (seed 1 ran 0.15-0.45 for most of the insertion), so a
# coherent-only slope would have collected no degrees for seven grasps running.
GRASP_MIN_DEG = 60.0
MAX_BLIND_GRASPS = 3

# --- Reading the gel ----------------------------------------------------------
# px of pad shear per mm of slide, measured on this rig three ways (0.1116,
# 0.1362 and 0.3107 mm of travel gave 17.6, 18.3, 20.8 px/mm).  Only ever used
# to turn a shear into millimetres, never a sign into a decision.
GAIN_PX_PER_MM = 18.0
# No contact returns the 34 mm sentinel with both flags down, so a shear taken
# then is a missing reading, not a null one.
NO_CONTACT_MM = 33.0
# The tracker reports its own confidence, and this is the whole of v12.
#
# An unlocked marker lattice does not give a noisy number, it gives an unrelated
# one at the same plausible magnitude -- seed 1's false slides were -0.111,
# -0.083 and -0.076 mm at the re-grasps of grasps 5, 6 and 8, three to five
# times the seat's own -0.043 mm.  Magnitude does not separate them, coherence
# does: every re-grasp transient sat under 0.5 and the one reading that matters
# (block 40, the block the torque came up on) sat at 0.70.  0.7 is the project's
# own threshold (univtac_api.py:903).  Only the after-reading is held to it.
SEAT_COH_MIN = 0.70

# --- What the seat looks like to the pads -------------------------------------
#
# A glass that has stopped does not drift a little under the hand -- it stops, so
# the slide becomes the WHOLE commanded descent of the block.  Hence a fraction
# of that descent, not a fixed depth: v10 tested a fixed 0.08 mm and seed 1 died
# of it, calling the seat with the bulb 3.0 mm into a 13 mm travel.
#
# 0.10, not v11's 0.5.  Seed 1 measured the seat directly -- block 40, the first
# block the torque moved on, -0.043 mm of slide against a 0.356 mm commanded
# descent = 0.122; seed 3 put the same event at 0.14.  Near a tenth because the
# seat's slide scales with the block's descent rather than being a fixed length.
# It also separates the seat 2.4x from the nearest false positive (seed 1's
# block 36: -0.014 mm, fraction 0.041, coherence 0.73).
SEAT_DESCENT_FRAC = 0.10
# ... and the seat cannot be called before the bulb is deep enough to have
# reached one.  Counted on the HAND's descent, which is commanded and therefore
# exact (the pads' own count finished 17.7 per cent over on seed 1).  Two things
# bound it from either side, and both are in hand millimetres:
#
# Above it, the first grasp turns at the probe's 11.1 mm/turn, and an over-lead
# block slides the pads at a fraction of its own descent that looks exactly like
# a seat -- so the arm has to clear the whole of that grasp: 120 deg at 11.1 is
# 3.7 mm of hand whatever the thread does.
#
# Below it, the bulb is home when the HAND has sunk the probe's 3.7 mm plus the
# travel at a matched feed -- 7.5 mm on the deepest sampled lead (7.935 mm,
# home at 6.5 mm of bulb) and 8.8 mm on the shallowest (4.271).  Arming at or
# past that leaves no block to read the seat on.
#
# 5.5 sits between the two with a block or two of margin either way.  It used to
# be 8.0, which was most of the way to home while the travel was 13 mm; the seat
# was raised on 2026-10-10 (seat_top_m .022 -> .0285) and 8.0 would now arm at
# the bulb, or never.
ARM_DESCENT_MM = 5.5

WIGGLE_DEG = 4.0
WIGGLE_ITER = 3
# The wiggle's own feed, only to keep the two legs identical.  Net descent is
# 2 * WIGGLE_DEG at this feed, which used to have to stay under the 0.2 mm the
# thread probe allowed the bulb for "home" (that probe no longer gates anything,
# see the note at the wiggle legs below).
LEAD_INIT_M = 0.0123 / 2.0

# The direction decision's own legs.  The 4 deg wiggle is not the decision: it
# separates the legs by only 0.05-0.12 mm, one or two pixels, under the
# tracker's own scatter.  At 30 deg the separation is lead/6, 0.71-1.32 mm.
# DIR_DEG_BIG is the one widening allowed if they come back close.
DIR_DEG = 30.0
DIR_DEG_BIG = 45.0
DIR_MIN_SEP_MM = 0.15

RELEASE_MM = 33.5
GRIP_TOOK_MM = 30.0


def quat_to_rot(q):
    w, x, y, z = (float(v) for v in q)
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    w, x, y, z = w / n, x / n, y / n, z / n
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def mat_vec(rot, v):
    return [sum(rot[i][k] * v[k] for k in range(3)) for i in range(3)]


# --- The tactile channel ------------------------------------------------------
# The fingertips are mirrored, so one and the same slide appears with OPPOSITE
# sign in the two images.  The difference doubles the signal and cancels the
# shared squeeze, which is why everything below is built on it and not on a pad.


def shear_in(fb):
    \"\"\"Mirrored marker shear in px, or None.

    The depth sentinel doubles as a contact test: a pad that is not touching
    reports 34 mm and a shear that means nothing.
    \"\"\"
    if not fb:
        return None
    if not (fb.get("left_contact") or fb.get("right_contact")):
        return None
    left = fb.get("left_marker_shear_dx")
    right = fb.get("right_marker_shear_dx")
    if left is None or right is None:
        return None
    return float(left) - float(right)


def coh_in(fb):
    \"\"\"The worse of the two pads' marker coherence, or None.

    A difference of two shears is only as good as the worse of them.
    \"\"\"
    if not fb:
        return None
    left = fb.get("left_marker_coherence")
    right = fb.get("right_marker_coherence")
    if left is None or right is None:
        return None
    return min(float(left), float(right))


def shear_now():
    \"\"\"Read the gel without moving.\"\"\"
    return shear_in(get_tactile_data())


def shear_detail(fb):
    \"\"\"Both halves of the pads' read and their coherence, for the log.

    shear_in() hands back the difference alone, and a difference cannot be
    audited after the fact: the pads can disagree twenty-five-fold and still land
    on a number that looks like a slide (seed 1: 5 px left, 131 px right).  The
    halves make a bad read visible as one; the coherence is what the seat turns
    on.
    \"\"\"
    if not fb:
        return "no read"

    def show(key):
        v = fb.get(key)
        return "None" if v is None else f"{float(v):+.2f}"

    return (f"L={show('left_marker_shear_dx')} "
            f"R={show('right_marker_shear_dx')} px, coh "
            f"{show('left_marker_coherence')}/"
            f"{show('right_marker_coherence')}")


def pad_depth(fb):
    \"\"\"The shallower pad's reading, or the no-contact sentinel when there is none.\"\"\"
    if not (fb.get("left_contact") or fb.get("right_contact")):
        return 34.0
    depths = [fb.get("left_depth_mm"), fb.get("right_depth_mm")]
    depths = [float(d) for d in depths if d is not None]
    return min(depths) if depths else 34.0


def clamp_lead(value):
    \"\"\"Hold a feed inside the range the task's sampler can produce.\"\"\"
    return min(LEAD_TRUE_MAX_MM, max(LEAD_TRUE_MIN_MM, value))


_, GRASP_QUAT = sample_grasp_pose(BULB)


def close_on_bulb():
    \"\"\"Close where the hand already stands, and say whether the grip took.

    No re-approach from the bulb's ground-truth pose: the hand never left the
    bulb's axis (the release opens the pads past contact, the wind back rolls the
    wrist in place, the thread captures the bulb throughout), so there is no pose
    to recover -- what the approach really undid was the lift of its own return
    path, and that lift is gone (see unwind).  The environment's own expert does
    the same (bulb_tightening.py:1878-1885).
    \"\"\"
    depth = pad_depth(close_gripper(force_threshold=GRIP_DEPTH_MM, adaptive=True))
    print(f"  close: pad depth {depth:.2f} mm (target {GRIP_DEPTH_MM})")
    return depth < GRIP_TOOK_MM


def grasp_bulb():
    \"\"\"Approach the bulb where it is, close on it, say whether the grip took.

    The approach is the whole difference from close_on_bulb, and it is for the
    first grasp only: the arm comes off a reset knowing nothing about where the
    bulb is, so that one is placed against the object pose and comes down from
    above in a single goto_pose.
    \"\"\"
    pos, _, _ = get_object_pose(BULB)
    centre = [float(pos[0]), float(pos[1]), float(pos[2]) + GRASP_LOCAL_Z]
    off = mat_vec(quat_to_rot(GRASP_QUAT), [0.0, 0.0, -GRIPPER_OFFSET_M])
    target = [centre[i] + off[i] for i in range(3)]
    goto_pose(target, GRASP_QUAT, z_approach=GRASP_Z_APPROACH)
    return close_on_bulb()


def turn(sign, deg, lead_m, steps=None):
    \"\"\"One block: twist and descend together, and hand back the readings.

    rotate_gripper raises on a refused block, so an over-long one would end the
    run rather than report; the readings live under "helix", with the pads'
    shear copied to the top level of the same reply.  steps defaults to one
    servo iteration per 1.5 deg; the wind back passes it explicitly, at its own
    coarser step.
    \"\"\"
    if steps is None:
        steps = max(2, int(round(deg / 1.5)))
    res = rotate_gripper(BULB, sign * math.radians(deg), steps, lead_m)
    if res is not None and "helix" not in res:
        raise RuntimeError(
            "rotate_gripper returned no helix block, so reached/aborted/trace "
            f"are absent -- got keys {sorted(res)}")
    return res or {}


def unwind(deg, sign):
    \"\"\"Wind the EMPTY hand back to the grasp yaw, in place, on the servo.

    move_delta is the obvious primitive for this and the wrong one: its 0.5 rad
    per-call cap makes a 180 deg return seven cuRobo plans, and the five calls a
    120 deg return needed cost 2556 s on the seed-1 run -- more than all the
    turning in the episode.  rotate_gripper is the servo the blocks already run
    on: one differential-IK step and two physics ticks per iteration, no planner.

    Zero lead, so it is a pure roll about world z through the gripper centre and
    the hand ends exactly where it began.  Retracing the block path instead
    descends by lead/2, leaving the hand 2.2 mm high -- which is what the full
    goto_pose was really undoing, 146 s of the 557 s a re-grasp cost.  Rolling
    about the axis the hand is centred on also means the open jaws sweep around
    the bulb rather than down past it.

    Coarser than a block's 1.5 deg per iteration on purpose: nothing is read on
    the way out, so there is no shear to keep inside a lattice pitch.  The one
    real limit is _ik_to_center's refusal at a 0.08 rad single-step delta, which
    latches the task for the rest of the run; 2.5 deg is 0.044 rad.  Whatever
    the servo will not wind, move_delta winds.
    \"\"\"
    steps = max(2, int(round(deg / UNWIND_DEG_PER_STEP)))
    try:
        res = turn(-sign, deg, 0.0, steps=steps)
    except Exception as exc:
        res = {}
        print(f"  the servo would not take the wind back at all ({exc})")
    done = int((res.get("helix") or {}).get("completed") or 0)
    if done >= steps:
        return res
    # Whatever the servo did not wind, move_delta winds in its own 0.5 rad
    # chunks -- the expensive path this function exists to get away from, paid
    # only on the part the servo would not take.  A wrist left wound is not a
    # smaller run: the IK refuses the next block and that failure latches.
    left = deg * (1.0 - min(done, steps) / float(steps))
    print(f"  the servo wound {done} of {steps} iterations, so {left:.0f} deg "
          f"is left to move_delta")
    while left > UNWIND_MIN_DEG:
        step = max(-UNWIND_CHUNK_DEG, min(UNWIND_CHUNK_DEG, left))
        move_delta(drz=-sign * math.radians(step), coord="ee")
        left -= step
    return res


def printed_lead(value, raw):
    \"\"\"The tail of a feed report: the number, and whether a clamp chose it.

    v11 compared the damped result against the raw reading, so the clamp clause
    fired on every ordinary correction -- 27 times on seed 1, on readings like
    4.66 mm/turn that are squarely inside the range a bulb is built to.  Only
    the clamp is worth announcing, so the test is against the value the clamp
    was handed.
    \"\"\"
    if abs(value - raw) > 1e-9:
        return (f"{raw:.2f} mm/turn, outside the {LEAD_TRUE_MIN_MM:.2f} to "
                f"{LEAD_TRUE_MAX_MM:.2f} mm/turn the task draws from, so "
                f"{value:.2f} is taken")
    return f"{value:.2f} mm/turn"


# --- 1. Grasp -----------------------------------------------------------------

grasped = grasp_bulb()
if not grasped:
    print("the first close did not take -- opening and trying once more")
    open_gripper()
    grasped = grasp_bulb()

if not grasped:
    print("could not grip the bulb; there is nothing to turn")
    check_success()
else:

    # --- 2. Which way is in, and how far does one turn take it? ---------------
    #
    # The decision is made by the direction legs further down: an order of
    # magnitude longer, read across the outbound turn alone, then compared.
    #
    # Upstream used to hold the episode's whole accounting behind a reversible
    # +/-4 deg probe: until it completed, the formal turn count and formal
    # advance read zero however well the tightening went.  That gate was removed
    # locally on 2026-10-07 (see the LOCAL DEVIATION note on
    # ReversibleThreadProbe.formal_motion in ViTaForge/envs/bulb_tightening.py),
    # so the counters now measure the bulb from the start of the episode.  The
    # two wiggle legs below are kept because this build was verified with them
    # and they cost two short twists; their readings are printed and NOT acted
    # on (seed 1: the same grip read +1.8/-5.1, -1.2/-9.6 and -3.0/-1.8 px over
    # three runs, the first two picking opposite legs -- it is a coin toss, and
    # losing it turns the run into an unscrewing).
    #
    # The decision gets its own legs: four times longer, read across the outbound
    # turn alone, because the return cancels most of its own excursion.  Both
    # legs descend the hand alike and differ in ONE respect -- which way the
    # thread drives the glass.  The loosening leg lifts the glass while the hand
    # sinks, so the glass runs UP through the pads and the shear goes hard
    # negative; the tightening leg at worst keeps up.  Higher gel reading = in.
    def wiggle_leg(sign):
        \"\"\"One reversible probe leg (no longer required): out, back, residual.\"\"\"
        before = shear_now()
        turn(sign, WIGGLE_DEG, LEAD_INIT_M)
        res = turn(-sign, WIGGLE_DEG, LEAD_INIT_M)
        after = shear_in(res) if shear_in(res) is not None else shear_now()
        d = None if (before is None or after is None) else after - before
        print(f"  wiggle {sign * WIGGLE_DEG:+.0f} deg: the pads slid "
              f"{'an unreadable amount' if d is None else f'{d:+.3f} px'} "
              f"(positive = the glass came down through the fingers)")
        return d

    def dir_leg(sign, deg):
        \"\"\"One direction leg: the outbound turn's own slide, then home again.\"\"\"
        before = shear_now()
        turn(sign, deg, LEAD_INIT_M)
        after = shear_now()
        turn(-sign, deg, LEAD_INIT_M)
        d = None if (before is None or after is None) else after - before
        print(f"  direction {sign * deg:+.0f} deg: the outbound turn slid "
              f"{'an unreadable amount' if d is None else f'{d:+.3f} px'} "
              f"(positive = the glass came down through the fingers)")
        return d

    wiggle_leg(+1.0)
    wiggle_leg(-1.0)

    sign = None
    for deg in (DIR_DEG, DIR_DEG_BIG):
        d_pos = dir_leg(+1.0, deg)
        d_neg = dir_leg(-1.0, deg)
        if d_pos is None or d_neg is None:
            print(f"  a pad carried no reading at {deg:.0f} deg, so the two "
                  f"legs cannot be compared")
            continue
        sep_mm = abs(d_pos - d_neg) / GAIN_PX_PER_MM
        if sep_mm < DIR_MIN_SEP_MM:
            print(f"  the legs are {sep_mm:.3f} mm apart at {deg:.0f} deg, "
                  f"under the {DIR_MIN_SEP_MM:.2f} mm the pads resolve; "
                  f"widening them")
            continue
        sign = 1.0 if d_pos > d_neg else -1.0
        print(f"  the legs read {sep_mm:.3f} mm apart and the higher one was "
              f"{sign * deg:+.0f} deg, so that is the way in")
        break

    if sign is None:
        # Neither width separated the legs, so what is broken is the channel
        # rather than the thread, and a guess is all that is left.  Say so: the
        # right-handed thread is the commoner fitment, and everything after
        # this is built on the guess.
        sign = -1.0
        print("  the pads never separated the two legs -- starting out "
              "left-handed and letting the first block settle it")

    # --- 3. Read the lead, then turn it in ------------------------------------
    #
    # The first block is the probe: over-lead by construction, so its slide is
    # the overshoot and a pitch falls out of it.  From there the feed is set per
    # grasp from that grasp's slope, and the slide goes back to meaning the seat.
    feed_mm = LEAD_PROBE_MM
    at_lead = False
    sum_descent_mm = 0.0
    advance_mm = 0.0
    bottomed = False
    since_reset = 0.0
    reached = False
    seat_armed = False
    blind_grasps = 0
    pads_blind = False
    g_blocks = 0
    g_deg = 0.0
    g_gain = 0.0
    tot_deg = 0.0
    tot_gain = 0.0

    for block in range(MAX_BLOCKS):
        deg = BLOCK_DEG
        descent_mm = feed_mm * deg / 360.0
        before_fb = get_tactile_data()
        before = shear_in(before_fb)
        before_coh = coh_in(before_fb)
        try:
            res = turn(sign, deg, feed_mm / 1000.0)
        except Exception as exc:
            # A refused block is the end of the turning either way, but it is
            # worth saying where the bulb had got to: a stall on the last block
            # of a seated insertion and a stall on an unseated one want
            # different things done about them, and the log is all there is
            # afterwards.
            print(f"block {block + 1} refused: {exc}")
            print(f"  at that point the hand had sunk {sum_descent_mm:.3f} mm "
                  f"and the pads counted {advance_mm:.3f} mm of advance "
                  f"({advance_mm / max(sum_descent_mm, 1e-9):.2f} of it)")
            break
        hx = res.get("helix") or {}
        since_reset += deg

        if hx.get("reached"):
            reached = True
            print(f"  block {block + 1}: the fixture says the bulb is home -- "
                  f"{hx['reached']}")
            break
        if hx.get("aborted"):
            print(f"  block {block + 1} stopped at "
                  f"{hx.get('completed')}/{hx.get('requested')}: {hx['aborted']}")
            break
        if hx.get("grip_released") is False:
            print("  the bulb is not under physics -- this twist is moving the "
                  "hand without turning it")
            break

        sum_descent_mm += descent_mm
        g_blocks += 1

        after_fb = res
        after = shear_in(after_fb)
        after_coh = coh_in(after_fb)
        if after is None:
            after_fb = get_tactile_data()
            after = shear_in(after_fb)
            after_coh = coh_in(after_fb)
        if before is None or after is None:
            print(f"  block {block + 1}: the pads were not on the glass, so "
                  f"this block says nothing")
            if not bottomed:
                advance_mm += descent_mm
        else:
            slide_mm = (after - before) / GAIN_PX_PER_MM
            gain_mm = descent_mm + slide_mm

            if not at_lead:
                # The probe.  This feed is past anything the bulb could have
                # been given, so the thread held the glass to its pitch and the
                # slide just read is the whole overshoot: the pitch that
                # produced it is the arithmetic below, with nothing left to
                # search for.
                tried_mm = feed_mm
                read_mm = tried_mm + slide_mm * 360.0 / deg
                feed_mm = clamp_lead(read_mm)
                at_lead = True
                print(f"  block {block + 1}: {deg:.0f} deg at "
                      f"{tried_mm:.1f} mm/turn -- the hand sank "
                      f"{descent_mm:.3f} mm and the glass came only "
                      f"{gain_mm:+.3f} mm of it ({gain_mm * 360.0 / deg:.2f} "
                      f"mm/turn), so the pads read {slide_mm:+.3f} mm of slide "
                      f"and the thread is carrying the bulb at "
                      + printed_lead(feed_mm, read_mm)
                      + f".  Holding that until a grasp has measured it")
                print(f"    shears {shear_detail(before_fb)} -> "
                      f"{shear_detail(after_fb)}")
                if not bottomed:
                    advance_mm += gain_mm
            else:
                if not bottomed:
                    advance_mm += gain_mm
                    print(f"  block {block + 1}: hand sank {descent_mm:.3f} mm and "
                          f"the glass went {gain_mm:+.3f} mm of that "
                          f"({gain_mm * 360.0 / deg:.2f} mm/turn), so the bulb "
                          f"is {advance_mm:.3f} mm in by the pads' count")
                    print(f"    shears {shear_detail(before_fb)} -> "
                          f"{shear_detail(after_fb)}")

                    # The slope the feed is read from, accumulated on every
                    # block the pads could read.  The first block of the grasp
                    # is left out: it carries the re-grasp, and the re-grasp is
                    # not the thread.
                    if g_blocks > 1:
                        g_deg += deg
                        g_gain += gain_mm
                        tot_deg += deg
                        tot_gain += gain_mm

                    # The decision this task is about: while the thread is
                    # taking the bulb the glass keeps pace with the hand, or
                    # beats it; a glass that has stopped has a slide collapsed
                    # to the whole commanded descent, running the other way.
                    # Gated on depth (the travel is fixed geometry, so a bulb a
                    # few mm in cannot be seated -- v10 spent eight blocks
                    # tightening a bulb 3.0 mm into the 13 mm travel of the
                    # day), on pads the run has given up on, and on confidence
                    # in the reading.
                    if not pads_blind and sum_descent_mm >= ARM_DESCENT_MM:
                        if not seat_armed:
                            seat_armed = True
                            print(f"  the hand has sunk {sum_descent_mm:.3f} mm, "
                                  f"about {sum_descent_mm * 0.80:.1f} mm of bulb "
                                  f"at the rate this thread has been running, "
                                  f"past the {ARM_DESCENT_MM:.0f} mm a seat has "
                                  f"to be deeper than -- so the slide means the "
                                  f"seat from here")
                        frac = slide_mm / max(descent_mm, 1e-9)
                        # Only the after-reading is gated: the seat is a STEP
                        # change, and the block before it carries the same
                        # matched feed as every block since the grasp, so it
                        # reads at that grasp's noise floor.  Run 5 (2026-10-02)
                        # measured 13 consecutive pre-seat blocks at 0.13-0.27,
                        # so a pre-gate refuses the seat whatever it reads.
                        coh_ok = after_coh is not None and after_coh >= SEAT_COH_MIN
                        print(f"    the slide is {-frac:.3f} of the "
                              f"{descent_mm:.3f} mm the hand sank "
                              f"({slide_mm:+.3f} mm) at a coherence of "
                              f"{'--' if after_coh is None else f'{after_coh:.2f}'}"
                              f"; {SEAT_DESCENT_FRAC:.2f} of it on a read the "
                              f"tracker is sure of ({SEAT_COH_MIN:.2f}) is the "
                              f"seat")
                        if coh_ok and frac <= -SEAT_DESCENT_FRAC:
                            bottomed = True
                            print(f"  the pads say the glass has stopped "
                                  f"following the fingers: the hand went on "
                                  f"down {descent_mm:.3f} mm and the glass came "
                                  f"{gain_mm:+.3f} mm of it, {slide_mm:+.3f} mm "
                                  f"of slide against the "
                                  f"{SEAT_DESCENT_FRAC * descent_mm:.3f} mm that "
                                  f"means stopped, at coherence {after_coh:.2f}. "
                                  f"{advance_mm:.3f} mm in by the pads' count, "
                                  f"{sum_descent_mm:.3f} mm by the hand's -- the "
                                  f"bulb is home in its socket, which is the "
                                  f"whole of what this run is scored on")
                else:
                    # Past the seat the slide is the whole commanded descent
                    # every block and the pads' reading walks out of its
                    # lattice, so it is no longer counted.  Nothing here is
                    # scored either: the loop is on its way out.
                    print(f"  block {block + 1}: still turning it in, "
                          f"{hx.get('completed')}/{hx.get('requested')} iterations")

        # Hand re-set.  Winding while still holding the bulb unscrews what was
        # just put in, so the release comes first -- and the release is
        # believed on the PADS' evidence, not on the command's.
        #
        # The seat ends the run, not just the re-setting.  Until 2026-10-09 a
        # block past the seat went on turning to bring the torque up to a
        # target the scorer asked for; that target is gone, and a block past
        # the seat buys nothing and spends wrist the run may still want.  Seed
        # 1 stood at 2.79 turns / 12.23 mm in with 3.73 N on the seat when it
        # was stopped by hand at the wrist's limit, six re-grasps after it was
        # home.
        if reached or bottomed or block + 1 >= MAX_BLOCKS:
            break
        # The re-set is counted in DEGREES, not blocks, because the wrist's
        # range does not care how the turning was divided.  The test is whether
        # the NEXT block would fit, not whether this one already overflowed: a
        # wrist wound to the end of its travel refuses the twist that would
        # have finished the insertion, and a refusal latches the episode.
        if since_reset + BLOCK_DEG > DEG_PER_GRASP:
            # The grasp is over, so its degrees are in and the running slope can
            # be re-read.  What comes out is the feed the next grasp turns at --
            # not a correction to the last one, but the whole run's gain over
            # the whole run's turning so far.
            if g_deg >= GRASP_MIN_DEG:
                read_mm = g_gain * 360.0 / g_deg
                run_mm = tot_gain * 360.0 / tot_deg
                feed_mm = clamp_lead(run_mm)
                blind_grasps = 0
                print(f"  that grasp turned {g_deg:.0f} deg and carried the "
                      f"glass {g_gain:.3f} mm of it "
                      f"({read_mm:.2f} mm/turn); over the "
                      f"{tot_deg:.0f} deg the pads have read since the "
                      f"probe the thread is running at "
                      + printed_lead(feed_mm, run_mm)
                      + f".  Turning at {feed_mm:.2f} from here")
            else:
                blind_grasps += 1
                print(f"  that grasp put only {g_deg:.0f} deg of readable "
                      f"slide together, not the {GRASP_MIN_DEG:.0f} a slope "
                      f"needs -- {blind_grasps} of {MAX_BLIND_GRASPS} "
                      f"before the pads are given up on")
                if blind_grasps >= MAX_BLIND_GRASPS:
                    pads_blind = True
                    print(f"  the pads have gone {MAX_BLIND_GRASPS} grasps "
                          f"without a slope, so the slide is not this "
                          f"run's to read.  The seat decision goes to the "
                          f"fixture "
                          f"from here: turning on until it reports the "
                          f"seat, and judging nothing on the slide")
            g_blocks = 0
            g_deg = 0.0
            g_gain = 0.0
            rel = pad_depth(open_gripper())
            if rel < RELEASE_MM:
                rel = pad_depth(open_gripper())
            if rel < RELEASE_MM:
                print(f"  the pads still read {rel:.2f} mm after two opens "
                      f"({RELEASE_MM:.1f} mm is clear), so the fingers are "
                      f"still pressing the bulb.  Winding back now would drag "
                      f"it out of its thread and break the glass, so this "
                      f"stops here with the scene intact.")
                break
            print(f"  released: the pads read {rel:.2f} mm "
                  f"(clear at {RELEASE_MM:.1f}).  {since_reset:.0f} deg turned "
                  f"this grasp, bulb {advance_mm:.3f} mm in by the pads")
            unwind(since_reset, sign)
            since_reset = 0.0
            if not close_on_bulb():
                print("  re-grasp did not take -- stopping")
                break

    # --- 4. Stop ---------------------------------------------------------------
    # The bar is the bulb being all the way in, so there is nothing left to do
    # but say what the run got to.  Upstream held a gripped command still at
    # the end to submit the episode; that gesture went with the criteria on
    # 2026-10-09 (see the LOCAL DEVIATION notes in
    # ViTaForge/envs/bulb_tightening.py), and the torque the hold used to chase
    # is no longer scored -- the same episode turns on the bulb's own depth and
    # seat load whenever the verdict is asked for.  Turning any further would
    # only spend wrist travel the run may not have.
    if bottomed or reached:
        print(f"the bulb is home ({'the fixture agrees' if reached else 'by the pads'}); "
              f"stopping here rather than winding the wrist further in")
    else:
        print("the pads never said the glass had stopped, so the run ends with "
              "the bulb's depth whatever it is -- the verdict is taken on the "
              "scene as it stands")

    check_success()
"""


class UniVTACBulbTighteningCodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE


class UniVTACBulbTighteningPerceptionCodeEnv(UniVTACBulbTighteningCodeEnv):
    """The bulb on the perception line (``UniVTACPerceptionApi``).

    Same task and the same tactile routine; one skill is withheld.
    ``bulb-grasp-pose`` tells the run to ask ``sample_grasp_pose`` for the
    bulb's grasp pose after every re-grasp, and that recipe is the server's
    ground-truth branch (``_GT_GRASP_RULES``, ``launch_univtac_server.py``).
    The perception API class turns that branch off (``_ground_truth_grasp =
    False``), so on this line the same call hands back the hand's own parked
    pose instead of the bulb's -- the 2026-10-05 failure, where the grasp
    landed 63 mm off the bulb and closing on air ended the episode.  The
    config that uses this class carries the perception-side grasp recipe in
    its own ``prompt:`` block instead.

    The tuple is the library's bulb routing minus that one skill.  It is
    written out rather than derived so the delivered set can be read off the
    class; `test_the_only_hook_override_is_the_perception_bulb_variant`
    fails if the two sets ever drift apart.
    """

    tactile_skills = (
        # act -- everything routed to the bulb except bulb-grasp-pose
        "bulb-close-force",
        "descent-until-contact",
        "feed-must-match-lead",
        "lattice-pitch-budget",
        "lead-by-overshoot",
        "mirrored-shear-slide",
        "one-call-approach",
        "preserve-existing-grasp",
        "reflex-stop-on-events",
        "seat-by-slide-collapse",
        "small-step-insertion",
        "thread-direction-by-legs",
        "wrist-limit-segmentation",
        # protocol
        "channel-selection",
        "differential-across-states",
        "grasp-verification",
        "no-absolute-without-reference",
        "regrasp-after-slip",
        "tactile-cannot-locate",
        # sense
        "confidence-gate",
        "contact-flag-is-range",
        "depth-grip-scale",
        "shear-vs-displacement",
        "slip-pair-baseline",
    )
