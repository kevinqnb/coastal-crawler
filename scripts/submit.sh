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
#   bash scripts/submit.sh <id> [hold_jid]
#
# hold_jid: optional SGE job ID (or comma-separated list) to pass as
# -hold_jid, so this job is queued now but SGE won't start it until the
# held job(s) exit — used to chain pipeline legs (e.g. queue judge right
# after submitting extract) without needing to stay around to submit the
# next leg manually once the prior one finishes.
#
# configs/<id>.yaml's params.gpu_type: optional. When set, adds
# `-l gpu_type=<value>` to the qsub line, pinning this one run to that GPU
# model. The per-stage GPU_C floor below is a *minimum* compute capability,
# so SGE is otherwise free to place a job on any newer card that clears it —
# and 2026-09-08-extract-full-split-03 (job 7518862) died at vLLM startup
# doing exactly that: gpt-oss-120b's MARLIN mxfp4 MoE kernel asserts
# "Hidden size mismatch w1" on the cc-12.0 RTX PRO 6000, while the cc-9.0
# H200s pick the TRITON backend and run fine. This is a per-model serving
# constraint, not a per-stage one (a future non-mxfp4 MEAS_LM model would
# not need it), so it lives in the run's config next to batch_size, not in
# the stage profile here. Left unset → no gpu_type constraint.
#
# SGE project/account: like the rest of this repo's site-specific config
# (scripts/cluster.local.sh, scripts/filter.sh/extract.sh — all gitignored),
# the SGE `-P` account isn't hardcoded here. Export SGE_PROJECT in your
# shell profile if your cluster requires `qsub -P <project>`; if unset, -P
# is omitted and qsub's own default project applies.

set -euo pipefail

ID="${1:?Usage: bash scripts/submit.sh <id> [hold_jid]}"
HOLD_JID="${2:-}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="$REPO_DIR/configs/${ID}.yaml"

[ -f "$CONFIG_PATH" ] || { echo "Error: $CONFIG_PATH not found." >&2; exit 1; }

STAGE="$("$REPO_DIR/.venv/bin/python" -c "
import sys, yaml
config = yaml.safe_load(open(sys.argv[1]))
print(config.get('params', {}).get('stage', ''))
" "$CONFIG_PATH")"

# Optional per-run GPU-model pin (see header comment). Blank = unconstrained.
GPU_TYPE="$("$REPO_DIR/.venv/bin/python" -c "
import sys, yaml
config = yaml.safe_load(open(sys.argv[1]))
print(config.get('params', {}).get('gpu_type', '') or '')
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
# table_clean also has no legacy precedent (new stage) and DOES need a vLLM
# server (unlike judge). GPU_MEMORY=80G/GPU_C=8.0 again reuse MEAS_LM/judge's
# profile as the closest "80GB-class GPU" precedent — a 27B FP8 model needs
# meaningfully less VRAM than gpt-oss-120b (MEAS_LM's actual model), so this
# is very likely oversized/safe but UNVALIDATED for this specific checkpoint;
# check at the serve-smoke-test stage whether a smaller GPU class would queue
# faster (cluster courtesy) before treating this as settled.
# H_RT=48:00:00 is an unvalidated placeholder — no walltime precedent exists
# for this workload; revise once a real table-clean run's throughput is known.
case "$STAGE" in
    filter)      ROLE=FILTER;      H_RT=2:00:00;  GPU_MEMORY=8G;  GPU_C=7.0 ;;
    ocr)         ROLE=DOC_LM;      H_RT=72:00:00; GPU_MEMORY=24G; GPU_C=7.0 ;;
    table_clean) ROLE=TABLE_CLEAN; H_RT=48:00:00; GPU_MEMORY=80G; GPU_C=8.0 ;;
    extract)     ROLE=MEAS_LM;     H_RT=24:00:00; GPU_MEMORY=80G; GPU_C=9.0 ;;
    judge)       ROLE=JUDGE;       H_RT=24:00:00; GPU_MEMORY=80G; GPU_C=9.0 ;;
    *)
        echo "Error: configs/${ID}.yaml params.stage must be filter, ocr, table_clean, extract, or judge (got '$STAGE')." >&2
        exit 1
        ;;
esac

mkdir -p "$REPO_DIR/out"

QSUB_ARGS=(
    -N "job_$ID"
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
if [ -n "$GPU_TYPE" ]; then
    QSUB_ARGS+=(-l "gpu_type=$GPU_TYPE")
fi
if [ -n "${SGE_PROJECT:-}" ]; then
    QSUB_ARGS=(-P "$SGE_PROJECT" "${QSUB_ARGS[@]}")
fi
if [ -n "$HOLD_JID" ]; then
    QSUB_ARGS=(-hold_jid "$HOLD_JID" "${QSUB_ARGS[@]}")
fi

echo "Submitting $ID (stage=$STAGE, role=$ROLE, gpu_memory=$GPU_MEMORY, gpu_c=$GPU_C${GPU_TYPE:+, gpu_type=$GPU_TYPE}${HOLD_JID:+, hold_jid=$HOLD_JID})..."
qsub "${QSUB_ARGS[@]}" "$REPO_DIR/scripts/run_experiment_job.sh"
