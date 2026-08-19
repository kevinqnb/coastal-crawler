#!/bin/bash
# Submit wrapper for the experiment contract (see notes/hub/conventions.md
# and scripts/run_experiment.py).
#
# Reads params.stage out of configs/<id>.yaml and picks the GPU profile
# that matches the corresponding tracked job script (submit_filter_job.sh /
# submit_ocr_job.sh / submit_extract_job.sh) for that stage — those are this
# repo's actual per-stage resource requirements, not invented here — then
# qsubs scripts/run_experiment_job.sh (the generalized job body) with -N <id>.
#
# Usage:
#   bash scripts/submit.sh <id>
#
# SGE project/account: like the rest of this repo's site-specific config
# (scripts/cluster.local.sh, scripts/filter.sh/extract.sh — all gitignored),
# the SGE `-P` account isn't hardcoded here. Export SGE_PROJECT in your
# shell profile if your cluster requires `qsub -P <project>`; if unset, -P
# is omitted and qsub's own default project applies.

set -euo pipefail

ID="${1:?Usage: bash scripts/submit.sh <id>}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="$REPO_DIR/configs/${ID}.yaml"

[ -f "$CONFIG_PATH" ] || { echo "Error: $CONFIG_PATH not found." >&2; exit 1; }

STAGE="$("$REPO_DIR/.venv/bin/python" -c "
import sys, yaml
config = yaml.safe_load(open(sys.argv[1]))
print(config.get('params', {}).get('stage', ''))
" "$CONFIG_PATH")"

# GPU profile per stage — copied from submit_filter_job.sh / submit_ocr_job.sh
# / submit_extract_job.sh's #$ directives. judge has no legacy submit_*_job.sh
# precedent (new stage, no vLLM server — see run_experiment_job.sh's
# NEEDS_SERVER branch); GPU_MEMORY=80G/GPU_C=9.0 reuse MEAS_LM's profile as
# the closest existing "80GB-class GPU" precedent in this repo, based on
# scholarlm's own finding that a 1x16GB V100 OOM's loading Qwen2.5-7B alone
# (~15.2GB bf16) plus attribution's backward graph, but an 80GB GPU works
# (see research-notes/scholarlm/builds/2026-08-18-token-attribution-01.md).
# H_RT=24:00:00 is an unvalidated placeholder (matches extract's, since judge
# is also per-row/per-transaction rather than continuously batched) — revisit
# once a real judge-stage run's actual walltime is known.
case "$STAGE" in
    filter)  ROLE=FILTER;  H_RT=2:00:00;  GPU_MEMORY=8G;  GPU_C=7.0 ;;
    ocr)     ROLE=DOC_LM;  H_RT=72:00:00; GPU_MEMORY=24G; GPU_C=7.0 ;;
    extract) ROLE=MEAS_LM; H_RT=24:00:00; GPU_MEMORY=80G; GPU_C=9.0 ;;
    judge)   ROLE=JUDGE;   H_RT=24:00:00; GPU_MEMORY=80G; GPU_C=9.0 ;;
    *)
        echo "Error: configs/${ID}.yaml params.stage must be filter, ocr, extract, or judge (got '$STAGE')." >&2
        exit 1
        ;;
esac

mkdir -p "$REPO_DIR/out"

QSUB_ARGS=(
    -N "$ID"
    -l "h_rt=$H_RT"
    -pe omp 8
    -l gpus=1
    -l "gpu_memory=$GPU_MEMORY"
    -l "gpu_c=$GPU_C"
    -o "$REPO_DIR/out/${ID}_out.txt"
    -e "$REPO_DIR/out/${ID}_error.txt"
    -m e
    -v "REPO_DIR=$REPO_DIR,ROLE=$ROLE,EXPERIMENT_ID=$ID,STAGE=$STAGE"
)
if [ -n "${SGE_PROJECT:-}" ]; then
    QSUB_ARGS=(-P "$SGE_PROJECT" "${QSUB_ARGS[@]}")
fi

echo "Submitting $ID (stage=$STAGE, role=$ROLE, gpu_memory=$GPU_MEMORY, gpu_c=$GPU_C)..."
qsub "${QSUB_ARGS[@]}" "$REPO_DIR/scripts/run_experiment_job.sh"
