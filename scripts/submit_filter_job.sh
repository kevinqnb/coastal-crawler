#!/usr/bin/env bash
#
# SGE job script — start the vLLM server, run the filter, then shut down.
#
# The server and filter run on the same allocated node, so the filter client
# connects to localhost. The server is started in the background and killed
# automatically when the job exits (success or failure).
#
# Submit with:
#   qsub scripts/submit_filter_job.sh
#
# Customise the #$ directives below for your cluster.
#
#$ -P mcnet
#$ -l h_rt=2:00:00
#$ -pe omp 8
#$ -l gpus=1
#$ -l gpu_memory=8G
#$ -l gpu_c=7.0
#$ -o out/filter_out.txt
#$ -e out/filter_error.txt
#$ -m e

set -euo pipefail
mkdir -p logs

# Load .env so FILTER_PORT is available for the health check below.
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

FILTER_PORT="${FILTER_PORT:-8000}"

# ---- Start server in background ---------------------------------------------
./scripts/serve_model.sh FILTER &
SERVER_PID=$!

# Kill the server when this script exits for any reason.
trap 'echo "Stopping vLLM server (PID $SERVER_PID)..."; kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true' EXIT

# ---- Wait for server to be ready --------------------------------------------
./scripts/wait_for_health.sh "$FILTER_PORT" "$SERVER_PID"

# ---- Run filter -------------------------------------------------------------
coastal-crawler filter
