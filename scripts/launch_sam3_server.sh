#!/bin/bash
# Usage: bash scripts/launch_sam3_server.sh [gpu=1] [port=8114]
# Example: bash scripts/launch_sam3_server.sh 1 8114
#
# Standalone SAM3 segmentation server for the agent's non-privileged
# get_object_pose (capx/serving/launch_sam3_server.py, :8114 by default).
# Start it before the UniVTAC server (see docs/univtac_agent.md).

GPU="${1:-1}"      # default GPU 1: GPU 0 is usually the Isaac Sim / UniVTAC GPU
PORT="${2:-8114}"

cd "$(dirname "$0")/.."

# Runs in the cap-x venv (torch 2.8.0+cu128, sam3 importable) — NOT the
# UniVTAC conda env: the SAM3 server is a standalone FastAPI process.
export PYTHONPATH="${PWD}:${PYTHONPATH:-}"

# Supervisor loop (shared with launch_univtac_server.sh, see _supervise.sh):
# a crash (OOM, CUDA fault) auto-respawns; Ctrl+C (exit 0) breaks it.
source "$(dirname "$0")/_supervise.sh"
supervise "SAM3 server" \
    ./.venv/bin/python capx/serving/launch_sam3_server.py \
    --device "cuda:${GPU}" \
    --port "$PORT" \
    --host 127.0.0.1
