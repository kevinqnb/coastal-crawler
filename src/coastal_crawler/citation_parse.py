"""Heuristic parser: free-text citation -> (first author, year, title) for OpenAlex lookup.

Pure functions, no I/O. The parser never guesses silently: when it cannot find a title it
returns ``style="author_year_only"`` (a first author and year but no title) or
``style="unparsed"`` (neither) so the caller can route the row explicitly.

Styles handled (see tests/test_citation_parse.py for one hand-built example of each):
  fields       caller supplies a separate title (and authors) -- title is used as given
  quoted       Surname, First. 2019. “Title.” Journal ...
  apa          Authors (2019). Title. Journal, 16(4), 1-2.
  author_year  Authors, 2020. Title. Journal ... / Authors, 1996, Title. Journal
  sentence     Authors. Title. Journal vol, pages (2008)    (Nature / MEPS style)
  embedded     Authors, et al., Title. Journal, 2024.       (comma-separated, no sentence break)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MIN_TITLE_WORDS = 3  # a "title" shorter than this is a journal abbreviation or fragment

_URL = re.compile(r"https?://\S+")
_SENT_SPLIT = re.compile(r"(?<=[.?!])\s+(?=[“\"'A-Z0-9(])")
_ABBREV_END = re.compile(r"\b(?:vs|sp|spp|cf|al|fig|no|vol|st|dr|ca|var|subsp|[A-Z])\.$")
_QUOTED = re.compile(r"[“\"]([^”\"]{15,}?)[”\"]")
_APA_YEAR = re.compile(r"\((\d{4})(?:,\s*[A-Za-z]+\.?\s*\d*)?[a-z]?\)\.?\s+(?P<rest>.+)")
_NUMBERING = re.compile(r"(?:[IVX]+|\d+)\.")
_ENDS_INITIAL = re.compile(r"(?:^|[\s.])[A-Z]\.$")
_AUTHOR_YEAR = re.compile(r"[,.]\s*(?:19|20)\d\d[a-z]?\s*[.,]\s+(?P<rest>[A-Z“\"].+)")
_ET_AL = re.compile(r"\bet al\.?,?\s+(?P<rest>.+)")
_INITIALS_END = re.compile(r"(?:^|[\s.,&-])[A-Z]\.$")


@dataclass(frozen=True)
class ParsedCitation:
    style: str
    title: str | None
    first_author: str | None  # accent-folded lower-case surname
    year: int | None


def fold(s: str) -> str:
    """Accent-fold + lower-case, for surname/title comparison."""
    s = re.sub("[\u2010-\u2015\u2212]", "-", s)  # unicode hyphens/dashes -> ASCII
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def clean(text: str) -> str:
    """Repair mojibake, strip URLs and BibTeX braces, normalise whitespace."""
    if "Ã" in text or "Â" in text:
        try:
            text = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass  # not actually mojibake; leave as is (explicit, narrow exception)
    text = _URL.sub("", text).replace("{", "").replace("}", "")
    text = re.sub(r"\bdoi:\s*\S+", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _sentences(text: str) -> list[str]:
    raw = _SENT_SPLIT.split(text)
    merged: list[str] = []
    for p in raw:  # re-join splits that happened after an abbreviation like "sp." or "U.S."
        if merged and _ABBREV_END.search(merged[-1]) and not _is_authorish(merged[-1]):
            merged[-1] += " " + p
        else:
            merged.append(p)
    return merged


def _is_authorish(piece: str) -> bool:
    p = piece.strip()
    return bool(_INITIALS_END.search(p)) or p.lower().endswith("et al.") or p.lower() == "et al"


def _first_sentence(rest: str) -> str:
    """First sentence of a title-initial string (authors already removed).

    Re-joins splits caused by initials/abbreviations ("N.W. Atlantic") and by subtitle
    numbering ("... seabed. IV. Shipboard ...").
    """
    parts = _SENT_SPLIT.split(rest)
    out = parts[0]
    i = 1
    while i < len(parts):
        if _ENDS_INITIAL.search(out) or _ABBREV_END.search(out):
            out += " " + parts[i]
            i += 1
        elif _NUMBERING.fullmatch(parts[i]) and i + 1 < len(parts):
            out += " " + parts[i] + " " + parts[i + 1]
            i += 2
        else:
            break
    return out.rstrip(".").strip()


_HEAD_END = re.compile(r"\s*(?:,|;|\(|\bet al\b|\band\b|&|\d{4}|$)")
_INITIALS = re.compile(r"\b(?:[A-Z]\.\s*)+")
_TITLE_ARTIFACT = re.compile(r"^(?:Dataset:\s*|(?:[A-Z]\.\s*)+:\s*)")


def first_author_surname(text: str) -> str | None:
    """Surname of the first author, from an author block or an author-year key.

    Handles "Surname, I.", "Surname et al.", "I. Surname", "Firstname M. Surname and ...".
    """
    t = clean(text)
    head = _HEAD_END.split(t, maxsplit=1)[0]
    head = _INITIALS.sub(" ", head).strip()
    words = head.split()
    return fold(words[-1]) if words else None


def tidy_title(title: str) -> str:
    """Drop extraction artifacts: 'Dataset: ' prefixes and a leaked author-initial 'C.: '."""
    return _TITLE_ARTIFACT.sub("", title).strip()


_VENUE_TAIL = re.compile(
    r"^(?P<title>.+?),\s*[^,]+,\s*\d+[A-Za-z]?,\s*[\w.]+[-–]\w+(?:,\s*(?:doi:\s*)?\S+)*,?\s*(?:\d{4})?\.?$", re.IGNORECASE
)
_VOL_PAGES_TAIL = re.compile(r"^(?P<title>.+?[a-z)\]])\.\s+[A-Z][^.]*?\s\d+\s*[:,(]\s*[\w–-]+.*$")


def trimmed_title_variants(title: str) -> list[str]:
    """Shorter titles with trailing 'Journal, vol, pages, year' / 'Journal 67:670-683.' removed.

    Returned in order of preference, excluding the input and any variant under MIN_TITLE_WORDS.
    Used only as a fallback query when the full title found nothing.
    """
    out: list[str] = []
    for rx in (_VENUE_TAIL, _VOL_PAGES_TAIL):
        m = rx.match(title.strip())
        if m:
            t = m.group("title").strip().rstrip(".,")
            if t != title and len(t.split()) >= MIN_TITLE_WORDS and t not in out:
                out.append(t)
    return out


def _ok(title: str | None) -> bool:
    return bool(title) and len(title.split()) >= MIN_TITLE_WORDS


def parse_citation(text: str, year: int | None, title_field: str | None = None, authors_field: str | None = None) -> ParsedCitation:
    """Parse one citation.

    ``title_field`` / ``authors_field``: separately stored columns. When both are present the
    title is trusted as a title (style ``fields``); if only the title is present it is parsed
    like any other free text, since several sources store a whole citation there.
    """
    if title_field and authors_field and _ok(clean(title_field)):
        return ParsedCitation("fields", tidy_title(clean(title_field)), first_author_surname(authors_field), year)

    t = clean(title_field or text)
    t = _TITLE_ARTIFACT.sub("", t) if t.startswith("Dataset:") else t
    author = first_author_surname(authors_field) if authors_field else first_author_surname(t)

    m = _QUOTED.search(t)
    if m and _ok(m.group(1)):
        return ParsedCitation("quoted", m.group(1).rstrip(".,").strip(), author, year)

    m = _APA_YEAR.search(t)
    if m and m.start() < 400:
        title = _first_sentence(m.group("rest"))
        if _ok(title):
            return ParsedCitation("apa", title, author, year)

    m = _AUTHOR_YEAR.search(t)
    if m and m.start() < 400:
        title = _first_sentence(m.group("rest"))
        if _ok(title):
            return ParsedCitation("author_year", title, author, year)

    sents = _sentences(t)
    i = 0
    while i < len(sents) and _is_authorish(sents[i]):
        i += 1
    if 0 < i < len(sents):
        title = sents[i].rstrip(".").strip()
        if _ok(title):
            return ParsedCitation("sentence", title, author, year)

    m = _ET_AL.search(t[:250])
    if m:
        title = _first_sentence(m.group("rest"))
        if _ok(title):
            return ParsedCitation("embedded", title, author, year)

    if author and year:
        return ParsedCitation("author_year_only", None, author, year)
    return ParsedCitation("unparsed", None, author, year)
