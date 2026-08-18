from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: Grasp the prism, use tactile sensing to determine whether its surface
texture is rough or smooth, and gently place it on the matching pad:
  - Rough texture → orange pad
  - Smooth texture → green pad

Key rules:
- Classify texture from get_tactile_data's marker-flow coherence: rough
  surfaces give LOW coherence, smooth surfaces give HIGH coherence (see the
  API docs for the value ranges).  Read coherence once the grasp is stable.
- Compute the placement height from the object poses/extents: prism and pads
  have their origin at the bottom face and bbox_extent is the full size, so
  rest the prism with position[2] = pad_pos[2] + pad_ext[2]. Verify the
  descent before opening the gripper — never release from a height.
- Final placement: within 2 cm horizontally and 1 cm vertically, upright
  within 15°.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

ORACLE_CODE = """
import numpy as np

def contacts_ok(fb):
    return fb["left_contact"] and fb["right_contact"]

# --- Grasp (retry harder if the first close missed) ---
grasp_pos, grasp_quat = sample_grasp_pose("prism")
goto_pose(grasp_pos, grasp_quat, z_approach=0.05)
fb = close_gripper()
if not contacts_ok(fb):
    open_gripper()
    fb = close_gripper(force_threshold=25.0)

# Lift clear of the table before probing
move_delta(dz=0.05)

# --- Texture probe: marker-flow coherence (rough ~0.55, smooth ~0.75) ---
fb = get_tactile_data()
coh = 0.5 * (fb["left_marker_coherence"] + fb["right_marker_coherence"])
texture = "rough" if coh < 0.65 else "plain"
print(f"coherence={coh:.3f} -> {texture}")

# --- Verify the prism is still held (re-grasp if probing dropped it) ---
prism_pos, _, prism_ext = get_object_pose("prism", return_bbox_extent=True)
fb = get_tactile_data()
if not contacts_ok(fb) or prism_pos[2] < 0.03:
    open_gripper()
    goto_pose(grasp_pos, grasp_quat, z_approach=0.05)
    close_gripper()
    prism_pos, _, prism_ext = get_object_pose("prism", return_bbox_extent=True)

# --- Place on the matching pad ---
# position = prim origin (bottom face for prism/pads), bbox = FULL size:
# pad top = pad_pos[2] + pad_ext[2].  Prism origin must rest on it.
target_pad = "orange_pad" if texture == "rough" else "green_pad"
pad_pos, _, pad_ext = get_object_pose(target_pad, return_bbox_extent=True)

ee_pos = np.array(grasp_pos) + np.array([0.0, 0.0, 0.05])
offset = ee_pos - prism_pos
target_prism = np.array([pad_pos[0], pad_pos[1],
                         pad_pos[2] + pad_ext[2] - 0.001])   # 1 mm press
goto_pose(target_prism + offset, grasp_quat, z_approach=0.1)

# Verify the descent before releasing (err low, never drop from a height)
for _ in range(10):
    p, _, _ = get_object_pose("prism")
    gap = p[2] - (pad_pos[2] + pad_ext[2])
    if gap <= 0.002:
        break
    move_delta(dz=-max(gap, 0.002))
open_gripper()
move_delta(dz=0.08)
check_success()
"""


class UniVTACGraspClassifyCodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE
