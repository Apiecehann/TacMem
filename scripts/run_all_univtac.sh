#!/bin/bash
# Batch evaluation across all 8 UniVTAC tasks.
#
# Usage:
#   bash scripts/run_all_univtac.sh [model] [trials] [gpu]
#
# Examples:
#   bash scripts/run_all_univtac.sh "openrouter/deepseek/deepseek-r1" 10 0
#   bash scripts/run_all_univtac.sh "openrouter/deepseek/deepseek-chat-v3-0324" 100 1
#
# Prerequisites:
#   - OpenRouter server running on port 8110
#   - UniVTAC conda environment available

set -euo pipefail

MODEL="${1:-openrouter/deepseek/deepseek-r1}"
TRIALS="${2:-10}"
GPU="${3:-0}"
PORT=8120

TASKS=(
    insert_hole
    insert_tube
    insert_hdmi
    lift_bottle
    lift_can
    pull_out_key
    put_bottle_in_shelf
    grasp_classify
)

RESULTS_FILE="univtac_results_$(date +%Y%m%d_%H%M%S).txt"

echo "UniVTAC Batch Evaluation" | tee -a "$RESULTS_FILE"
echo "Model: $MODEL" | tee -a "$RESULTS_FILE"
echo "Trials per task: $TRIALS" | tee -a "$RESULTS_FILE"
echo "GPU: $GPU" | tee -a "$RESULTS_FILE"
echo "Started: $(date)" | tee -a "$RESULTS_FILE"
echo "" | tee -a "$RESULTS_FILE"

for task in "${TASKS[@]}"; do
    echo "=========================================" | tee -a "$RESULTS_FILE"
    echo "  Task: $task  ($(date +%H:%M:%S))" | tee -a "$RESULTS_FILE"
    echo "=========================================" | tee -a "$RESULTS_FILE"

    # Start UniVTAC server for this task
    echo "[1/3] Starting UniVTAC server..." | tee -a "$RESULTS_FILE"
    bash scripts/launch_univtac_server.sh "$task" "$PORT" "$GPU" > /tmp/univtac_server_${task}.log 2>&1 &
    SERVER_PID=$!
    echo "  Server PID: $SERVER_PID" | tee -a "$RESULTS_FILE"

    # Wait for server to be ready
    echo "[2/3] Waiting for server..." | tee -a "$RESULTS_FILE"
    READY=0
    for i in $(seq 1 120); do
        if curl -s "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
            echo "  Server ready after ${i} checks." | tee -a "$RESULTS_FILE"
            READY=1
            break
        fi
        sleep 5
    done

    if [ "$READY" -eq 0 ]; then
        echo "  ERROR: Server did not become ready. Skipping $task." | tee -a "$RESULTS_FILE"
        kill $SERVER_PID 2>/dev/null || true
        continue
    fi

    # Run evaluation
    echo "[3/3] Running evaluation ($TRIALS trials)..." | tee -a "$RESULTS_FILE"
    ./.venv/bin/python3 capx/envs/launch.py \
        --config-path "env_configs/univtac/${task}/univtac_${task}.yaml" \
        --model "$MODEL" \
        --total-trials "$TRIALS" 2>&1 | tee -a "$RESULTS_FILE"

    # Count results
    OUTPUT_DIR="outputs/$(echo "$MODEL" | tr '/' '_')/univtac_${task}"
    if [ -d "$OUTPUT_DIR" ]; then
        SUCCESS=$(ls -d "$OUTPUT_DIR"/trial_*_taskcompleted_1 2>/dev/null | wc -l)
        TOTAL=$(ls -d "$OUTPUT_DIR"/trial_* 2>/dev/null | wc -l)
        echo "  Results: $SUCCESS / $TOTAL ($(( SUCCESS * 100 / (TOTAL > 0 ? TOTAL : 1) ))%)" | tee -a "$RESULTS_FILE"
    fi

    # Stop server
    echo "  Stopping server..." | tee -a "$RESULTS_FILE"
    kill $SERVER_PID 2>/dev/null || true
    sleep 5
    # Force kill if still running
    kill -9 $SERVER_PID 2>/dev/null || true

    echo "" | tee -a "$RESULTS_FILE"
done

echo "All tasks complete at $(date)!" | tee -a "$RESULTS_FILE"
echo "Results saved to: $RESULTS_FILE"
