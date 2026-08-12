#!/bin/bash -l
#
# One-off SGE job: run the full pytest suite against crawler_test.
#
# Not a contract experiment (scripts/submit.sh is for those — see
# notes/hub/conventions.md) and not a permanent pipeline stage; this is a
# small infra job, same pattern as scripts/serve_postgres.sh: a
# self-contained script qsub'd directly rather than through a wrapper.
# Anything expected to run 15+ minutes on this cluster goes through qsub
# instead of the login node — see CLAUDE.local.md.
#
#   qsub -P mcnet -v REPO_DIR="$REPO_DIR" scripts/run_tests_job.sh
#
#$ -l h_rt=00:30:00
#$ -pe omp 4
#$ -o out/run_tests_out.txt
#$ -e out/run_tests_error.txt
#$ -m e

: "${REPO_DIR:?REPO_DIR must be exported by the submitter (e.g. qsub -v REPO_DIR=...)}"
cd "$REPO_DIR"

if [ -f scripts/cluster.local.sh ]; then
    source scripts/cluster.local.sh
else
    echo "scripts/cluster.local.sh not found — copy scripts/cluster.local.sh.example and edit it for your environment." >&2
    exit 1
fi

set -euo pipefail

# cluster.local.sh builds DATABASE_URL against the production db; tests need
# their own disposable database instead (crawler_test's fixtures TRUNCATE
# and drop_all — never point this at production).
PG_HOST_FILE=/projectnb/mcnet/kevin/my_pgserver_host.txt
export TEST_DATABASE_URL="postgresql://coastal_app@$(cat "$PG_HOST_FILE"):5432/crawler_test"

uv run --with pytest --with pytest-mock pytest -q
