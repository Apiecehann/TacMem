from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: exactly ONE red block is standing on the table. Decide by touch whether
it is soft or hard, then place it on the matching plate:
  - Soft block -> the blue_plate
  - Hard block -> the yellow_plate

In the scene the block is named "block" and the plates "blue_plate" and
"yellow_plate" - use those exact names with the pose and grasp helpers.

The catch: the other block is parked a metre away, out of reach, so there is no
reference object to compare against. The reading has to be absolute, taken the
same way every trial.

Your fingertips are the instrument: hardness is how much of your grip the block
passes on to the gel, and it only shows up as the gel's response to a CHANGE in
grip force, never as the depth of one close. Which two grips to compare, how to
set the squeeze, and the value to split them at are in the tactile skills at the
end of this prompt.

After classifying, carry the block to its plate. The placement recipe - how to
move a held block, where to stop, and whether to let go - is in those same
skills. This task is scored on where the block ends up: seat it on the right
plate, upright, and stop.

Final placement: within 4 cm horizontally and 1 cm vertically, upright within 15
degrees.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

ORACLE_CODE = """
import time

# Hardness is how much of the squeeze the block passes on to the gel, so it is
# read as the gel's RESPONSE to a change in grip force -- two closes at
# different positions -- and never from the depth of a single close.
# Measured on min(left, right) depth, n=4 per class:
#   hard 1.59-1.62 mm, soft 0.81-0.85 mm  ->  split at 1.2 mm.
#
# Position with adaptive=False, NOT force_threshold.  Two independent reasons:
#  - a soft block yields until the jaws bottom out at ~26.4 mm, so any
#    force_threshold firmer than ~26.5 is a target the servo cannot reach on it
#    and close_gripper fails outright;
#  - and with the depth servo left on it keeps pushing until those jaws do
#    bottom out, at which point the gel reads a firm JAM - which reverses the
#    sign of the reading, a soft block coming out firmer than a hard one.
# Commanding the finger travel and letting the block yield underneath it is
# what makes the direction come out right.
#
# For the same bottoming-out reason a single close at the weight-sensing depth
# of 27.7 shows nothing at all: both hardnesses land on the same depth there.
GRIP_LIGHT = 0.10
GRIP_FIRM = 0.06
COMPLIANCE_MM = 1.2
SETTLE_S = 0.4


def gel_depth():
    \"\"\"Deeper pad's depth; the two pads sit a roughly constant 0.3 mm apart.\"\"\"
    fb = get_tactile_data()
    return min(fb["left_depth_mm"], fb["right_depth_mm"])


grasp_pos, grasp_quat = sample_grasp_pose("block")
goto_pose(grasp_pos, grasp_quat, z_approach=0.05)

# 1. Light close: the block is pressed just enough to be felt, so nothing has
#    yielded yet and this is the reference the squeeze is measured against.
close_gripper(position=GRIP_LIGHT, adaptive=False)
time.sleep(SETTLE_S)
depth_light = gel_depth()

# 2. Firmer close: a hard block passes the extra squeeze through to the gel, a
#    soft one absorbs it in its own body.  The difference IS the hardness.
#    Firmer is the only direction available from here -- going back to a
#    lighter position right after a firm one reads nothing.
close_gripper(position=GRIP_FIRM, adaptive=False)
time.sleep(SETTLE_S)
depth_firm = gel_depth()

compliance = depth_light - depth_firm
label = "hard" if compliance > COMPLIANCE_MM else "soft"
print(f"gel response to squeeze={compliance:.3f}mm "
      f"(light {depth_light:.3f} -> firm {depth_firm:.3f}) -> {label}")


def move_by(dx=0.0, dy=0.0, dz=0.0):
    \"\"\"move_delta caps each axis at 0.10 m per call, so chunk long moves.

    It also has a 2 mm FLOOR: a smaller delta is rejected, not executed.  So
    stop once the remainder is under that, and leave the last millimetre or
    two unmoved -- the placement tolerance is 4 cm.
    \"\"\"
    for key, val in (("dx", dx), ("dy", dy), ("dz", dz)):
        while abs(val) > 0.002:
            step = max(-0.05, min(0.05, val))
            move_delta(**{key: step})
            val -= step


# --- Place on the matching plate ---
plate = "blue_plate" if label == "soft" else "yellow_plate"
plate_pos, _, plate_ext = get_object_pose(plate, return_bbox_extent=True)
rest_z = plate_pos[2] + plate_ext[2]          # plate top; origins sit at the bottom face

# Lift clear of the table BEFORE carrying sideways.  The block is 80 mm tall
# and stands on a 15 mm footprint, so dragging it horizontally while it is
# still resting on the table only tips it over: it slid ~1 cm of a commanded
# 7 cm and fell out of the grip.  Held in the air it follows the hand exactly.
for _ in range(3):
    move_delta(dz=0.01)
    time.sleep(0.2)

# Carry the block across with world-frame deltas: it is held rigidly, so moving
# the hand by d moves the block by d, with no need to know the hand's pose.
# Deliberately NOT goto_pose: re-commanding the wrist to an absolute
# orientation while the block touches down twists it inside the grip --
# measured 11-45 deg of tilt that way against 0.2-0.3 deg this way.
# Horizontal first (the block is well clear of the 6 mm plate), then down.
block_now, _, _ = get_object_pose("block")
move_by(dx=plate_pos[0] - block_now[0],
        dy=plate_pos[1] - block_now[1],
        dz=rest_z + 0.002 - block_now[2])

# Seat it: step the descent down until the block rests on the plate.
for _ in range(12):
    p, _, _ = get_object_pose("block")
    gap = p[2] - rest_z
    if gap <= 0.002:
        break
    move_delta(dz=-max(gap, 0.002))

# Stop here, still holding the block.  Do NOT open the gripper: when the jaws
# come apart they drag the block off the plate or tip it over.  The task is
# scored on where the block ends up, not on whether the hand let go, and
# ViTaForge's own expert places it the same way -- place_actor(is_open=False)
# with no open afterwards.
check_success()
"""


class UniVTACHardnessClassifyCodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE
