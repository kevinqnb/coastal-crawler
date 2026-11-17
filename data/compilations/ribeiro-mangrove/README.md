# ribeiro-mangrove (reference list only: studies behind the neotropical mangrove litterfall model; NO MEASUREMENTS)

Ribeiro, R.A., Rovai, A.S., Twilley, R.R., Castañeda-Moya, E. (2019). *Spatial variability of mangrove primary productivity in
the neotropics.* Ecosphere 10(8), e02841, https://doi.org/10.1002/ecs2.2841. Appendix S1:
`ecs22841-sup-0001-appendixs1.pdf`.

- **Version:** none exists; `Ecosphere 10(8) e02841, Appendix S1 (PDF created 2019-07-23)` is recorded (the creation date is the
  PDF's own metadata).
- **Access date:** 2026-11-15. **License:** CC BY 3.0 (decided by the user; the PDF itself states none).
- **Raw file:** 13 PDF pages: page 1 front matter (authors and affiliations), page 2 Tables S1 (AIC model coefficients) and S2
  (ANOVA) of the authors' litterfall model, pages 3-13 "List of studies used to build the litterfall model for mangroves in the
  neotropics".

**This source has no measurements.** Tables S1 and S2 are the authors' regression output (model coefficients, AIC, ANOVA terms), not
measurements of mangrove litterfall, and are not loaded. What is loaded is the **list of studies**, so that their DOIs can be
resolved and the papers added to literature extraction.

**Rows: 0 facts; 87 references; 87 citations; 1 source row; 0 locations, samples, methods, study screening rows.** (The plan
estimated ~88 references; the list has **87** entries, counted three ways: 87 paragraphs parsed by pdfplumber; 87 year-bearing
paragraphs; 87 `. YEAR. ` occurrences in the poppler text of pages 3-13.)

## Building it: `--allow-factless`

The build refuses a source with no facts. This one must be built with the explicit flag, which the contract provides for exactly this
case (change C1):

```
python scripts/build_compilations.py --out data/compilations/compilations.duckdb \
    --allow-factless ribeiro-mangrove data/compilations/*/extracted
```

`extract.py` also passes the exemption to `write_extracted`. The flag exempts the named source from the empty-fact-table check and,
for an empty location table, from the "no habitat label map declared" error; it **raises if the named source has facts** and if it
names a source that is not being built. **`reference_dim` (and `citation_dim`) may therefore hold study rows that no fact refers to**:
here all 87 of them. No label-map row exists or is needed for this source.

## Mapping

- `reference_key` = `ref:<first author [and second author | et al.] year>` (`ref:Adame et al. 2013`, `ref:Hernández and Mullen 1979`,
  `ref:Heald 1969`); unique (asserted; none of the 87 has a year letter). `citation_key` = `<reference_key>|0`; one citation per
  reference. `doi` is NULL (no entry prints one: 0 of 87); the later OpenAlex job fills `doi_resolved`.
- `citation.authors` = the author string as printed (Ecosphere style: surname-first for the first author, initials-first after);
  `citation.year`; `citation.title` = **everything after the year** (title, journal, volume:pages, or institution / publisher, as
  printed: the entries have no separable title and the contract has no journal column); `bibliography_id` = authors + year tag.
- `publication_type` from keywords: **`journal article` 56** (the entry ends in `volume:pages.`), **`dissertation` 10** and
  **`thesis` 8** (the printed word, kept apart as the authors wrote it), **`book chapter` 10** (`Pages N-N in ...`, 8 of them
  CARICOMP site chapters in one UNESCO volume), **`proceedings` 1** (Pool et al. 1975), and **NULL for 2** (Schaeffer-Novelli et al.
  1988, a field-expedition report; Snedaker & Brown 1981, an EPA report). The plan said "else `article`"; the 2 reports and the one
  proceedings volume are not articles, so they are not labelled as such.
- The text layer garbles the author string of **Agraz Hernández et al. 2011** (`S. . Iriarte-Vivar`, `C. M. A. . M. A. Hernández`:
  duplicated initials and a repeated author block in the source file); it is kept as printed, and its `et al.` key does not
  depend on the garble. Two entries have a typographic space after an en dash in the page range (`101– 111`); kept.

## By-eye review of the parsed list (done once, 2026-11-17)

All 87 parsed entries (`references_parsed.csv`: key, type, author count, full text) were read against the extracted text. Result:
every entry starts at an author list and ends at its closing full stop; none is truncated or merged with a neighbour. The four
entries that run across a page break are joined correctly (Alcolado et al. 2001, 34 authors; Castañeda-Moya et al. 2013;
Flores-Verdugo et al. 1990, including the hyphen break `Yáñez-` / `Arancibia`; Schmiegelow 2009), and the independent check below
found each of them in poppler's text. A fifth entry (Menezes 2010) differs between the two PDF engines only by a line-end hyphen
(`estuarina-lagunar`). The keys are all distinct; the two `Menezes` and the two `Santos` entries differ by first-author initial and
year. The count of 87 equals the number of year-bearing paragraphs (asserted in `extract.py`, which also refuses to run unless it is
87, the reviewed count).

## Row accounting

0 measurement cells exist, so 0 facts + 0 skipped. 87 list paragraphs = 87 references = 87 citations. After the central build
(`verify_list.py <compilations.duckdb>`, which does not use `extract.py`): the database holds 87 references and 87 citations for this
source and **no fact**; all 87 citation rows (authors. year. text) reappear verbatim in `pdftotext`'s text of pages 3-13, and
poppler counts 87 year-bearing entries. A prediction was made before the build and matched: adding this source to the other ten
changed the totals by exactly +1 source, +87 references, +87 citations and **0 facts** (316,806 before and after;
`reference_dim` 3,404 -> 3,491, `citation_dim` 4,017 -> 4,104), and a build without the flag fails with `fact table is empty`.
Adding this source left the other ten sources unchanged. Two runs of `extract.py` give byte-identical parquet and CSV.

## Not loaded

Tables S1 and S2 (model coefficients), the front matter and the page numbers. No fact, location, sample, method or screening row
exists for this source.

## Overlap with other compilations

Many of the 87 studies report mangrove litterfall and net primary productivity and are likely to be in the CCN / USGS mangrove
data (`smithsonian-coastal-carbon`, `usgs-wetlands`). The list is here to seed literature extraction, which needs their DOIs; no
join is attempted. The 8 CARICOMP chapters (Bush, García et al., Gerace et al., Koltes et al., Laydoo et al., Pors & Nagelkerken,
Smith, Varela et al., all 1998) are from one edited volume (Kjerfve, ed., *CARICOMP - Caribbean coral reef, seagrass and mangrove
sites*, UNESCO).

**J1 (joined water-chemistry fields):** not applicable.

## Files

`extract.py` (`PYTHONPATH=<repo>/src python -P extract.py --access-date 2026-11-15`; it needs a `coastal_crawler` that has the `allow_factless` parameter of `write_extracted`; `python -I` ignores `PYTHONPATH`),
`verify_list.py` (poppler cross-check and database round trip), `references_parsed.csv` (the 87 parsed entries, for review).
`extracted/` is gitignored. Tests: `tests/test_compilations_ribeiro.py` and, for the flag, `tests/test_compilations_factless.py`.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
