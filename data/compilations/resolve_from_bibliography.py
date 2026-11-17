"""Resolve author-year-only citations through the introducing paper's own bibliography.

Chain per citation (all three links must hold for ``bib_resolved``, the only status that
apply_doi_resolution.py writes):
  1. label -> exactly one bibliography entry   (first author + year [+ second author])
  2. entry -> OpenAlex work                    (the entry's printed DOI, else a title search)
  3. work agrees with the entry on title, first author and year

Targets: doi_resolution.parquet rows with status author_year_only / unparsed in the sources
listed in BIBLIOGRAPHIES. Rows whose stored DOI is a truncated fragment (e.g. '10.1111/1462-2920')
are matched by DOI prefix + year instead of by label.
Output: doi_resolution_bib.parquet (one row per target). Nothing is written to compilations.duckdb.
Run: uv run python data/compilations/resolve_from_bibliography.py
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import duckdb
from _citation_common import citation_text
from dotenv import dotenv_values

from coastal_crawler.bibliography import (
    BibEntry, load_jats, load_numbered_layout, load_numbered_text, load_ris, match_label)
from coastal_crawler.citation_parse import ParsedCitation, parse_citation
from coastal_crawler.citation_resolve import TITLE_RECALL, author_match, resolve_title, toks, year_match
from coastal_crawler.openalex_client import OpenAlexClient, norm_doi

HERE = Path(__file__).parent
USER_AGENT = "coastal-crawler/0.1 (compilations bibliography resolution)"
REQUEST_DELAY = 0.1
TIMEOUT = 120
MAX_SERVER_RETRIES = 5
RETRY_BACKOFF = 10.0
BATCH_SIZE = 50
PER_PAGE = 5
PDF_TWOCOL = {  # Davis main reference list (Communications Earth & Environment layout)
    "first_page": 8, "last_page": 10, "col_width": 298, "page_height": 782,
    "end_marker": "Acknowledgements",
    "drop_line": r"COMMUNICATIONS|EARTH & ENVIRONMENT|nature\.com|doi\.org/10\.1038|^ARTICLE|^\s*\d+\s*$|^s43247|^oi\.org|^/s43247",
}
LOOKUP_SELECT = "id,doi,title,publication_year,authorships,type"
TARGET_STATUSES = ("author_year_only", "unparsed")

# source_key -> [(loader, path)]
BIBLIOGRAPHIES: dict[str, list[tuple[str, str]]] = {
    "tang-nitrification": [("jats", "tang-nitrification/essd-15-5039-2023.xml")],
    "strydom-seagrass": [("jats", "strydom-seagrass/essd-15-511-2023.xml")],
    "shao-nfix": [("jats", "shao-nfix/essd-15-3673-2023.xml")],
    "francesco-phytoplankton": [("jats", "francesco-phytoplankton/essd-13-4967-2021.xml")],
    "mouw-carbon": [("jats", "mouw-carbon/essd-8-531-2016.xml")],
    "pessarrodona-seaweed": [("ris", "pessarrodona-seaweed/10.1038_s41597-022-01554-5-references.ris")],
    "davis-calcification": [("pdf_supp", "davis-calcification/43247_2021_168_MOESM2_ESM.pdf"),
                            ("pdf_twocol", "davis-calcification/s43247-021-00168-w (1).pdf")],
}


def load_entries(kind: str, rel: str) -> list[BibEntry]:
    p = HERE / rel
    if kind == "jats":
        return load_jats(p)
    if kind == "ris":
        return load_ris(p)
    if kind == "pdf_supp":
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "t.txt"
            subprocess.run(["pdftotext", str(p), str(out)], check=True)
            raw = out.read_text()
            raw = raw[raw.index("Supplementary References"):]
            out.write_text(raw)
            return load_numbered_text(out, p.name)
    if kind == "pdf_twocol":
        g = PDF_TWOCOL
        chunks = []
        for page in range(g["first_page"], g["last_page"] + 1):
            for x in (0, g["col_width"]):
                chunks.append(subprocess.run(
                    ["pdftotext", "-layout", "-f", str(page), "-l", str(page), "-x", str(x), "-y", "0",
                     "-W", str(g["col_width"]), "-H", str(g["page_height"]), str(p), "-"],
                    check=True, capture_output=True, text=True).stdout)
        return load_numbered_layout("\n".join(chunks), p.name, g["end_marker"], g["drop_line"])
    raise ValueError(kind)


def entry_title(e: BibEntry) -> str | None:
    """Title for an entry: separate field (RIS), Copernicus 'Authors: Title, Journal, ...', or parsed."""
    if e.title:
        return e.title
    head, sep, rest = e.text.partition(": ")
    if sep and len(head) < 300 and not re.search(r"https?://", head):
        return rest
    return parse_citation(e.text, e.year).title


@dataclass
class EntryResult:
    status: str  # verified | doi_mismatch | no_work | weak | ambiguous
    openalex_id: str | None
    doi: str | None
    oa_title: str | None
    route: str
    signals: dict


def verify_work(e: BibEntry, w: dict) -> dict:
    authors = [a["author"]["display_name"] for a in w["authorships"]]
    oa_t = toks(w["title"] or "")
    return {
        "title": bool(oa_t) and len(oa_t & toks(e.text)) / len(oa_t) >= TITLE_RECALL,
        "author": bool(e.first_author) and author_match(e.first_author, authors),
        "year": e.year is not None and w["publication_year"] is not None and year_match(e.year, w["publication_year"]),
    }


def resolve_entries(client: OpenAlexClient, entries: list[BibEntry]) -> dict[tuple[str, int], EntryResult]:
    out: dict[tuple[str, int], EntryResult] = {}
    with_doi = {e.doi for e in entries if e.doi}
    found = client.works_by_dois(sorted(with_doi), LOOKUP_SELECT, BATCH_SIZE) if with_doi else {}
    for e in entries:
        key = (e.source_file, e.index)
        if e.doi:
            w = found.get(e.doi)
            if w is None:
                out[key] = EntryResult("no_work", None, None, None, "doi", {})
                continue
            sig = verify_work(e, w)
            ok = all(sig.values())
            out[key] = EntryResult("verified" if ok else "doi_mismatch", w["id"].rsplit("/", 1)[-1],
                                   norm_doi(w["doi"]) if w.get("doi") else None, w["title"], "doi", sig)
        else:
            t = entry_title(e)
            if not t or len(t.split()) < 3:
                out[key] = EntryResult("no_work", None, None, None, "title", {"reason": "no title"})
                continue
            r = resolve_title(client, ParsedCitation("bib", t, e.first_author, e.year), PER_PAGE)
            out[key] = EntryResult("verified" if r.status == "resolved" else r.status, r.openalex_id, r.doi,
                                   r.oa_title, "title", r.evidence)
    return out


def main() -> None:
    con = duckdb.connect(str(HERE / "compilations.duckdb"), read_only=True)
    con.execute(f"create temp view r as select * from '{HERE / 'doi_resolution.parquet'}'")
    cur = con.execute(
        f"""select c.citation_id, s.source_key, c.citation_key, c.bibliography_id, c.doi, c.title, c.authors,
                   c.year, r.status from citation_dim c join source_dim s using (source_id) join r using (citation_id)
            where s.source_key in {tuple(BIBLIOGRAPHIES)} and r.status in {TARGET_STATUSES}
            order by c.citation_id""")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, x)) for x in cur.fetchall()]

    client = OpenAlexClient(dotenv_values(HERE.parents[1] / ".env")["OPENALEX_API_KEY"], HERE / "_openalex_cache",
                            USER_AGENT, REQUEST_DELAY, TIMEOUT, MAX_SERVER_RETRIES, RETRY_BACKOFF)
    out = []
    for source_key in BIBLIOGRAPHIES:
        entries: list[BibEntry] = []
        for kind, rel in BIBLIOGRAPHIES[source_key]:
            entries += load_entries(kind, rel)
        assert entries, f"{source_key}: bibliography is empty"
        results = resolve_entries(client, entries)
        n_ver = sum(r.status == "verified" for r in results.values())
        print(f"{source_key}: {len(entries)} entries, {n_ver} verified against OpenAlex")
        for r in (x for x in rows if x["source_key"] == source_key):
            text = citation_text(r) or ""
            stored = r["doi"]
            if stored and re.match(r"10\.\d{4,9}/", stored.strip().lower()):  # truncated/odd stored DOI
                prefix = re.match(r"10\.\d{4,9}/[^\s)）]*", stored.strip().lower()).group(0)
                cands = [e for e in entries if e.doi and e.doi.startswith(prefix) and (r["year"] is None or e.year == r["year"])]
                lstat = "matched" if len(cands) == 1 else ("ambiguous" if cands else "no_entry")
                label = f"doi-prefix:{prefix}"
            else:
                lstat, cands = match_label(text, entries)
                label = text
            if lstat == "ambiguous":  # several entries are fine if they are one and the same verified work
                res_c = [results[(e.source_file, e.index)] for e in cands]
                if all(x.status == "verified" for x in res_c) and len({x.openalex_id for x in res_c}) == 1:
                    lstat, cands = "matched", cands[:1]
            if lstat != "matched":
                out.append((r["citation_id"], source_key, r["citation_key"], label, lstat, None, None, None, None, None, None, None, None, "{}"))
                continue
            e = cands[0]
            er = results[(e.source_file, e.index)]
            status = "bib_resolved" if er.status == "verified" else f"bib_{er.status}"
            out.append((r["citation_id"], source_key, r["citation_key"], label, lstat, e.source_file, e.index, e.text[:300], e.doi,
                        er.route, status, er.openalex_id, er.doi, json.dumps({**er.signals, "oa_title": er.oa_title}, default=str)))
    client.close()

    assert len({o[0] for o in out}) == len(out) == len(rows)
    mem = duckdb.connect()
    mem.execute("""create table t (citation_id integer, source_key varchar, citation_key varchar, label varchar, label_status varchar,
        bib_file varchar, bib_index integer, bib_text varchar, bib_doi varchar, route varchar, status varchar,
        openalex_id varchar, resolved_doi varchar, evidence_json varchar)""")
    mem.executemany("insert into t values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
    mem.execute(f"copy t to '{HERE / 'doi_resolution_bib.parquet'}' (format parquet)")

    by: dict[str, Counter] = {}
    for o in out:
        by.setdefault(o[1], Counter())[o[10] or f"label:{o[4]}"] += 1
    for s, c in by.items():
        print(f"  {s:26s}", dict(c))


if __name__ == "__main__":
    main()
