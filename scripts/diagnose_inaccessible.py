#!/usr/bin/env python3
"""Diagnose why Wiley papers are marked 'inaccessible'.

Read-only: re-requests each inaccessible paper's PDF URL and buckets the
result by actual status code / exception type, instead of the single bool
that `check_pdf_accessible` records. Helps distinguish:

  - 401/403       no TDM entitlement for this DOI/journal
  - 429           rate-limited by Wiley
  - 404           bad DOI -> TDM URL mapping
  - 5xx           Wiley-side error
  - timeout/DNS/other exception   network/transient

Usage:
    uv run scripts/diagnose_inaccessible.py [--limit N] [--delay SECONDS]
"""

from __future__ import annotations

import argparse
import time
from collections import Counter, defaultdict

import httpx
from sqlalchemy import select

from coastal_crawler.db.engine import get_session
from coastal_crawler.db.models import Paper
from coastal_crawler.pdf import normalize_pdf_url, pdf_headers

_DELAY_DEFAULT = 0.5
_BODY_PREVIEW_LEN = 300
# Headers that would signal "this is a disguised rate/concurrency limit" or
# otherwise tell us how long to back off, rather than a plain server error.
_INTERESTING_HEADERS = (
    "retry-after",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "x-request-id",
    "server",
)


def doi_prefix(doi: str | None) -> str:
    """Return the DOI registrant prefix (e.g. '10.1002' from '10.1002/lno.11924')."""
    if not doi or "/" not in doi:
        return "unknown"
    return doi.split("/", 1)[0]


def classify(url: str, discovered_from: str | None) -> tuple[str, dict[str, str] | None, str | None]:
    """Return (bucket label, response headers of interest, body preview).

    Headers/body are only populated for non-2xx HTTP responses — that's the
    case where we need more than a status code to tell a genuine server
    error apart from a disguised rate limit.
    """
    url = normalize_pdf_url(url)
    try:
        resp = httpx.get(
            url,
            headers=pdf_headers(discovered_from, url),
            timeout=60,
            follow_redirects=True,
        )
        if resp.status_code in (200, 206):
            return "200/206 (now accessible)", None, None
        headers = {h: resp.headers[h] for h in _INTERESTING_HEADERS if h in resp.headers}
        body = resp.text[:_BODY_PREVIEW_LEN].strip() or None
        return f"HTTP {resp.status_code}", headers, body
    except httpx.TimeoutException:
        return "timeout", None, None
    except httpx.ConnectError:
        return "connect-error", None, None
    except Exception as exc:
        return f"{type(exc).__name__}", None, None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=200, help="Max papers to sample (default: 200)")
    parser.add_argument("--delay", type=float, default=_DELAY_DEFAULT, help="Seconds between requests")
    args = parser.parse_args()

    with get_session() as session:
        rows = session.execute(
            select(Paper.id, Paper.doi, Paper.oa_pdf_url, Paper.discovered_from)
            .where(Paper.status == "inaccessible", Paper.oa_pdf_url.isnot(None))
            .limit(args.limit)
        ).all()

    print(f"Sampling {len(rows)} inaccessible paper(s)...\n")

    buckets: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    prefix_buckets: dict[str, Counter[str]] = defaultdict(Counter)
    error_details: dict[str, list[tuple[str, dict[str, str], str | None]]] = defaultdict(list)

    for i, (paper_id, doi, url, discovered_from) in enumerate(rows, 1):
        label, headers, body = classify(url, discovered_from)
        prefix = doi_prefix(doi)
        buckets[label] += 1
        prefix_buckets[prefix][label] += 1
        if len(examples[label]) < 3:
            examples[label].append(f"[{paper_id}] {doi or 'no-doi'}  {url}")
        if headers is not None and len(error_details[label]) < 3:
            error_details[label].append((f"[{paper_id}] {doi or 'no-doi'}", headers, body))

        if i % 25 == 0:
            print(f"  ...{i}/{len(rows)}")

        time.sleep(args.delay)

    print("\n--- Result distribution ---")
    for label, count in buckets.most_common():
        pct = 100 * count / len(rows) if rows else 0
        print(f"{count:5d} ({pct:5.1f}%)  {label}")
        for ex in examples[label]:
            print(f"           {ex}")

    print("\n--- By DOI prefix ---")
    all_labels = sorted(buckets.keys())
    header = f"{'prefix':<14}" + "".join(f"{label:<24}" for label in all_labels) + "total"
    print(header)
    for prefix, counts in sorted(prefix_buckets.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(counts.values())
        row = f"{prefix:<14}" + "".join(f"{counts.get(label, 0):<24}" for label in all_labels) + f"{total}"
        print(row)

    print("\n--- Error detail samples (headers + body preview) ---")
    for label in sorted(error_details.keys()):
        print(f"\n{label}:")
        for ident, headers, body in error_details[label]:
            print(f"  {ident}")
            print(f"    headers: {headers if headers else '(none of the interesting ones present)'}")
            print(f"    body:    {body!r}")

    print("\nInterpretation:")
    print("  HTTP 401/403     -> no TDM entitlement for this DOI/journal (check WILEY_API_KEY / TDM agreement scope)")
    print("  HTTP 429         -> rate-limited by Wiley (check_pdf_accessible has no backoff/delay)")
    print("  HTTP 404         -> bad DOI -> TDM URL mapping")
    print("  HTTP 5xx         -> Wiley-side error; check the detail samples above for a Retry-After/rate-limit")
    print("                      header or a body message before assuming a plain retry-with-backoff will help")
    print("  timeout/connect  -> network/transient, likely transient")
    print("  200/206          -> now accessible; was probably a transient failure or rate limit at filter time")


if __name__ == "__main__":
    main()
