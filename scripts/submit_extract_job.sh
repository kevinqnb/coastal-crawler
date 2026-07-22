#!/bin/bash -l
#
# SGE job script — start the OCRLM (OCR) and ExtractionLM (extraction)
# vLLM servers on separate GPUs of the same node, run extraction, then shut
# both servers down.
#
# Both servers and the extraction client run on the same allocated node, so
# the client connects to localhost. Servers are pinned to distinct GPUs via
# CUDA_VISIBLE_DEVICES (see scripts/serve_model.sh's gpu_id argument) and are
# started in the background, killed automatically when the job exits.
#
# This script is cluster-agnostic: it expects REPO_DIR to be exported by the
# submitter (SGE jobs land in $HOME, not the submission directory) and
# sources scripts/cluster.local.sh for any site-specific bootstrap (module
# loads, HF_HOME, a local Postgres, venv activation). Copy
# scripts/cluster.local.sh.example to scripts/cluster.local.sh and edit it
# for your environment, then submit via a small personal wrapper that also
# carries your SGE project/account, e.g.:
#
#   qsub -P <your_project> -v REPO_DIR="$REPO_DIR" scripts/submit_extract_job.sh
#
# Customise the #$ directives below for your cluster and the resource
# requirements of your chosen models.
#
#$ -l h_rt=24:00:00
#$ -pe omp 16
#$ -l gpus=2
#$ -l gpu_memory=24G
#$ -l gpu_c=7.0
#$ -o out/extract_out.txt
#$ -e out/extract_error.txt
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

# Load .env so DOC_LM_PORT/MEAS_LM_PORT are available for the health checks below.
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

DOC_LM_PORT="${DOC_LM_PORT:-8083}"
MEAS_LM_PORT="${MEAS_LM_PORT:-8084}"

# ---- Start servers in background, pinned to distinct GPUs -------------------
cd scripts
./serve_model.sh DOC_LM 0 &
DOC_LM_PID=$!
./serve_model.sh MEAS_LM 1 &
MEAS_LM_PID=$!

# Kill both servers when this script exits for any reason.
trap 'echo "Stopping vLLM servers (PIDs $DOC_LM_PID $MEAS_LM_PID)..."; kill "$DOC_LM_PID" "$MEAS_LM_PID" 2>/dev/null || true; wait "$DOC_LM_PID" "$MEAS_LM_PID" 2>/dev/null || true' EXIT

# ---- Wait for both servers to be ready ---------------------------------------
./wait_for_health.sh "$DOC_LM_PORT" "$DOC_LM_PID"
./wait_for_health.sh "$MEAS_LM_PORT" "$MEAS_LM_PID"

# ---- Run extraction -----------------------------------------------------------
cd "$REPO_DIR"
coastal-crawler extract --batch-size 100
