"""Resolve a parsed citation to an OpenAlex work, with explicit match evidence.

Acceptance is by comparing the OpenAlex candidate to the parsed citation on three
independent signals -- title, first author, year -- never by search rank alone.

Statuses returned by ``resolve_title``:
  resolved       title + first author + year all agree with exactly one distinct work
  resolved_weak  title agrees, and exactly one of author/year is checkable-and-agrees
                 (or the other is absent from the citation); review before trusting
  ambiguous      more than one distinct work passes the same bar
  no_match       no candidate passes (the work may simply not be in OpenAlex)

Thresholds are module constants: changing one invalidates every earlier resolution run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from dataclasses import replace

from coastal_crawler.citation_parse import ParsedCitation, fold, trimmed_title_variants
from coastal_crawler.openalex_client import OpenAlexClient, norm_doi

TITLE_RECALL = 0.8  # fraction of OpenAlex-title tokens that must appear in the parsed title
TITLE_LEN_RATIO = 3.0  # parsed title may have at most this many times the OA title's tokens
MIN_JACCARD = 0.4  # token Jaccard floor: blocks a short OA title that is merely contained in a long parsed one
YEAR_TOLERANCE = 1  # online-first vs issue year
STRONG_TITLE_TOKENS = 6  # a title this long is distinctive enough to accept despite author/year disagreement
TIE_MARGIN = 0.05  # best candidate must beat the runner-up (distinct work) by this much Jaccard
SEARCH_SELECT = "id,doi,title,publication_year,authorships,type"
MAX_QUERY_CHARS = 300


def toks(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{2,}", fold(s)))


def title_match(oa_title: str, parsed_title: str) -> bool:
    oa_t, got_t = toks(oa_title), toks(parsed_title)
    if not oa_t:
        raise ValueError(f"OpenAlex title has no usable tokens: {oa_title!r}")
    return len(oa_t & got_t) / len(oa_t) >= TITLE_RECALL and len(got_t) <= TITLE_LEN_RATIO * len(oa_t)


def author_match(first_author: str, oa_authors: list[str]) -> bool:
    """First-author surname appears among the first three OpenAlex authors."""
    oa_auth = set().union(*(toks(a) for a in oa_authors[:3]))
    return bool(toks(first_author) & oa_auth)


def year_match(stored: int, oa: int) -> bool:
    return abs(stored - oa) <= YEAR_TOLERANCE


@dataclass(frozen=True)
class Resolution:
    status: str
    openalex_id: str | None
    doi: str | None
    oa_title: str | None
    evidence: dict[str, Any]  # per-signal booleans for the chosen candidate, plus n_passing


def _evidence(w: dict[str, Any], p: ParsedCitation) -> dict[str, Any] | None:
    """Signals for one candidate; None if the title does not match (candidate rejected)."""
    if not w.get("title") or not p.title:
        return None
    if not toks(w["title"]):  # e.g. a Thai/CJK title: no Latin tokens, cannot match a Latin citation
        return None
    if not title_match(w["title"], p.title):
        return None
    authors = [a["author"]["display_name"] for a in w["authorships"]]
    return {
        "title": True,
        "author": author_match(p.first_author, authors) if p.first_author and authors else None,
        "year": year_match(p.year, w["publication_year"]) if p.year and w["publication_year"] else None,
    }


def _jaccard(a: str, b: str) -> float:
    ta, tb = toks(a), toks(b)
    return len(ta & tb) / len(ta | tb)


def resolve_title(client: OpenAlexClient, p: ParsedCitation, per_page: int) -> Resolution:
    """Resolve by full title; if nothing matches, retry with venue/volume/pages trimmed off.

    ``evidence["query"]`` is "full" or "trimmed" so the fallback's contribution is auditable.
    """
    res = _resolve_one(client, p, per_page)
    if res.status != "no_match":
        return Resolution(res.status, res.openalex_id, res.doi, res.oa_title, {**res.evidence, "query": "full"})
    for t in trimmed_title_variants(p.title):
        alt = _resolve_one(client, replace(p, title=t), per_page)
        if alt.status != "no_match":
            return Resolution(alt.status, alt.openalex_id, alt.doi, alt.oa_title, {**alt.evidence, "query": "trimmed"})
    return Resolution("no_match", None, None, None, {"n_passing": 0, "query": "full"})


def _resolve_one(client: OpenAlexClient, p: ParsedCitation, per_page: int) -> Resolution:
    """Search by parsed title and accept a candidate only on explicit evidence.

    A candidate must match the title. If its first author or year is *contradicted*, it is
    kept only when the title is distinctive (>= STRONG_TITLE_TOKENS tokens) -- a compilation's
    ``authors`` field can be a data contributor rather than the paper's first author -- and
    is then reported as ``resolved_weak`` with the contradiction in ``evidence``.
    """
    assert p.title, "resolve_title needs a parsed title"
    body = client.get({"search": p.title[:MAX_QUERY_CHARS], "per-page": per_page, "select": SEARCH_SELECT})
    strong_title = len(toks(p.title)) >= STRONG_TITLE_TOKENS
    passing: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for w in body["results"]:
        ev = _evidence(w, p)
        if ev is None:
            continue
        jac = _jaccard(w["title"], p.title)
        if jac < MIN_JACCARD:
            continue
        if ev["author"] is False and ev["year"] is False:
            continue  # both checkable signals contradict: title match alone is not enough
        if (ev["author"] is False or ev["year"] is False) and not strong_title:
            continue
        passing.append((jac, w, ev))
    if not passing:
        return Resolution("no_match", None, None, None, {"n_passing": 0})
    passing.sort(key=lambda x: -x[0])
    distinct = list(dict.fromkeys(w["id"] for _, w, _ in passing))
    score, w, ev = passing[0]
    ev = {**ev, "n_passing": len(distinct), "jaccard": round(score, 3)}
    if len(distinct) > 1:
        runner_up = next(sc for sc, w2, _ in passing if w2["id"] != w["id"])
        if score - runner_up < TIE_MARGIN:
            return Resolution("ambiguous", None, None, None, ev)
    # Dataset records share generic series titles with siblings and mirrors (Pangaea vs GBIF,
    # per-cruise releases), so a title match cannot identify one: never auto-"strong".
    is_dataset = w.get("type") == "dataset"
    ev = {**ev, "dataset": is_dataset}
    strong = ev["author"] is True and ev["year"] is True and not is_dataset
    return Resolution(
        "resolved" if strong else "resolved_weak",
        w["id"].rsplit("/", 1)[-1],
        norm_doi(w["doi"]) if w.get("doi") else None,
        w["title"],
        ev,
    )
