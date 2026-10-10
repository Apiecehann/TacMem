from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: move two objects into their slots, strictly in order:
  1. Pick up object_a and place it into slot_a.
  2. Pick up object_b and place it into slot_b.
Both objects must end up resting in their slots for the task to succeed.

Key rules:
- Work on object_a first.  Touching object_b before object_a is released in
  slot_a fails the task.
- object_b sits inside a recessed compartment with walls around it — that
  compartment is NOT its slot.  slot_b is the open pad next to the
  compartment.  Reach in from above and grasp object_b normally.
- Place both objects by the placement recipe in the tactile skills at the end
  of this prompt. What is scored is where each one ends up.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

ORACLE_CODE = """
import numpy as np

# Held-object centre height at release: objects rest at ~0.021 (4 cm-diameter
# cans, origin at centre).  Release at resting + release_z_clearance (0.018,
# the env's own value): descending further wedges the fingertips under the
# can's shoulder and the grip slips, dragging the can sideways.
RELEASE_Z = 0.039


def contacts_ok(fb):
    return fb["left_contact"] and fb["right_contact"]


def grasp_object(obj_name, grasp_pos, grasp_quat):
    \"\"\"Approach a grasp pose and close on the object; return (ok, offsets).\"\"\"
    goto_pose(grasp_pos, grasp_quat, z_approach=0.06)
    fb = close_gripper()
    if not contacts_ok(fb):
        open_gripper()
        fb = close_gripper(force_threshold=25.0)
    pos, _, _ = get_object_pose(obj_name)
    # EE offset relative to the object centre while held (get_ee_pose is not
    # available in the eval sandbox, so derive it from the grasp pose).
    offset_xy = np.asarray(grasp_pos)[:2] - pos[:2]
    offset_z = float(np.asarray(grasp_pos)[2] - pos[2])
    return contacts_ok(fb), offset_xy, offset_z


def place_object(held_obj, slot_name, grasp_quat, offset_xy, offset_z):
    \"\"\"Transport the held object over its slot, descend, and release.\"\"\"
    slot_pos, _, _ = get_object_pose(slot_name)
    hover_ee = np.array([slot_pos[0] + offset_xy[0],
                         slot_pos[1] + offset_xy[1],
                         RELEASE_Z + offset_z + 0.08])
    goto_pose(hover_ee, grasp_quat, z_approach=0.05)
    obj_pos, _, _ = get_object_pose(held_obj)
    while obj_pos[2] > RELEASE_Z + 0.002:
        step = min(0.01, obj_pos[2] - RELEASE_Z)
        move_delta(dz=-step)
        obj_pos, _, _ = get_object_pose(held_obj)
    open_gripper()
    move_delta(dz=0.08)


# ---- Object A: grasp, lift, place into slot_a ----
grasp_a, quat_a = sample_grasp_pose("object_a")
ok_a, off_xy_a, off_z_a = grasp_object("object_a", grasp_a, quat_a)
print(f"Grasp object_a ok={ok_a} offset_xy={np.round(off_xy_a, 3)} "
      f"offset_z={off_z_a:.3f}")
move_delta(dz=0.10)  # lift clear of the table
place_object("object_a", "slot_a", quat_a, off_xy_a, off_z_a)
rest_a, _, _ = get_object_pose("object_a")
print(f"object_a after release: pos={np.round(rest_a, 3)}")

# ---- Object B: same grasp geometry as object_a.
# sample_grasp_pose only knows the pre-moved object — the EE is far from
# object_b, so its cached offset would be wrong.  Instead reuse the offset
# measured for object_a (EE relative to the held object centre: fingertip
# length + grasp offset, independent of the object) with object_b's live
# pose.  The can is a symmetric upright cylinder, so the same quaternion
# stays valid.
pos_b, _, _ = get_object_pose("object_b")
grasp_b = np.array([pos_b[0] + off_xy_a[0],
                    pos_b[1] + off_xy_a[1],
                    pos_b[2] + off_z_a])
ok_b, off_xy_b, off_z_b = grasp_object("object_b", grasp_b, quat_a)
print(f"Grasp object_b ok={ok_b} at {np.round(grasp_b, 3)}")
move_delta(dz=0.10)
place_object("object_b", "slot_b", quat_a, off_xy_b, off_z_b)
rest_b, _, _ = get_object_pose("object_b")
print(f"object_b after release: pos={np.round(rest_b, 3)}")

# ---- Verify ----
success = check_success()
print(f"Success: {success}")
"""


class UniVTACTactileTransferCodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE
