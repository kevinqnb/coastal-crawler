# cloern-phytoplankton (median annual phytoplankton primary production in estuarine-coastal ecosystems)

Cloern, J.E., Foster, S.Q., Kleckner, A.E. (2014). *Phytoplankton primary production in the world's estuarine-coastal ecosystems.*
Biogeosciences 11, 2477-2501, DOI 10.5194/bg-11-2477-2014 (supplement DOI 10.5194/bg-11-2477-2014-supplement).

- **Version:** none exists, so `Biogeosciences 11 (2014) supplement` is recorded. **Access date:** 2026-11-15. **License:** CC BY 3.0
  (printed on the supplement).
- **Raw file:** `bg-11-2477-2014-supplement.pdf` (19 pages): front matter, Table S1 on pages 2 to 7, and the supplement's own reference list on
  pages 8 to 19.

The median annual phytoplankton primary production (APPP) of 131 estuarine-coastal ecosystems. Each row is one ecosystem, measure type
(GPP gross, NPP net, U unreported) and method (14C, O2, 13C), with the number `n` of annual measurements pooled into the median. These are
second-hand values from the review's literature search.

## Facts (163)

One fact per table row, one attribute `cloern_appp_median` (B8 `depth_integrated_primary_production`), in `g C m-2 yr-1`. `n` is the number of
annual measurements, and there is no SD or SE. Values run from -105 (Scheldt NPP, printed negative and flagged) to 1,890 (Tamagawa NPP). 77 rows
have `n` = 1.

The measure sits in the method row, not the attribute: 74 rows are `U`, 51 `GPP` and 38 `NPP`. `technique` is the printed method (`14C` 112,
`O2` 43, `13C` 6, `O2 & 14C` 2). **Do not pool GPP, NPP and U without reading the measure.**

## Row accounting and the source's own count

163 cells = 163 facts, nothing skipped.
- **The plan expected about 250 rows. The file has 163.**
- **Ecosystems: 131**, matching the caption.
- **The `n` column sums to 1,147, not the 1,148 in the caption** ("computed from 1148 measurements"). The cells were read three independent ways with
  the same result, so nothing was changed. The extract asserts 1,147 and records both numbers. **Use 1,147.**
- The caption's "47 single measurements" is not reproduced: the table has 77 rows with `n` = 1, and the two statements count different things.

All 163 facts were checked back to the PDF cells by independent code (`verify_table.py`). A test on page 2 alone predicted 20 facts and matched, and
two runs give identical output.

## How it maps

- **Locations (131):** one per ecosystem. `country` and `bioregion` (the source's continental region, not a Short et al. bioregion) are the
  source's strings. **There are no coordinates.** Geocoding 131 names would be a guess and is not done. The ecosystem name and country are the
  handle for a later join.
- **Habitat** is Water column for every row, from the dataset's scope, so all facts carry `habitat_from_scope`. No setting or salinity label.
- **Samples (163):** one per row, with no depth, year or taxon.
- **References:** the Reference cell holds in-text `(Author, year)` citations. They are matched to the supplement's own reference list (144
  entries, all cited, 202 cited items). 191 items match automatically by first-author surname and year. 11 items (7 strings) go through an explicit
  `CROSSWALK` that is inferred from surname and year being unique and not checked against the papers (`verification_report.md` lists them). An
  unmatched item raises.
  - The key is the `+`-joined paper keys. 124 references, 159 citations. A paper's key is `doi:<doi>` when its entry prints a DOI (28 do),
    otherwise `ref:<authors> <year>`. The other 116 get DOIs from the later OpenAlex job.
  - An entry has no separable title, so `citation.title` holds the text after the first `: `.
  - `cited_secondary` (13 rows) marks a cell saying `(A) cited in (B)`. Both are citations of the row.
  - `pers_comm` (4 rows) marks a personal communication beside the citation. It has no citation row, and its text is in the report.

## Flags

`habitat_from_scope` 163, `source_median` 163 (the column is a median; for `n` = 1 it is the one measurement), `cited_secondary` 13, `pers_comm` 4,
`negative_value` 1.

## Files

`extract.py` (reads the PDF with pdfplumber), `verify_table.py` (independent re-reading and database round trip), `verification_report.md`, and the
review CSVs `table_s1_rows.csv`, `references_parsed.csv` and `reference_crosswalk.csv`. Tests are in `tests/test_compilations_cloern.py`.

## Overlap

Water-column production at estuarine-coastal sites also appears in `francesco-phytoplankton` and `rosentreter-ghg`. With no coordinates, a join goes
through the ecosystem name and country and needs a gazetteer.

Back to the [compilations index](../README.md).
