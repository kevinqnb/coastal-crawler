"""Results website — read-only list/detail of extracted measurements, plus voting.

Phase 0 of the site roadmap: runs against the live cluster DB (see
scripts/db_env.sh). A later phase points the same app at a periodically
synced copy on an external host instead — no app-code changes needed, since
`find_snippet` already works from `data['context']` embedded on each
extraction row rather than requiring OCR_DIR filesystem access.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from pathlib import Path
from typing import Any

import markdown as md
import nh3
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from coastal_crawler.db import store
from coastal_crawler.db.engine import get_session
from coastal_crawler.measurement_schema import ATTRIBUTE_INFO_DICT
from coastal_crawler.site.snippets import find_snippet

app = FastAPI(title="coastal-crawler")

_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=_DIR / "static"), name="static")
templates = Jinja2Templates(directory=_DIR / "templates")

_PAGE_SIZE = 25
# Search box: dropdown suggestions are capped tighter than the full /search
# results page since they render inline as the user types.
_SEARCH_SUGGESTION_LIMIT = 8
_SEARCH_RESULTS_LIMIT = 50
_SEARCH_MIN_CHARS = 2

# Column order for /export.csv — see notes/coastal-crawler/builds/
# 2026-08-04-csv-export-01.md for the agreed set. Header names on the left;
# the matching attribute on an `_extraction_rows` row (or, for "authors", a
# small transform) on the right.
_CSV_COLUMNS: list[tuple[str, str]] = [
    ("attribute", "attribute"),
    ("value", "value"),
    ("units", "units"),
    ("name", "entity_name"),
    ("identifiers", "identifiers"),
    ("ecosystem_type", "ecosystem_type"),
    ("location", "location"),
    ("latitude", "latitude"),
    ("longitude", "longitude"),
    ("date", "event_date"),
    ("sub_location", "sub_location"),
    ("additional_details", "additional_details"),
    ("judgement", "judgement"),
    ("confidence", "confidence"),
    ("title", "title"),
    ("authors", "authors"),
    ("doi", "doi"),
    ("publication_date", "publication_date"),
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    return _SLUG_RE.sub("_", value.lower()).strip("_")

# Paper titles from discovery APIs (Crossref/OpenAlex) sometimes embed HTML
# for genus/species italics (e.g. "<i>Trichodesmium</i>") or chemical
# sub/superscripts ("CO<sub>2</sub>"). Jinja autoescapes by default, so those
# tags showed up literally instead of rendering — sanitize to a small
# allow-list instead of escaping everything.
_TITLE_ALLOWED_TAGS = {"i", "em", "b", "strong", "sup", "sub"}

# Articles/conjunctions/short prepositions stay lowercase in title case
# unless they open or close the title.
_MINOR_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "nor", "of", "on", "onto", "or", "over", "per", "so", "the", "to", "up",
    "via", "vs", "with", "yet",
}

# Splits into HTML tags, words, or runs of other characters (whitespace,
# punctuation, dashes) — so title-casing can skip over tags and treat
# hyphenated compounds as separate words without needing a second pass.
_TAG_OR_WORD_RE = re.compile(r"<[^>]+>|[A-Za-z0-9']+|[^A-Za-z0-9'<]+")


def _title_case_html(text: str) -> str:
    """Title-case the words in `text`, skipping over embedded HTML tags.

    Only the first letter of each major word is uppercased — the rest of
    the word is left untouched — so existing internal casing (acronyms like
    "CO2", "pH") survives. Minor words are lowercased unless first/last.
    """
    tokens = _TAG_OR_WORD_RE.findall(text)
    word_idxs = [i for i, t in enumerate(tokens) if t[0].isalnum()]
    if not word_idxs:
        return text
    first_word, last_word = word_idxs[0], word_idxs[-1]
    out = list(tokens)
    for i in word_idxs:
        word = tokens[i]
        if word.lower() in _MINOR_WORDS and i not in (first_word, last_word):
            out[i] = word.lower()
        else:
            out[i] = word[0].upper() + word[1:]
    return "".join(out)


# Many older Wiley/Crossref-sourced titles carry a footnote or affiliation
# marker (originally a superscript "1", or "1,2" for multiple footnotes)
# that got flattened into plain text glued directly onto the last word with
# no separating space, e.g. "...Narragansett Bay1" (confirmed present in the
# raw `papers.title` column itself, not introduced by this app). Strip it,
# except for the rare title that legitimately ends in a chemical formula
# like "CO2" — checked against a short allow-list first.
_FORMULA_SUFFIXES = ("CO2", "NO2", "NO3", "SO2", "SO4", "PO4", "NH3", "NH4", "H2S", "N2O", "CH4")
_FOOTNOTE_MARKER_RE = re.compile(r"(?<=[A-Za-z>])\d{1,2}(?:,\d{1,2})*$")


def _strip_footnote_marker(text: str) -> str:
    if text.endswith(_FORMULA_SUFFIXES):
        return text
    return _FOOTNOTE_MARKER_RE.sub("", text)


def _render_title(raw: str) -> str:
    safe = nh3.clean(raw, tags=_TITLE_ALLOWED_TAGS)
    stripped = _strip_footnote_marker(safe)
    return _title_case_html(stripped)


templates.env.filters["render_title"] = _render_title


def _render_ocr_markdown(text: str) -> str:
    """Markdown -> sanitized HTML. OCR text originates from third-party PDFs
    and must be treated as untrusted input, not just markdown."""
    html = md.markdown(text, extensions=["tables"])
    return nh3.clean(html)


def _voter_hash(request: Request) -> str:
    """Best-effort duplicate-vote deterrent, not authentication."""
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")
    return hashlib.sha256(f"{ip}|{ua}".encode()).hexdigest()[:16]


def _split_model_version(model_version: str) -> tuple[str | None, str | None]:
    """`model_version` defaults to "doc_lm={ocr model}+meas_lm={extraction model}"
    (see adapter.py's build_measurement_adapter) — split it back into the two
    models for display. Falls back to (None, raw string) if it doesn't match
    that format (e.g. MEAS_LM_MODEL_VERSION was overridden to a custom tag)."""
    doc_lm, sep, meas_lm = model_version.partition("+meas_lm=")
    if sep and doc_lm.startswith("doc_lm="):
        return doc_lm.removeprefix("doc_lm="), meas_lm
    return None, model_version


@app.get("/")
def list_view(
    request: Request,
    page: int = 1,
    title: str | None = None,
    attribute: str | None = None,
    ecosystem_type: str | None = None,
) -> Response:
    page = max(page, 1)
    with get_session() as session:
        rows, total = store.list_extractions(
            session,
            page=page,
            page_size=_PAGE_SIZE,
            title=title,
            attribute=attribute,
            ecosystem_type=ecosystem_type,
        )
        ecosystem_types = store.list_ecosystem_types(session)
    total_pages = max(1, math.ceil(total / _PAGE_SIZE))
    return templates.TemplateResponse(
        request,
        "list.html",
        {
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "title": title,
            "attribute": attribute,
            "attributes": sorted(ATTRIBUTE_INFO_DICT.keys()),
            "ecosystem_type": ecosystem_type,
            "ecosystem_types": ecosystem_types,
            "row_offset": (page - 1) * _PAGE_SIZE,
        },
    )


@app.get("/export.csv")
def export_csv(
    title: str | None = None,
    attribute: str | None = None,
    ecosystem_type: str | None = None,
) -> Response:
    """Every measurement matching the list view's current filter, as CSV
    (unpaginated) — see _CSV_COLUMNS for the agreed column set/order."""
    with get_session() as session:
        rows = store.export_extractions(
            session, title=title, attribute=attribute, ecosystem_type=ecosystem_type
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(name for name, _attr in _CSV_COLUMNS)
    for r in rows:
        row = []
        for _name, attr in _CSV_COLUMNS:
            value = getattr(r, attr)
            row.append(", ".join(value) if attr == "authors" and value else value)
        writer.writerow(row)

    name_parts = [_slugify(p) for p in (attribute, ecosystem_type) if p]
    filename = "measurements_" + "_".join(name_parts) + ".csv" if name_parts else "measurements.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/search.json")
def search_json(q: str = "") -> Response:
    """Live suggestions for the header search box's dropdown."""
    q = q.strip()
    if len(q) < _SEARCH_MIN_CHARS:
        return JSONResponse([])
    with get_session() as session:
        rows = store.search_papers_by_title(session, q, _SEARCH_SUGGESTION_LIMIT)
        results = [
            {
                "id": paper_id,
                # Sanitized/title-cased server-side (same as list/detail pages)
                # so the frontend can insert it via innerHTML directly.
                "title": _render_title(title) if title else "(untitled)",
                "authors": authors or [],
                "year": publication_date.year if publication_date else None,
            }
            for paper_id, title, authors, publication_date, _doi in rows
        ]
    return JSONResponse(results)


@app.get("/search")
def search_view(request: Request, q: str = "") -> Response:
    """Full-page search results — also the no-JS fallback for the search form."""
    q = q.strip()
    rows: list[Any] = []
    if len(q) >= _SEARCH_MIN_CHARS:
        with get_session() as session:
            rows = store.search_papers_by_title(session, q, _SEARCH_RESULTS_LIMIT)
    return templates.TemplateResponse(
        request,
        "search.html",
        {"q": q, "rows": rows, "min_chars": _SEARCH_MIN_CHARS},
    )


@app.get("/papers/{paper_id}")
def paper_view(request: Request, paper_id: int, page: int = 1) -> Response:
    page = max(page, 1)
    with get_session() as session:
        paper = store.get_paper(session, paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail="Paper not found")
        rows, total = store.list_extractions(
            session, page=page, page_size=_PAGE_SIZE, paper_id=paper_id
        )
        total_pages = max(1, math.ceil(total / _PAGE_SIZE))
        # Rendered inside the session block — the template touches `paper`'s
        # ORM attributes, which need a live session (see detail_view below).
        return templates.TemplateResponse(
            request,
            "paper.html",
            {
                "paper": paper,
                "rows": rows,
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "row_offset": (page - 1) * _PAGE_SIZE,
            },
        )


@app.get("/extraction/{extraction_id}")
def detail_view(request: Request, extraction_id: int) -> Response:
    with get_session() as session:
        extraction = store.get_extraction(session, extraction_id)
        if extraction is None:
            raise HTTPException(status_code=404, detail="Extraction not found")

        data = extraction.data or {}
        # Prefer the per-paper table (see PaperOcrContext) — the primary
        # source since migration c2d3e4f5a6b7 stripped `context` out of
        # every extraction row's `data`. Falls back to the old embedded
        # shape for any row that predates that migration and wasn't backfilled.
        ocr_context = store.get_paper_ocr_context(session, extraction.paper_id) or data.get(
            "context", ""
        )
        snippet = find_snippet(
            ocr_context, data.get("value"), data.get("attribute"), data.get("units")
        )
        valid_votes = sum(1 for v in extraction.votes if v.vote)
        invalid_votes = sum(1 for v in extraction.votes if not v.vote)
        ocr_model, extraction_model = _split_model_version(extraction.model_version)
        context = {
            "extraction": extraction,
            "data": data,
            "paper": extraction.paper,
            "snippet_html": _render_ocr_markdown(snippet.text),
            "snippet_page": snippet.page_number,
            "snippet_matched": snippet.matched,
            "valid_votes": valid_votes,
            "invalid_votes": invalid_votes,
            "ocr_model": ocr_model,
            "extraction_model": extraction_model,
        }
        # Rendered inside the session block — the template touches
        # extraction/paper ORM attributes, which need a live session.
        return templates.TemplateResponse(request, "detail.html", context)


@app.post("/extraction/{extraction_id}/vote")
def cast_vote(request: Request, extraction_id: int, vote: str = Form(...)) -> Response:
    if vote not in ("valid", "invalid"):
        raise HTTPException(status_code=400, detail="vote must be 'valid' or 'invalid'")
    with get_session() as session:
        if store.get_extraction(session, extraction_id) is None:
            raise HTTPException(status_code=404, detail="Extraction not found")
        store.record_vote(session, extraction_id, vote == "valid", _voter_hash(request))
    return RedirectResponse(url=f"/extraction/{extraction_id}", status_code=303)
