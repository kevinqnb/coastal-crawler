#!/bin/bash -l
#
# SGE job script — start the OCRLM (OCR) vLLM server, run the OCR stage,
# then shut it down.
#
# The server and the OCR client run on the same allocated node, so the
# client connects to localhost. This script is cluster-agnostic: it expects
# REPO_DIR to be exported by the submitter (SGE jobs land in $HOME, not the
# submission directory) and sources scripts/cluster.local.sh for any
# site-specific bootstrap (module loads, HF_HOME, discovering/connecting to
# Postgres, venv activation). Copy scripts/cluster.local.sh.example to
# scripts/cluster.local.sh and edit it for your environment, then submit via
# a small personal wrapper that also carries your SGE project/account, e.g.:
#
#   qsub -P <your_project> -v REPO_DIR="$REPO_DIR" scripts/submit_ocr_job.sh
#
# Run this alongside scripts/submit_extract_job.sh (two separate 1-GPU
# submissions instead of one combined 2-GPU job) so OCR and extraction can
# proceed concurrently — extraction polls for newly-written OCR text via its
# own --poll-interval/--idle-timeout options.
#
# Customise the #$ directives below for your cluster and the resource
# requirements of your chosen OCR model.
#
#$ -l h_rt=72:00:00
#$ -pe omp 8
#$ -l gpus=1
#$ -l gpu_memory=24G
#$ -l gpu_c=7.0
#$ -o out/ocr_out.txt
#$ -e out/ocr_error.txt
#$ -m e

: "${REPO_DIR:?REPO_DIR must be exported by the submitter (e.g. qsub -v REPO_DIR=...) — see scripts/cluster.local.sh.example}"
cd "$REPO_DIR"

# Load .env first so DOC_LM_PORT is available for the health check below.
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

DOC_LM_PORT="${DOC_LM_PORT:-8083}"

# ---- Preflight: fail fast if the DB isn't reachable --------------------------
# Without this, a stale/unreachable DATABASE_URL still burns several minutes
# starting a vLLM server before `coastal-crawler ocr` makes its first query
# and fails.
python3 scripts/check_db.py

# ---- Preflight: warn (don't block) if the Wiley PDF cache looks empty -------
# The OCR stage is what reads scripts/wiley_download.py's pre-downloaded
# cache (see worker.py's Wiley-dependency note in CLAUDE.md) — a claimed
# Wiley paper whose PDF isn't in WILEY_PDF_DIR yet gets requeued to
# 'relevant' instead of OCR'd, so an empty/missing cache dir means this job
# would spend several minutes starting vLLM just to requeue its whole batch
# and exit. Not a hard failure (a fresh downloader may still be catching
# up), just a heads-up in the logs.
WILEY_PDF_DIR="${WILEY_PDF_DIR:-data/wiley_pdfs}"
if [ ! -d "$WILEY_PDF_DIR" ] || [ -z "$(ls -A "$WILEY_PDF_DIR" 2>/dev/null)" ]; then
    echo "WARNING: $WILEY_PDF_DIR is empty or missing — is scripts/wiley_download.py running? Wiley papers claimed before it catches up will be requeued to 'relevant', not OCR'd." >&2
fi

# ---- Start the server in the background --------------------------------------
cd scripts
./serve_model.sh DOC_LM 0 &
DOC_LM_PID=$!

# Kill the server when this script exits for any reason.
trap 'echo "Stopping vLLM server (PID $DOC_LM_PID)..."; kill "$DOC_LM_PID" 2>/dev/null || true; wait "$DOC_LM_PID" 2>/dev/null || true' EXIT

# ---- Wait for the server to be ready -----------------------------------------
./wait_for_health.sh "$DOC_LM_PORT" "$DOC_LM_PID"

# ---- Run OCR -------------------------------------------------------------------
cd "$REPO_DIR"
coastal-crawler ocr --batch-size 5000
