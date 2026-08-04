#!/usr/bin/env python3
"""Print one free TCP port on this host.

Used by run_experiment_job.sh to give each job's model server its own port,
so two same-role experiments (e.g. two "ocr" configs) don't collide on the
static <ROLE>_PORT from .env if SGE schedules them onto the same host.
"""
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind(("", 0))
    print(s.getsockname()[1])
