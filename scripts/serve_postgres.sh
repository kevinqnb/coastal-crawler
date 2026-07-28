#!/bin/bash -l
#$ -P mcnet
#$ -l h_rt=24:00:00
#$ -pe omp 4
#$ -o out/serve_postgres_out.txt
#$ -e out/serve_postgres_error.txt
#$ -m e
#$ -notify
#
# Dedicated, long-running Postgres server job. This is the *only* process
# that starts/stops the shared Postgres instance in /projectnb/mcnet/kevin/my_pgserver
# — scripts/wiley_download.sh and submit_extract_job.sh (via cluster.local.sh)
# just discover its current hostname from PG_HOST_FILE below and connect.
#
# Why a separate job: wiley_download.sh and submit_extract_job.sh request
# different resources (plain CPU vs GPUs), so SGE is not guaranteed to land
# them on the same node. Postgres previously only listened on 127.0.0.1, so
# whichever of those two jobs didn't share a node with the one managing
# Postgres simply couldn't connect. Worse, each job independently ran its own
# `pg_ctl start` against the same on-disk data directory — if that happened
# from two different nodes at once, pg_ctl's "is the old postmaster still
# alive" check is PID-based and only meaningful on the same host, so it could
# decide a live remote postmaster's lock was stale and start a second one
# against the same files. Splitting Postgres into its own job removes both
# problems: exactly one process manages its lifecycle, and consumers just
# read this job's node from a shared file.
module load postgresql/18.1

PGDATA_DIR=/projectnb/mcnet/kevin/my_pgserver
PG_HOST_FILE=/projectnb/mcnet/kevin/my_pgserver_host.txt

pg_ctl -D "$PGDATA_DIR" -l /projectnb/mcnet/kevin/my_pgserver.log start
hostname -f > "$PG_HOST_FILE"
echo "Postgres serving on $(cat "$PG_HOST_FILE"), data dir $PGDATA_DIR"

SLEEP_PID=""
_shutdown() {
    echo "Stopping PostgreSQL server..."
    rm -f "$PG_HOST_FILE"
    pg_ctl -D "$PGDATA_DIR" -m fast -t 60 stop
    [ -n "$SLEEP_PID" ] && kill "$SLEEP_PID" 2>/dev/null
    exit 0
}
# SGE's -notify sends SIGUSR2 (then SIGUSR1) ahead of the hard walltime
# SIGKILL, giving us a chance to shut Postgres down cleanly instead of it
# crash-recovering (WAL replay) on the next start.
trap _shutdown EXIT SIGTERM SIGUSR1 SIGUSR2

sleep infinity &
SLEEP_PID=$!
wait "$SLEEP_PID"
