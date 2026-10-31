from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: pull the elastic strap to 12 newtons and hold it there for three
seconds, then pull it to 18 newtons and hold that for three seconds. A hold
counts only if it stays within half a newton of its target. The strap is the
only thing in the scene, and it is called "elastic_strap".

The simulation advances only while the hand is being commanded; waiting passes
no time at all, so a hold is made of commands.

The arm's own travel is not a measure of the load. How far this strap
stretches for a given force is a property of the strap, and it is different
every episode, so a plan made of distances cannot be this task. What does not
change is that the load passes through the fingers: the pads carry it, and the
gel is therefore the only thing here that can say how hard the strap is being
pulled.

The strap is elastic, so the load is set by where the hand is and by nothing
else - the pull raises it and backing off lowers it by as much. There is no
second lever and nothing to press against, and nothing is scored on arriving:
the score is the two holds.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

ORACLE_CODE = """
import math

# --- Where the tab is ---------------------------------------------------------
# The strap is added at base [0.55, 0, 0.005] and the tab -- the block at its
# top, the only part the pads can close on -- is centred 0.174 above that base
# (envs/tension_strap.py:561,578).  The wrist sits GRIPPER_OFFSET along the
# gripper centre's own +z (the tool points down), so the flange that puts the
# pads on the tab's centre is the centre plus that offset.
STRAP = "elastic_strap"
STRAP_BASE = (0.55, 0.0, 0.005)
TAB_LOCAL_Z = 0.174
GRIPPER_OFFSET_M = 0.131
APPROACH_CLEAR_M = 0.05

# --- The grip -----------------------------------------------------------------
# The task frees the strap from its initialization fixture only once BOTH pads
# have acquired the tab (envs/tension_strap.py:604-609); until then the strap
# stays pinned to its rest shape however hard it is pulled, so a pull measures
# the fixture rather than the strap.  The close is asked for the task's own
# depth, but the depth is NOT the test of whether the tab was taken: the
# release is read off the tactile manager's own minimum while the fingers are
# still moving, and the reading that settles afterwards is shallower -- both
# closes that worked returned 27.79 and 27.06 mm, on the wrong side of the
# task's own 27.7 mm line.  What separates a grasp from an empty close is the
# marker field: an empty close moves the markers a few thousandths of a pixel
# and a real grasp moves them several.
GRASP_DEPTH_MM = 27.6
CONTACT_PX = 1.0

# --- The two stages -----------------------------------------------------------
TARGETS_N = (12.0, 18.0)

# --- The gel as the force sensor ----------------------------------------------
# What carries the load is the tab slipping down between the pads, and what
# measures it is the gel's marker field.  The two pads are mounted mirrored,
# so the same physical slip appears with OPPOSITE sign in the two marker
# frames: as the strap is pulled, left_marker_shear_dx climbs and
# right_marker_shear_dx falls by the same amount, while anything that moves
# both the same way (a change of squeeze, a drift of the whole hand) cancels
# in the difference.
#
# Measured on seed 0 by raising the hand in 2 mm steps from the release point
# to 19.3 N: the difference is a straight line in tension at 1.031 N per px
# with -1.77 N of intercept, r^2 0.99992, residual 0.05 N -- a tenth of the
# half-newton band this task is scored on.  The same run's depth readings do
# not move at all (the tab is dragged along the pads, not pushed into them),
# and the mean marker displacement is only half as linear because the right
# pad's own displacement saturates near 9.3 px.
GEL_N_PER_PX = 1.031
GEL_OFFSET_N = -1.77

# --- How the hand moves -------------------------------------------------------
# A 2 mm rise is about 4 N on this strap, far too coarse to land inside
# +-0.5 N, so each stage is a coarse run that measures the strap's own
# stiffness, one secant jump, and a short correction loop.  The stiffness is a
# property of the seed -- E is drawn from U(0.25, 0.40) MPa -- which is why it
# is measured here instead of written down: seed 0 came out at 2.12 N/mm and
# the next seed needs whatever it needs.
COARSE_M = 0.002
COARSE_STOP_MARGIN_N = 2.5
MAX_CORRECTIONS = 6
CORRECTION_CAP_M = 0.004
SETTLE_N = 0.15

# --- The hold -----------------------------------------------------------------
# A hold is not a sleep.  Nothing advances the simulation but a motion call, so
# holding means asking for the pose the hand already holds, over and over, and
# the hold is counted in those calls.  One such call advanced the episode by
# 41 physics ticks -- a 31-step plan plus the 10 the server always delays --
# against a 120 Hz clock, so a 3 s stage is 360 ticks and lands inside ten
# calls.  Fourteen is that with room, and overshooting is harmless (the stage
# is already counted) while undershooting loses the run.
#
# The hold does NOT close a loop, and that is deliberate.  Standing still, the
# strap keeps its load: measured over eight of these calls, the tension fell
# 0.05 N in 330 ticks, so 3 s costs about a tenth of a newton and a stage that
# starts on target stays on target.  The gel, however, does not stand still --
# the tab keeps creeping between the pads, and the same eight calls moved the
# marker difference by 0.47 px, half a newton of reading for no change in load.
# A loop that believed that would pull the hand down and turn a good hold into
# a real release.  The creep is handled where it belongs: the reading is
# re-anchored after each hold, at a point where the load is known.
HOLD_MOVES = 14


def pads(reply):
    \"\"\"(left, right) gel depth in mm from a motion reply, or None.\"\"\"
    left, right = reply.get("left_depth_mm"), reply.get("right_depth_mm")
    if left is None or right is None:
        return None
    return float(left), float(right)


def taken(reply):
    \"\"\"Whether both pads are actually on the tab, by the markers, not the depth.\"\"\"
    left = reply.get("left_marker_displacement_px")
    right = reply.get("right_marker_displacement_px")
    if left is None or right is None:
        return False
    return float(left) > CONTACT_PX and float(right) > CONTACT_PX


def slip_px(reply):
    \"\"\"The signed slip of the tab between the pads, in marker pixels.\"\"\"
    left = reply.get("left_marker_shear_dx")
    right = reply.get("right_marker_shear_dx")
    if left is None or right is None:
        return None
    return float(left) - float(right)


def tension_n(reply, offset=GEL_OFFSET_N):
    \"\"\"The strap's load as the gel measures it, in newtons.\"\"\"
    slip = slip_px(reply)
    return None if slip is None else GEL_N_PER_PX * slip + offset


# --- 1. The hand's own orientation (the only thing worth sampling here) ------
# sample_grasp_pose's first call returns the hand's own pose.  The strap's own
# pose is the base of a body that deforms, not a point to hang a grasp off, so
# what this is for is the quaternion.
_, q0 = sample_grasp_pose(STRAP)

tab = (STRAP_BASE[0], STRAP_BASE[1], STRAP_BASE[2] + TAB_LOCAL_Z)
grasp_z = tab[2] + GRIPPER_OFFSET_M
xy = (tab[0], tab[1])
print("tab at %s, flange target z %.4f" % (tab, grasp_z))

# --- 2. Take the tab ----------------------------------------------------------
goto_pose((xy[0], xy[1], grasp_z), q0, z_approach=APPROACH_CLEAR_M)
try:
    reply = close_gripper(force_threshold=GRASP_DEPTH_MM, adaptive=True)
except Exception as error:
    # A close that stopped short still leaves the fingers where the tab
    # stopped them, and the reading that comes back with the error describes
    # that state.  Losing the trial over the exception would throw away the
    # only information it carries.
    print("close raised: %s -- reading the stopped fingers instead" % error)
    reply = get_tactile_data()
held = taken(reply)
print("grip depths %s, tab taken: %s" % (pads(reply), held))

# One dict rather than four globals: every helper below writes the hand's
# height, the stiffness it has measured and the last gel reply, and a plain
# module-level assignment inside a function would shadow instead of update it.
state = {"z": grasp_z, "slope": None, "reply": reply, "offset": GEL_OFFSET_N}


def go(z):
    \"\"\"Move the hand to height z, remember the reply, and return it.\"\"\"
    state["z"] = float(z)
    state["reply"] = goto_pose((xy[0], xy[1], state["z"]), q0)
    return state["reply"]


def raise_to(target):
    \"\"\"Bring the gel reading up to `target` newtons, and report where it landed.

    The coarse steps exist to measure the strap, not to close in: the first is
    a fixed 2 mm, and every later one is sized from the stiffness the previous
    step revealed so that it lands just under the target.  The corrections
    then re-fit that stiffness from their own jumps, so a strap that stiffens
    as it stretches is followed instead of fought.
    \"\"\"
    start = tension_n(state["reply"], state["offset"])
    est = start
    if est is None:
        return None
    for step_index in range(12):
        if est >= target - COARSE_STOP_MARGIN_N:
            break
        # A strap still held by its fixture does not load no matter how far
        # the hand rises, so after three rises that bought nothing the reading
        # is about the fixture and the rest of the episode would only be a
        # longer way of measuring the same nothing.
        if step_index >= 3 and est - start < 0.5:
            print("the strap is not following the hand -- it never left its fixture")
            return None
        if state["slope"] and state["slope"] > 0.0:
            delta = (target - COARSE_STOP_MARGIN_N - est) / state["slope"]
            delta = min(max(delta, 0.0002), 0.005)
        else:
            delta = COARSE_M
        est_next = tension_n(go(state["z"] + delta), state["offset"])
        if est_next is None:
            return None
        state["slope"] = (est_next - est) / delta
        est = est_next
    for _ in range(MAX_CORRECTIONS):
        error = target - est
        if abs(error) <= SETTLE_N:
            break
        slope = state["slope"] if state["slope"] else 2000.0
        delta = min(max(error / slope, -CORRECTION_CAP_M), CORRECTION_CAP_M)
        est_next = tension_n(go(state["z"] + delta), state["offset"])
        if est_next is None:
            return None
        if abs(delta) > 1e-6:
            state["slope"] = (est_next - est) / delta
        est = est_next
    return est


def hold(moves):
    \"\"\"Spend `moves` motion calls at the height the hand already holds.\"\"\"
    for _ in range(moves):
        go(state["z"])
    return tension_n(state["reply"], state["offset"])


def reanchor(at_target):
    \"\"\"Re-zero the gel reading at a load it is safe to call known.

    The tab creeps between the pads while the strap is loaded even though the
    load itself does not change, so the marker difference keeps climbing at a
    fixed rate and any estimate built on an absolute line drifts with it.  At
    the end of a hold the load is the one just held -- that is what the hold
    was -- so the offset is moved to make the reading say so, and the next
    stage starts from a zero that the creep has not walked away from.
    \"\"\"
    slip = slip_px(state["reply"])
    if slip is not None:
        state["offset"] = at_target - GEL_N_PER_PX * slip


if not held:
    print("ABORT: the pads did not reach the tab; a pull here measures nothing")
else:
    for index, target in enumerate(TARGETS_N):
        landed = raise_to(target)
        if landed is None:
            print("gel reading went missing; stopping")
            break
        print("stage %d: %.2f N at z %.4f (slope %.0f N/m)"
              % (index + 1, landed, state["z"], state["slope"] or 0.0))
        hold(HOLD_MOVES)
        held_n = tension_n(state["reply"], state["offset"])
        print("stage %d held; reading now %.2f N"
              % (index + 1, -99.0 if held_n is None else held_n))
        reanchor(target)
    print("episode finished; the harness scores it")
"""


class UniVTACTensionStrapCodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE
