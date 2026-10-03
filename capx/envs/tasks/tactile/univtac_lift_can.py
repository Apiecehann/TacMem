from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: Grasp the can lying on its side and rotate it upright so it stands on
the table, then release it.

The robot is already beside the can — grasp directly without repositioning.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

ORACLE_CODE = """
import numpy as np

close_gripper()
rotate_gripper("can", 1.5, steps=6)  # rotate in place, around can's axis
open_gripper()
if not check_success():
    close_gripper()
    rotate_gripper("can", 0.3, steps=2)
    open_gripper()

# Diagnostics
pos, quat, _ = get_object_pose("can")
tactile = get_tactile_data()
success = check_success()
print(f"Success: {success}")
print(f"Can pos: pos={pos}, quat_w={quat[0]:.3f}")
print(f"Tactile: L_contact={tactile['left_contact']} R_contact={tactile['right_contact']} "
      f"L_depth={tactile['left_depth_mm']:.2f}mm R_depth={tactile['right_depth_mm']:.2f}mm")
"""


class UniVTACLiftCanCodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE
    # Optional API-doc sections this task's LLM needs; the rest of the
    # shared UniVTACTactileApi doc is pruned.  See DOC_SECTION_TAGS.
    api_doc_sections = ()
