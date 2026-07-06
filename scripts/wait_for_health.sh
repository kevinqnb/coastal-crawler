#!/usr/bin/env bash
# Poll a vLLM server's /health endpoint until it responds, the server process
# dies, or a timeout elapses.
#
# Usage:
#   ./scripts/wait_for_health.sh <port> <server_pid> [max_wait_seconds=300]
set -euo pipefail

PORT="${1:?Usage: wait_for_health.sh <port> <server_pid> [max_wait_seconds]}"
SERVER_PID="${2:?Usage: wait_for_health.sh <port> <server_pid> [max_wait_seconds]}"
MAX_WAIT="${3:-300}"

HEALTH_URL="http://localhost:$PORT/health"
ELAPSED=0

echo "Waiting for server at $HEALTH_URL..."
until curl -sf "$HEALTH_URL" > /dev/null 2>&1; do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "Error: server process (PID $SERVER_PID) exited unexpectedly." >&2
        exit 1
    fi
    if [ "$ELAPSED" -ge "$MAX_WAIT" ]; then
        echo "Error: server at $HEALTH_URL did not become ready within ${MAX_WAIT}s." >&2
        exit 1
    fi
    sleep 10
    ELAPSED=$((ELAPSED + 10))
done
echo "Server at $HEALTH_URL ready after ${ELAPSED}s."
