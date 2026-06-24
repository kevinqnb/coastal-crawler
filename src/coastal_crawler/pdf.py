"""Shared PDF download utilities — headers, accessibility check, and download."""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx

from coastal_crawler.config import get_settings


def normalize_pdf_url(url: str) -> str:
    """Normalize known broken URL patterns before making requests."""
    # CrossRef registers v2 TDM URLs for Wiley but only v1 works.
    return url.replace(
        "api.wiley.com/onlinelibrary/tdm/v2/",
        "api.wiley.com/onlinelibrary/tdm/v1/",
    )


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
    try:
        resp = httpx.get(url, headers=pdf_headers(discovered_from, url), timeout=60, follow_redirects=True)
        return resp.status_code in (200, 206)
    except Exception:
        return False


def download_pdf(url: str, discovered_from: str | None = None) -> Path:
    """Download *url* to a temporary file and return its Path."""
    url = normalize_pdf_url(url)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    pdf_path = Path(tmp.name)
    tmp.close()
    resp = httpx.get(url, headers=pdf_headers(discovered_from, url), timeout=60, follow_redirects=True)
    resp.raise_for_status()
    pdf_path.write_bytes(resp.content)
    return pdf_path
