"""Small client env integrated with the colleague's trial runner."""
from types import SimpleNamespace

from .protocol import load_spec


class BaselineCodeEnv:
    tactile_skills = ()
    regenerate_prompt = None

    def __init__(self, cfg):
        self.cfg = SimpleNamespace(**cfg)
        self.spec = load_spec(self.cfg.config_path)
        # The ordinary runner's health check discovers the server through this.
        self.low_level_env = SimpleNamespace(_server=self.cfg.server_url)

    def close(self):
        pass
