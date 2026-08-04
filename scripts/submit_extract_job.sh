#!/bin/bash -l
#
# SGE job script — start the ExtractionLM (measurement extraction) vLLM
# server, run extraction, then shut it down.
#
# The server and the extraction client run on the same allocated node, so
# the client connects to localhost. This script is cluster-agnostic: it
# expects REPO_DIR to be exported by the submitter (SGE jobs land in $HOME,
# not the submission directory) and sources scripts/cluster.local.sh for any
# site-specific bootstrap (module loads, HF_HOME, discovering/connecting to
# Postgres, venv activation). Copy scripts/cluster.local.sh.example to
# scripts/cluster.local.sh and edit it for your environment, then submit via
# a small personal wrapper that also carries your SGE project/account, e.g.:
#
#   qsub -P <your_project> -v REPO_DIR="$REPO_DIR" scripts/submit_extract_job.sh
#
# This job reads OCR text from OCR_DIR (written by a separate OCR job — see
# scripts/submit_ocr_job.sh) instead of downloading/OCRing PDFs itself, so
# it has zero Wiley awareness. Run both jobs at roughly the same time (two
# separate 1-GPU submissions instead of one combined 2-GPU job); this job's
# --idle-timeout keeps it polling for newly-OCR'd papers instead of exiting
# as soon as it catches up to the OCR job.
#
# Customise the #$ directives below for your cluster and the resource
# requirements of your chosen extraction model.
#
#$ -l h_rt=24:00:00
#$ -pe omp 8
#$ -l gpus=1
#$ -l gpu_memory=80G
#$ -l gpu_c=9.0
#$ -o out/extract_out.txt
#$ -e out/extract_error.txt
#$ -m e

: "${REPO_DIR:?REPO_DIR must be exported by the submitter (e.g. qsub -v REPO_DIR=...) — see scripts/cluster.local.sh.example}"
cd "$REPO_DIR"

# Load .env first so MEAS_LM_PORT is available for the health check below.
# Loaded *before* cluster.local.sh so that if cluster.local.sh exports its
# own DATABASE_URL (e.g. pointing at wherever scripts/serve_postgres.sh
# currently lives), that export is the last word and isn't clobbered by
# .env's static value.
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

if [ -f scripts/cluster.local.sh ]; then
    source scripts/cluster.local.sh
else
    echo "scripts/cluster.local.sh not found — copy scripts/cluster.local.sh.example and edit it for your environment." >&2
    exit 1
fi

set -euo pipefail
mkdir -p logs

MEAS_LM_PORT="${MEAS_LM_PORT:-8084}"

# ---- Preflight: fail fast if the DB isn't reachable --------------------------
# Without this, a stale/unreachable DATABASE_URL still burns several minutes
# starting a vLLM server before `coastal-crawler extract` makes its first
# query and fails.
python3 scripts/check_db.py

# ---- Start the server in the background --------------------------------------
# No gpu_id argument: this is a standalone single-GPU job, so SGE's own
# CUDA_VISIBLE_DEVICES assignment for the allocated GPU should stand rather
# than being overridden to a hardcoded physical device index.
cd scripts
./serve_model.sh MEAS_LM &
MEAS_LM_PID=$!

# Kill the server when this script exits for any reason.
trap 'echo "Stopping vLLM server (PID $MEAS_LM_PID)..."; kill "$MEAS_LM_PID" 2>/dev/null || true; wait "$MEAS_LM_PID" 2>/dev/null || true' EXIT

# ---- Wait for the server to be ready -----------------------------------------
./wait_for_health.sh "$MEAS_LM_PORT" "$MEAS_LM_PID"

# ---- Run extraction -----------------------------------------------------------
# --idle-timeout keeps this job polling for newly-OCR'd papers instead of
# exiting the moment it catches up to a concurrently-running OCR job (see
# scripts/submit_ocr_job.sh); --poll-interval controls how often it checks.
cd "$REPO_DIR"
coastal-crawler extract --batch-size 250 --poll-interval 60 --idle-timeout 1800
