from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: Grasp the bottle leaning against the wall and rotate it to an upright
position, then release it.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

ORACLE_CODE = """
import numpy as np

close_gripper()
rotate_gripper("bottle", 1.22, steps=4)
open_gripper()
"""


class UniVTACLiftBottleCodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE
