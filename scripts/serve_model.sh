#!/usr/bin/env bash
# Launch a vLLM server for one of the pipeline's models.
#
# All parameters are read from .env (or environment variables already set),
# namespaced by role: FILTER_*, DOC_LM_*, or MEAS_LM_*. The corresponding
# client code (relevance_filter.py / adapter.py's build_extraction_adapter)
# reads the same file, so inference settings stay in sync automatically.
#
# GPU pinning: pass a GPU id as the second argument to pin this server to
# one GPU via CUDA_VISIBLE_DEVICES. Needed when colocating multiple servers
# (e.g. DOC_LM and MEAS_LM) on the same multi-GPU node.
#
# Singularity mode (recommended on HPC clusters):
#   Set <ROLE>_SIF_PATH in .env to the path of a vLLM .sif image.
#   The script will run vLLM inside the container with GPU passthrough.
#   Build the SIF once with:
#     singularity pull vllm-openai.sif docker://vllm/vllm-openai:<tag>
#
# Direct mode (local workstation with vllm installed):
#   Leave <ROLE>_SIF_PATH unset.
#
# HuggingFace cache:
#   The standard HF_HOME environment variable is respected. Set it in your
#   shell profile or job script before calling this script — do not put it
#   in .env. Defaults to ~/.cache/huggingface if unset.
#
# tiktoken-rs / Harmony vocab cache (gpt-oss models):
#   vLLM's Harmony parser (used for gpt-oss's response format) loads its
#   tokenizer via openai_harmony, which downloads and caches a vocab file
#   through tiktoken-rs. tiktoken-rs reads TIKTOKEN_RS_CACHE_DIR (its own
#   var — not the standard TIKTOKEN_CACHE_DIR) and otherwise falls back to
#   a system temp dir, which on this cluster is the per-job scratch dir.
#   Inside the Singularity container that path isn't bound/visible, so
#   creating it lands on the container's read-only root filesystem —
#   crashing gpt-oss servers outright on first request (see
#   out/extract_error.txt). If the caller hasn't already exported
#   TIKTOKEN_RS_CACHE_DIR, this script creates (persistently — it's a tiny,
#   reusable cache) and owns its own per-role directory under the repo,
#   and explicitly binds it into the container below.
#
# Port override:
#   Set PORT_OVERRIDE to bind a specific port instead of <ROLE>_PORT from
#   .env. Used by run_experiment_job.sh so two same-role experiments don't
#   collide on the static .env port if SGE schedules them onto the same
#   host — harmless no-op for direct/manual invocations, which never set it.
#
# Usage:
#   ./scripts/serve_model.sh FILTER
#   ./scripts/serve_model.sh DOC_LM 0
#   ./scripts/serve_model.sh MEAS_LM 1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ROLE="${1:?Usage: serve_model.sh <FILTER|DOC_LM|TABLE_CLEAN|MEAS_LM> [gpu_id]}"
case "$ROLE" in
    FILTER|DOC_LM|TABLE_CLEAN|MEAS_LM) ;;
    *)
        echo "Error: unknown role '$ROLE' (expected FILTER, DOC_LM, TABLE_CLEAN, or MEAS_LM)." >&2
        exit 1
        ;;
esac
GPU_ID="${2:-}"

# Load .env from the repo root if present.
ENV_FILE="$REPO_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
else
    echo "Warning: .env not found at $ENV_FILE — relying on environment variables." >&2
fi

# Per-run overrides (configs/<id>.yaml params.env), layered over .env. Set only
# by scripts/run_experiment_job.sh, which writes the file; without this the
# client (run_experiment.py applies params.env itself) and this server could
# disagree on <ROLE>_MODEL etc. because .env is re-sourced above. An unset
# PARAMS_ENV_FILE means "no overrides"; a set one that is missing is an error.
if [ -n "${PARAMS_ENV_FILE:-}" ]; then
    if [ ! -f "$PARAMS_ENV_FILE" ]; then
        echo "Error: PARAMS_ENV_FILE=$PARAMS_ENV_FILE does not exist." >&2
        exit 1
    fi
    set -a
    # shellcheck source=/dev/null
    source "$PARAMS_ENV_FILE"
    set +a
