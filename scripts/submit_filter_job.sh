#!/bin/bash -l
#
# SGE job script — start the vLLM server, run the filter, then shut down.
#
# The server and filter run on the same allocated node, so the filter client
# connects to localhost. The server is started in the background and killed
# automatically when the job exits (success or failure).
#
# This script is cluster-agnostic: it expects REPO_DIR to be exported by the
# submitter (SGE jobs land in $HOME, not the submission directory) and
# sources scripts/cluster.local.sh for any site-specific bootstrap (module
# loads, venv activation, etc). Copy scripts/cluster.local.sh.example to
# scripts/cluster.local.sh and edit it for your environment, then submit via
# a small personal wrapper that also carries your SGE project/account, e.g.:
#
#   qsub -P <your_project> -v REPO_DIR="$REPO_DIR" scripts/submit_filter_job.sh
#
# Customise the #$ directives below for your cluster.
#
#$ -l h_rt=2:00:00
#$ -pe omp 8
#$ -l gpus=1
#$ -l gpu_memory=8G
#$ -l gpu_c=7.0
#$ -o out/filter_out.txt
#$ -e out/filter_error.txt
#$ -m e

: "${REPO_DIR:?REPO_DIR must be exported by the submitter (e.g. qsub -v REPO_DIR=...) — see scripts/cluster.local.sh.example}"
cd "$REPO_DIR"

if [ -f scripts/cluster.local.sh ]; then
    source scripts/cluster.local.sh
else
    echo "scripts/cluster.local.sh not found — copy scripts/cluster.local.sh.example and edit it for your environment." >&2
    exit 1
fi

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
