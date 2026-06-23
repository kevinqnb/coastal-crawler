#!/usr/bin/env bash
# Launch a vLLM server for the abstract relevance filter.
#
# All parameters are read from .env (or environment variables already set).
# Edit .env to change the model, quantization, GPU count, etc. — the filter
# client reads the same file, so inference settings stay in sync automatically.
#
# Singularity mode (recommended on HPC clusters):
#   Set FILTER_SIF_PATH in .env to the path of a vLLM .sif image.
#   The script will run vLLM inside the container with GPU passthrough.
#   Build the SIF once with:
#     singularity pull vllm-openai.sif docker://vllm/vllm-openai:<tag>
#
# Direct mode (local workstation with vllm installed):
#   Leave FILTER_SIF_PATH unset.
#
# HuggingFace cache:
#   The standard HF_HOME environment variable is respected. Set it in your
#   shell profile or job script before calling this script — do not put it
#   in .env. Defaults to ~/.cache/huggingface if unset.
#
# Usage:
#   ./scripts/serve_filter_model.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

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

# ---- Required ---------------------------------------------------------------
if [ -z "${FILTER_MODEL:-}" ]; then
    echo "Error: FILTER_MODEL is not set." >&2
    exit 1
fi

# ---- Serving args -----------------------------------------------------------
FILTER_PORT="${FILTER_PORT:-8000}"
FILTER_TENSOR_PARALLEL_SIZE="${FILTER_TENSOR_PARALLEL_SIZE:-1}"
FILTER_GPU_MEMORY_UTILIZATION="${FILTER_GPU_MEMORY_UTILIZATION:-0.90}"
FILTER_DTYPE="${FILTER_DTYPE:-auto}"
FILTER_SEED="${FILTER_SEED:-0}"

EXTRA_ARGS=()
if [ -n "${FILTER_QUANTIZATION:-}" ]; then
    EXTRA_ARGS+=(--quantization "$FILTER_QUANTIZATION")
fi
if [ -n "${FILTER_MAX_MODEL_LEN:-}" ]; then
    EXTRA_ARGS+=(--max-model-len "$FILTER_MAX_MODEL_LEN")
fi

# Shared server flags (same regardless of launch mode).
SERVER_FLAGS=(
    --host 0.0.0.0
    --port "$FILTER_PORT"
    --served-model-name "$FILTER_MODEL"
    --seed "$FILTER_SEED"
    --dtype "$FILTER_DTYPE"
    --tensor-parallel-size "$FILTER_TENSOR_PARALLEL_SIZE"
    --gpu-memory-utilization "$FILTER_GPU_MEMORY_UTILIZATION"
    "${EXTRA_ARGS[@]}"
)

# ---- Launch -----------------------------------------------------------------
if [ -n "${FILTER_SIF_PATH:-}" ]; then
    # ---- Singularity mode ---------------------------------------------------
    # The vllm/vllm-openai container's entrypoint is the API server directly
    # (python3 -m vllm.entrypoints.openai.api_server), so we pass --model and
    # flags straight through — NOT "vllm serve MODEL".
    if [ ! -f "$FILTER_SIF_PATH" ]; then
        echo "Error: FILTER_SIF_PATH=$FILTER_SIF_PATH does not exist." >&2
        echo "Build it with: singularity pull <path>.sif docker://vllm/vllm-openai:<tag>" >&2
        exit 1
    fi

    HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}"
    BIND_ARGS=(--bind "$HF_CACHE:/root/.cache/huggingface")

    if [[ "$FILTER_MODEL" = /* ]]; then
        MODEL_DIR="$(dirname "$FILTER_MODEL")"
        BIND_ARGS+=(--bind "$MODEL_DIR:$MODEL_DIR")
    fi

    echo "Serving (Singularity): $FILTER_MODEL"
    echo "  sif=$FILTER_SIF_PATH"
    echo "  port=$FILTER_PORT  tp=$FILTER_TENSOR_PARALLEL_SIZE  dtype=$FILTER_DTYPE  seed=$FILTER_SEED"
    [ ${#EXTRA_ARGS[@]} -gt 0 ] && echo "  extra: ${EXTRA_ARGS[*]}"

    exec singularity run \
        --nv \
        "${BIND_ARGS[@]}" \
        "$FILTER_SIF_PATH" \
        --model "$FILTER_MODEL" \
        "${SERVER_FLAGS[@]}"
else
    # ---- Direct mode --------------------------------------------------------
    # Uses the vllm CLI: "vllm serve MODEL [flags]"
    echo "Serving (direct): $FILTER_MODEL"
    echo "  port=$FILTER_PORT  tp=$FILTER_TENSOR_PARALLEL_SIZE  dtype=$FILTER_DTYPE  seed=$FILTER_SEED"
    [ ${#EXTRA_ARGS[@]} -gt 0 ] && echo "  extra: ${EXTRA_ARGS[*]}"

    exec vllm serve "$FILTER_MODEL" "${SERVER_FLAGS[@]}"
fi
