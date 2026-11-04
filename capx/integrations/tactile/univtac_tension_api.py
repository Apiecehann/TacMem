from __future__ import annotations

from functools import wraps

from .univtac_api import UniVTACTactileApi


class UniVTACTensionApi(UniVTACTactileApi):
    def functions(self):
        def guard(fn):
            @wraps(fn)
            def call(*args, **kwargs):
                if getattr(self, "_ended", False):
                    raise RuntimeError("END was already submitted; no further actions are allowed")
                return fn(*args, **kwargs)
            return call
        functions = {**super().functions(), "wait": self.wait, "finish_episode": self.finish_episode}
        return {name: guard(fn) for name, fn in functions.items()}

    def wait(self, seconds: float) -> None:
        """Advance physical simulation while retaining the current joint targets.

        Args:
            seconds: Physical duration, greater than zero and at most 10 seconds.
        """
        self._post("/wait", {"seconds": float(seconds)})

    def finish_episode(self) -> None:
        """Submit END to the native task. No score or success flag is returned.

        Stop issuing actions after this call.
        """
        self._post("/submit_episode", {})
        self._ended = True
