"""Stub low-level environment for UniVTAC tasks.

The actual simulation runs in the UniVTAC API server process (Isaac Sim).
This stub satisfies the caP-X BaseEnv interface.  ``compute_reward`` and
``task_completed`` query the UniVTAC server over HTTP so trial results are
meaningful.
"""

from __future__ import annotations

import io
import os
from typing import Any

import gymnasium as gym
import numpy as np
import requests
from PIL import Image

from capx.envs.base import BaseEnv

_DEFAULT_SERVER = "http://127.0.0.1:8120"


class UniVTACStubEnv(BaseEnv):
    """Minimal BaseEnv that delegates all sim work to the UniVTAC server."""

    def __init__(self, task_name: str = "insert_hole",
                 server_url: str | None = None):
        self._task_name = task_name
        self._server = server_url or os.environ.get(
            "UNIVTAC_SERVER_URL", _DEFAULT_SERVER
        )
        self._obs: dict[str, Any] = {}
        self._last_success: bool = False

        # Minimal observation/action spaces
        self.observation_space = gym.spaces.Dict(
            {"task_prompt": gym.spaces.Text(max_length=4096)}
        )
        self.action_space = gym.spaces.Box(
            low=-1.0, high=1.0, shape=(7,), dtype=np.float32
        )

    # ---- BaseEnv interface ----

    def reset(
        self, seed: int | None = None, options: dict | None = None
    ) -> tuple[dict, dict]:
        r = requests.post(
            f"{self._server}/reset",
            json={"seed": seed or 0},
            timeout=300,
        )
        r.raise_for_status()
        self._last_success = False
        return {
            "task_prompt": f"UniVTAC {self._task_name}",
        }, {}

    def step(self, action: np.ndarray) -> tuple[dict, float, bool, bool, dict]:
        return {"task_prompt": ""}, 0.0, False, False, {}

    def render(self, mode: str = "rgb_array") -> np.ndarray:
        """Fetch head camera image from UniVTAC server for VDM."""
        try:
            import base64 as _b64
            r = requests.get(f"{self._server}/observation", timeout=10)
            r.raise_for_status()
            data = r.json()
            b64 = data.get("head_rgb_base64", "")
            if b64:
                img = Image.open(io.BytesIO(_b64.b64decode(b64)))
                return np.array(img)
        except Exception:
            pass
        return np.zeros((270, 480, 3), dtype=np.uint8)

    def close(self) -> None:
        pass

    def get_observation(self) -> dict:
        return self._obs

    def compute_reward(self) -> float:
        """Query the UniVTAC server for task success."""
        try:
            r = requests.get(
                f"{self._server}/check_success", timeout=10
            )
            r.raise_for_status()
            data = r.json()
            self._last_success = data.get("completed", False)
            return 1.0 if self._last_success else 0.0
        except Exception:
            return 0.0

    def task_completed(self) -> bool:
        """Return whether the last server check indicated success."""
        return self._last_success

    @property
    def dt(self) -> float:
        return 0.01

    # ---- Video / rendering stubs (not used by UniVTAC) ----

    def enable_video_capture(
        self, enabled: bool = True, *, clear: bool = True, wrist_camera: bool = False
    ) -> None:
        pass

    def get_video_frames(self, clear: bool = True) -> list[np.ndarray]:
        """Fetch captured frames from the UniVTAC server."""
        try:
            r = requests.get(
                f"{self._server}/video_frames", timeout=30
            )
            r.raise_for_status()
            data = r.json()
            frames_b64 = data.get("frames", [])
            frames = []
            import base64 as _b64
            for f in frames_b64:
                img = Image.open(io.BytesIO(_b64.b64decode(f)))
                frames.append(np.array(img))
            return frames
        except Exception:
            return []

    def get_video_frame_count(self) -> int:
        return 0

    def get_video_frames_range(self, start: int, end: int) -> list[np.ndarray]:
        return []

    def render_wrist(self) -> np.ndarray | None:
        return None

    def get_wrist_video_frames(self, clear: bool = True) -> list[np.ndarray]:
        return []

    def get_wrist_video_frames_range(self, start: int, end: int) -> list[np.ndarray]:
        return []

    def _update_viser_server(self) -> None:
        pass

    max_steps: int = 999999
