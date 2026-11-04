from __future__ import annotations

import os

import requests

from capx.envs.tasks.base import CodeExecutionEnvBase


class UniVTACTensionStrapCodeEnv(CodeExecutionEnvBase):
    def __init__(self, cfg):
        url = os.environ.get("UNIVTAC_SERVER_URL", "http://127.0.0.1:8120")
        response = requests.get(url + "/task_request", timeout=30)
        response.raise_for_status()
        request = response.json()
        sequence = request["force_time_sequence"]
        self.prompt = (
            "Control a Franka gripper with two GelSight Mini sensors. Grasp the top tab of "
            "elastic_strap, pull vertically and complete the native two-stage tension task. "
            f"First hold {sequence[0]} N for {sequence[1]} seconds, then hold {sequence[2]} N "
            f"for {sequence[3]} seconds. Force tolerance is {request['tolerance_N']} N; "
            f"the overload limit is {request['overload_N']} N. "
            "These are requested forces, not measurements. Use public tactile observations, "
            "the scene and public robot actions. Do not read native force state or scores. "
            "wait(seconds) advances physical time with current targets; time.sleep does not. "
            "Submit finish_episode() once when your complete attempt is over, then stop. "
            "Write one complete executable Python program using the documented API. "
            "No additional coding block or oracle feedback will be supplied."
        )
        super().__init__(cfg)
