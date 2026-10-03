from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: Insert the HDMI plug straight down into its socket.

Objects (refer to them by these exact names):
- "prism" — the HDMI plug.
- "slot" — the HDMI socket on the table.

The prism is already grasped by the gripper and held just above the slot.
Keep the gripper closed — do not re-grasp.

Key rules:
- The plug starts already upright and roughly aligned. Correct only the lateral
  (x/y) offset, then insert straight down in small steps. Do NOT rotate the
  gripper to "fix" a small tilt — the plug is within tolerance, and rotating
  swings the grasped plug off-target.
- Judge "seated" from geometry: track the plug's depth with get_object_pose 
  and stop once the plug origin is about 1 cm above the slot origin (its origin 
  sits above the insertion tip). Descend in small steps and do not overshoot 
  — over-inserting jams the plug and tips it over.
- Watch the plug's orientation (quat_w) while descending. If it starts tilting
  (quat_w drops below ~0.99) or stops descending, it is jammed on the socket
  rim: lift it slightly, re-align laterally, then descend again.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

ORACLE_CODE = """
import numpy as np

# The plug ("prism") is already grasped and hovering above the socket ("slot").
slot_pos, _, _ = get_object_pose("slot")
plug_pos, _, _ = get_object_pose("prism")

# Correct lateral offset, then insert straight down in small steps.
move_delta(dx=slot_pos[0] - plug_pos[0], dy=slot_pos[1] - plug_pos[1])

# Descend until the plug origin is ~1 cm above the slot origin (fully seated).
for _ in range(40):
    plug_pos, _, _ = get_object_pose("prism")
    if plug_pos[2] - slot_pos[2] < 0.010:
        break
    move_delta(dz=-0.002)

# Verify once at the end.
check_success()
"""


class UniVTACInsertHDMICodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE
    # Optional API-doc sections this task's LLM needs; the rest of the
    # shared UniVTACTactileApi doc is pruned.  See DOC_SECTION_TAGS.
    api_doc_sections = ()
