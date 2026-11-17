# paradis-mosaic: MOSAIC v2.0 (Modern Ocean Sediment Archive and Inventory of Carbon)

Numbers below are from `extracted/extract_report.json` of the build (access date 2026-11-15, `--min-subtidal-depth-m 1`).

**Citation.** Paradis, S. et al. (2023): Modern Ocean Sediment Archive and Inventory of Carbon (MOSAIC), v2.0 [Dataset].
Zenodo, doi:10.5281/zenodo.8322094 (ESSD preprint essd-2023-158). License CC BY 4.0 (`docs/README.md`; Zenodo's own
license field says "other-open").

**Version.** The file we hold is the PostgreSQL dump `Datasets/mosaic_ESSD` of the database `mosaic_2_ESSD`, archive
created 2023-04-25 (dumped from PostgreSQL 13.4, archive format 1.14). The Zenodo v2.0 release is dated 2023-09-06, so the
dump **predates** the release and may differ from it; `source.version` says both.

## What it holds

368 articles (343 DOIs), 20,851 cores (`geopoints`), 39,487 samples (depth intervals of those cores), 199,429
`sample_metadata` rows (sample x material x analysis x replicate x article, giving the reference, `method`,
`method_details` and a "calculated" flag; `sample_exclusivity_clause` is 0 everywhere, so no restricted data), and 13 value
tables (`sample_composition` 35,218 rows, `sample_sediment_properties` 14,520, `sample_isotopes` 12,955, `sample_14C`
3,141, biomarker tables, ...). `spatial_ref_sys` is empty. All of this is a marine-margin sediment archive: shelf to abyssal,
no tidal wetlands.

**Loaded (147,121 facts):** every non-null value cell of `sample_composition` and (selected columns of)
`sample_sediment_properties`. **Not loaded:** see "Not loaded" below.

## Files and how to run

| File | What |
|---|---|
| `Datasets/mosaic_ESSD` | the pg_restore archive (raw, ignored by git) |
| `Datasets/DatasetS1.csv`, `DatasetS2.csv` | authors' QC thresholds and variable descriptions (raw) |
| `src/`, `Excel_templates/`, `docs/` | authors' QC/population code, input templates, docs (raw) |
| `extract.py` | dump -> contract tables (tracked) |
| `extracted/` | output: eight parquet tables, `skip_report.jsonl`, `extract_report.json` (ignored) |

```bash
python -I data/compilations/paradis-mosaic/extract.py --access-date 2026-11-15 \
    --pg-restore /opt/homebrew/bin/pg_restore --min-subtidal-depth-m 1
```

Both `--pg-restore` and `--min-subtidal-depth-m` are required (no defaults). `--article-ids ...` is a debug subset used for the
staged-gate check (other cells are counted as `outside_subset`).

### The dump format and `pg_restore`

`mosaic_ESSD` is a PostgreSQL **custom-format** archive (version 1.14), not text. It can only be read with `pg_restore`.
The plan recorded that the cluster's system `pg_restore` (v10) refuses it ("unsupported version (1.14)") and that the
PostgreSQL 18.1 module's binary works; on the machine used for this build `/opt/homebrew/bin/pg_restore` is **16.14** and
reads it. So `extract.py` does **not** gate on a version number: it takes the binary path as an argument and runs

1. `pg_restore --version` (recorded in the report),
2. `pg_restore --list` (the header must say `dbname: mosaic_2_ESSD` and `Archive created at 2023-04-25`),
3. `pg_restore --data-only -f <temp file>` (plain-text `COPY` blocks, about 23 MB, no server needed),

and raises if any step exits non-zero or writes to stderr. The COPY blocks must then be **exactly the 27 expected tables
with the expected row counts** (`EXPECTED_ROWS`; all counts above are checked) and the expected column lists of the tables read
by name. The COPY text format is decoded completely (`\N` is NULL only as a whole field; `\\ \n \t \r \b \f \v`, octal and
hex escapes; unit-tested). The report stores the `pg_restore` version string, the archive header and the SHA-256 of the
dump (`4be8aa4f...`). Reading the whole dump takes seconds; writing the parquet takes a few minutes.

