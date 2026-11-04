from __future__ import annotations

import base64
import io
import json

import numpy as np
import requests
from PIL import Image

from capx.integrations.base_api import ApiBase


class UniVTACShadowPianoApi(ApiBase):
    def __init__(self, env):
        super().__init__(env)
        self._session = requests.Session()
        self._session.trust_env = False
        self._server = env._server
        self._ended = False

    def functions(self):
        return {name: getattr(self, name) for name in (
            'get_keyboard_endpoints', 'locate_visual_keys', 'get_hand_state',
            'move_shadow_index', 'wait_steps', 'get_index_tactile_rgb',
            'estimate_key_force', 'finish_piano_note_attempt', 'finish_episode')}

    def _request(self, method, path, **kwargs):
        response = self._session.request(method, self._server + path, timeout=600, **kwargs)
        if not response.ok:
            try:
                detail = response.json().get('detail', response.text)
            except ValueError:
                detail = response.text
            raise RuntimeError(f'{path}: {detail}')
        return response.json()

    def get_keyboard_endpoints(self) -> dict:
        """Read static A0/C8 endpoints in world metres and the mirrored HEAD image.

        Image size is 960x662 with a 22-pixel label strip. Convert a displayed
        pixel to u=x/960, v=(y-22)/640. Includes calibration_ready and mapped_keys.
        Endpoints describe geometry, not live contact or note success.
        Exact endpoint fields: result['A0']['display_image_center_px'] = [x,y]
        and result['C8']['display_image_center_px'] = [x,y]; each also contains
        world_key_top_center_m and midi. There is no 'pixel' or 'endpoints' key.
        """
        return self._request('GET', '/shadow/endpoints')

    def locate_visual_keys(self, pixels: dict) -> dict:
        """Calibrate visible endpoints and return all requested key wrist targets.

        Supply exactly {'A0': {'u': ..., 'v': ..., 'color': 'white'},
        'C8': {'u': ..., 'v': ..., 'color': 'white'}} after checking the HEAD
        image. Values must agree with the public projected endpoint centers.
        Returns per-note color, wrist_x_offset_m, wrist_y_m, press_lift_z_m,
        hover_lift_z_m. Geometry does not prove contact or force. Reuse this map.
        """
        result = self._request('POST', '/shadow/locate', json=pixels)
        print('PIANO_KEY_MAP', json.dumps(result, ensure_ascii=False))
        return result

    def get_hand_state(self) -> dict:
        """Read measured wrist X/Y/Z in m, index flexion in rad, and arm state.

        Exact position keys: wrist_x_offset_m, wrist_y_m, wrist_z_m,
        index_flexion_rad. wrist_and_index_velocity is [Y m/s, Z m/s, index rad/s].
        step and action_step are the SAME 60-Hz control counter; elapsed
        seconds=(step_after-step_before)/60. physics_step is a separate
        240-Hz counter; never mix these two units. Includes native_done and
        visual_key_map_ready. Reads advance no time.
        A commanded target is not an achieved position. No key angles or score.
        """
        return self._request('GET', '/shadow/hand_state')

    def move_shadow_index(self, wrist_x_offset_m: float, wrist_y_m: float,
                          lift_z_m: float, index_flexion_rad: float, steps: int = 1) -> dict:
        """Send absolute control-coordinate targets for 1..240 action steps.

        Each step is four 240-Hz physics ticks (1/60 s). Bounds: X [-.01,.09],
        Y [-.32,.26], Z [-.006,.05] m; finger [0,.50] rad. Lowering below .035 m
        or curling past .05 rad requires calibrated keys, measured XY error
        <=.002 m and abs(Y speed)<=.015 m/s. Otherwise the action is refused.
        Returns ok, steps_executed, step and native_done. Stop actions when done.
        """
        if self._ended:
            raise RuntimeError('END was submitted; further motion is forbidden')
        target = [float(wrist_x_offset_m), float(wrist_y_m), float(lift_z_m), float(index_flexion_rad)]
        result = self._request('POST', '/shadow/move', json={'action': target, 'steps': steps})
        print('PIANO_ACTION', result['step'], target, 'steps', result['steps_executed'], 'done', result['native_done'])
        return result

    def wait_steps(self, steps: int) -> dict:
        """Advance 1..240 action steps holding the last target; one step is 1/60 s.

        This advances physics and the robot can still move toward its target.
        The same motion/arrival limits apply. Returns ok, steps_executed and
        native_done. time.sleep and repeated reads do not advance physical time.
        """
        if self._ended:
            raise RuntimeError('END was submitted; further motion is forbidden')
        result = self._request('POST', '/shadow/move', json={'action': None, 'steps': steps})
        print('PIANO_WAIT', result['step'], result['steps_executed'], 'done', result['native_done'])
        return result

    def get_index_tactile_rgb(self) -> np.ndarray:
        """Return the current single-index DIGIT RGB as uint8[H,W,3]."""
        value = self._request('GET', '/shadow/rgb')['rgb']
        return np.asarray(Image.open(io.BytesIO(base64.b64decode(value))).convert('RGB')).copy()

    def estimate_key_force(self, note: str | int) -> dict:
        """Estimate current key-top force from public DIGIT depth with fixed 5D calibration.

        note selects black/white calibration; it does not verify which key was
        touched. Returns estimated_key_force_N, sample_step, age_steps, quality,
        calibration_version and tactile_features. Only fresh (age_steps=0),
        finite quality='ok' values support force feedback. 'no_contact' returns
        None; 'ood', 'insufficient_history' and other unavailable states are not
        zero force. Reads do not create history. This estimate is not a score.
        sample_step uses the same 60-Hz counter as get_hand_state()['step'];
        age_steps is measured in control steps. The force estimate does not
        advance either counter.
        """
        value = self._request('GET', '/shadow/force', params={'note': str(note)})
        features = value.get('tactile_features', {}).get('features')
        print('PIANO_TACTILE', value['sample_step'], str(note), value['quality'],
              value['estimated_key_force_N'], None if features is None else [round(float(x), 6) for x in features])
        return value

    def finish_piano_note_attempt(self, index: int) -> dict:
        """Record your completed attempt in sequence after measured release.

        While active, actual index must be <=.02 rad and wrist Z within .002 m
        of .04 m continuously for .12 physical seconds after arrival. Repeated
        reads do not count. This records public progress only; it does not skip
        notes, alter native scoring or certify success. Returns ok, native_done.
        """
        result = self._request('POST', '/shadow/attempt', json={'index': index})
        print('PIANO_ATTEMPT_END', index, result)
        return result

    def finish_episode(self) -> None:
        """Submit END once after the whole program, including native termination.

        No success flag, key force or private verdict is returned. Do not issue
        more actions afterward. The host also submits END on program return.
        """
        if not self._ended:
            self._request('POST', '/shadow/submit', json={})
            self._ended = True
            print('PIANO_END')
