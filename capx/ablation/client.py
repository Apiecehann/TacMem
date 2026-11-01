"""Only sanitized baseline endpoints are reachable by generated programs."""
from __future__ import annotations

import base64
import io
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from .protocol import sha256


def decode_rgb(value):
    with Image.open(io.BytesIO(base64.b64decode(value))) as img:
        if img.mode != 'RGB':
            raise ValueError('Expected RGB, not depth/feature visualization')
        return np.array(img, dtype=np.uint8)


class BaselineClient:
    def __init__(self, url, mode, output, log, expected_contract=None):
        self.url, self.mode = url.rstrip('/'), mode
        self.output, self.log = Path(output), log
        self.session = requests.Session()
        self.session.trust_env = False
        self.reads = {}
        self.action_calls = 0
        self.action_physics_steps = 0
        self.last_step = None
        self.expected_contract = expected_contract

    def call(self, name, payload=None):
        # Not bound into generated globals; transport errors disclose no body.
        headers = {'X-Capx-Contract': self.expected_contract} if self.expected_contract else {}
        response = self.session.post(self.url + '/ablation/' + name, json=payload or {},
                                     headers=headers, timeout=1800)
        if not response.ok:
            raise RuntimeError('environment_request_failed')
        data = response.json()
        if not data.get('ok', False):
            raise RuntimeError(data.get('error_code', 'environment_operation_failed'))
        return data

    def reset(self, seed):
        self.reads.clear()
        self.action_calls = self.action_physics_steps = 0
        data = self.call('reset', {'seed': seed})
        if self.expected_contract is not None and data.get('contract_hash') != self.expected_contract:
            raise RuntimeError('server_client_protocol_mismatch')
        self.last_step = data['physics_step']

    def get_robot_state(self):
        """Public joint/arm position and velocity only. No contact or task progress."""
        return self.call('state')['state']

    def get_scene_rgb(self, camera='head'):
        """Return ordinary camera RGB; camera='head' or 'wrist'."""
        return decode_rgb(self.call('scene', {'camera': camera})['rgb_base64'])

    def action(self, operation, **arguments):
        """Position-commanded motion. See task API contract for operations/units."""
        data = self.call('action', {'operation': operation, 'arguments': arguments})
        self.action_calls += 1
        self.action_physics_steps += data['physics_steps_executed']
        self.last_step = data['physics_step']
        result = {k: data[k] for k in ('executed', 'physics_step', 'time_s')}
        self.log('action', {'operation': operation, 'arguments': arguments, **result})
        return result

    def read_tactile_rgb(self, sensor):
        """Raw RGB only, sensor='left'/'right' for gripper or 'index' for Shadow."""
        if self.mode != 'tactile-rgb-opencv':
            raise RuntimeError('interface_disabled')
        data = self.call('rgb', {'sensor': sensor})
        key = (data['episode'], data['sensor'], data['physics_step'])
        if key not in self.reads:
            rgb = decode_rgb(data['rgb_base64'])
            # Sensor is validated server-side and again here before path creation.
            if data['sensor'] not in ('index', 'left', 'right'):
                raise ValueError('invalid_sensor')
            relative = f"tactile_images/{data['sensor']}_{data['physics_step']:09d}.png"
            path = self.output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(rgb).save(path)
            metadata = {k: data[k] for k in ('episode', 'sensor', 'physics_step', 'time_s')}
            metadata.update(image=relative, sha256=sha256(path))
            self.log('tactile_image', metadata)
            self.reads[key] = (rgb, metadata)
        rgb, metadata = self.reads[key]
        return {'rgb': rgb.copy(), 'sensor': metadata['sensor'], 'physics_step': metadata['physics_step'],
                'time_s': metadata['time_s']}

    def functions(self):
        result = {'get_robot_state': self.get_robot_state, 'get_scene_rgb': self.get_scene_rgb,
                  'action': self.action}
        if self.mode == 'tactile-rgb-opencv':
            result['read_tactile_rgb'] = self.read_tactile_rgb
        return result