## Facts

One fact = one non-null value cell, identified by (table, `sample_id`, `material_analyzed_id`, `replicate`, column).

| Attribute | Type / subfield | Source column | Facts | Units (source's) |
|---|---|---|---|---|
| `mosaic_organic_carbon` | C1 organic_carbon | `total_organic_carbon_%` | 33,073 | % |
| `mosaic_total_carbon` | C1 total_carbon | `total_carbon_%` | 13,900 | % |
| `mosaic_total_inorganic_carbon` | C1 total_inorganic_carbon | `total_inorganic_carbon_%` | 12,300 | % |
| `mosaic_caco3` | C1 carbonate | `CaCO3_%` | 14,880 | % |
| `mosaic_loi_organic_matter` | C1 loi_organic_matter | `OM_LOI_%` | 2,730 | % |
| `mosaic_total_nitrogen` | C1 total_nitrogen | `total_nitrogen_%` | 10,658 | % |
| `mosaic_oc_tn_ratio` | C1 c_n_ratio | `OC:TN_ratio` | 9,998 | `(blank in source)` |
| `mosaic_dry_bulk_density` | C1 dry_bulk_density | `dry_bulk_density_g_cm3` | 2,067 | g·cm-3 (U+00B7) |
| `mosaic_porosity` | C1 porosity | `porosity` | 2,186 | `-` |
| `mosaic_water_content` | C1 water_content | `water_content_%` | 1,445 | % |
| `mosaic_gravel_content` / `sand` / `silt` | G1 gravel/sand/silt_content | `gravel_%`, `sand_%`, `silt_%` | 5,503 / 8,333 / 7,366 | % |
| `mosaic_clay_lt4um`, `mosaic_clay_lt2um` | G1 clay_content | `clay_%_4microm`, `clay_%_2microm` | 4,334 / 3,012 | % |
| `mosaic_mud_content` | G1 mud_content | `mud_silt_clay_%` | 3,165 | % |
| `mosaic_mean_grain_size` / `median` / `mode` | G1 mean/median/mode_grain_size | `*_grain_size_microm` | 2,139 / 708 / 76 | µm (U+00B5) |
| `mosaic_organic_nitrogen`, `inorganic_nitrogen` | other | `organic_nitrogen_%`, `inorganic_nitrogen_%` | 1,235 / 158 | % |
| `mosaic_oc_on_ratio` | other | `OC:ON_ratio` | 2,289 | `(blank in source)` |
| `mosaic_total_phosphorus`, `organic_phosphorus` | other | `P_total_%`, `P_org_%` | 930 / 21 | % |
| `mosaic_total_sulphur` | other | `S_total_%` | 3,181 | % |
| `mosaic_biogenic_silica` | other | `SiO2_%` (opal) | 1,434 | % |

Phase 1 follows the plan: from `sample_composition` all value columns (OC, TC, TIC, CaCO3, LOI, TN, OC:TN as C1;
organic and inorganic N, OC:ON, P, S and SiO2 as `other`, as the plan's "ON/IN/P/S/SiO2"; OC:ON is not named by the plan and is
treated like ON), from `sample_sediment_properties` the listed columns. The counts are smaller than the raw non-null counts by
the skips and attached errors below (e.g. OC 33,193 cells -> 33,073 facts; TN 10,699 -> 10,658; OC:TN 10,019 -> 9,998;
mean grain size 2,141 -> 2,139).

### The `*_error` columns (SD or SE?)

**The source never says.** `DatasetS2.csv` and the Excel template README describe every `*_error` column only as "Error of
..." (e.g. "Error of the total organic carbon content, if available"); the tutorial and the authors' code are silent. So the
error is **not** stored in `sd` or `se` (both NULL for every fact). It sits in the `method_dim.params` JSON of the fact's own
method row: `error_value`, `error_column` and `error_meaning` ("unstated ... SD or SE not given"). 1,140 error cells (1,107
composition, 33 sediment) are attached this way. 220 of the attached error values are exactly 0 (likely a placeholder for "no
error"); they are carried as given. **114 error cells are skipped** (`error_without_fact`, all in `sample_sediment_properties`:
`mean_grain_size_microm_error` 114 of 147 and none other) because their value cell is blank (there is no mean to attach them
to); the skip report lists each. Error columns exist for OC, TC, TIC, CaCO3, LOI, ON, IN, TN, OC:ON, OC:TN, SiO2, P (not S) and mean grain size.

### Method, calculated and replicate

* `sample_metadata.method` -> `method_dim.technique` (`elemental analyzer`, `sieving`, `laser`, `titration`, ...);
  `method_details` and all metadata rows are in `params`. `method` = `_unknown` (75% of the metadata rows) gives technique NULL
  and `method_status` = `method_id_null` (329 of 884 method rows); otherwise `matched` (552). There is deliberately **no
  `method_missing` flag** for these (it would sit on most facts; use `method_status`).
* **`sample_analysis_calculated`** is 1 on 30,061 of the 199,429 metadata rows (all tables, including unloaded ones). On loaded
  facts it gives the flag `calculated_by_source` on **22,290 facts** ("we calculated the value from other parameters", e.g.
  OC from TOC fractions, or the sum of grain-size classes).
* **`replicate`** is part of the cell key and in `params`; replicate > 1 gives the flag `replicate` (1,143 facts). Replicate 1 is
  unflagged even when a replicate 2 exists.
* A cell can be backed by several metadata rows (different articles): **355 facts** have a composite `reference_key`
  (`article:a+article:b`, one citation row per article). Where those rows also disagree on method (7 facts) or "calculated"
  (3 facts) the method row is `ambiguous_duplicate_method_id`, technique NULL, and every metadata row is in `params`
  (the flag `calculated_by_source` is set if any row says calculated).

### Material analysed (non-bulk fractions)

`material_analyzed_id` 1 (`bulk`) holds 142,544 facts. The other 4,577 are analyses of a size or density fraction (`<2 mm`
2,860, `<0.063 mm` 1,056, density classes, ...): they are loaded with the flag **`non_bulk_material`**, the material label is
in `params` and in the method key and `fact_key`. **Filter on this flag before comparing OC/N values with bulk sediment.**
Material `surface_area` (108 organic-carbon values between 0.009 and 0.073 in a `%` column; evidently not a bulk percentage) is
**skipped** (`material_unit_unclear`).

### Units

Verbatim from the dump's `sample_analysis_units` (checked against the declared units at run time). Two exceptions, both
documented: (1) blank source unit (OC:TN, OC:ON ratios; the source does not say mass or molar) -> the literal `(blank in
source)`; (2) `clay_%_2microm` has unit `µm` in the dump **and** DatasetS2, although its description is "Content of clay
(< 2 µm) in the sample" and DatasetS1 sums it with the other percentage classes to 100: it is declared `%`, and the source's
`µm` is in `params` (`unit_in_source`, `unit_note`). `surface_area_m2_g` carries the odd unit `m2·g` in the source (not loaded).
Code points: micro sign U+00B5 (`µm`), middle dot U+00B7 (`g·cm-3`); the Chang compilation uses U+03BC for `μM`/`μmol`.

### Flags used

`zero_value` 4,171; `negative_value` 18 (CaCO3 8, TIC 10); `outside_source_qc_range` 38 (value outside the min/max DatasetS1
lists for the column: CaCO3 below 0 / above 100, OC:TN above 55, TIC, median grain size); `calculated_by_source` 22,290;
`replicate` 1,143; `non_bulk_material` 4,577; `habitat_from_scope` 133,775; `year_missing` 29,082. (`depth_missing`
is registered but is not on any fact.) Nothing is changed or dropped because of a flag. New vocabulary added for this source:
flags `non_bulk_material`, `outside_source_qc_range`; skip reasons `no_metadata_row`, `error_without_fact`,
`material_unit_unclear`; extension subfield `G1 mode_grain_size`; 26 `mosaic_*` attributes.

## Locations, depth and habitat

17,311 of the 20,851 cores have at least one fact and are loaded (one location per core; `location_key` = `core:<core_id>`).
`site_name` = `core_name`; `water_body` = `seas`; `bioregion` = the Longhurst province; `country` is NULL in the whole dump;
`position_method` says whether `georeferenced_coordinates` = 1 (coordinates obtained by georeferencing a map; 1,264 loaded
cores) or 0 (from the source). Not loaded from `geopoints`: core length, sampling method and campaign, EEZ, `ocean` (22 cores,
always with `seas` also set), comments, user name, MARCATS, sampling day.

**Depth source.** `water_depth_source_id` 1 = "Water depth provided in manuscript" (17,608 cores); 2 = "Water depth
extracted from GEBCO 2020" (3,243 cores; 2,833 of them have facts). The GEBCO depth is a derived layer, not a measurement
beside the sample, so it is **not loaded** (`water_depth_m` NULL, plan rule 3.5). Only manuscript depths are loaded (positive
down, 0 to 10,677 m).

**Habitat and the shallow-core threshold.** The dataset is marine sediment cores, so habitat comes from the dataset's scope,
not from a label: raw `habitat_type` = `marine sediment core` -> `Subtidal soft sediment`, with the fact flag
`habitat_from_scope`. Very shallow cores may be intertidal, so this is applied **only to cores whose loaded depth is >=
`--min-subtidal-depth-m`**; the value is a parameter with no default and is recorded in `extract_report.json` (and `source.notes`).
**This build uses 1 m**, the value proposed in the plan (a decision for the owner to confirm; changing it means re-running
`extract.py` and rebuilding). Counts of cores by water depth:

| depth <= | 0 m | 1 m | 5 m | 10 m | 200 m |
|---|---|---|---|---|---|
| all cores (manuscript + GEBCO depths; the plan's numbers) | 83 | 113 | 335 | 774 | 13,144 |
| manuscript depths only (the ones loaded) | 58 | 64 | 165 | 444 | 10,426 |

With the threshold at 1 m, 58 cores (all at exactly 0 m) are below it; 17,550 cores have habitat from scope. Cores with no
loaded depth (GEBCO-only) also get NULL habitat. In the loaded set: 14,448 of 17,311 cores have a habitat label (2,833 GEBCO +
30 shallow cores have none), and 133,775 of 147,121 facts carry `habitat_from_scope`; the other 13,346 facts have NULL habitat.
`setting` comes from `geomorphological_site` (91% blank in the dump): raw -> `geomorphic_setting`; only `shelf`, `estuary`, `fjord`
map to a setting; `canyon`, `glacial_trough`, `slope`, `basin`, `pro-delta`, `abyss`, `trough`, `trench`, `loch`, `plateau`,
`ridge`, `rise`, `fans`, `shelf_valley` stay blank (all 17 raw labels have a label-map row; `loch` is a Scottish sea inlet
but is not mapped because the plan lists the three settings only).

## Samples, dates, references

* `sample_dim` (34,913 samples with facts): depth min/max = `sample_depth_upper_cm` / `bottom`; representative depth = the
  source's `sample_depth_average_cm`, kept as given (138 samples have an average that is not the interval midpoint).
  13,467 samples have only an average depth, which is loaded as the representative depth with min/max NULL; every loaded sample has at least one depth value.
* Sampling year and month are core attributes in the dump: `sampling_year` / `sampling_month`; where those are NULL and
  `sampling_date` exists, the date is used (16 cores). 20 cores have a date that disagrees with the year or month columns: the
  columns win and the cores are listed in the report. 29,082 facts have no year (flag `year_missing`).
* References: `reference_key` = `article:<article_id>` (the dump's own id). 293 of 368 articles are cited by a loaded fact
  (312 reference rows including composites; 331 citation rows). DOI as stored for 316 of 331 citations (15 without, e.g. two MAREANO data
  releases and several older regional reports). The dump has no author-order column, so `authors` is ordered
  by `author_id` (population order) and may not be the paper's author order. The journal has no contract column.

## Not loaded (and why)

| What | Cells | Reason |
|---|---|---|
| Isotopes (`sample_isotopes`), 14C (`sample_14C`), biomarker tables (CuO, CuO grouped, alkanes, alkenones, alcohols, fatty acids, amino acids, sterols), `sample_rock_eval_pyrolysis` | 55,104 | phase 2 of the plan: not loaded here (the 14C and isotope families need their own attribute design; sample 10544's 14C rows even have a core id that disagrees with `samples`, asserted as the only such case) |
| `surface_area_m2_g` and `_error` (sediment properties) | 1,461 | not in the plan's phase-1 list |
| `cumulative_dry_mass_g_cm2` | 93 | no `sample_analysis_description` row (no analysis id, hence no metadata link) |
| GEBCO-derived water depths | 3,243 cores | derived layer (rule 3.5) |
| value cells with no metadata row | 172 skipped | see below |
| surface-area-material OC cells | 108 skipped | see above |

**Value cells with no metadata row (172 cells; composition 124, sediment 48).** The replicate numbering of the value table and of
`sample_metadata` disagree for these cells (169 of them have metadata only under a different replicate; 3 have none for that
analysis), so no article, method or calculated flag can be attached without guessing. They are skipped with reason
`no_metadata_row` (full detail per cell in `skip_report.jsonl`); nothing is borrowed from another replicate.

## Row accounting

`non_null_cells = facts + attached errors + skipped + not loaded` per column, per table family and overall (asserted in
`extract.py`, counted independently from the classification; every column of all 13 value tables):

| | Cells |
|---|---|
| non-null cells in the 13 value tables | 205,313 |
| facts | 147,121 |
| error cells attached to facts (params) | 1,140 |
| skipped: `no_metadata_row` / `material_unit_unclear` / `error_without_fact` | 172 / 108 / 114 (394) |
| not loaded: 11 other families | 55,104 |
| not loaded: surface area (+error) / cumulative mass | 1,461 / 93 |
| sum | 205,313 |

By family: `sample_composition` 108,126 = 106,787 facts + 1,107 errors + 124 + 108 skipped; `sample_sediment_properties`
42,083 = 40,334 facts + 33 errors + 48 + 114 skipped + 1,461 + 93 not loaded.

## Round trip and determinism

Every fact's `source_row_ref` (`mosaic_ESSD[<table>]#data_row=<0-based row of that table's COPY block>:<column>`) was joined to
the raw cell by an independent parse of the dump (not `extract.py`'s): all 147,121 values, table keys and `fact_key`s match,
and all 1,140 `error_value`s match their raw cells. Two `extract.py` runs gave identical tables and skip report. Staged
gate: a one-article subset (articles 1 and 62) predicted from SQL on the raw tables as 147 facts and 67 attached errors;
the extract produced 147 and 67. The full prediction (147,121 value cells with metadata, non-surface-area material) matched
as well. The full central build adds these facts to the existing 316,643 (463,764 in total).

## Overlap

None by design with SCOC / CCN (marine margins, not wetlands). Possibly overlaps dbSEABED (future) and SCOC's shelf sites in
the future join.

## Things to know

* The error columns are of unknown type (see above).
* `OC:TN` and `OC:ON` have no unit and the source does not say mass or molar.
* `calculated_by_source` and `non_bulk_material` should be filtered on before analysis; so should `outside_source_qc_range`.
* The dump is the April 2023 one, not necessarily Zenodo v2.0.
* Habitat depends on the required threshold (1 m here).
