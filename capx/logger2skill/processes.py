from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path


def without_proxy(environment: dict | None = None) -> dict:
    env = dict(os.environ if environment is None else environment)
    for key in list(env):
        if key.lower().endswith("_proxy"):
            env.pop(key)
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", NO_PROXY="*", no_proxy="*")
    return env


def stop_owned_process(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def run_process(command: list[str], cwd: Path, env: dict, timeout: float,
                log: Path, input_text: str | None = None) -> dict:
    if timeout <= 0:
        raise TimeoutError("No remaining process budget")
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as output:
        proc = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True,
                                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                stdout=output, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        timed_out = False
        try:
            proc.communicate(input=input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            stop_owned_process(proc)
    return {"exit_code": 124 if timed_out else proc.returncode,
            "timed_out": timed_out, "elapsed_s": time.monotonic() - started}
