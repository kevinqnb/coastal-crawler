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
#$ -N coastal-filter
#$ -l h_rt=12:00:00
#$ -l gpu=1                 # adjust to your cluster's GPU resource flag
#$ -cwd                     # run from the directory where qsub is called
#$ -j y                     # merge stdout and stderr
#$ -o logs/filter_job.log
#$ -V                       # inherit environment (picks up HF_HOME, etc.)

set -euo pipefail
mkdir -p logs

# Load .env so FILTER_PORT is available for the health check below.
if [ -f .env ]; then
    set -a
    # shellcheck source=/dev/null
    source .env
    set +a
fi

FILTER_PORT="${FILTER_PORT:-8000}"

# ---- Start server in background ---------------------------------------------
./scripts/serve_filter_model.sh &
SERVER_PID=$!

# Kill the server when this script exits for any reason.
trap 'echo "Stopping vLLM server (PID $SERVER_PID)..."; kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true' EXIT

# ---- Wait for server to be ready --------------------------------------------
HEALTH_URL="http://localhost:$FILTER_PORT/health"
MAX_WAIT=300  # seconds
ELAPSED=0

echo "Waiting for vLLM server at $HEALTH_URL..."
until curl -sf "$HEALTH_URL" > /dev/null 2>&1; do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "Error: vLLM server process exited unexpectedly." >&2
        exit 1
    fi
    if [ "$ELAPSED" -ge "$MAX_WAIT" ]; then
        echo "Error: vLLM server did not become ready within ${MAX_WAIT}s." >&2
        exit 1
    fi
    sleep 10
    ELAPSED=$((ELAPSED + 10))
done
echo "Server ready after ${ELAPSED}s."

# ---- Run filter -------------------------------------------------------------
coastal-crawler filter
