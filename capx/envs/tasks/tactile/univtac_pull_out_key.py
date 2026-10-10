from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: Grasp the key, rotate it in the slot to unlock, and pull it straight out.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

ORACLE_CODE = """
import numpy as np

close_gripper()

# Rotate to unlock
for step in range(6):
    move_delta(drz=0.15)
    if detect_slip(threshold_px=2.0):
        close_gripper()

# Pull out
for step in range(8):
    move_delta(dz=-0.005, coord="ee")
    data = get_tactile_data()
    if not data["left_contact"] and not data["right_contact"]:
        break
"""


class UniVTACPullOutKeyCodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE
