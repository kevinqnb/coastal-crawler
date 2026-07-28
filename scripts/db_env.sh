#!/bin/bash
#
# Point DATABASE_URL at wherever scripts/serve_postgres.sh is currently
# running, for interactive use (running `coastal-crawler ...` by hand from a
# login/interactive shell). Batch jobs (submit_extract_job.sh,
# wiley_download.sh) already do this discovery themselves.
#
# Must be sourced, not executed — `export` only affects the current shell,
# and running this as a subprocess (`bash db_env.sh`) would set DATABASE_URL
# in that subprocess and lose it the moment the script exits:
#
#   source scripts/db_env.sh
#   # or: . scripts/db_env.sh

PG_HOST_FILE=/projectnb/mcnet/kevin/my_pgserver_host.txt

if [ ! -s "$PG_HOST_FILE" ]; then
    echo "ERROR: $PG_HOST_FILE missing or empty — is scripts/serve_postgres.sh running?" >&2
    return 1 2>/dev/null || exit 1
fi

export DATABASE_URL="postgresql://coastal_app@$(cat "$PG_HOST_FILE"):5432/coastal-crawler"
echo "DATABASE_URL set to $DATABASE_URL"
