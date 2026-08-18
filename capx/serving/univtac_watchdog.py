"""Freeze watchdog for the UniVTAC API server.

cuRobo IK can hang in a way that holds the GIL inside C++/CUDA code, freezing
the entire server process: no Python thread (not even uvicorn's ``/health``
handler) can run, so an *in-process* watchdog is useless.  This watchdog is a
**separate** stdlib-only process spawned by the server at startup.  It polls
the server's ``/health`` endpoint and, if the server fails to answer for
longer than ``--grace`` seconds, sends SIGKILL to the server PID so the launch
supervisor (``scripts/launch_univtac_server.sh``) can respawn it.

SIGKILL is deliberate: SIGTERM would be deferred by the server's own signal
handler (which runs on the frozen main thread), so it could never fire.

Usage::

    python -m capx.serving.univtac_watchdog --pid <server_pid> --port 8120
"""

from __future__ import annotations

import argparse
import http.client
import os
import signal
import time


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* exists (0 is the catch-all signal for a probe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _health_ok(host: str, port: int, timeout: float) -> bool:
    """Return True if the server answers GET /health with 200."""
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp.status == 200
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="UniVTAC server freeze watchdog")
    parser.add_argument("--pid", type=int, required=True, help="server PID to watch")
    parser.add_argument("--port", type=int, default=8120)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument(
        "--grace",
        type=float,
        default=180.0,
        help="seconds of /health silence before SIGKILL (default 180)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="seconds between /health probes (default 10)",
    )
    args = parser.parse_args()

    print(
        f"[univtac_watchdog] watching pid={args.pid} "
        f"{args.host}:{args.port} grace={args.grace:.0f}s",
        flush=True,
    )

    last_ok = time.time()
    while _pid_alive(args.pid):
        time.sleep(args.interval)
        if _health_ok(args.host, args.port, timeout=min(5.0, args.interval)):
            last_ok = time.time()
            continue
        if time.time() - last_ok > args.grace:
            print(
                f"[univtac_watchdog] no /health response for "
                f"{args.grace:.0f}s — SIGKILL frozen server pid={args.pid}",
                flush=True,
            )
            try:
                os.kill(args.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break


if __name__ == "__main__":
    main()
