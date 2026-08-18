from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: Insert the grasped prism into the tilted slot until fully seated.

Key rules:
- Use small incremental motions for fine insertion control.
- Asymmetric tactile contact between the fingertips indicates misalignment
  with the tilted slot.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

ORACLE_CODE = """
import numpy as np

for step in range(15):
    move_delta(dz=-0.002)
    if detect_slip(threshold_px=2.0):
        open_gripper(); move_delta(dz=0.005); close_gripper()
    data = get_tactile_data()
    depth_diff = abs(data["left_depth_mm"] - data["right_depth_mm"])
    if depth_diff > 0.5:
        correction = 0.01 if data["left_depth_mm"] > data["right_depth_mm"] else -0.01
        move_delta(dry=correction)
    if data["left_depth_mm"] > 1.5 and data["right_depth_mm"] > 1.5:
        break
move_delta(dz=-0.005)
"""


class UniVTACInsertTubeCodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE
