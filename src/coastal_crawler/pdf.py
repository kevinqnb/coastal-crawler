"""Shared PDF download utilities — headers, accessibility check, and download."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import httpx

from coastal_crawler.config import get_settings

# Wiley's published TDM rate limits are "up to 3 articles/second" and "up to
# 60 requests per 10 minutes." The second limit is the binding one — 60 per
# 10 minutes averages out to one request per 10 seconds, far stricter than
# 3/second — and Wiley's own guidance is to build in a 10s delay between
# requests. Exceeding it doesn't come back as a 429; Wiley's Apigee gateway
# returns a bare HTTP 500 with a "policies.ratelimit.QuotaViolation" fault
# body, which is indistinguishable from a genuine server error unless you
# read the body.
#
# This only paces requests within a single process. It assumes the pipeline
# is run one filter/extraction job at a time (per project convention) — it
# does not coordinate across concurrent processes, and a fresh process
# doesn't know how much quota a just-finished process already used.
_WILEY_MIN_INTERVAL_SECONDS = 10.0
_last_wiley_request_at: float | None = None


def normalize_pdf_url(url: str) -> str:
    """Normalize known broken URL patterns before making requests."""
    # CrossRef registers v2 TDM URLs for Wiley but only v1 works.
    return url.replace(
        "api.wiley.com/onlinelibrary/tdm/v2/",
        "api.wiley.com/onlinelibrary/tdm/v1/",
    )


def _is_wiley_request(discovered_from: str | None, url: str) -> bool:
    return discovered_from == "wiley" or "wiley" in url.lower()


def _throttle_wiley(discovered_from: str | None, url: str) -> None:
    """Sleep as needed so consecutive Wiley TDM requests stay >= 10s apart."""
    global _last_wiley_request_at
    if not _is_wiley_request(discovered_from, url):
        return
    if _last_wiley_request_at is not None:
        elapsed = time.monotonic() - _last_wiley_request_at
        remaining = _WILEY_MIN_INTERVAL_SECONDS - elapsed
        if remaining > 0:
            time.sleep(remaining)
    _last_wiley_request_at = time.monotonic()


def pdf_headers(discovered_from: str | None, url: str) -> dict[str, str]:
    """Build request headers for a PDF URL, including Wiley auth when applicable."""
    headers: dict[str, str] = {"User-Agent": "coastal-crawler/1.0"}
    if discovered_from == "wiley" or "wiley" in url.lower():
        key = get_settings().wiley_api_key
        if key:
            headers["Wiley-TDM-Client-Token"] = key
    return headers


def check_pdf_accessible(url: str, discovered_from: str | None = None) -> bool:
    """Return True if the PDF URL returns 200 or 206.

    Performs a full (uncached) download so redirect chains are followed to
    completion.  Content is discarded.  Any exception returns False.
    """
    url = normalize_pdf_url(url)
    _throttle_wiley(discovered_from, url)
    try:
        resp = httpx.get(url, headers=pdf_headers(discovered_from, url), timeout=60, follow_redirects=True)
        return resp.status_code in (200, 206)
    except Exception:
        return False


def download_pdf(url: str, discovered_from: str | None = None) -> Path:
    """Download *url* to a temporary file and return its Path."""
    url = normalize_pdf_url(url)
    _throttle_wiley(discovered_from, url)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    pdf_path = Path(tmp.name)
    tmp.close()
    resp = httpx.get(url, headers=pdf_headers(discovered_from, url), timeout=60, follow_redirects=True)
    resp.raise_for_status()
    pdf_path.write_bytes(resp.content)
    return pdf_path
