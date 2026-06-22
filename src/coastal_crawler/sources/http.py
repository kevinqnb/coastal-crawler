"""Shared HTTP helpers for discovery sources."""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_MAX_RETRIES = 5
_MIN_RETRY_WAIT = 60.0  # seconds — rate limit windows are typically 1 minute


def get_with_retry(
    client: httpx.Client,
    url: str,
    params: dict[str, Any],
    headers: dict[str, str],
    base_delay: float,
) -> httpx.Response:
    """GET with retry on 429.

    Respects the Retry-After response header when present; otherwise waits at
    least _MIN_RETRY_WAIT seconds (rate limit windows are typically ~1 minute)
    with exponential backoff on repeated failures.  Raises the last
    HTTPStatusError if all retries are exhausted.
    """
    for attempt in range(_MAX_RETRIES):
        resp = client.get(url, params=params, headers=headers)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp

        retry_after = resp.headers.get("Retry-After")
        wait = float(retry_after) if retry_after else max(_MIN_RETRY_WAIT, base_delay * (2 ** attempt))
        log.warning("rate_limited", url=url, attempt=attempt + 1, wait_seconds=wait)
        time.sleep(wait)

    resp.raise_for_status()
    return resp  # unreachable; raise_for_status above will fire
