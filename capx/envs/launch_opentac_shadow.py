"""Start Isaac Kit, then run the OpenTacBench Shadow piano through CaP-X."""

from __future__ import annotations

import argparse
import faulthandler
import os
import subprocess
import sys
import time
import traceback

os.environ.setdefault("CAPX_UNIVTAC_MINIMAL_IMPORTS", "1")

# CUDA ordinals are remapped by the visibility mask; Vulkan/EGL keep their
# physical ordinals. Never remove the mask to make those ordinal spaces match.
_PHYSICAL_GPU = os.environ.get('CAPX_SHADOW_PHYSICAL_GPU') or os.environ.get('CAPX_GPU')
_GRAPHICS_GPU = os.environ.get('CAPX_SHADOW_GRAPHICS_GPU', _PHYSICAL_GPU)
_GPU_UUID = None
if _PHYSICAL_GPU is not None:
    if not _PHYSICAL_GPU.isdigit() or not _GRAPHICS_GPU.isdigit():
        raise ValueError('Shadow physical and graphics GPU indices must be numeric')
    # A device-isolated namespace may expose a different graphics ordinal.
    # Resolve a supplied host UUID directly so NVML enumeration cannot retarget it.
    gpu_selector = os.environ.get('CAPX_SHADOW_GPU_UUID', _PHYSICAL_GPU)
    _GPU_UUID = subprocess.check_output(
        ['nvidia-smi', '-i', gpu_selector, '--query-gpu=uuid', '--format=csv,noheader'],
        text=True, timeout=10).strip()
    if not _GPU_UUID.startswith('GPU-') or '\n' in _GPU_UUID:
        raise RuntimeError('Cannot resolve exactly one physical Shadow GPU')
    visible = os.environ.get('CUDA_VISIBLE_DEVICES')
    if visible not in (None, _PHYSICAL_GPU, _GPU_UUID):
        raise ValueError('Shadow physical GPU conflicts with CUDA_VISIBLE_DEVICES')
    egl = os.environ.get('EGL_DEVICE_ID')
    if egl not in (None, _GRAPHICS_GPU):
        raise ValueError('Shadow graphics GPU conflicts with EGL_DEVICE_ID')
    os.environ['CUDA_VISIBLE_DEVICES'] = _GPU_UUID
    os.environ['EGL_DEVICE_ID'] = _GRAPHICS_GPU

from capx.envs.launch_univtac import build_parser


def _register_shadow_components() -> None:
    from capx.envs.base import register_env
    from capx.envs.simulators.opentac_shadow import ShadowPianoLowLevelEnv
    from capx.envs.tasks.base import CodeExecutionEnvBase
    from capx.integrations.base_api import register_api
    from capx.integrations.opentac_shadow import ShadowPianoControlApi, ShadowPianoVisualForceApi

    class ShadowPianoCodeEnv(CodeExecutionEnvBase):
        def _exec_env_binding(self):
            return None

        def _exec_apis_binding(self):
            # Generated code receives only the documented function bindings.
            return {}

    register_env("opentac_shadow_piano", ShadowPianoLowLevelEnv)
    register_api("ShadowPianoControlApi", ShadowPianoControlApi)
    register_api("ShadowPianoVisualForceApi", ShadowPianoVisualForceApi)


def main() -> None:
    process_started = time.monotonic()
    args = build_parser().parse_args()
    device = 'cuda:0' if _PHYSICAL_GPU is not None else args.univtac_device
    args.univtac_device = device
    opentac_root = os.environ.get("UNIVTAC_ROOT")
    if not opentac_root:
        raise RuntimeError("UNIVTAC_ROOT must point to OpenTacBench")
    sys.path.insert(0, opentac_root)
    app = None
    original_argv = sys.argv[:]
    try:
        sys.argv = [original_argv[0]]
        from isaaclab.app import AppLauncher

        class SingleGpuAppLauncher(AppLauncher):
            def _resolve_device_settings(self, launcher_args):
                super()._resolve_device_settings(launcher_args)
                if _PHYSICAL_GPU is not None:
                    # Isaac Lab 0.41 sets both fields to the CUDA ordinal.
                    # Override only the launcher config, not Isaac Lab sources.
                    launcher_args['active_gpu'] = int(_GRAPHICS_GPU)
                    launcher_args['physics_gpu'] = 0
                launcher_args['multi_gpu'] = False

        app = SingleGpuAppLauncher(argparse.Namespace(enable_cameras=True, num_envs=1,
            device=device, multi_gpu=False,
            kit_args='--/renderer/multiGpu/maxGpuCount=1')).app
        if _PHYSICAL_GPU is not None:
            import torch
            if torch.cuda.device_count() != 1:
                raise RuntimeError('Shadow launcher requires exactly one visible CUDA GPU')
            torch.cuda.set_device(0)
            print(f'[capx-shadow] single GPU physical={_PHYSICAL_GPU} uuid={_GPU_UUID} '
                  'cuda=cuda:0 visible_count=1 multi_gpu=False '
                  f'kit_active_gpu={_GRAPHICS_GPU} egl_device={_GRAPHICS_GPU}', flush=True)
    except Exception as exc:
        raise RuntimeError("IsaacLab AppLauncher must start before the Shadow task imports") from exc
    finally:
        sys.argv = original_argv

    _register_shadow_components()
    from capx.envs.runner import _run_headless_trials, _start_api_servers, _stop_api_servers
    from capx.utils.launch_utils import _load_config

    env_factory, config, api_servers = _load_config(args)
    config['_continual_process_started'] = process_started
    low_level = env_factory["cfg"]["low_level"]
    if low_level.get("_target_") != "capx.envs.simulators.opentac_shadow.ShadowPianoLowLevelEnv":
        raise ValueError("Shadow launcher requires ShadowPianoLowLevelEnv config")
    low_level["univtac_root"] = opentac_root
    low_level['device'] = device
    server_procs = _start_api_servers(api_servers)
    run_ok = False
    run_error: BaseException | None = None
    try:
        _run_headless_trials(args, env_factory, config, time.time())
        run_ok = True
    except BaseException as exc:
        run_error = exc
        print("[capx-shadow] run error before Kit shutdown", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        raise
    finally:
        _stop_api_servers(server_procs)
        from capx.envs.simulators.opentac_shadow import ACTIVE_SHADOW_ENVS
        for low_level_env in tuple(ACTIVE_SHADOW_ENVS):
            try:
                low_level_env.close()
            except Exception as exc:
                print(f"[capx-shadow] native task cleanup failed: {exc}", file=sys.stderr, flush=True)
        if config.get('shadow_piano_continual_arm') in ('A','B'):
            try:
                from capx.integrations.opentac_shadow_code_memory import record_continual_private_result
                record_continual_private_result(config)
            except Exception as exc:
                print(f'[piano-continual] private report failed: {exc}', file=sys.stderr, flush=True)
        if app is not None:
            if run_ok and os.getenv("CAPX_FORCE_EXIT_AFTER_RUN", "1").lower() in ("1", "true", "yes"):
                os._exit(0)
            print("[capx-shadow] Kit close begin", flush=True)
            faulthandler.dump_traceback_later(60, file=sys.stderr)
            try:
                app.close()
            except BaseException:
                print("[capx-shadow] Kit close error", file=sys.stderr, flush=True)
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()
                if run_error is None:
                    raise
            else:
                print("[capx-shadow] Kit close end", flush=True)
            finally:
                faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
