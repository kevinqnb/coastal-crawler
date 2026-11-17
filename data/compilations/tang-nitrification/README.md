# tang-nitrification (ocean nitrification rates, with water nutrients and oxygen)

Tang, W., Ward, B.B., Beman, M., Bristow, L., Clark, D., Fawcett, S., et al. (2023). *Database of nitrification and nitrifiers
in the global ocean* [Dataset]. Zenodo, https://doi.org/10.5281/zenodo.8355912 (paper: *Earth System Science Data* 15,
5039-5077, https://doi.org/10.5194/essd-15-5039-2023).

- **Version:** `v1 (2023-09-18)`, read from the Zenodo record by the integration plan; the workbook states no version.
- **Access date:** 2026-11-15. **License:** CC BY 4.0 (Zenodo record).
- **Raw files:** `Database of nitrification and nitrifiers in the global ocean.xlsx` (8 sheets) and
  `Template_Database of nitrification and nitrifiers in the global ocean.xlsx` (the empty submission template; not read).

A literature compilation of volumetric ammonia-oxidation (2,417 rows) and nitrite-oxidation (1,010 rows) rates in the water
column, each row with the ambient temperature, salinity, nutrients and oxygen of the sample, plus gene-abundance sheets for the
nitrifiers and two sheets of method metadata. 72 reference strings; 2,589 distinct samples (latitude, longitude, depth, date).

**Facts:** 9,824 = 2,941 rates + 6,883 J1 water values. Units are the header's (`nmol N L-1 d-1`; `μM`, U+03BC); nothing is
converted.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `tang_ammonia_oxidation` | C5 `ammonia_oxidation` | 2,065 | nmol N L-1 d-1 |
| `tang_nitrite_oxidation` | C5 `nitrite_oxidation` | 876 | nmol N L-1 d-1 |
| `water_ammonium` ("Ammonium+ammonia") | J1 `ammonium` | 1,824 | μM |
| `water_nitrite` | J1 `nitrite` | 1,890 | μM |
| `water_nitrate` | J1 `nitrate` | 2,062 | μM |
| `water_o2` | J1 `dissolved_oxygen` | 1,107 | μM |

`sd` is the sheet's `Standard deviation of rate` where it is a number (1,612 facts); `NA`, `NM` and blank give a NULL `sd`. 34 rows
have an SD but no rate fact (the rate is `BDL`/`NM`): the SD is not loaded on its own.

## J1: one fact per sample and attribute

Each row of a rate sheet carries the ambient nutrients and oxygen of its sample; the same sample is repeated on the ammonia and
nitrite sheets. A J1 value therefore belongs to the **sample**: the sample key is latitude | longitude | depth | date (floats cast
before the key is built, datetimes as ISO dates, text dates verbatim). 9,235 numeric J1 cells give **6,883 facts and 2,352
collapsed repeats** (exactly equal values of one sample and attribute: ammonium 610, nitrate 637, nitrite 639, oxygen 466; counted in
`extract_report.json`). Where one sample carries two different values (57 sample-attribute pairs: ammonium 5, nitrate 17,
nitrite 25, oxygen 10), every distinct value is loaded with the flag `covariate_conflict`: **141 facts**
(ammonium 19, nitrate 43, nitrite 59, oxygen 20); nothing is averaged. 141 - 57 = 84 is the plan's "84 pairs". The first raw cell
(sheet order, then row order) is the one a collapsed fact points at with `source_row_ref` and takes its reference from.

**Deviation from the plan's wording.** INTEGRATION_PLAN 1.2 names the data source as part of the sample key. It is not: the
plan's own counts (2,352 collapsed, 6,883 facts) are only reproduced without it (with the reference string in the key the numbers
are 1,542 collapsed and 7,694 facts). The two rate sheets write the same paper differently (`Narure Comms` / `Nature Comms`,
`Clark et al., 2008. LO.` / `L&O.`, `Mdutyana et al., 2022. L&O` / `Biogeosciences`, trailing dots) and the same stations are
reported under two papers (Beman 2012 on the ammonia sheet and Beman 2013 on the nitrite sheet: 63 samples; Wan 2018 and Wan 2022: 24).
The reference stays on the fact. Reference strings are never merged by similarity.

- **J1 beside a skipped rate still loads** (rule 3): 1,021 J1 facts sit on rows whose rate is `BDL` or `NM`.
- **J1 cells coded `BDL`/`NM`** (452: ammonium 214 + 13, nitrite 103 + 14, nitrate 63 + 16, oxygen 7 + 22) are skips, like the
  rates (D6). `NA` gives nothing.
- **pH.** The plan says the pH column is empty; it holds 66 numbers (Breider et al. 2019, rows 175-184, and Shiozaki et al. 2019).
  The column has no unit anywhere in the file, so J1 rule 2 (units required) skips them with `units_missing`; the value is in the
  skip report. **Open for the coordinator:** load as `unitless` (as for Fulweiler) if the paper confirms it.
- **One extra NaN cell.** The workbook holds one literal NaN in the oxygen column (ammonia sheet, data row 516); it is read as blank.
  This is the single cell that made a plain `isinstance(float)` count 9,236 instead of the plan's 9,235.
- **Sample slots.** Depth -> `sample_depth_m` (13 ammonia-sheet and 14 nitrite-sheet rows have a blank or `NA` depth: 14 samples, flag `depth_missing`, 66 facts),
  temperature -> `water_temperature_c`, salinity -> `water_salinity` (`psu`, header). The rows of one sample must agree on a slot or the
  extract raises. **One sample disagrees** (-67.774 N, -69.2762 E, 80 m, 2011-01-17: two consecutive rows with replicate casts, T -0.955 / -0.888,
  S 33.974 / 33.947, and different nutrients): its slots are NULL (`SLOT_CONFLICT_SAMPLES`, named in the report); the values are
  recoverable through `source_row_ref`. Not merged, not split.

