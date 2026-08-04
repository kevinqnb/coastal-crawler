#!/bin/bash -l
#
# SGE job body for the experiment contract's adapter (see
# scripts/run_experiment.py and notes/hub/conventions.md). Not meant to be
# qsub'd directly — scripts/submit.sh reads params.stage out of
# configs/<id>.yaml, picks the matching GPU profile, and qsubs this script
# with ROLE/EXPERIMENT_ID (and REPO_DIR) passed via `qsub -v`.
#
# Otherwise identical in shape to submit_filter_job.sh / submit_ocr_job.sh /
# submit_extract_job.sh: start the stage's vLLM server, wait for health, run
# the (one) command, tear the server down on exit. The #$ directives below
# are just qsub defaults for direct testing — scripts/submit.sh always
# overrides them on the command line with the resource profile that matches
# ROLE, since that varies per stage (see submit.sh's ROLE_RESOURCES).
#
#$ -l h_rt=24:00:00
#$ -pe omp 8
#$ -l gpus=1
#$ -l gpu_memory=24G
#$ -l gpu_c=7.0
#$ -m e

: "${REPO_DIR:?REPO_DIR must be exported by the submitter (scripts/submit.sh does this)}"
: "${ROLE:?ROLE must be exported by the submitter (scripts/submit.sh does this)}"
: "${EXPERIMENT_ID:?EXPERIMENT_ID must be exported by the submitter (scripts/submit.sh does this)}"
cd "$REPO_DIR"

# Load .env first — same ordering rationale as the other submit_*_job.sh
# scripts: cluster.local.sh is sourced after, so a DATABASE_URL it exports
# wins over .env's static value. (The per-job port override below happens
# after both, so it wins over whatever either of them set.)
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
mkdir -p logs out

# Pick a private port for this job's model server rather than using the
# static <ROLE>_PORT from .env: two same-role experiments (e.g. two "ocr"
# configs) submitted at once can land on the same host, and a shared static
# port means the second server fails to bind. PORT_OVERRIDE tells
# serve_model.sh to use this port instead; exporting <ROLE>_BASE_URL points
# this job's Settings (and thus run_experiment.py's client) at it too —
# both override .env's static values for this process only.
ROLE_PORT="$(python3 "$REPO_DIR/scripts/find_free_port.py")"
export PORT_OVERRIDE="$ROLE_PORT"
declare "${ROLE}_BASE_URL=http://localhost:${ROLE_PORT}/v1"
export "${ROLE}_BASE_URL"

# ---- Preflight: fail fast if the DB isn't reachable --------------------------
python3 scripts/check_db.py

# ---- Start the server in the background --------------------------------------
# No gpu_id argument: standalone single-GPU job, so SGE's own
# CUDA_VISIBLE_DEVICES assignment for the allocated GPU stands.
cd scripts
./serve_model.sh "$ROLE" &
SERVER_PID=$!

trap 'echo "Stopping vLLM server (PID $SERVER_PID)..."; kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true' EXIT

./wait_for_health.sh "$ROLE_PORT" "$SERVER_PID"

# ---- Run the experiment --------------------------------------------------------
cd "$REPO_DIR"
python3 scripts/run_experiment.py "configs/${EXPERIMENT_ID}.yaml"
