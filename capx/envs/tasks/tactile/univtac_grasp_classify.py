from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: Grasp the prism, use tactile sensing to determine whether its surface
texture is rough or smooth, and gently place it on the matching pad:
  - Rough texture → orange pad
  - Smooth texture → green pad

Key rules:
- Classify texture from the gel's marker-image anisotropy (get_tactile_data):
  a rough surface imprints the gel with horizontal banding, so the row/column
  gradient ratio climbs as the grip firms, while a smooth surface stays near
  isotropic.  How far it climbs depends on how hard the gel is pressed, so
  read it at two COMMANDED grip depths and compare the two — see the API docs
  for the calibrated numbers.
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

def aniso(fb):
    # A dropped render makes one pad read None; fall back to the other, and to
    # isotropic (the plain side) if both are gone, rather than raising.
    v = [fb["left_marker_anisotropy"], fb["right_marker_anisotropy"]]
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else 1.0

def depth(fb):
    return 0.5 * (fb["left_depth_mm"] + fb["right_depth_mm"])

def contacts_ok(fb):
    return fb["left_contact"] and fb["right_contact"]

# --- Grasp at a light commanded grip (retry firmer if the first close missed) ---
grasp_pos, grasp_quat = sample_grasp_pose("prism")
goto_pose(grasp_pos, grasp_quat, z_approach=0.05)
fb = close_gripper(force_threshold=27.5)
if not contacts_ok(fb):
    open_gripper()
    fb = close_gripper(force_threshold=25.0)

# Lift clear of the table before probing
move_delta(dz=0.05)

# --- Texture probe: marker anisotropy at a light vs a firm grip ---
# Rough => the gel picks up the ridges as horizontal banding, so the row/column
# gradient ratio climbs steeply with gel depth (0.10-0.13 per mm); plain stays
# near isotropic (0.01-0.03 per mm).  Taking the CHANGE between two commanded
# grips cancels wherever the first close happened to land.
light = get_tactile_data()
close_gripper(force_threshold=20.5)
firm = get_tactile_data()

dd = depth(light) - depth(firm)          # depth falls as the grip firms
da = aniso(firm) - aniso(light)
if dd > 1.0:
    slope = da / dd
    texture = "rough" if slope > 0.06 else "plain"
    print(f"aniso {aniso(light):.3f} -> {aniso(firm):.3f} over {dd:.2f} mm "
          f"= {slope:.3f}/mm -> {texture}")
else:
    # The second close did not move the gel (fingers pinned): the difference
    # carries no information, so fall back to the single-reading calibration
    # for the depth band the grip actually landed in.
    d = depth(firm)
    if d < 25.0:
        texture = "rough" if aniso(firm) > 1.38 else "plain"
    else:
        texture = "rough" if aniso(firm) > 1.07 else "plain"
    print(f"grip pinned (dd={dd:.2f} mm, depth={d:.2f}); single reading "
          f"{aniso(firm):.3f} -> {texture}")

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
    # Optional API-doc sections this task's LLM needs; the rest of the
    # shared UniVTACTactileApi doc is pruned.  See DOC_SECTION_TAGS.
    api_doc_sections = ("texture", "servo")
