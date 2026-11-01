"""Baseline endpoints on the colleague's Kit/main-thread HTTP server.

Native tasks remain unmodified. Native safety/fixture/scoring may use sensors;
the position controller and public response construction never do.
"""
from __future__ import annotations

import base64
import importlib
import io
import os
from pathlib import Path
import uuid

import numpy as np
from PIL import Image

from .protocol import contract_hash, read_json, request_for, resolve, write_json


def array(value):
    if hasattr(value, 'detach'):
        value = value.detach().cpu().numpy()
    return np.asarray(value)


def rgb_payload(value):
    rgb = array(value)
    if rgb.ndim == 4 and rgb.shape[0] == 1:
        rgb = rgb[0]
    if rgb.ndim != 3 or rgb.shape[2] not in (3, 4) or rgb.dtype != np.uint8:
        raise ValueError('invalid_raw_rgb')
    buffer = io.BytesIO()
    Image.fromarray(rgb[:, :, :3]).save(buffer, format='PNG')
    return base64.b64encode(buffer.getvalue()).decode('ascii')


def vector(value, n):
    value = np.asarray(value, dtype=float)
    if value.shape != (n,) or not np.isfinite(value).all():
        raise ValueError('invalid_action')
    return value


class BaselineServer:
    def __init__(self, peer, spec, seed, output):
        self.peer, self.spec, self.seed = peer, spec, int(seed)
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.request = request_for(spec, self.seed)
        self.contract_value = contract_hash(spec, self.seed)
        self.asset = read_json(resolve(spec['assets_manifest']))['tasks'][spec['task']]
        self.episode = uuid.uuid4().hex
        self.ended = False
        self.reset_called = False
        self.cache = {}
        self.calls = 0
        self.started_step = 0

    def initialize(self, device):
        root = Path(self.asset['environment_root'])
        self.peer._activate_backing_root(root)
        self.peer._prefer_vitforge_tacex(root)
        task_id = self.spec['task']
        if task_id == 'shadow_piano':
            from env.tasks.arm_shadow_piano import Task, TaskCfg
            from env.tasks import _digit_tactile
            from .digit import bridge_class
            config = TaskCfg(device=device, tactile=True, tactile_sensor='digit',
                tactile_image_width=320, record_video=True, piano_seed=self.seed,
                piano_notes=self.request['notes'], piano_dynamics=self.request['dynamics'],
                piano_hold_seconds=self.request['hold_seconds'], piano_require_dynamics=True,
                piano_continue_on_note_failure=False)
            original = _digit_tactile.DigitTactile
            _digit_tactile.DigitTactile = bridge_class(original, root,
                Path(self.asset['digit_config']), self.output / 'private')
            try:
                self.task = Task(config, render_mode='rgb_array')
            finally:
                _digit_tactile.DigitTactile = original
            # Rendering only: the whole keyboard is visible, no score overlay.
            import torch
            self.task.camera.set_world_poses_from_view(
                torch.tensor([[.40, -.02, 1.55]], device=device),
                torch.tensor([[.40, -.02, .04]], device=device))
        elif task_id == 'blind_pick_balls':
            from env.tasks.blind_pick_balls import Task, TaskCfg
            from capx.serving.univtac_ball_adapter import BallTaskAdapter
            cfg = TaskCfg(device=device, seed=self.seed, record_video=True,
                workspace=str(self.output / 'private' / 'world'), target_class=self.request['target_class'])
            self.task = BallTaskAdapter(Task(cfg, render_mode='rgb_array'), record_trace=False)
            # The peer adapter's debug trace calculates scalar tactile features.
            # Native task scoring remains intact; omit this optional debug producer.
            self.task._trace = lambda *a, **kw: None
        else:
            module = importlib.import_module(self.asset['native_module'])
            cfg = module.TaskCfg()
            import yaml
            path = root / self.asset['task_config']
            task_config = yaml.safe_load(path.read_text(encoding='utf-8'))
            if task_id == 'tension_strap':
                task_config['task_parameters']['force_time_sequence'] = self.request['force_time_sequence']
            self.peer._apply_task_config(cfg, task_config)
            cfg.save_dir = self.output / 'private' / 'native'
            try:
                from envs._force_task_utils import FINAL_TASKS, prepare_force_task_config
            except ImportError:
                FINAL_TASKS = ()
            if self.asset['native_module'].split('.')[-1] in FINAL_TASKS:
                prepare_force_task_config(cfg, task_config, path, seed=self.seed)
            cfg.save_frequency = cfg.video_frequency = 0
            cfg.sim.device = device
            cfg.scene.num_envs = 1
            cfg.debug_vis = False
            self.task = module.Task(cfg, mode='collect')
        self.peer._TASK = self.task
        self.peer._TASK_NAME = self.asset['native_module'].split('.')[-1]
        # Existing native reset/pre_move, identical in both variants, no expert policy.
        self.task.reset(seed=self.seed)
        self.started_step = self.step()
        self._capture_scene()

    def native(self):
        return self.task.task if self.spec['task'] == 'blind_pick_balls' else self.task

    def step(self):
        return int(self.task.step_count)

    def timing(self):
        cfg = self.native().cfg
        dt = float(cfg.physics_dt) if hasattr(cfg, 'physics_dt') else float(cfg.sim.dt) * int(cfg.decimation)
        return {'physics_step': self.step(), 'time_s': self.step() * dt}

    def reset(self, seed):
        # One process/seed. Initialization has already done the one native reset.
        if self.reset_called or seed != self.seed:
            raise ValueError('one_episode_per_process')
        self.reset_called = True
        return {'ok': True, 'contract_hash': contract_hash(self.spec, self.seed), **self.timing()}

    def scene(self, camera='head'):
        if camera not in ('head', 'wrist'):
            raise ValueError('unknown_camera')
        if self.spec['task'] in ('shadow_piano', 'blind_pick_balls'):
            if camera != 'head':
                raise ValueError('camera_unavailable')
            rgb = self.native().render()
        else:
            camera_obs = self.task._camera_manager.get_observations(['rgb'])
            rgb = camera_obs[camera]['rgb']
        return {'ok': True, 'rgb_base64': rgb_payload(rgb), **self.timing()}

    def _capture_scene(self):
        scene = self.scene()
        folder = self.output / 'scene_images'
        folder.mkdir(exist_ok=True)
        path = folder / f'{self.step():09d}.png'
        if not path.exists():
            path.write_bytes(base64.b64decode(scene['rgb_base64']))
            with (folder / 'index.jsonl').open('a', encoding='utf-8') as stream:
                import json
                stream.write(json.dumps({**self.timing(), 'image': path.name}) + '\n')

    def state(self):
        if self.spec['task'] == 'shadow_piano':
            hand = self.task.hand.data
            state = {'action_coordinates': self.task._action_state().tolist(),
                     'joint_positions': array(hand.joint_pos[0]).tolist(),
                     'joint_velocities': array(hand.joint_vel[0]).tolist()}
        else:
            pose = self.task._robot_manager.get_ee_pose()
            state = {'ee_position_m': array(pose.p).tolist(), 'quaternion_wxyz': array(pose.q).tolist(),
                     'gripper_qpos_m': float(self.task._robot_manager.get_gripper_qpos())}
            robot = self.task._robot_manager.robot.data
            state['joint_positions'] = array(robot.joint_pos[0]).tolist()
            state['joint_velocities'] = array(robot.joint_vel[0]).tolist()
        return {'ok': True, 'state': {**state, **self.timing()}}

    def rgb(self, sensor):
        if self.spec['mode'] != 'tactile-rgb-opencv':
            raise ValueError('interface_disabled')
        if sensor not in (('index',) if self.spec['task'] == 'shadow_piano' else ('left', 'right')):
            raise ValueError('unknown_sensor')
        key = (sensor, self.step())
        if key not in self.cache:
            if self.spec['task'] == 'shadow_piano':
                rgb = self.task.tactile.observation()['rgb']
            elif self.spec['task'] == 'blind_pick_balls':
                self.native().physics.observation()
                sensors = self.native().physics.sensors
                matches = [s for s in sensors if s.name == sensor or s.name == sensor + '_tactile']
                if len(matches) != 1:
                    raise ValueError('sensor_identity_unavailable')
                rgb = matches[0].get_observations(['rgb'])['rgb']
            else:
                rgb = self.task._tactile_manager.get_observations(['rgb'])[sensor + '_tactile']['rgb']
            self.cache[key] = {'ok': True, 'episode': self.episode, 'sensor': sensor,
                               'rgb_base64': rgb_payload(rgb), **self.timing()}
        return self.cache[key]

    def stopped(self):
        native = self.native()
        return self.ended or bool(getattr(native, '_done', False)) or (
            hasattr(native, 'check_early_stop') and bool(native.check_early_stop()))

    def action(self, operation, arguments):
        if self.stopped():
            raise RuntimeError('native_episode_ended')
        if self.calls >= self.spec['action_call_budget']:
            raise RuntimeError('action_budget_exhausted')
        self.calls += 1
        before = self.step()
        if self.spec['task'] == 'shadow_piano':
            if operation != 'shadow_target' or set(arguments) != {'target', 'steps'}:
                raise ValueError('invalid_action_interface')
            target = vector(arguments['target'], 4)
            if np.any(target < self.task.action_space.low) or np.any(target > self.task.action_space.high):
                raise ValueError('action_out_of_bounds')
            steps = arguments['steps']
            if type(steps) is not int or not 1 <= steps <= 60:
                raise ValueError('invalid_step_count')
            for _ in range(steps):
                if self.stopped():
                    break
                self.task.step(target)
            executed = True
        else:
            executed = self._franka_action(operation, arguments)
        self.cache.clear()
        self._capture_scene()
        return {'ok': True, 'executed': bool(executed), 'physics_steps_executed': self.step()-before,
                **self.timing()}

    def _franka_action(self, operation, arguments):
        if operation == 'wait' and set(arguments) == {'steps'}:
            steps = arguments['steps']
            if type(steps) is not int or not 1 <= steps <= 120:
                raise ValueError('invalid_step_count')
            for _ in range(steps):
                if self.stopped():
                    break
                if self.spec['task'] == 'blind_pick_balls':
                    self.task._hold(1)
                else:
                    self.task._step(is_save=False)
            return True
        from envs.utils.atom import Action
        from envs.utils.transforms import Pose
        if operation == 'goto_pose' and set(arguments) == {'position', 'quaternion_wxyz'}:
            position, quat = vector(arguments['position'], 3), vector(arguments['quaternion_wxyz'], 4)
            if position[2] < .01 or not .999 <= np.linalg.norm(quat) <= 1.001:
                raise ValueError('invalid_pose')
            cmd = Action(action='move', target_pose=Pose(p=position, q=quat))
        elif operation == 'gripper' and set(arguments) == {'position'}:
            pos = float(arguments['position'])
            if not np.isfinite(pos) or not 0 <= pos <= 1:
                raise ValueError('invalid_gripper_position')
            cmd = Action(action='gripper', target_gripper_pos=pos)
        else:
            raise ValueError('invalid_action_interface')
        # Disable peer/native adaptive *controller* for these explicit commands.
        previous = self.task.cfg.use_adaptive_grasp
        self.task.cfg.use_adaptive_grasp = False
        try:
            return self.task.move([cmd], tag='ablation_position', delay=True, is_save=False)
        finally:
            self.task.cfg.use_adaptive_grasp = previous

    def finish(self, submitted_end):
        self.ended = True
        execution_steps = self.step() - self.started_step
        native = self.native()
        if submitted_end and self.spec['task'] == 'tension_strap':
            native.finish_episode()
        elif submitted_end and self.spec['task'] == 'bulb_tightening':
            native._finish_bulb_episode()
        if self.spec['task'] == 'shadow_piano':
            verdict = native._info()
            completed = bool(verdict['success'])
        else:
            completed = bool(self.task.check_success())
            verdict = {'completed': completed}
            if hasattr(self.task, 'check_success_detail'):
                verdict['detail'] = self.task.check_success_detail()
        result = {'completed': completed, 'submitted_end': bool(submitted_end),
                  'action_calls': self.calls, 'execution_physics_steps': execution_steps,
                  'native': self.peer._jsonable(verdict), **self.timing()}
        write_json(self.output / 'private' / 'native_result.json', result)
        return {'ok': True, 'native_result': result}


