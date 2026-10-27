from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: wipe the stains off the porcelain vase with the sponge. The sponge rests
on the table; the vase is fixed in place and lies on its side. Use the exact
names "wiping_sponge" and "porcelain_vase" with the pose and grasp helpers.

How the wiping is judged is not only how much stain comes off. The load the
sponge carries while it moves is judged too, and staying inside the load the
task asks for is part of the job rather than a safety margin on it - so
leaning harder is not a way to finish sooner.

The sponge is soft and has to be picked up before any of this: a grip that
only pinches it leaves it on the table, and nothing downstream works if it
does. What the pads read after the close is what says whether it was taken.

Contact is the whole task. The sponge cleans only where it is actually
touching the vase, and the pads are what tell you it is - the sponge riding
the surface and the sponge hanging in the air read differently, and so do a
sponge on the wall and a sponge skidding off it.

The wall is curved and its radius changes from one end of the vase to the
other, so a stroke that follows it is not a straight line in the world.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

ORACLE_CODE = """
import math
import numpy as np

# --- The two objects, by the names the scene knows them by --------------------
SPONGE = "wiping_sponge"
VASE = "porcelain_vase"

# --- The sponge, from its own mesh -------------------------------------------
# foam.npz sits on its origin and measures 0.052 x 0.028 x 0.042 m, so the pose
# `get_object_pose` reports is the centre of its BOTTOM face -- which is also
# the face that does the wiping.  The task grasps it 0.031 above that origin
# (envs/wipe_vase.py:805, grasp = foam_base + [0,0,.031]), so the pad hangs
# 0.031 below the gripper centre and the sponge's top face stays clear of it.
SPONGE_GRASP_LOCAL_Z = 0.031
# End effector to gripper centre: the centre is GRIPPER_OFFSET along the EE's
# own -z (envs/robot/robot.py:90 builds the offset, :285 applies it in the
# local frame).  For a tool pointed down at the table that puts the flange
# 0.131 above the gripper centre.
GRIPPER_OFFSET_M = 0.131
# The task's own grasp depth, in gel mm: envs/wipe_vase.py:809 closes to
# params['grasp_depth_mm'] (27.2).  Depth falls as the grip hardens, so this is
# a target, not a limit.
GRIP_DEPTH_MM = 27.2
# The simulator frees the sponge from its animation only when BOTH pads read at
# or under grasp_depth_mm + 0.1 (envs/wipe_vase.py:692-697).  That release is
# the only thing that makes the sponge come off the table, and nothing in the
# API reports it, so the two depths that come back from the close are the whole
# check.  A close that finds nothing leaves the pads at their no-contact
# reading (~34 mm) and the sponge stays behind.
SPONGE_FREE_MM = 27.3
SPONGE_ABOVE_M = 0.07
SPONGE_LIFT_M = 0.18
# A grasp slip measured off the sponge's own pose is only believed below this:
# past it the reading is a stale pose rather than a pad that let the sponge
# shift, and the nominal geometry is the safer thing to believe.
GRASP_SLIP_LIMIT_M = 0.02

# --- The vase's own profile ---------------------------------------------------
# (z, r) pairs lifted from assets/objects/task_assets/vase_wiping/vase.npz.
# np.gradient over this window is the full profile's gradient everywhere inside
# it, since the differences are central; the two ends, where it is not, sit at
# 0.0298 and 0.1105 -- outside every height wiped here.
VASE_PROFILE_Z = (
    0.02975, 0.03188, 0.03400, 0.03613, 0.03825, 0.04038, 0.04250, 0.04463,
    0.04675, 0.04888, 0.05100, 0.05313, 0.05525, 0.05738, 0.05950, 0.06163,
    0.06375, 0.06588, 0.06800, 0.07013, 0.07225, 0.07438, 0.07650, 0.07863,
    0.08075, 0.08288, 0.08500, 0.08713, 0.08925, 0.09138, 0.09350, 0.09563,
    0.09775, 0.09988, 0.10200, 0.10413, 0.10625, 0.10838, 0.11050,
)
VASE_PROFILE_R = (
    0.04732, 0.04797, 0.04855, 0.04899, 0.04937, 0.04971, 0.04991, 0.05009,
    0.05018, 0.05026, 0.05029, 0.05028, 0.05022, 0.05009, 0.04991, 0.04966,
    0.04935, 0.04900, 0.04854, 0.04792, 0.04730, 0.04662, 0.04578, 0.04479,
    0.04369, 0.04254, 0.04122, 0.03983, 0.03827, 0.03661, 0.03491, 0.03317,
    0.03146, 0.02963, 0.02781, 0.02608, 0.02452, 0.02306, 0.02169,
)

# --- Where the wipe happens ---------------------------------------------------
# The task sweeps between these two heights at azimuths 0 and 0.14 rad off the
# vase's top (envs/wipe_vase.py:885, wipe_lane_angles_rad in wipe_vase.yml).
H_MIN, H_MAX = 0.038, 0.095
H_CONTACT = 0.043
LANES = (0.0, 0.14)
# The sponge's bottom face sits GRIPPER_OFFSET + SPONGE_GRASP_LOCAL_Z below the
# EE when the tool points at the wall, so the EE that lands that face exactly on
# the surface is the surface point plus this much along the outward normal --
# and `indent` is how far past that point it is driven.  That is the whole
# contact geometry of this task: there is no force reading anywhere in the API,
# so the depth of the press is the only thing a call can ask for.
STANDOFF_M = GRIPPER_OFFSET_M + SPONGE_GRASP_LOCAL_Z

# --- The press ----------------------------------------------------------------
# The sponge is a Neo-Hookean solid whose modulus is drawn per seed,
# E = U(0.12, 0.30) MPa (envs/wipe_vase.py:331) -- a 2.5x spread -- so the
# depth that lands a given load is a different number on every seed, and the
# API carries no force reading to close a loop with.  What the first round
# measured on seed 6 (E = 0.123 MPa, the softest draw) is a straight line: the
# vase's normal load climbs 9.3 N per mm of commanded indentation and starts
# about 0.4 mm before the commanded zero, so 0.40 mm stands at about 7.4 N and
# drags at about 8 N.  That is the task's 8.0 N target force, sitting inside the
# band it scores -- [5.0, 10.0] N since 2026-10-14, widened from the upstream
# [7.2, 8.8] for the reason the last section of univtac_wipe_vase.yaml records
# -- and clear of the 10.5 N above which the glaze starts to abrade -- the first
# round wiped at 0.8 mm, which is 11-13 N, and latched a physical failure in
# the first second of the first stroke.  On the stiffest sponge the same
# 0.40 mm would be about 18 N, since load scales with modulus: this number is
# a calibration for this seed, not a universal one, and the ladder is what
# re-measures it on the next.
# The ladder is a measurement: every rung stops on the same spot, so the trace
# carries the load against the commanded depth and nothing else.  Its deepest
# rung is the wipe press itself, so the depth the whole episode runs at is the
# one depth the ladder actually measured -- and it stops there rather than
# climbing, because the next thing up the line is the 10.5 N at which the glaze
# work starts to accumulate, and staying under that is the whole point.
PRESS_LADDER_M = (0.0001, 0.00025, 0.0004)
WIPE_INDENT_M = PRESS_LADDER_M[-1]
# Standoffs the approach stops at on its way in, held clear of contact.
APPROACH_GAPS_M = (0.040, 0.008)
# Clearance the pad is pulled back to on its way in and on its way out, so it
# leaves and rejoins the wall along the normal instead of sliding along it.  A
# lane change is what a pad still pressed in must never do: dragging one across
# the shoulder, where the wall stands at 40 deg, put 31 N on the vase in the
# first round.
CLEAR_GAP_M = 0.008
# Standoff the lane change itself is flown at.  The pad hangs 162 mm below the
# wrist, so a lane change is not a 57 mm pad move: it is a 230 mm end-effector
# move that turns the tool 45 deg on the end of that lever, and the arm cuts the
# corner.  Measured on the third round's own trace, before the pad had touched
# anything it was already 4-6 mm below the straight line it had been given.
# Flown at 8 mm out the transit then only ever held 2.2 mm of real clearance,
# which is why the pad went through the wall at 47 N there; at 100 mm it holds
# 66 mm, more than twice the corner the arm cuts on the way.
TRANSIT_STANDOFF_M = 0.100
# Standoff the second stroke stops at on its way in, so the pad meets the wall
# with a short step rather than a 100 mm plunge.
PRESS_GAP_M = 0.002
# Waypoints per stroke.  The expert servos continuously at 120 Hz; a plan-and-
# move API cannot, so each stroke is a chain of poses dense enough that the
# sponge is never off the wall between two of them.  The fifth round doubles
# the chain -- 12 poses per stroke to 22, 4.7 mm apart to 2.5 -- because the
# 2-5 N stop-start oscillation in the load was blamed on the arm cutting the
# chord between waypoints, and the chord error scales with the spacing.
# Three offline checks on the fourth round's own trace say that is not what the
# oscillation is, so this round is an honest falsification test rather than a
# fix.  (a) The commanded path is already exact: comparing the straight
# end-effector line the arm is given between waypoints against the ideal
# continuous path puts the penetration error at +0.000 mm at 12, 22 and 42
# poses, with the orientation error only 0.86, 0.33 and 0.17 deg -- there is no
# corner left to cut.  (b) The drag is not viscous: regressing the trace's
# tangential force on the pad's own speed gives 0.023 N per mm/s over a median
# of 6 mm/s and bucket means that are flat from 2.9 N at 0.1 mm/s to 3.8 N at
# 40, so a shorter, slower move unloads nothing.  (c) The oscillation is in
# phase with the stops, not with the spacing: at each dwell the arm settles
# 0.4 mm short of the commanded pose and the pad reads 7.2 N, and 0.2 mm into
# the next move it is back on the line and the pad reads 9.3 N.
# So the prediction is neutral at best and likelier worse, twice the stops
# being twice the restart transient.  It came out worse, and the round's own
# trace says how much a stop costs: out-of-band sliding runs about 1.3 mm per
# stop, half of it above 8.8 N and half below 7.2.  Round 4 stopped 24 times
# and held the band for 58.3% of its 114 mm; round 5 stopped 44 times and held
# it for 51.6% of its 113.  Doubling the stops doubled the sub-band sliding
# (10.5% -> 24.4%) and bought a collapse in the other direction for it
# (above 10.5 N: 6.6% -> 0.3%, peak 12.41 -> 10.58 N), so the abrasion risk is
# gone and the arithmetic is unchanged: 95% of 113 mm leaves 5.7 mm to spend,
# which is about four stops in the whole episode.
STROKE_STEPS = 21


def rot_x(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def rot_y(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def quat_to_rot(q):
    \"\"\"3x3 rotation from a WXYZ quaternion.\"\"\"
    w, x, y, z = [float(v) for v in q]
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def mat_to_quat(m):
    \"\"\"WXYZ from a 3x3 rotation, by Shepperd's method.\"\"\"
    trace = float(m[0, 0] + m[1, 1] + m[2, 2])
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        return np.array([0.25 * s, (m[2, 1] - m[1, 2]) / s,
                         (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s])
    if m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        return np.array([(m[2, 1] - m[1, 2]) / s, 0.25 * s,
                         (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s])
    if m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        return np.array([(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s,
                         0.25 * s, (m[1, 2] + m[2, 1]) / s])
    s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
    return np.array([(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s,
                     (m[1, 2] + m[2, 1]) / s, 0.25 * s])


def profile(height):
    \"\"\"Radius of the vase and its slope along the axis, at one height.\"\"\"
    z = np.asarray(VASE_PROFILE_Z)
    r = np.asarray(VASE_PROFILE_R)
    return float(np.interp(height, z, r)), float(np.interp(height, z, np.gradient(r, z)))


def surface_frame(height, lane, base):
    \"\"\"Point, orientation and outward normal of the vase's upper wall.

    The vase lies on its side: the task rotates it by +90 deg about x
    (envs/wipe_vase.py:341), which sends its own [x, y, z] to [x, -z, y], so a
    point at azimuth `lane` and height `height` is
    base + [-r sin(lane), -height, r cos(lane)] and its axis runs along world
    -y.  The frame's +z is the wall's outward normal, tilted by the profile's
    slope so that it stays perpendicular to the wall as the radius changes.
    \"\"\"
    radius, slope = profile(height)
    rot = rot_y(-lane) @ rot_x(-math.atan(slope))
    point = np.asarray(base, dtype=float) + rot_x(math.pi / 2.0) @ np.array(
        [-radius * math.sin(lane), radius * math.cos(lane), height])
    return point, rot, rot[:, 2]


def ee_target(height, lane, indent, base, grip, R0):
    \"\"\"The pose to send the EE to, to put the sponge's face `indent` into the wall.

    `grip` is the sponge's bottom face measured off the EE while the hand held
    it level, as a world-frame vector -- the same quantity the task itself
    measures (envs/wipe_vase.py:822, grip_offset = foam centre - EE).  The
    expert then rotates that vector by the surface frame alone rather than by
    the full frame change, which is right because the vector was taken in the
    lift orientation: rot @ grip is exactly the vector the hand carries into
    the new orientation.  So the EE that lands the face on the surface point is
    that point minus the rotated offset, and `indent` drives it past.
    \"\"\"
    point, rot, normal = surface_frame(height, lane, base)
    return point - rot @ grip - indent * normal, mat_to_quat(rot @ R0)


def pads(fb):
    \"\"\"(left depth, right depth) in mm from a motion reply, or None if absent.\"\"\"
    left, right = fb.get("left_depth_mm"), fb.get("right_depth_mm")
    if left is None or right is None:
        return None
    return float(left), float(right)


def squeeze(position, rot, normal, grip):
    \"\"\"How far the sponge's own pose has risen out of the hand, in mm.

    The vase pushes the pad back and the sponge squashes between the wall and
    the fingers, so the pose the simulator reports for it -- a rigid fit of the
    deformed mesh onto its undeformed self (ViTaForge/envs/utils/actor.py:205)
    -- moves as the press gets harder.  It was printed for a round on the
    theory that this makes it the force sensor the API otherwise lacks.  The
    fifth round's trace says no, and the arithmetic is worth keeping:

      the pad's distance to the *actual* end effector is a real proxy -- over
      the fourth round's in-contact rows it tracks the normal force at
      r = -0.93 with 0.53 N of residual, against the 0.8 N half-width the band
      had then -- but the oracle cannot form it, because the API carries no end-effector
      pose (the server has one; it is deliberately not in functions());

      what the oracle *can* form is this: the sponge's reported pose minus the
      pose it was commanded to.  That is the compression *plus* the arm's own
      tracking error, and the two run in opposite directions (the arm sags
      0.44 mm off the wall at a dwell and the load falls; the pad squashes into
      the hand and the load rises), so they cancel.  Measured: r = -0.24 on the
      fourth round's trace, -0.12 on the fifth, residual 1.4 and 1.1 N.  Against
      the 0.8 N half-band those rounds were scored on, not a sensor.

    Printed, not acted on, and the number above is why the next round should
    not close a loop on it.
    \"\"\"
    now = np.asarray(get_object_pose(SPONGE)[0], dtype=float)
    return float(np.dot(now - (np.asarray(position, dtype=float) + rot @ grip),
                        normal)) * 1000.0


def report(note, fb):
    depths = pads(fb)
    if depths is None:
        print("  %-18s no reading" % note)
    else:
        print("  %-18s depth L %.2f R %.2f mm | contact L %s R %s | slide L %.2f R %.2f px"
              % (note, depths[0], depths[1],
                 fb.get("left_contact"), fb.get("right_contact"),
                 float(fb.get("left_marker_displacement_px", 0.0)),
                 float(fb.get("right_marker_displacement_px", 0.0))))


# --- 1. Where the sponge is, and how the hand is oriented over it ------------
# sample_grasp_pose is the hand's offset from the object taken on its first
# call, which is here -- so its position is wherever the hand already stands
# and only its orientation is worth having.  The sponge's own origin comes from
# get_object_pose instead, which is the object's real pose.
_, q0 = sample_grasp_pose(SPONGE)
R0 = quat_to_rot(q0)
foam = np.asarray(get_object_pose(SPONGE)[0], dtype=float)
print("sponge origin (bottom face) at %s" % np.round(foam, 4))
print("grasp orientation: EE +z in world = %s" % np.round(R0[:, 2], 3))

# --- 2. Grasp it, and believe only what the pads say about the grip ----------
grasp_centre = foam + np.array([0.0, 0.0, SPONGE_GRASP_LOCAL_Z])
grasp_ee = grasp_centre + R0 @ np.array([0.0, 0.0, -GRIPPER_OFFSET_M])
goto_pose(grasp_ee + np.array([0.0, 0.0, SPONGE_ABOVE_M]), q0)
goto_pose(grasp_ee, q0)
closed = close_gripper(force_threshold=GRIP_DEPTH_MM, adaptive=True)
report("at_sponge", closed)

# Both pads at or past the release depth means the simulator let the sponge go,
# and only then does lifting it do anything.  A sponge still held by its own
# animation is not on the pads, so going on to press a phantom into the vase
# would measure nothing while looking exactly like a wipe.
depth = pads(closed)
held = depth is not None and max(depth) <= SPONGE_FREE_MM
print("sponge released to physics: %s%s"
      % (held, "" if held else "  <-- grip did not take; the wipe below is not real"))
goto_pose(grasp_ee + np.array([0.0, 0.0, SPONGE_LIFT_M]), q0)

# --- 3. Where the sponge sits in the hand now that it is free ----------------
# Nominal: the face hangs 0.162 below the flange (0.031 from the gripper centre
# to the sponge's origin, plus the 0.131 from the centre up to the EE), so the
# offset from the EE down to the face is [0, 0, -0.162].  If the sponge shifted
# in the pads, its own pose says by how much and the dive is corrected by that;
# a reading too large to be a shift in the fingers is a stale pose and is
# thrown away.
lift_centre = grasp_centre + np.array([0.0, 0.0, SPONGE_LIFT_M])
lift_ee = lift_centre + R0 @ np.array([0.0, 0.0, -GRIPPER_OFFSET_M])
sponge_now = np.asarray(get_object_pose(SPONGE)[0], dtype=float)
lifted = sponge_now - foam - np.array([0.0, 0.0, SPONGE_LIFT_M])
print("sponge moved with the hand: %s (expected [0 0 %.2f])"
      % (np.round(sponge_now - foam, 4), SPONGE_LIFT_M))
grip = np.array([0.0, 0.0, -STANDOFF_M])
measured = float(np.linalg.norm(lifted)) <= GRASP_SLIP_LIMIT_M
if measured:
    grip = sponge_now - lift_ee
    print("  grasp offset measured from the sponge: %s (nominal %s)"
          % (np.round(grip, 4), np.round([0.0, 0.0, -STANDOFF_M], 4)))
else:
    print("  sponge pose does not track the hand; using the nominal offset")

# --- 4. Bring the pad down onto the vase -------------------------------------
# The vase is kinematic, so its base is fixed; it is read once here and again
# before each stroke, since every target is built off it.
base = np.asarray(get_object_pose(VASE)[0], dtype=float)
print("vase base at %s" % np.round(base, 4))
# The measured offset makes the reach below correct at any wrist orientation,
# because it was taken in the orientation the wrist is in.  Only the nominal
# constant needs the wrist to be pointing its -Z at the sponge, so only that
# combination is worth refusing.
if not held or (not measured and R0[2, 2] > 0.0):
    print("ABORT: no grip on the sponge, or the hand is not over it and its "
          "offset is a guess -- pressing now would drive the flange into the vase")
else:
    for gap in APPROACH_GAPS_M:
        position, quat = ee_target(H_CONTACT, LANES[0], -gap, base, grip, R0)
        report("gap %.3f" % gap, goto_pose(position, quat))

    # --- 5. Press, one rung at a time, on one spot ---------------------------
    # Same height and azimuth for every rung: the only thing that changes is how
    # far past the surface the pad is driven, so the trace reads as a force
    # against indentation curve and nothing else.
    for indent in PRESS_LADDER_M:
        position, quat = ee_target(H_CONTACT, LANES[0], indent, base, grip, R0)
        report("indent %.4f" % indent, goto_pose(position, quat))

    # --- 6. Wipe ------------------------------------------------------------
    # Two strokes and both climb.  The second round wiped the expert's own shape
    # -- one lane up, the next lane back down (envs/wipe_vase.py:883-916) -- and
    # the two turned out not to be the same job: the climbing stroke held
    # 6.9-10.2 N and never once reached the 10.5 N at which the glaze starts to
    # abrade, while the descending one spiked to 15.5 N and carried 34 of its
    # 60 mm of sliding above the band.  Both strokes stop at every waypoint and
    # start again, so the stop-start is not what separates them -- dragging the
    # pad down the shoulder is.  The second stroke therefore climbs as well,
    # which is what the expert's own third stroke does.
    # The press depth is held for both; the surface frame is rebuilt at every
    # waypoint, so the pad follows the wall's curvature instead of cutting
    # through it.  The expert's fourth and later passes are dropped: past the
    # first two the cleaned fraction moved 0.004, for another two and a half
    # minutes of dragging a loaded pad around -- and every millimetre of that
    # drag is a millimetre the load has to stay inside the band for.
    strokes = ((LANES[0], H_CONTACT, H_MAX), (LANES[1], H_MIN, H_MAX))
    for index, (lane, first, last) in enumerate(strokes):
        base = np.asarray(get_object_pose(VASE)[0], dtype=float)
        if index:
            # Straight out of the wall first -- the pad is still pressed in where
            # the last stroke left it -- then one move to the far end of the lane
            # change, then straight in.  Both ends of that move stand
            # TRANSIT_STANDOFF_M off the wall, so the corner the arm cuts getting
            # from one to the other has room for it.
            out, quat = ee_target(H_MAX, LANES[0], -TRANSIT_STANDOFF_M,
                                  base, grip, R0)
            report("back off lane %.2f to %.0f mm"
                   % (LANES[0], TRANSIT_STANDOFF_M * 1000.0), goto_pose(out, quat))
            out, quat = ee_target(first, lane, -TRANSIT_STANDOFF_M, base, grip, R0)
            report("cross to lane %.2f h %.3f" % (lane, first), goto_pose(out, quat))
            gap, quat = ee_target(first, lane, -PRESS_GAP_M, base, grip, R0)
            report("press gap %.3f" % PRESS_GAP_M, goto_pose(gap, quat))
        for step in range(STROKE_STEPS + 1):
            height = first + (last - first) * step / float(STROKE_STEPS)
            _, rot, normal = surface_frame(height, lane, base)
            position, quat = ee_target(height, lane, WIPE_INDENT_M, base, grip, R0)
            fb = goto_pose(position, quat)
            print("  stroke %d wp %2d h %.3f squeeze %+.3f mm"
                  % (index + 1, step, height,
                     squeeze(position, rot, normal, grip)))
            if step % 5 == 0 or step == STROKE_STEPS:
                report("stroke %d h %.3f" % (index + 1, height), fb)
        print("  stroke %d done (lane %.2f, %.3f -> %.3f at %.4f m in)"
              % (index + 1, lane, first, last, WIPE_INDENT_M))

    # Off the wall the way it came on: straight back along the normal, and only
    # then away.  Sliding out sideways with the pad still pressed in is the same
    # drag the lane change above is pulled clear to avoid.
    clear, quat = ee_target(H_MAX, LANES[1], -CLEAR_GAP_M, base, grip, R0)
    report("lift_clear", goto_pose(clear, quat))
print("wipe episode finished; the harness scores it")
"""


class UniVTACWipeVaseCodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE
