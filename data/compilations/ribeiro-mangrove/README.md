# ribeiro-mangrove (reference list only: studies behind the neotropical mangrove litterfall model)

**This source has no measurements.** It holds only a list of studies.

Ribeiro, R.A., Rovai, A.S., Twilley, R.R., Castañeda-Moya, E. (2019). *Spatial variability of mangrove primary productivity in the
neotropics.* Ecosphere 10(8), e02841, DOI 10.1002/ecs2.2841. Appendix S1: `ecs22841-sup-0001-appendixs1.pdf`.

- **Version:** none exists, so `Ecosphere 10(8) e02841, Appendix S1 (PDF created 2019-07-23)` is recorded. **Access date:** 2026-11-15.
  **License:** CC BY 3.0 (your decision; the PDF states none).
- **Raw file:** 13 pages. Page 2 holds Tables S1 and S2 (the authors' model coefficients and ANOVA, which are regression output, not
  measurements, and are not loaded). Pages 3 to 13 hold the "List of studies used to build the litterfall model for mangroves in the neotropics".

What is loaded is that list, so its DOIs can be resolved and the papers added to literature extraction.

**Contents:** 0 facts, 87 references, 87 citations, 1 source row. The plan estimated about 88 references. The list has 87, counted three ways
(pdfplumber paragraphs, year-bearing paragraphs, and the poppler text).

## Building it: `--allow-factless`

The build refuses a source with no facts, so this one needs the explicit flag:

```
python scripts/build_compilations.py --out data/compilations/compilations.duckdb \
    --allow-factless ribeiro-mangrove data/compilations/*/extracted
```

The flag exempts only the named source from the empty-fact-table check and the "no habitat label map" check. It **raises if that source has
facts** or is not being built. So `reference_dim` and `citation_dim` here hold 87 study rows that no fact refers to.

## How it maps

- `reference_key` is `ref:<first author [and second | et al.] year>`, for example `ref:Adame et al. 2013`. All 87 are unique.
- `citation.authors` is the author string as printed. `citation.title` is everything after the year (the entries have no separable title or
  journal). `doi` is NULL, since no entry prints one. The OpenAlex job fills it later.
- `publication_type` from keywords: journal article 56, dissertation 10, thesis 8, book chapter 10 (8 are CARICOMP site chapters in one
  UNESCO volume), proceedings 1, and NULL for 2 reports. The plan said "else article", but reports and a proceedings volume are not articles.
- The text layer garbles the authors of Agraz Hernández et al. 2011. It is kept as printed.

## By-eye review (done once, 2026-11-17)

All 87 parsed entries (`references_parsed.csv`) were read against the extracted text. Each starts at an author list and ends at its full stop.
None is truncated or merged. The four entries that cross a page break are joined correctly (Alcolado 2001, Castañeda-Moya 2013, Flores-Verdugo
1990, Schmiegelow 2009). The count of 87 equals the number of year-bearing paragraphs, and the extract refuses to run unless it is 87.

## Row accounting

0 measurement cells, so 0 facts and 0 skips. 87 paragraphs = 87 references = 87 citations. An independent check (`verify_list.py`) finds all 87
citations verbatim in poppler's text. Adding this source changed the totals by exactly +1 source, +87 references, +87 citations and 0 facts, as
predicted. Without the flag the build fails with `fact table is empty`. Two runs give identical output.

## Not loaded

Tables S1 and S2, the front matter and page numbers.

## Overlap

Many of these studies report mangrove litterfall and productivity, and are likely in `smithsonian-coastal-carbon` and `usgs-wetlands`. The list
is here to seed literature extraction, which needs their DOIs. No join is attempted.

## Files

`extract.py`, `verify_list.py` (poppler cross-check and database round trip) and `references_parsed.csv`. Tests are in
`tests/test_compilations_ribeiro.py` and `tests/test_compilations_factless.py`.

Back to the [compilations index](../README.md).