def mount(peer, baseline):
    """Reuse the upstream main-thread queue; disable its richer public routes."""
    from fastapi.responses import JSONResponse

    @peer.app.middleware('http')
    async def gate(request, call_next):
        if request.url.path != '/health' and not request.url.path.startswith('/ablation/'):
            return JSONResponse({'ok': False, 'error_code': 'interface_disabled'}, status_code=403)
        if request.url.path.startswith('/ablation/') and request.headers.get('X-Capx-Contract') != baseline.contract_value:
            return JSONResponse({'ok': False, 'error_code': 'protocol_mismatch'}, status_code=403)
        return await call_next(request)

    @peer.app.post('/ablation/{operation}')
    def endpoint(operation: str, body: dict):
        handlers = {'reset': baseline.reset, 'state': baseline.state, 'scene': baseline.scene,
                    'action': baseline.action, 'finish': baseline.finish}
        if baseline.spec['mode'] == 'tactile-rgb-opencv':
            handlers['rgb'] = baseline.rgb
        if operation not in handlers:
            return JSONResponse({'ok': False, 'error_code': 'interface_disabled'}, status_code=403)
        try:
            return peer._run_on_main(handlers[operation], timeout=1800, **body)
        except Exception as exc:
            # Exception values may include private task state. Types only.
            return JSONResponse({'ok': False, 'error_code': type(exc).__name__}, status_code=400)
