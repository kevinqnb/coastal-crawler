# cloern-phytoplankton (Table S1: median annual phytoplankton primary production in estuarine-coastal ecosystems)

Cloern, J.E., Foster, S.Q., Kleckner, A.E. (2014). *Phytoplankton primary production in the world's estuarine-coastal
ecosystems.* Biogeosciences 11, 2477-2501, https://doi.org/10.5194/bg-11-2477-2014. Supplement:
https://doi.org/10.5194/bg-11-2477-2014-supplement.

- **Version:** none exists; `Biogeosciences 11 (2014) supplement` is recorded.
- **Access date:** 2026-11-15. **License:** CC BY 3.0 (printed on the supplement's first page).
- **Raw file:** `bg-11-2477-2014-supplement.pdf` (19 PDF pages): page 1 front matter, pages 2-7 Table S1, pages 8-19 the
  supplement's own reference list.

An inventory of the median annual phytoplankton primary production (APPP) of 131 estuarine-coastal ecosystems, each row one
ecosystem and measure type (GPP gross, NPP net, U unreported) and method (14C, O2, 13C, O2 & 14C) with the number `n` of
annual measurements pooled into the median. The numbers come from the review's literature search; the paper is a
*compilation of published values*, so these are second-hand measurements.

**Facts: 163**, one per table row, one attribute `cloern_appp_median` (B8 `depth_integrated_primary_production`), unit
**`g C m-2 yr-1`** (the column header's `g C m-2 yr-1` with the PDF's hyphen artifacts, soft hyphen U+00AD and U+2010, removed;
asserted). `value` = the median APPP, `n` = the number of annual measurements; no SD or SE. Values range from **-105**
(Scheldt Estuary NPP, printed negative; flag `negative_value`) to 1,890 (Tamagawa Estuary NPP). 77 rows have `n` = 1.

The measure is in the method row, not in the attribute: 74 rows are `U` (unreported), 51 `GPP`, 38 `NPP`
(`method.params.measure`, with its meaning); `technique` is the method label as printed: `14C` 112, `O2` 43, `13C` 6,
`O2 & 14C` 2 (10 distinct method rows). **Mixed GPP / NPP / U must not be pooled without reading the measure.**

## Row accounting and the source's own count

163 APPP cells = 163 facts + 0 skipped + 0 collapsed (`skip_report.jsonl` empty; asserted in `extract.py`).

- **The plan expected ~250 rows; the file has 163.** The plan's estimate was unverified.
- **Ecosystems: 131**, matching the caption (`Chesapeake Bay 1960s` is a separate name in the source and counted as one).
- **Sum of `n` is 1,147, not the 1,148** in the table caption ("computed from 1148 measurements made in 131 estuarine-coastal
  ecosystems"). The source is one short of its own caption; the cells were read three independent ways with the same result
  (see `verification_report.md`), so nothing was changed. `extract.py` asserts 1,147 and records both numbers
  (`extract_report.json`, `source.notes`). Which number is right cannot be told from the supplement. **Use 1,147 as the sum of
  `n` in this database.**
- The caption also says 47 of the values are single measurements made at one site during one year; this table has 77 rows with
  `n` = 1 (`n` counts annual measurements per region and measure type, which is not the same statement), so that sentence is
  not reproduced here.

After the central build every one of the 163 facts was checked against the raw cell it points to by code that does not use
`extract.py` (`verify_table.py <compilations.duckdb>`: geometry-based re-reading of the PDF; ecosystem, country, region,
measure, method, APPP and n): **163 of 163 agree** and the set of raw rows equals the set of facts. A tiny end-to-end on PDF
page 2 alone (predicted from the page: 20 facts, `n` sum 69, 15 locations, 2 items through the crosswalk, 1 personal
communication, 2 "cited in" rows) matched. Adding this source left the other nine sources unchanged in `compilations.duckdb`
(316,806 facts = 316,643 + 163). Two runs of `extract.py` give byte-identical parquet and CSVs.

## Mapping

- **Locations (131):** one per ecosystem name; `site_name` = ecosystem, `country` = the source's COUNTRY string
  (whitespace-normalised, capitals as printed, e.g. `DENMARK/ SWEDEN`, `USA / CANADA`), `bioregion` = the source's Region
  (`North America` 62 rows, `Europe`, `Baltic`, `Asia`, `South-Central America`, `Australia-New Zealand`, `Africa`: continental
  labels, **not** a Short et al. bioregion). Each ecosystem has exactly one (country, region) pair (asserted).
  **No coordinates**: `latitude`/`longitude` are NULL on every location. Geocoding 131 names would be a guess and is
  deliberately not done here; the ecosystem name and country are the handle for a later join. `location_key` =
  `<ecosystem>|water column`.
- **Habitat:** `habitat_type` = `water column` for every row (label map: `Water column`, `bottom_type` NULL), a habitat derived
  from the dataset's scope (pelagic production), so **every fact carries `habitat_from_scope`**. No setting or salinity
  label (the ecosystem names say `Bay`, `Estuary`, `Lagoon`, `Sea` inconsistently; not mapped).
- **Samples (163):** one per row, no depth, year or taxon (annual, depth-integrated, pooled over years and sites).
- **Flags:** `habitat_from_scope` 163; **`source_median`** 163 (the column is "Median APPP"; for `n` = 1 the median is the one
  measurement, so the flag is on every fact, as the plan asked); `cited_secondary` 13; `pers_comm` 4; `negative_value` 1.

## References

The Reference cell holds in-text `(Author, year)` citations. 163 rows cite **144 distinct papers** (202 items; **every one of
the 144 entries of the supplement's own reference list is cited**) through an explicit match to that list: 191 items matched
automatically (first-author surname + year with an author-count check), 11 items (7 strings) through the `CROSSWALK` in
`extract.py`, none unmatched (an unmatched item raises; every CROSSWALK entry must be used; see
`reference_crosswalk.csv` and `verification_report.md`, which lists the seven and why). The crosswalk is an inference from
surname + year being unique in the list and is not checked against the papers.

- `reference_key` of a row = the `+`-joined keys of the papers it cites (**124 references**, **159 citations**: one citation
  row per cited paper, `<key>|<k>`). A paper's key is `doi:<bare doi>` when its list entry prints a DOI (28 entries do), else
  `ref:<authors part of the entry> <year>`.
- A list entry has no separable title: `citation.title` holds the entry text after the first `: ` (title, journal, volume,
  pages as printed), `citation.authors` the text before it (as printed), `citation.year` the entry's year. No journal column
  exists. `bibliography_id` = `<authors> <year>`. Entry text is recoverable in full from `references_parsed.csv`.
- **`cited_secondary`** (13 rows): the cell says `(A) cited in (B)`; the first paper is the study and carries the value's
  provenance, the later ones are the secondary source. Both are citations of the row; the flag marks the case.
- **`pers_comm`** (4 rows: Bay of Brest, Gullmar Fjord, Rhode River Estuary, Tampa Bay): the cell names a personal communication
  beside the citation. The communication is not a paper and has no citation row; its text is in
  `extract_report.json` (`pers_comm_texts`) and in the raw cell.
- A web address after two Kattegat citations is not loaded as a citation.
- DOIs for the 116 entries without one come from the later OpenAlex job.
- Because every entry is cited by the table, the supplement's list and the table agree completely.

## Not loaded

Table S1 is the only data. The front-matter text, the caption, the headers and the page numbers are not loaded. The Reference
cell's personal-communication text and web address are not loaded as citations (see above).

## Flags in the vocabulary used here

`habitat_from_scope`, `source_median`, `cited_secondary`, `pers_comm`, `negative_value` (all existing). New this session:
attribute `cloern_appp_median` (unit `g C m-2 yr-1`, ASCII hyphens, no U+2212) and the label-map row
`cloern-phytoplankton,habitat_type,water column,habitat,Water column`.

## Files

`extract.py` (reads the PDF with pdfplumber; `python -I extract.py --access-date 2026-11-15`), `verify_table.py` (independent
re-reading + database round trip), `verification_report.md`, and the review CSVs it writes: `table_s1_rows.csv` (the 163
rows as read), `references_parsed.csv` (144 entries, DOI, whether cited), `reference_crosswalk.csv`. `extracted/` is
gitignored. Tests: `tests/test_compilations_cloern.py`.

## Overlap with other compilations

Never merged. Water-column primary production at estuarine-coastal sites also appears in `francesco-phytoplankton` and
`jorgensen-oxygen` (not built yet); benthic and water-column fluxes at some of the same estuaries appear in `rosentreter-ghg`.
With no coordinates, a join to these goes through the ecosystem name and country, and needs a gazetteer.

**J1 (joined water-chemistry fields):** not applicable; the table has no in-situ concentrations.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
