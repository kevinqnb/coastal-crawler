"""Bibliography loaders + label->entry matching (general-purpose; no network, no DB).

A *label* is a short in-database citation ("Atkinson & Grigg, 1984. Coral Reefs"). A
*bibliography* is the reference list of the paper that introduced the data (JATS XML, RIS, or
a numbered PDF list). ``match_label`` links a label to exactly one entry or says why not:

  matched | no_entry | ambiguous | year_suffix | secondary_citation | no_label_key

Linking is by first-author surname + year, plus the second author when the label names one.
It never guesses between candidates; two equally good entries give ``ambiguous``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from coastal_crawler.citation_parse import clean, first_author_surname, fold
from coastal_crawler.openalex_client import norm_doi

_YEAR = re.compile(r"\b((?:19|20)\d\d)[a-z]?\b")
_SECONDARY = re.compile(r"\bin\s*:?\s+[A-Z]|\bunpubl|pers\.?\s*comm|personal comm", re.IGNORECASE)
_SECOND_AUTHOR = re.compile(r"(?:&|\band\b)\s+([A-Za-z'’\-]+)")
_VANCOUVER = re.compile(r"^(.+?)\s+[A-Z]{1,3}$")  # 'Marubini F' -> surname 'Marubini'


@dataclass(frozen=True)
class BibEntry:
    source_file: str
    index: int  # position in the file, for audit
    text: str
    first_author: str | None  # accent-folded lower-case surname
    year: int | None
    doi: str | None  # normalized, as printed in the bibliography
    title: str | None  # only when the format gives one separately (RIS)


def _entry_year(text: str) -> int | None:
    no_doi = re.sub(r"10\.\d{4,9}/\S+", "", text)
    years = _YEAR.findall(no_doi)
    return int(years[-1]) if years else None


def _vancouver_fix(text: str, surname: str | None) -> str | None:
    """'Muscatine L, Porter JW.' -> 'muscatine' (initials without periods)."""
    head = re.split(r",|;|\bet al\b", text.strip(), maxsplit=1)[0].strip()
    m = _VANCOUVER.match(head)
    return fold(m.group(1).split()[-1]) if m else surname


def load_jats(path: Path) -> list[BibEntry]:
    root = ET.parse(path).getroot()
    out: list[BibEntry] = []
    for i, ref in enumerate(root.findall(".//ref-list/ref")):
        mc = ref.find("mixed-citation")
        assert mc is not None, f"{path.name}: ref {i} has no mixed-citation"
        text = clean(" ".join("".join(mc.itertext()).split()))
        doi = None
        for link in mc.findall("ext-link"):
            if (link.get("ext-link-type") or "").upper() == "DOI":
                try:
                    doi = norm_doi("".join(link.itertext()))
                except ValueError:
                    doi = None  # malformed DOI string in the bibliography: treat as no DOI
        out.append(BibEntry(path.name, i, text, first_author_surname(text), _entry_year(text), doi, None))
    return out


def load_ris(path: Path) -> list[BibEntry]:
    out: list[BibEntry] = []
    rec: dict[str, list[str]] = {}
    idx = 0
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("ER"):
            au = rec.get("AU", [""])
            title = " ".join(rec.get("TI", [])) or None
            year = (rec.get("PY") or [None])[0]
            doi_raw = (rec.get("DO") or [None])[0]
            try:
                doi = norm_doi(doi_raw) if doi_raw else None
            except ValueError:
                doi = None
            text = f"{'; '.join(au)}. {title or ''}. {' '.join(rec.get('JO', []))} {year or ''}"
            out.append(BibEntry(path.name, idx, clean(text), first_author_surname(au[0]), int(year[:4]) if year else None, doi, title))
            idx += 1
            rec = {}
        elif len(line) > 6 and line[2:6] == "  - ":
            rec.setdefault(line[:2], []).append(line[6:].strip())
    assert not rec, f"{path.name}: RIS record without terminating ER"
    return out


def load_numbered_text(path: Path, name: str) -> list[BibEntry]:
    """Numbered reference list from `pdftotext` raw output ('1.\\n\\nEntry...'). Numbering must be 1..N."""
    text = path.read_text()
    text = text[text.rfind("References") :] if "References" in text else text
    parts = re.split(r"(?m)^(\d{1,3})\.\s*\n", text)
    out: list[BibEntry] = []
    for k in range(1, len(parts) - 1, 2):
        n, body = int(parts[k]), clean(" ".join(parts[k + 1].split()))
        assert n == len(out) + 1, f"{name}: reference numbering broke at {n} (expected {len(out) + 1})"
        assert body, f"{name}: reference {n} has no text (multi-column layout?)"
        out.append(BibEntry(name, n, body, _vancouver_fix(body, first_author_surname(body)), _entry_year(body), None, None))
    return out


def load_numbered_layout(text: str, name: str, end_marker: str, drop_line: str) -> list[BibEntry]:
    """Numbered list in `pdftotext -layout` output ('12.  Author ... \n      continuation'), columns
    already concatenated in reading order. Numbering must run 1..N without gaps.

    ``end_marker``: heading that follows the list (text from it on is cut; must be present).
    ``drop_line``: regex for running headers/footers, removed line by line.
    """
    text = text[text.index("References") + len("References") :]
    text = text[: text.index(end_marker)]
    pat = re.compile(drop_line)
    text = "\n".join(ln for ln in text.splitlines() if not pat.search(ln))
    parts = re.split(r"(?m)^\s*(\d{1,3})\.\s+(?=\S)", text)
    out: list[BibEntry] = []
    for k in range(1, len(parts) - 1, 2):
        n, body = int(parts[k]), clean(" ".join(parts[k + 1].split()))
        assert n == len(out) + 1, f"{name}: reference numbering broke at {n} (expected {len(out) + 1})"
        out.append(BibEntry(name, n, body, _vancouver_fix(body, first_author_surname(body)), _entry_year(body), None, None))
    assert out, f"{name}: no numbered references found"
    return out


@dataclass(frozen=True)
class Label:
    first: str
    second: str | None
    year: int
    hint: str  # trailing words, e.g. journal abbreviation
    suffix: str | None  # 'a'/'b' after the year: one of several same-year papers by this author


def parse_label(text: str) -> Label | None:
    """First author, optional second author, year, from 'A & B, 1984. Journal' style labels."""
    t = clean(text)
    first = first_author_surname(t)
    m = re.search(r"\b((?:19|20)\d\d)([a-z])?\b", t)
    if not first or not m:
        return None
    head = t[: m.start()]
    sec = None if re.search(r"\bet al\b", head) else _SECOND_AUTHOR.search(head)
    hint = re.sub(r"^[\s.,;:)]+", "", t[m.end() :])
    return Label(first, fold(sec.group(1)) if sec else None, int(m.group(1)), hint, m.group(2))


def is_secondary(text: str) -> bool:
    return bool(_SECONDARY.search(text))


def match_label(text: str, entries: list[BibEntry]) -> tuple[str, list[BibEntry]]:
    if is_secondary(text):
        return "secondary_citation", []
    lab = parse_label(text)
    if lab is None:
        return "no_label_key", []
    cands = [e for e in entries if e.first_author == lab.first and e.year == lab.year]
    if lab.second:
        cands = [e for e in cands if re.search(rf"\b{re.escape(lab.second)}\b", fold(e.text))]
    if len(cands) > 1 and lab.hint:
        hint = {w for w in re.findall(r"[a-z]{3,}", fold(lab.hint))}
        scored = [(len(hint & set(re.findall(r"[a-z]{3,}", fold(e.text)))), e) for e in cands]
        best = max(s for s, _ in scored)
        if best > 0:
            cands = [e for s, e in scored if s == best]
    if not cands:
        return "no_entry", []
    if lab.suffix:  # '2018b': cannot tell which same-year paper the entry is without the sibling
        return "year_suffix", cands
    return ("matched" if len(cands) == 1 else "ambiguous"), cands