fi

# ---- GPU pinning --------------------------------------------------------
if [ -n "$GPU_ID" ]; then
    export CUDA_VISIBLE_DEVICES="$GPU_ID"
fi

# ---- DeepGEMM disabled globally (see header comment) --------------------
# vLLM already auto-disables DeepGEMM as the *inference* kernel on Blackwell
# GPUs for FP8 block-scaled models ("DeepGemm E8M0 scale format causes
# accuracy degradation for this architecture. Falling back to CUTLASS.") —
# but its separate startup *warmup* path (gpu_worker.compile_or_warm_up_model
# -> deep_gemm_warmup) isn't gated by that same auto-disable and
# unconditionally calls into DeepGEMM's fp8_gemm_nt regardless, which then
# crashes because DeepGEMM has no valid kernel "recipe" for this
# GPU+E8M0-scale combination at all: `RuntimeError: Assertion error
# (.../utils/layout.hpp:76): Unknown recipe` (see the
# 2026-09-05-table-clean-smoke-01 job log, TABLE_CLEAN's Qwen3.5-27b-FP8 on
# a Blackwell node). vLLM's own warning names the fix: set this to disable
# DeepGEMM everywhere rather than just for inference. Harmless for every
# other role today (none use FP8), so applied unconditionally rather than
# gated per-ROLE.
export VLLM_USE_DEEP_GEMM=0

# ---- tiktoken-rs / Harmony vocab cache (see header comment) -------------
TIKTOKEN_RS_CACHE_DIR="${TIKTOKEN_RS_CACHE_DIR:-$REPO_ROOT/.cache/tiktoken-rs/$ROLE}"
mkdir -p "$TIKTOKEN_RS_CACHE_DIR"
export TIKTOKEN_RS_CACHE_DIR

# ---- Indirect through ${ROLE}_* env vars --------------------------------
model_var="${ROLE}_MODEL"
port_var="${ROLE}_PORT"
tp_var="${ROLE}_TENSOR_PARALLEL_SIZE"
gpu_mem_var="${ROLE}_GPU_MEMORY_UTILIZATION"
dtype_var="${ROLE}_DTYPE"
seed_var="${ROLE}_SEED"
quant_var="${ROLE}_QUANTIZATION"
max_len_var="${ROLE}_MAX_MODEL_LEN"
sif_var="${ROLE}_SIF_PATH"

MODEL="${!model_var:-}"
SIF_PATH="${!sif_var:-}"

# ---- Required ---------------------------------------------------------------
if [ -z "$MODEL" ]; then
    echo "Error: $model_var is not set." >&2
    exit 1
fi

# ---- Serving args -----------------------------------------------------------
PORT="${PORT_OVERRIDE:-${!port_var:-8000}}"
TENSOR_PARALLEL_SIZE="${!tp_var:-1}"
GPU_MEMORY_UTILIZATION="${!gpu_mem_var:-0.90}"
DTYPE="${!dtype_var:-auto}"
SEED="${!seed_var:-0}"
QUANTIZATION="${!quant_var:-}"
MAX_MODEL_LEN="${!max_len_var:-}"

EXTRA_ARGS=()
if [ -n "$QUANTIZATION" ]; then
    EXTRA_ARGS+=(--quantization "$QUANTIZATION")
fi
if [ -n "$MAX_MODEL_LEN" ]; then
    EXTRA_ARGS+=(--max-model-len "$MAX_MODEL_LEN")
fi

# Shared server flags (same regardless of launch mode).
SERVER_FLAGS=(
    --host 0.0.0.0
    --port "$PORT"
    --served-model-name "$MODEL"
    --seed "$SEED"
    --dtype "$DTYPE"
    --tensor-parallel-size "$TENSOR_PARALLEL_SIZE"
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
    "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
)

