#!/usr/bin/env python3
"""Test Wiley TDM PDF download for a given DOI.

Replicates the exact logic used by `coastal-crawler filter` and the extraction
worker: CrossRef URL lookup → check_pdf_accessible → download_pdf.

Usage:
    uv run scripts/test_wiley_download.py 10.1002/lno.12345
"""

import sys
import httpx
from coastal_crawler.pdf import check_pdf_accessible, download_pdf, normalize_pdf_url

_CROSSREF_URL = "https://api.crossref.org/works"


def crossref_tdm_url(doi: str) -> str | None:
    """Mirror _extract_tdm_url logic from sources/wiley.py."""
    resp = httpx.get(
        f"{_CROSSREF_URL}/{doi}",
        headers={"User-Agent": "coastal-crawler/0.1"},
        timeout=15,
        follow_redirects=True,
    )
    resp.raise_for_status()
    links = resp.json().get("message", {}).get("link") or []
    print(f"CrossRef links:")
    for link in links:
        print(f"  {link.get('intended-application'):30s} {link.get('URL')}")
    for link in links:
        if link.get("intended-application") == "text-mining":
            url = link.get("URL")
            return normalize_pdf_url(url) if url else None
    return None


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: test_wiley_download.py <doi>")
        sys.exit(1)

    doi = sys.argv[1]
    print(f"\nDOI: {doi}")

    url = crossref_tdm_url(doi)
    if not url:
        print("\nNo text-mining link in CrossRef — using constructed URL")
        url = f"https://api.wiley.com/onlinelibrary/tdm/v1/articles/{doi}"

    print(f"\nURL: {url}")

    from coastal_crawler.pdf import pdf_headers
    headers = pdf_headers("wiley", url)
    print(f"Request headers: {headers}")

    print("\nRaw GET (diagnostic) ...")
    try:
        resp = httpx.get(url, headers=headers, timeout=60, follow_redirects=True)
        print(f"  Status:       {resp.status_code}")
        print(f"  Content-Type: {resp.headers.get('content-type')}")
        print(f"  Bytes:        {len(resp.content)}")
        if resp.status_code not in (200, 206):
            print(f"  Body:         {resp.text[:500]}")
    except Exception as exc:
        print(f"  Exception: {exc}")

    print("\ncheck_pdf_accessible() ...")
    accessible = check_pdf_accessible(url, "wiley")
    print(f"Result: {accessible}")

    if accessible:
        print("\ndownload_pdf() ...")
        path = download_pdf(url, "wiley")
        print(f"Written to: {path}  ({path.stat().st_size} bytes)")
    else:
        print("\nMarked inaccessible — would not proceed to filter or extraction.")


if __name__ == "__main__":
    main()
