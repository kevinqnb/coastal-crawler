#!/usr/bin/env bash
# Launch a vLLM server for one of the pipeline's models.
#
# All parameters are read from .env (or environment variables already set),
# namespaced by role: FILTER_*, DOC_LM_*, or MEAS_LM_*. The corresponding
# client code (relevance_filter.py / adapter.py's build_scholarlm_adapter)
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
# Usage:
#   ./scripts/serve_model.sh FILTER
#   ./scripts/serve_model.sh DOC_LM 0
#   ./scripts/serve_model.sh MEAS_LM 1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ROLE="${1:?Usage: serve_model.sh <FILTER|DOC_LM|MEAS_LM> [gpu_id]}"
case "$ROLE" in
    FILTER|DOC_LM|MEAS_LM) ;;
    *)
        echo "Error: unknown role '$ROLE' (expected FILTER, DOC_LM, or MEAS_LM)." >&2
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

# ---- GPU pinning --------------------------------------------------------
if [ -n "$GPU_ID" ]; then
    export CUDA_VISIBLE_DEVICES="$GPU_ID"
fi

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
PORT="${!port_var:-8000}"
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
    BIND_ARGS=(--bind "$HF_CACHE:$HF_CACHE")

    if [[ "$MODEL" = /* ]]; then
        MODEL_DIR="$(dirname "$MODEL")"
        BIND_ARGS+=(--bind "$MODEL_DIR:$MODEL_DIR")
    fi

    echo "Serving (Singularity): $ROLE = $MODEL"
    echo "  sif=$SIF_PATH"
    echo "  port=$PORT  tp=$TENSOR_PARALLEL_SIZE  dtype=$DTYPE  seed=$SEED${GPU_ID:+  gpu=$GPU_ID}"
    [ ${#EXTRA_ARGS[@]} -gt 0 ] && echo "  extra: ${EXTRA_ARGS[*]}"

    exec singularity run \
        --nv \
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