# ---- Launch -----------------------------------------------------------------
if [ -n "$SIF_PATH" ]; then
    # ---- Singularity mode ---------------------------------------------------
    # The vllm/vllm-openai container's entrypoint is the API server directly
    # (python3 -m vllm.entrypoints.openai.api_server), so we pass --model and
    # flags straight through — NOT "vllm serve MODEL".
    if [ ! -f "$SIF_PATH" ]; then
        echo "Error: $sif_var=$SIF_PATH does not exist." >&2
        echo "Build it with: singularity pull <path>.sif docker://vllm/vllm-openai:<tag>" >&2
        exit 1
    fi

    HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}"
    BIND_ARGS=(--bind "$HF_CACHE:$HF_CACHE" --bind "$TIKTOKEN_RS_CACHE_DIR:$TIKTOKEN_RS_CACHE_DIR")

    if [[ "$MODEL" = /* ]]; then
        MODEL_DIR="$(dirname "$MODEL")"
        BIND_ARGS+=(--bind "$MODEL_DIR:$MODEL_DIR")
    fi

    echo "Serving (Singularity): $ROLE = $MODEL"
    echo "  sif=$SIF_PATH"
    echo "  port=$PORT  tp=$TENSOR_PARALLEL_SIZE  dtype=$DTYPE  seed=$SEED${GPU_ID:+  gpu=$GPU_ID}"
    [ ${#EXTRA_ARGS[@]} -gt 0 ] && echo "  extra: ${EXTRA_ARGS[*]}"

    # CUDA_HOME/CUDA_PATH are pinned to the container's own CUDA install,
    # not left to pass through: Singularity (unlike Docker) passes the host
    # environment into the container by default, and cluster.local.sh's
    # `module load cuda/12.5` sets these to a host-only path
    # (/share/pkg.8/cuda/...) that doesn't exist inside the container's
    # filesystem. That's harmless for a plain vLLM forward pass, but an FP8
    # model that goes through DeepGEMM's JIT kernel compiler (e.g.
    # Qwen3.5-27b-FP8's TABLE_CLEAN role) has that compiler resolve nvcc via
    # $CUDA_HOME/bin/nvcc — the leaked host path means it looks for a
    # compiler that isn't there and crashes with `std::filesystem::exists
    # (nvcc_path)` (see the 2026-09-05-table-clean-smoke-01 job log).
    # Clearing it to empty (an earlier version of this fix) isn't enough
    # either — DeepGEMM's compiler asserts `not cuda_home.empty()`, it has
    # no built-in fallback. It must be set to a real, non-empty path that
    # exists *inside the container* — confirmed present at /usr/local/cuda
    # (this image's own CUDA 13 install) via `singularity exec --nv
    # <sif> /usr/local/cuda/bin/nvcc --version`. Only these two are pinned —
    # not `--cleanenv`, which would also drop HF_HOME/TIKTOKEN_RS_CACHE_DIR/
    # CUDA_VISIBLE_DEVICES pass-through that this script deliberately relies
    # on.
    exec singularity run \
        --nv \
        --env CUDA_HOME=/usr/local/cuda \
        --env CUDA_PATH=/usr/local/cuda \
        "${BIND_ARGS[@]}" \
        "$SIF_PATH" \
        --model "$MODEL" \
        "${SERVER_FLAGS[@]}"
else
    # ---- Direct mode --------------------------------------------------------
    # Uses the vllm CLI: "vllm serve MODEL [flags]"
    echo "Serving (direct): $ROLE = $MODEL"
    echo "  port=$PORT  tp=$TENSOR_PARALLEL_SIZE  dtype=$DTYPE  seed=$SEED${GPU_ID:+  gpu=$GPU_ID}"
    [ ${#EXTRA_ARGS[@]} -gt 0 ] && echo "  extra: ${EXTRA_ARGS[*]}"

    exec vllm serve "$MODEL" "${SERVER_FLAGS[@]}"
fi
