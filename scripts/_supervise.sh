#!/bin/bash
# Shared supervisor loop for the agent-line launch scripts.
#
# Respawns a crashing process forever (crash / SIGKILL -> restart in 2s) and
# breaks the loop on a clean shutdown (Ctrl+C -> graceful exit 0).  Usage:
#
#     source "$(dirname "$0")/_supervise.sh"
#     supervise "Server name" command --args...
#
# Each launch script keeps its own env setup (venv, PYTHONPATH, GPU) and
# only shares this respawn skeleton.

supervise() {
    name="$1"
    shift
    while true; do
        "$@"
        code=$?
        if [ "$code" -eq 0 ]; then
            echo "$name exited cleanly (code 0) — not restarting."
            break
        fi
        echo "$name exited with code $code — restarting in 2s..."
        sleep 2
    done
}
