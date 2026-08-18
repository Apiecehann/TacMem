from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: Lift the bottle, maneuver it past the shelf lip, and place it into the
shelf bay, then release it.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

ORACLE_CODE = """
import numpy as np

# Lift up
move_delta(dz=0.18)

# Tilt back to clear shelf lip
move_delta(dry=-1.0)

# Rotate upright
for step in range(4):
    move_delta(dry=0.3)
    if detect_slip():
        close_gripper()

# Lower into bay
for step in range(10):
    move_delta(dz=-0.005)
    if detect_slip():
        break

open_gripper()
"""


class UniVTACPutBottleInShelfCodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE
