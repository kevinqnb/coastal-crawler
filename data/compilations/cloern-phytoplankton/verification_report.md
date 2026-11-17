# Hand-verification report: Cloern et al. (2014) supplement, Table S1

Checked 2026-11-17 against `bg-11-2477-2014-supplement.pdf` (19 PDF pages). Everything below is reproducible:
`python -I extract.py --access-date 2026-11-15` writes `table_s1_rows.csv`, `references_parsed.csv` and
`reference_crosswalk.csv` (checked in beside this file); `python -I verify_table.py [compilations.duckdb]` re-reads the
PDF a second, independent way and exits non-zero on any difference.

## What the plan expected and what the file holds

| Quantity | Plan / caption | Found | Status |
|---|---|---|---|
| Table S1 data rows | "~250" (plan estimate, unverified) | **163** (PDF pages 2-7: 20, 30, 31, 29, 29, 24) | plan wrong; real count used |
| Ecosystems | 131 (caption) | **131** distinct names (`Chesapeake Bay 1960s` is a separate name in the source) | agrees (the documented split) |
| Sum of `n` | 1,148 (caption, plan) | **1,147** | **source discrepancy of -1** |
| Reference-list paragraphs | none estimated | **144** (PDF pages 8-19), every one ends in a year | |

The 1,147 is not an extraction error: the `n` cells were read three ways (pdfplumber table cells; the geometry/word-position
rebuild in `verify_table.py`; poppler `pdftotext -layout` tally) and all give the same per-page values (page sums 69, 196,
179, 367, 159, 177 = 1,147). The table's own caption says "computed from 1148 measurements"; the table sums to one fewer.
Which cell (if any) the authors mis-printed cannot be told from the supplement; no cell is changed. `extract.py` asserts
1,147 and records the caption's 1,148 and the difference in `extract_report.json` and `source.notes`.

## Independent re-reading (`verify_table.py`)

1. **Geometry rebuild.** Cells are rebuilt from the ruled-cell rectangles (vertical rules give the 8 columns, horizontal
   rules the row bands; words are assigned by their centre point) and all 8 columns of all 163 rows are compared with
   `table_s1_rows.csv`: **163 of 163 rows identical** (wrapped cells such as `Ciénaga Grande de Santa Marta` and
   `NETHERLANDS/ BELGIUM` included).
2. **poppler tally.** The (measure, APPP, n) triples of each page from `pdftotext -layout` equal the CSV's multiset on every
   page: 161 triples matched directly; the other 2 rows have the method cell wrapped over two lines (`O2 &` / `14C`), which
   poppler reads in a different order, and are accounted from the CSV.
3. **Round trip of the built database** (`python -I verify_table.py ../compilations.duckdb`): all 163 `measurement_fact`
   rows of this source, located through `source_row_ref` (`pdf_page`, `table_row`), equal the geometry-read raw row in
   ecosystem, country, region, measure, method, APPP and n, and the set of rows reached equals the set of raw rows.

## Checked by eye against the rendered page

Pages 2, 4, 6 and 7 of the PDF (rendered with `pdftoppm`) were compared with the CSV. Pages 2, 4 and 7 were compared row by
row (all 20 + 31 + 24 rows: every cell and every reference string agrees), page 6 was only spot-checked (about half its rows, including
the one negative value). Pages 3 and 5 were verified by the machine checks above only. What the eye check adds is that the
text layer is what is drawn (no hidden or re-ordered text); it found nothing.

Things seen in the rendering:

- **Scheldt Estuary NPP is printed `-105`** (page 6): a negative net production. Kept; flagged `negative_value`.
- Hyphens in the text layer are `-` + U+00AD + U+2010 (e.g. `Core-Bogue`, `m-2`): removed by `clean()`; the unit header
  `g C m-2 yr-1` reduces to exactly `g C m-2 yr-1`.
- Young Sound is listed under country GREENLAND, region `Europe`; Region is the source's own continental label and is kept.
- `Narragansett Bay` has two rows with different measures/methods (`GPP 14C 237 n=17`; `NPP O2 & 14C 236 n=8`).
- Four rows name a personal communication in the reference cell (Bay of Brest, Gullmar Fjord, Rhode River Estuary, Tampa Bay).
- Two Kattegat rows (n = 118 and 117) quote a web address (`http://mads.dmu.dk`) after the citation.

## Reference list and crosswalk

The reference list is 144 paragraphs separated by a vertical gap of more than 20 pt (lines are 14 pt apart; the whole list
was split without a single paragraph that fails to end in a year). 28 entries print a DOI (extracted, lower-cased). Each of
the 202 cited items (e.g. `Rydberg et al., 2006`) is matched to a list entry by the automatic rule (first-author surname
+ year, accent- and case-folded, with a structural check that one author / `X and Y` / `et al.` agrees with the entry's
author part): 191 matched automatically, 11 (7 distinct strings) through the explicit `CROSSWALK` in `extract.py`, none
unmatched (an unmatched item raises). **All 144 list entries are cited by at least one row**, which is a strong check that
no item was matched to the wrong entry (a wrong match would leave an entry uncited). The full crosswalk
(`reference_crosswalk.csv`, one line per distinct item and role) was read by eye once: every `in-text -> entry` pair was
checked for surname, year and (for `and` / `et al.`) author plausibility.

The 7 crosswalk strings and why they cannot be automatic:

| In-text | List entry | Reason |
|---|---|---|
| `Sandoval-Rojo, 1988` | Sandoval-Rojo, Verdugo, Zaragoza-Araujo, Day, Estrada-Mercado 1988 | one author cited, five in the entry; the only Sandoval-Rojo 1988 |
| `Tundisi, 1973` | Tundisi, Tundisi, Kutner 1973 | same |
| `Boynton, 1982` | Boynton, Kemp, Keefe 1982 | same; the list's only Boynton 1982 (the other Boynton entry is 1973) |
| `Stockner et al., 1979` | Stockner, Cliff, Shortreed 1979 | three Stockner entries (1977, 1979 x2) |
| `Stockner and Cliff, 1979` | Stockner and Cliff 1979 | same |
| `Stockner, 1977` | Stockner, Cliff, Buchanan 1977 | same; single author cited, three in the entry |
| `Randall and Day Jr, 1987` | Randall and Day Jr. 1987 | in-text drops the entry's `Jr.` punctuation |

These are inferences from surname + year being unique in the supplement's own list; they are **not** confirmed against the
papers. `Wassman` (in-text and list spelling agree) is kept as the source spells it.
