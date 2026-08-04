"""Locate which OCR'd page an extracted measurement likely came from.

Pure text-in/text-out functions — deliberately decoupled from where the OCR
text comes from (a live read of OCR_DIR today, or a precomputed value stored
at sync time once the site runs against a synced copy of the DB elsewhere).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PAGE_RE = re.compile(r'<page number="(\d+)">(.*?)</page>', re.DOTALL)


@dataclass
class SnippetResult:
    page_number: int | None
    text: str
    matched: bool  # True if `value` was actually located on `text`'s page


def split_pages(ocr_text: str) -> list[tuple[int, str]]:
    """Split OCR text on `<page number="N">...</page>` tags into (page_number, text)."""
    return [(int(m.group(1)), m.group(2).strip()) for m in _PAGE_RE.finditer(ocr_text)]


def _normalize_number(s: str) -> str | None:
    """Strip thousands separators so '1,234.50' and '1234.5' compare equal."""
    try:
        return repr(float(s.strip().replace(",", "")))
    except ValueError:
        return None


def find_snippet(
    ocr_text: str,
    value: str | None,
    attribute: str | None = None,
    units: str | None = None,
) -> SnippetResult:
    """Find the OCR page most likely to contain `value`.

    Tries, in order: literal token match (`value` as a standalone number, not
    a substring of a larger one), numeric-normalized match, then falls back
    to a page mentioning `attribute`/`units`, then the first page.
    `matched=False` on any fallback path means the page shown is a best guess,
    not a confirmed location.
    """
    pages = split_pages(ocr_text)
    if not pages:
        return SnippetResult(None, ocr_text.strip()[:2000], False)

    if not value:
        return SnippetResult(pages[0][0], pages[0][1], False)

    value_norm = _normalize_number(value)

    literal_re = _literal_re(value)
    literal_hits = [(n, t) for n, t in pages if literal_re.search(t)]
    if literal_hits:
        result = _best_of(literal_hits, attribute, units)
        result.text = _highlight_literal(result.text, value)
        return result

    if value_norm is not None:
        numeric_hits = [(n, t) for n, t in pages if _page_has_number(t, value_norm)]
        if numeric_hits:
            result = _best_of(numeric_hits, attribute, units)
            result.text = _highlight_numeric(result.text, value_norm)
            return result

    for page_num, text in pages:
        lower = text.lower()
        if (attribute and attribute.lower() in lower) or (units and units.lower() in lower):
            return SnippetResult(page_num, text, False)

    return SnippetResult(pages[0][0], pages[0][1], False)


_NUMBER_RE = re.compile(r"-?[\d,]+\.?\d*")


def _page_has_number(text: str, value_norm: str) -> bool:
    for match in _NUMBER_RE.finditer(text):
        if _normalize_number(match.group()) == value_norm:
            return True
    return False


def _literal_re(value: str) -> re.Pattern[str]:
    """Match `value` only as a standalone token, not as a substring of a
    larger number — plain `value in text` containment would match "5" inside
    "125.3", "50" inside "1234.50", or "5.0" inside "-5.0". A digit, '.', or
    ',' immediately following (or, on the left, a digit/'.'/','/'-') means
    the hit is actually part of a bigger number."""
    return re.compile(rf"(?<![\d.,-]){re.escape(value)}(?![\d.,])")


def _highlight_literal(text: str, value: str) -> str:
    """Wrap every standalone occurrence of `value` in `<mark>` — `<mark>` is
    in the OCR-rendering markdown sanitizer's default allowed-tag set, so it
    survives `_render_ocr_markdown` untouched."""
    return _literal_re(value).sub(lambda m: f"<mark>{m.group(0)}</mark>", text)


def _highlight_numeric(text: str, value_norm: str) -> str:
    """Wrap every number in `text` that normalizes to `value_norm` in `<mark>`."""

    def repl(match: re.Match[str]) -> str:
        if _normalize_number(match.group()) == value_norm:
            return f"<mark>{match.group()}</mark>"
        return match.group()

    return _NUMBER_RE.sub(repl, text)


def _best_of(
    hits: list[tuple[int, str]], attribute: str | None, units: str | None
) -> SnippetResult:
    """Among pages that contain the value, prefer one that also mentions attribute/units."""
    if len(hits) > 1 and (attribute or units):
        for page_num, text in hits:
            lower = text.lower()
            if (attribute and attribute.lower() in lower) or (units and units.lower() in lower):
                return SnippetResult(page_num, text, True)
    page_num, text = hits[0]
    return SnippetResult(page_num, text, True)
