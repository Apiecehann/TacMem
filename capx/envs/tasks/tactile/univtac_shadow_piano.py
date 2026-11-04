from __future__ import annotations

import json
import os
from dataclasses import replace

import requests

from capx.envs.tasks.base import CodeExecutionEnvBase


class UniVTACShadowPianoCodeEnv(CodeExecutionEnvBase):
    def __init__(self, cfg):
        url = os.environ.get('UNIVTAC_SERVER_URL', 'http://127.0.0.1:8120')
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.get(url + '/shadow/request', timeout=30)
            response.raise_for_status()
            request = response.json()
        finally:
            session.close()
        prompt = cfg.prompt.replace('{piano_request}', json.dumps(request, ensure_ascii=False))
        super().__init__(replace(cfg, prompt=prompt))

    def _exec_user_code(self, code):
        result = super()._exec_user_code(code)
        try:
            self._apis['UniVTACShadowPianoApi'].finish_episode()
            if 'PIANO_END' not in result['stdout']:
                result['stdout'] += '\nPIANO_END host submitted after program return\n'
        except Exception as exc:
            result['ok'] = False
            result['stderr'] += f'\nHost END failed: {exc}\n'
        return result
