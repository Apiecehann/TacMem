"""Native DIGIT geometry/physics with the fixed piano optical config; RGB IPC only."""
import os
from pathlib import Path
import subprocess


def bridge_class(native, root, config, output):
    class RGBDigit(native):
        def __init__(self, task):
            self.task = task
            self.image_shape = (task.cfg.tactile_image_width * 3 // 4, task.cfg.tactile_image_width)
            self.peak_indentation = 0.
            assets = root / '.cache/piano_tactile/digit'
            environment = dict(os.environ)
            for key in ('PYTHONPATH', 'PYTHONHOME'):
                environment.pop(key, None)
            if not environment.get('EGL_DEVICE_ID', '').isdigit():
                raise ValueError('An explicit physical EGL device is required')
            environment.update(PYTHONNOUSERSITE='1', PYOPENGL_PLATFORM='egl')
            output.mkdir(parents=True, exist_ok=True)
            self._log = (output / 'renderer.log').open('w', encoding='utf-8')
            self._process = subprocess.Popen([str(assets / 'venv/bin/python'),
                str(Path(__file__).with_name('digit_renderer.py')), str(assets), str(config),
                str(task.cfg.tactile_image_width)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._log, env=environment)
            try:
                self._geometry = self._receive()
                if self._geometry['config_path'] != str(config) or self._geometry['tacto_version'] != '0.0.3':
                    raise RuntimeError('DIGIT configuration/version mismatch')
                self._mount()
                self._send(dict(command='setup', keys={str(k.midi): k.size for k in task.layout}))
                self._receive()
            except BaseException:
                self.close()
                raise
    return RGBDigit
