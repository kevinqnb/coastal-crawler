#!/bin/bash
# Personal launcher for submit_extract_job.sh. SGE jobs land in $HOME on this
# cluster rather than the submission directory, so REPO_DIR is passed
# through explicitly, and the SGE project/account (-P) lives only here, not
# in the tracked job script.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "Submitting extract job to SGE with REPO_DIR=$REPO_DIR"
qsub -P mcnet -v REPO_DIR="$REPO_DIR" "$REPO_DIR/scripts/submit_ocr_job.sh"