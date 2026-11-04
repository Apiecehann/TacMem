from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .shadow_piano_force import PublicDigitKeyForce
from .shadow_piano_keyboard import make_calibration, targets_from_calibration
from .shadow_piano_vision import HEAD_EYE, HEAD_LOOK_AT, HEAD_WIDTH, HEAD_HEIGHT, HEAD_FOCAL_PX, head_world_to_display_pixel, parse_note


def json_value(value):
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


class ShadowPianoRuntime:
    def __init__(self, root: Path, output: Path, device: str, seed: int):
        from env.tasks.arm_shadow_piano import Task, TaskCfg
        import env.tasks._digit_tactile as native_digit
        from .shadow_digit_bridge import make_digit_bridge_class

        self.output = output
        self.private = output / "private"
        self.private.mkdir(parents=True, exist_ok=True)
        config_dir = Path(__file__).resolve().parents[2] / "env_configs/univtac/arm_shadow_piano"
        self.sensor_config = config_dir / "config_digit_piano_0_2.v1.yml"
        self.force_model = config_dir / "shadow_digit_key_top_force_5d_area_rate.v1.json"
        self.estimator = PublicDigitKeyForce(self.force_model, self.sensor_config)
        cfg = TaskCfg(device=device, tactile=True, tactile_sensor="digit", tactile_image_width=320,
                      action_repeat=4, piano_seed=seed, piano_note_count=6, piano_notes=None,
                      piano_hold_seconds=.12, piano_require_dynamics=True,
                      piano_continue_on_note_failure=False, record_video=True)
        cfg.tactile_workspace = str(self.private / "digit_world")
        original = native_digit.DigitTactile
        native_digit.DigitTactile = make_digit_bridge_class(original, opentac_root=root,
            piano_config=self.sensor_config, episode_dir=self.private)
        try:
            self.task = Task(cfg, render_mode="rgb_array")
        finally:
            native_digit.DigitTactile = original
        self.writer = None
        self._submitted = False
        self._done = False
        self._obs = None
        self._info = {}
        self.configure_camera()
        self._write("runtime.json", {"task_source": str(Path(__import__(Task.__module__, fromlist=['Task']).__file__)),
            "sensor_sha256": hashlib.sha256(self.sensor_config.read_bytes()).hexdigest(),
            "force_model_sha256": hashlib.sha256(self.force_model.read_bytes()).hexdigest(),
            "action_repeat": cfg.action_repeat, "physics_dt": cfg.physics_dt,
            "piano_require_dynamics": cfg.piano_require_dynamics,
            "piano_continue_on_note_failure": cfg.piano_continue_on_note_failure})

    def _write(self, name, value):
        (self.private / name).write_text(json.dumps(json_value(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def configure_camera(self):
        import torch
        self.task.camera.set_world_poses_from_view(
            torch.tensor([HEAD_EYE.tolist()], dtype=torch.float32, device=self.task.sim.device),
            torch.tensor([HEAD_LOOK_AT.tolist()], dtype=torch.float32, device=self.task.sim.device))

    def reset(self, seed):
        if self.writer is not None:
            self.writer.release()
            self.writer = None
        self.estimator.reset_history()
        self._obs, self._info = self.task.reset(seed=seed)
        self._done = False
        self._submitted = False
        self._verdict = None
        self._actions = 0
        self._policy_actions = 0
        self._attempts = 0
        self._release_since = None
        self._map = {}
        self._calibration = None
        self._action = np.asarray(self.task._action_state(), dtype=np.float32)
        self._sampling()
        self.configure_camera()
        for _ in range(16):
            self.task.sim.render()
            self.task.camera.update(self.task.cfg.physics_dt, force_recompute=True)
        current_y = self.hand_state()["wrist_y_m"]
        for y, steps in ((current_y, 60), (.24, 80), (.24, 80), (.24, 80)):
            self.move([0., y, .05, 0.], steps, policy=False)
            if self._done:
                raise RuntimeError("Native task ended before the initial keyboard view")
            if y == .24 and abs(self.hand_state()["wrist_y_m"] - .24) <= .025:
                break
        else:
            raise RuntimeError("Wrist did not reach the clear keyboard viewing pose")
        self._initial_actions = self._actions
        Image.fromarray(self.frame()).save(self.output / "initial_head.png")
        Image.fromarray(self.digit_rgb()).save(self.output / "initial_digit.png")
        self._write("request.json", self.request())
        return {"success": True, "seed": seed, "step_limit": int(self.task.cfg.episode_seconds / self.task.cfg.physics_dt)}

    def request(self):
        sequence = self._info["piano_sequence"]
        return {"notes": sequence["notes"], "dynamics": sequence["dynamics"],
            "hold_seconds": sequence["hold_seconds"],
            "force_targets_N": list(self.task.piano_annotations.targets),
            "force_tolerances_N": list(self.task.piano_annotations.tolerances),
            "release_seconds": .12, "control_dt_seconds": self.task.cfg.physics_dt * self.task.cfg.action_repeat,
            "physics_dt_seconds": self.task.cfg.physics_dt,
            "require_dynamics": True, "continue_on_note_failure": False}

    def hand_state(self):
        state = np.asarray(self._obs["state"], dtype=np.float32)
        actual = np.asarray(self.task._action_state(), dtype=np.float32)
        if state.shape != (111,) or actual.shape != (4,):
            raise RuntimeError("Unexpected native Shadow public state shape")
        return {"wrist_x_offset_m": float(actual[0]), "wrist_y_m": float(actual[1]),
            "wrist_z_m": float(actual[2]), "index_flexion_rad": float(actual[3]),
            "wrist_and_index_velocity": state[3:6].tolist(),
            "arm_joint_position_rad": state[97:104].tolist(),
            "arm_joint_velocity_rad_s": state[104:111].tolist(),
            "visual_key_map_ready": int(bool(self._map)), "step": self._actions,
            "physics_step": int(self.task.step_count),
            "action_step": self._actions, "native_done": self._done or self._submitted}

    def endpoints(self):
        from envs._piano import keyboard_layout
        layout = keyboard_layout()
        anchors = {}
        for name, key in (("A0", layout[0]), ("C8", layout[-1])):
            point = [float(key.position[0]), float(key.position[1]), float(key.position[2] + key.size[2])]
            anchors[name] = {"midi": int(key.midi), "world_key_top_center_m": point,
                "display_image_center_px": head_world_to_display_pixel(point, mirrored=True)}
        camera = self.task.camera
        intrinsics = camera.data.intrinsic_matrices[0].detach().cpu().numpy()
        expected = np.array([[HEAD_FOCAL_PX, 0, HEAD_WIDTH / 2], [0, HEAD_FOCAL_PX, HEAD_HEIGHT / 2], [0, 0, 1.]])
        if camera.cfg.width != HEAD_WIDTH or camera.cfg.height != HEAD_HEIGHT or not np.allclose(intrinsics, expected, atol=2.):
            raise ValueError("HEAD camera differs from the endpoint geometry")
        result = {"frame": "fixed_HEAD_display_image", "mirrored": True,
            "display_image_size_px": [960, 662], "camera_picture_origin_px": [0, 22],
            "white_key_count": 52, "white_center_intervals": 51,
            "white_center_spacing_m": (anchors['C8']['world_key_top_center_m'][1] - anchors['A0']['world_key_top_center_m'][1]) / 51,
            **anchors, "observed_camera_intrinsics": np.round(intrinsics, 7).tolist(),
            "calibration_ready": bool(self._map), "mapped_keys": self._map,
            "calibration_id": self._calibration.get('calibration_id') if self._calibration else None}
        for field in ("pos_w", "quat_w_world"):
            value = getattr(camera.data, field, None)
            if value is not None:
                result['camera_' + field] = np.round(value[0].detach().cpu().numpy(), 7).tolist()
        return result

    def locate(self, pixels):
        endpoints = self.endpoints()
        record = make_calibration(pixels, endpoints, self.output.name)
        targets = targets_from_calibration(record, endpoints, self.request()["notes"])
        self._calibration, self._map = record, targets
        (self.output / "keyboard_calibration.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return targets

    def _validate_target(self, action):
        value = np.asarray(action, dtype=np.float32)
        bounds = self.task.action_space
        if value.shape != (4,) or not np.isfinite(value).all() or np.any(value < bounds.low) or np.any(value > bounds.high):
            raise ValueError("Shadow target must contain four finite values within native bounds")
        if value[2] < .035 or value[3] > .05:
            if not self._map:
                raise ValueError("Calibrate the visible keyboard endpoints before pressing")
            hand = self.hand_state()
            error = np.linalg.norm(np.asarray([hand['wrist_x_offset_m'], hand['wrist_y_m']]) - value[:2])
            speed = float(hand['wrist_and_index_velocity'][0])
            if not np.isfinite(error + speed) or error > .002 or abs(speed) > .015:
                raise ValueError(f"Shadow XY arrival check: error_m={error:.6f}, y_speed_m_s={speed:.6f}; remain at extended-finger hover")
        return value

    def move(self, action, steps, *, policy=True):
        if self._submitted or self._done:
            raise RuntimeError("Native episode ended; further physical actions are forbidden")
        if type(steps) is not int or not 1 <= steps <= 240:
            raise ValueError("steps must be an integer in [1, 240]")
        value = self._validate_target(self._action if action is None else action)
        executed = 0
        for _ in range(steps):
            self._validate_target(value)
            self._obs, _, terminated, truncated, self._info = self.task.step(value)
            self._done = bool(terminated or truncated)
            self._action = value.copy()
            self._actions += 1
            self._policy_actions += int(policy)
            executed += 1
            self._sampling()
            hand = self.hand_state()
            ready = abs(hand['index_flexion_rad']) <= .02 and abs(hand['wrist_z_m'] - .04) <= .002
            if ready:
                if self._release_since is None:
                    self._release_since = int(self.task.step_count)
            else:
                self._release_since = None
            if self._actions % 4 == 0:
                self._capture_video()
            if self._done:
                break
        return {"success": True, "ok": True, "steps_executed": executed,
                "step": self._actions, "physics_step": int(self.task.step_count), "native_done": self._done}

    def _sampling(self):
        tactile = self.task.tactile
        self.estimator.observe_frame(tactile.depth_m, tactile.reference_depth_m, physics_step=int(self.task.step_count))

    def estimate(self, note):
        _, black = parse_note(note)
        tactile = self.task.tactile
        diagnostics = {}
        result = self.estimator.predict(tactile.depth_m, tactile.reference_depth_m,
            color='black' if black else 'white', sample_step=self._actions, current_step=self._actions,
            physics_step=int(self.task.step_count), diagnostics_out=diagnostics)
        result['tactile_features'] = {k: diagnostics.get(k) for k in
            ('feature_names', 'features', 'valid_depth_fraction', 'sensor_sha256')}
        return result

    def finish_attempt(self, index):
        if type(index) is not int or index != self._attempts or not 0 <= index < len(self.request()['notes']):
            raise ValueError("Finish attempts once in the requested order")
        if not self._done:
            elapsed = 0. if self._release_since is None else (int(self.task.step_count) - self._release_since) * self.task.cfg.physics_dt
            if elapsed + 1.e-12 < .12:
                raise ValueError(f"Release pose has held for {elapsed:.6f}s; require 0.12s after measured arrival")
        self._attempts += 1
        self._release_since = None
        return {"ok": True, "native_done": self._done}

    def submit(self):
        if not self._submitted:
            self._submitted = True
            self._verdict = bool(self._info.get('success', False))
            self._write('native_result.json', {'submitted_end': True, 'native': self._info,
                'public_attempts_finalized': self._attempts, 'policy_action_steps': self._policy_actions,
                'initial_action_steps': self._initial_actions})
            Image.fromarray(self.digit_rgb()).save(self.output / 'final_digit.png')
            if self.writer is not None:
                self.writer.release()
                self.writer = None
        return {"success": True, "message": "END submitted"}

    def verdict(self):
        return {"completed": self._verdict, "verified": self._submitted,
                "check_early_stop": self._done, "detail": None}

    def digit_rgb(self):
        image = np.asarray(self._obs['tactile']['index']['rgb'])
        if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError('Invalid native DIGIT RGB')
        return image.copy()

    @staticmethod
    def encode_image(image):
        out = io.BytesIO()
        Image.fromarray(image).save(out, format='PNG')
        return base64.b64encode(out.getvalue()).decode('ascii')

    def frame(self):
        picture = np.ascontiguousarray(np.asarray(self.task.render())[:, ::-1])
        if picture.shape != (HEAD_HEIGHT, HEAD_WIDTH, 3):
            raise ValueError('Unexpected HEAD image shape')
        return np.concatenate((np.zeros((22, HEAD_WIDTH, 3), dtype=np.uint8), picture), axis=0)

    def _capture_video(self):
        import cv2
        frame = self.frame()
        if self.writer is None:
            self.writer = cv2.VideoWriter(str(self.output / 'piano.mp4'), cv2.VideoWriter_fourcc(*'mp4v'),
                1. / (self.task.cfg.physics_dt * self.task.cfg.action_repeat * 4), (frame.shape[1], frame.shape[0]))
            if not self.writer.isOpened():
                raise RuntimeError('Could not open the piano diagnostic video')
        self.writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))

    def close(self):
        if self.writer is not None:
            self.writer.release()
            self.writer = None
        self.task.close()
