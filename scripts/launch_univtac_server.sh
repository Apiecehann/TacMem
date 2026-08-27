#!/bin/bash
# Usage: bash scripts/launch_univtac_server.sh <task> [port] [gpu_id] [seed]
# Example: bash scripts/launch_univtac_server.sh lift_can 8120 0 7

TASK="${1:?Usage: $0 <task> [port] [gpu_id] [seed]}"
PORT="${2:-8120}"
GPU="${3:-0}"
SEED="${4:-0}"

cd "$(dirname "$0")/.."

# UniVTAC root: sibling directory by default, override with UNIVTAC_ROOT
export UNIVTAC_ROOT="${UNIVTAC_ROOT:-${PWD}/../UniVTAC}"
export PYTHONPATH="${PWD}:${UNIVTAC_ROOT}:${PYTHONPATH:-}"

# Isaac Sim's usdrt scenegraph only supports cuda:0 ("GPUs other than
# cuda:0 are not currently supported").  To run on GPU N, hide the other
# GPUs from CUDA so the chosen one appears as cuda:0 — the same pattern
# UniVTAC's own scripts/collect_data.py uses (--gpu).
export CUDA_VISIBLE_DEVICES="$GPU"

# Supervisor loop (shared with launch_sam3_server.sh, see _supervise.sh): a
# cuRobo hang — which the server's freeze watchdog terminates with SIGKILL
# (exit 137) — auto-respawns the server; Ctrl+C (graceful exit 0) breaks it.
source "$(dirname "$0")/_supervise.sh"
supervise "UniVTAC server" \
    "${UNIVTAC_PYTHON:-${HOME}/miniconda3/envs/UniVTAC/bin/python}" \
    capx/serving/launch_univtac_server.py \
    --task "$TASK" \
    --port "$PORT" \
    --host 127.0.0.1 \
    --headless \
    --device "cuda:0" \
    --seed "$SEED"
