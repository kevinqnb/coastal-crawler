"""Shared helpers for the compilations DOI-resolution scripts (run without -I so this imports).

Matching thresholds live in coastal_crawler.citation_resolve (single source of truth).
"""

from __future__ import annotations

import json

from coastal_crawler.citation_resolve import author_match as _author_match
from coastal_crawler.citation_resolve import title_match, toks, year_match  # noqa: F401 (re-exported)


def author_match(first_author: str, oa_authors_json: str) -> bool:
    return _author_match(first_author, json.loads(oa_authors_json))


def citation_text(row: dict) -> str | None:
    """Best free text for a citation_dim row, or None if it holds only a bare DOI/key.

    ``bibliography_id`` is the citation text for foster/smithsonian-style rows; a
    ``ref:A+ref:B`` key lists several references, so each segment is a candidate.
    """
    key = row["citation_key"].partition("|")[0]
    segs = [s.removeprefix("ref:") for s in key.split("+ref:")] if key.startswith("ref:") else []
    cands = [row["title"], row["authors"], row["bibliography_id"], *segs]
    cands = [c for c in cands if c and not c.lower().startswith(("doi:10", "10."))]
    return max(cands, key=len) if cands else None
