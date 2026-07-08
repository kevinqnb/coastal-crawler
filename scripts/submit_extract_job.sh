#!/usr/bin/env bash
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
# Submit with:
#   qsub scripts/submit_extract_job.sh
#
# Customise the #$ directives below for your cluster and the resource
# requirements of your chosen models.
#
#$ -P mcnet
#$ -l h_rt=4:00:00
#$ -pe omp 16
#$ -l gpus=2
#$ -l gpu_memory=16G
#$ -l gpu_c=7.0
#$ -o out/extract_out.txt
#$ -e out/extract_error.txt
#$ -m e

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
./scripts/serve_model.sh DOC_LM 0 &
DOC_LM_PID=$!
./scripts/serve_model.sh MEAS_LM 1 &
MEAS_LM_PID=$!

# Kill both servers when this script exits for any reason.
trap 'echo "Stopping vLLM servers (PIDs $DOC_LM_PID $MEAS_LM_PID)..."; kill "$DOC_LM_PID" "$MEAS_LM_PID" 2>/dev/null || true; wait "$DOC_LM_PID" "$MEAS_LM_PID" 2>/dev/null || true' EXIT

# ---- Wait for both servers to be ready ---------------------------------------
./scripts/wait_for_health.sh "$DOC_LM_PORT" "$DOC_LM_PID"
./scripts/wait_for_health.sh "$MEAS_LM_PORT" "$MEAS_LM_PID"

# ---- Run extraction -----------------------------------------------------------
coastal-crawler extract
