#!/bin/bash -l
#$ -P mcnet
#$ -l h_rt=24:00:00
#$ -pe omp 8
#$ -o out/wiley_download_out.txt
#$ -e out/wiley_download_error.txt
#$ -m e

cd /projectnb/mcnet/kevin/coastal/coastal-crawler

# Postgres is managed by its own job (scripts/serve_postgres.sh), since it
# and this job aren't guaranteed to land on the same node. Discover where
# it's currently running instead of starting a competing instance here.
PG_HOST_FILE=/projectnb/mcnet/kevin/my_pgserver_host.txt
if [ ! -s "$PG_HOST_FILE" ]; then
    echo "ERROR: $PG_HOST_FILE missing or empty — is scripts/serve_postgres.sh running?" >&2
    exit 1
fi
export DATABASE_URL="postgresql://coastal_app@$(cat "$PG_HOST_FILE"):5432/coastal-crawler"

uv run python scripts/wiley_download.py