## Mapping

- **Habitat** is `Water column` for every location (raw `habitat_type` constant `water column`, label map row), derived from the
  dataset's scope and never stated by the source: flag `habitat_from_scope` on all 9,824 facts. Setting NULL. `bottom_type` NULL.
- `location_key` = latitude | longitude: **603 locations**; no names, no water depth. Latitudes and longitudes are all numeric and in range.
- **Dates.** A datetime gives year and month (the day is only in the sample key). The 29 text forms are an explicit table in
  `extract.py` (`DATE_TEXT`): `YYYY Month` -> year and month; an in-year range (`1993 March to May`, `2008 May/June`) -> year, month
  NULL; the cross-year range `2008 Dec-2009 Jan` -> year and month NULL, flag `year_missing` (419 facts). The text of a range is kept in
  `sample.depth_notes` (`date as given: ...`). 2,556 samples carry facts (the other 33 of the 2,589 have only a skipped rate); years 1982-2020.
- **Methods.** `rate metadata` (57 rows) and `rate metadata 2` (26) hold the experiment description per `Data source`. A rate fact gets one
  `method_dim` row per (attribute, reference string): `technique` = the `Method` column, every other column in `params`
  (tracer, enrichment, incubation condition, volume, time, termination, product measured, isotope method, rate calculation, the
  detection limit). 60 are `matched` (exact string match within the process's own metadata sheet), 33 `method_id_unmatched`
  (the metadata sheets spell 23 strings differently from the data sheets; they are not matched by similarity). The J1 attributes have one
  `method_id_null` row each (4). The metadata sheet's detection-limit column is filled for one source only (`0.001 to 0.34`).
- **Not loaded** (listed in `extract_report.json`): `PAR` (319 numeric cells), `Light level` (1,446) and `Potential density` (1,096) are phase-2
  `other` attributes; the gene sheets `ammonia oxidizer` (2,249 rows, 6,414 gene values) and `NOB` (658 rows, 696) and their two qPCR metadata sheets are phase 2
  (`copies L-1`). The empty template workbook is not read.

## Missing codes and skips

Per column (all counted in the report): blank and `NA` -> no value; `NM` (not measured) and `BDL` (below detection limit) in a rate or J1
column -> a skip, reasons `not_measured` / `below_detection_limit`; an unrecognised non-numeric string raises.

| Skip | Cells |
|---|---|
| rates `BDL` / `NM` | ammonia 266 / 86, nitrite 119 / 15 |
| J1 `BDL` / `NM` | 387 / 65 |
| pH `units_missing` | 66 |

**1,004 skips.** Each `BDL` skip row carries `detection_limit_comments` (the `detection limit ...` comment lines of the reference's block, e.g.
`detection limit of ammonium: 0.006 uM`) and `detection_limit_metadata` (the metadata sheet's limit). 155 of the 772 `BDL` skips have neither.

## The `Data source` column

Only the first row of each block holds the reference string; **comment lines are written into the same column** (`Data shown in Figure 3`,
`detection limit: 0.02-1.87 nM d-1 for winter rates`, `INSPIRE`, `SSB Autumn`, `(Agulhas Current/SW subtropical Indian Ocean)`, a lone space).
The extract recognises them with explicit prefix and exact-string tables (`NOTE_PREFIXES`, `NOTE_EXACT`); anything else must contain a year followed
by a full stop (or `unpublished`) to count as a reference, or the extract raises. A row belongs to the last reference above it. The plan's "130 + 80
strings" counted the comments too: there are **72 reference strings** (some spelled differently on the two sheets).

## References

`reference_key` = `ref:<normalised string>`: 72 `reference_dim` and 72 `citation_dim` rows (every reference carries at least one fact). `bibliography_id`
= the string; `year` = the first four-digit year in it (5 `unpublished` entries have none); no title, authors or DOI (the DOI-resolution job
can attach them). A collapsed value keeps the reference of its first raw cell.

## Flags

`habitat_from_scope` 9,824, `year_missing` 419 (facts), `depth_missing` 66, `zero_value` 264 (18 rate zeros), `covariate_conflict` 141, `negative_value` 1 (a J1
nitrate of -0.000214 uM, Clark et al. 2008, data row 228 of the nitrite sheet; a slightly negative autoanalyser value, kept).

## Row accounting

13,180 cells = 9,824 facts + 1,004 skips + 2,352 collapsed repeats: 3,427 source rows; rate cells 2,941 numeric + 486 skipped; J1 cells 9,235 numeric
(6,883 facts + 2,352 collapsed) + 452 skipped; pH 66 skipped. The extract asserts the totals, and that the samples, locations, methods and
references of the facts are exactly the rows written. After the central build every fact was checked against the raw cell it points to by code that
does not use `extract.py` (value, unit from the header, sample key, location key, `sd`, sample depth): 9,824 of 9,824 agree. A one-study check (Baer et al. 2017
plus the first two nitrite-sheet rows, predicted from the raw rows alone: 26 facts, 2 skips, 8 samples, 2 locations, 2 references) matched. Adding this
source left the other nine sources' content unchanged (316,643 facts + 9,824 = 326,467). Two runs of `extract.py` give byte-identical parquet.
`source_row_ref` is `<file>[<sheet>]#data_row=<0-based row>:<attribute>`.

## Overlap with other compilations

Never merged. N2 fixation (`shao-nfix`) is a different process, but some stations are shared; Fulweiler `wc` rows and the Tang J1 values may report the same
water chemistry (set beside the joined WOD/GLODAP layers later).

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
