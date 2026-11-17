"""Cached OpenAlex works client + DOI normalization (general-purpose helpers).

Every response is cached on disk, keyed by the exact request params, so reruns cost no
credits and the responses behind any number stay inspectable. Non-2xx responses raise
``OpenAlexHTTPError`` (429 is retried by ``get_with_retry``; 5xx and transport errors are
retried a bounded number of times, then raise). The API key is redacted from every error
message. Nothing here swallows an error or substitutes an empty result for a failed request.

Costs (measured 2026-11-17): search = 10 credits, filter lookup = 1 credit regardless
of how many DOIs it holds, single ``/works/doi:...`` = 0.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

from coastal_crawler.sources.http import get_with_retry

BASE_URL = "https://api.openalex.org/works"
_DOI_URL_PREFIX = re.compile(r"^(?:https?://)?(?:dx\.)?doi\.org/", re.IGNORECASE)
_DOI_LABEL_PREFIX = re.compile(r"^doi:\s*", re.IGNORECASE)
_DOI_SHAPE = re.compile(r"^10\.\d{4,9}/\S+$")


class OpenAlexHTTPError(RuntimeError):
    """Request failed after retries. Message carries status + params, never the API key."""

    def __init__(self, status_code: int | None, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def norm_doi(raw: str) -> str:
    """Lower-cased bare DOI ('10.x/y'). Raises ValueError if ``raw`` is not DOI-shaped."""
    d = unquote(raw.strip())
    d = _DOI_URL_PREFIX.sub("", d)
    d = _DOI_LABEL_PREFIX.sub("", d).lower()
    if not _DOI_SHAPE.match(d):
        raise ValueError(f"not a DOI: {raw!r}")
    return d


class OpenAlexClient:
    def __init__(
        self,
        api_key: str,
        cache_dir: Path,
        user_agent: str,
        request_delay: float,
        timeout: float,
        max_server_retries: int,
        retry_backoff: float,
    ) -> None:
        assert api_key, "api_key is empty"
        self._max_server_retries = max_server_retries
        self._retry_backoff = retry_backoff
        self._api_key = api_key
        self._cache_dir = cache_dir
        self._headers = {"User-Agent": user_agent}
        self._delay = request_delay
        self._client = httpx.Client(timeout=timeout)
        cache_dir.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        self._client.close()

    def get(self, params: dict[str, Any], path: str = "") -> dict[str, Any]:
        """GET ``/works{path}`` with disk cache. ``path`` e.g. '/doi:10.x/y' for one work."""
        key = hashlib.sha256(json.dumps([path, params], sort_keys=True).encode()).hexdigest()
        cache_file = self._cache_dir / f"{key}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text())
        resp = self._request(path, params)
        body = resp.json()
        body["_request"] = {"path": path, "params": params}
        body["_cost_usd"] = float(resp.headers["x-ratelimit-cost-usd"])
        cache_file.write_text(json.dumps(body))
        time.sleep(self._delay)
        return body

    def _request(self, path: str, params: dict[str, Any]) -> httpx.Response:
        """One request; retries 5xx/transport errors ``max_server_retries`` times, then raises."""
        for attempt in range(self._max_server_retries + 1):
            try:
                return get_with_retry(
                    self._client, BASE_URL + path, {**params, "api_key": self._api_key}, self._headers, self._delay
                )
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code >= 500 and attempt < self._max_server_retries:
                    time.sleep(self._retry_backoff * (attempt + 1))
                    continue
                raise OpenAlexHTTPError(code, f"HTTP {code} for {path} {params}") from None
            except httpx.TransportError as e:
                if attempt < self._max_server_retries:
                    time.sleep(self._retry_backoff * (attempt + 1))
                    continue
                raise OpenAlexHTTPError(None, f"{type(e).__name__} for {path} {params}") from None
        raise AssertionError("unreachable")

    def works_by_openalex_ids(self, ids: list[str], select: str, batch_size: int) -> dict[str, dict[str, Any]]:
        """Look up works by short OpenAlex id ('W123'). Returns {id: work}; absent ids are absent."""
        assert len(set(ids)) == len(ids), "duplicate ids passed"
        found: dict[str, dict[str, Any]] = {}
        for i in range(0, len(ids), batch_size):
            chunk = ids[i : i + batch_size]
            body = self.get({"filter": "openalex:" + "|".join(chunk), "per-page": batch_size, "select": select})
            assert body["meta"]["count"] <= batch_size, "batch response truncated"
            for w in body["results"]:
                wid = w["id"].rsplit("/", 1)[-1]
                assert wid in chunk, f"OpenAlex returned {wid!r} not in request"
                found[wid] = w
        return found

    def works_by_dois(self, dois: list[str], select: str, batch_size: int) -> dict[str, dict[str, Any]]:
        """Look up works by normalized DOI. Returns {doi: work} for DOIs OpenAlex has.

        DOIs absent from the result are absent from OpenAlex (a 404 on the single-work
        path, or missing from the batch response) -- the caller decides what that means.
        DOIs containing ',' or '|' break filter syntax and go through the single-work path.
        """
        assert len(set(dois)) == len(dois), "duplicate DOIs passed"
        found: dict[str, dict[str, Any]] = {}
        plain = [d for d in dois if "," not in d and "|" not in d]
        odd = [d for d in dois if d not in set(plain)]
        retry_single = list(odd)
        stray_ids: set[str] = set()  # batch results whose DOI we did not request (aliases)
        for i in range(0, len(plain), batch_size):
            chunk = plain[i : i + batch_size]
            body = self.get({"filter": "doi:" + "|".join(chunk), "per-page": batch_size, "select": select})
            assert body["meta"]["count"] <= batch_size, "batch response truncated"
            for w in body["results"]:
                d = norm_doi(w["doi"])
                if d in chunk:
                    assert d not in found, f"duplicate result for {d!r}"
                    found[d] = w
                else:
                    stray_ids.add(w["id"])
            retry_single += [d for d in chunk if d not in found]
        # DOIs not matched exactly: the work may exist under an alias DOI (its ``doi``
        # field then differs from the requested one), or not exist at all (404).
        single_ids: set[str] = set()
        for d in retry_single:
            try:
                w = self.get({"select": select}, path=f"/doi:{d}")
            except OpenAlexHTTPError as e:
                if e.status_code != 404:
                    raise
                continue
            found[d] = w
            single_ids.add(w["id"])
        assert stray_ids <= single_ids, f"batch results unexplained by single lookups: {stray_ids - single_ids}"
        return found
