# paradis-mosaic (MOSAIC v2.0: Modern Ocean Sediment Archive and Inventory of Carbon)

Paradis, S. et al. (2023). *Modern Ocean Sediment Archive and Inventory of Carbon (MOSAIC), v2.0.* Zenodo, DOI 10.5281/zenodo.8322094 (ESSD
preprint essd-2023-158).

- **License:** CC BY 4.0 (from the project docs; Zenodo's own field says "other-open"). **Access date:** 2026-11-15.
- **Version:** the file is the PostgreSQL dump `mosaic_ESSD` of the database `mosaic_2_ESSD`, created 2023-04-25. The Zenodo v2.0 release is
  dated 2023-09-06, so the dump **predates** it and may differ. `source.version` says both.
- Counts below come from `extracted/extract_report.json`, built with `--min-subtidal-depth-m 1`.

## What it holds

A marine-margin sediment archive, from shelf to abyss, with no tidal wetlands: 368 articles (343 DOIs), 20,851 cores, 39,487 samples (depth
intervals) and 199,429 metadata rows. Value tables hold composition (35,218 rows), sediment properties (14,520), isotopes, 14C and biomarkers.

**Loaded:** 147,121 facts, from `sample_composition` and selected columns of `sample_sediment_properties`.

## How to run

```bash
python -I data/compilations/paradis-mosaic/extract.py --access-date 2026-11-15 \
    --pg-restore /opt/homebrew/bin/pg_restore --min-subtidal-depth-m 1
```

Both `--pg-restore` and `--min-subtidal-depth-m` are required, with no defaults. `--article-ids ...` is a debug subset.

**The dump format.** `mosaic_ESSD` is a PostgreSQL custom-format archive (version 1.14), readable only with `pg_restore`. The plan recorded that
an old system `pg_restore` (v10) refuses it. Here `/opt/homebrew/bin/pg_restore` (16.14) reads it, so the extract does not gate on a version. It takes
the binary path, runs `--version`, `--list` (the header must say `mosaic_2_ESSD` and 2023-04-25) and `--data-only` (plain `COPY` text, about
23 MB, no server), and raises on any non-zero exit or stderr output. The result must be exactly the 27 expected tables with the expected row counts
and columns. The report records the `pg_restore` version, the header and the dump's SHA-256. Reading takes seconds and writing the parquet a few
minutes.

## Facts

One fact per non-null value cell. Units are the source's own.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `mosaic_organic_carbon` | C1 `organic_carbon` | 33,073 | % |
| `mosaic_total_carbon` | C1 `total_carbon` | 13,900 | % |
| `mosaic_total_inorganic_carbon` | C1 `total_inorganic_carbon` | 12,300 | % |
| `mosaic_caco3` | C1 `carbonate` | 14,880 | % |
| `mosaic_loi_organic_matter` | C1 `loi_organic_matter` | 2,730 | % |
| `mosaic_total_nitrogen` | C1 `total_nitrogen` | 10,658 | % |
| `mosaic_oc_tn_ratio` | C1 `c_n_ratio` | 9,998 | `(blank in source)` |
| `mosaic_dry_bulk_density` | C1 `dry_bulk_density` | 2,067 | g·cm-3 |
| `mosaic_porosity`, `_water_content` | C1 `porosity`, `water_content` | 2,186 / 1,445 | `-`, % |
| `mosaic_gravel_content`, `_sand_`, `_silt_` | G1 content | 5,503 / 8,333 / 7,366 | % |
| `mosaic_clay_lt4um`, `_clay_lt2um`, `_mud_content` | G1 clay and mud content | 4,334 / 3,012 / 3,165 | % |
| `mosaic_mean_`, `_median_`, `_mode_grain_size` | G1 grain size | 2,139 / 708 / 76 | µm |
| organic and inorganic N, OC:ON, total and organic P, total S, biogenic silica | `other` | 1,235 / 158 / 2,289 / 930 / 21 / 3,181 / 1,434 | % (OC:ON blank) |

The counts are a little below the raw non-null counts because of the skips and attached errors below.

**Units** are verbatim from the dump. Two exceptions: OC:TN and OC:ON have a blank unit (the source does not say mass or molar), so they carry
`(blank in source)`. `clay_%_2microm` has unit `µm` in the dump but is described as a percentage content, so it is declared `%` with the raw unit in
`params`. The micro sign is U+00B5 and the middle dot U+00B7 (Chang uses U+03BC).

### Error columns: assumed to be SD

**The source never says whether an error is a standard deviation or a standard error.** Its descriptions say only "Error of ...". By your decision
the default is SD. Each attached error is stored in `sd` (`se` stays NULL), the fact carries `error_type_missing`, and its method row's `notes` says
the type is assumed. Treat these `sd` values as unverified and filter on the flag. 1,140 errors became `sd` (1,107 composition, 33 sediment), and 220
are exactly 0. **114 error cells are skipped** (`error_without_fact`), because their mean grain size is blank.

### Methods, calculated values and replicates

- `sample_metadata.method` is `technique` (`elemental analyzer`, `sieving`, `laser`, `titration`). The value `_unknown` (75% of rows) gives a NULL
  technique and `method_id_null` (329 of 884 method rows). There is no `method_missing` flag, since it would sit on most facts.
- `sample_analysis_calculated` = 1 on 30,061 metadata rows. On loaded facts it gives `calculated_by_source` (22,290 facts): the authors
  calculated the value from other parameters.
- `replicate` above 1 gives the flag `replicate` (1,143 facts).
- A cell backed by several articles gives a combined `reference_key` (355 facts). Where those rows disagree on method (7) or calculated (3), the
  method is `ambiguous_duplicate_method_id` with every row in `params`.

### Size and density fractions

142,544 facts are on bulk sediment. 4,577 are on a size or density fraction (`<2 mm` 2,860, `<0.063 mm` 1,056, density classes). They carry
`non_bulk_material`, and the material is in `params`. **Filter on it before comparing with bulk sediment.** Material `surface_area` (108 organic
carbon values between 0.009 and 0.073 in a `%` column) is skipped (`material_unit_unclear`).

## Locations, depth and habitat

- **Locations:** 17,311 cores have a fact and are loaded, one location each. `water_body` is `seas`, `bioregion` is the Longhurst province, and
  `country` is NULL throughout. `position_method` records whether coordinates came from georeferencing a map (1,264 cores).
- **Depth:** the dump says whether each depth came from the manuscript (17,608 cores) or was extracted from GEBCO 2020 (3,243 cores; 2,833 have
  facts). The GEBCO depth is a derived layer, not a measurement, so it is **not loaded**. Only manuscript depths are (0 to 10,677 m, positive down).
- **Habitat:** the dataset is marine sediment cores, so every core with a loaded depth at or above the threshold gets "Subtidal soft sediment"
  from the dataset's scope (flag `habitat_from_scope`). Very shallow cores may be intertidal, so the threshold `--min-subtidal-depth-m` decides
  which cores get the label. **This build uses 1 m**, the plan's proposal (changing it means re-running and rebuilding).

| Cores with depth up to | 0 m | 1 m | 5 m | 10 m | 200 m |
|---|---|---|---|---|---|
| all cores (manuscript and GEBCO depths; the plan's numbers) | 83 | 113 | 335 | 774 | 13,144 |
| manuscript depths only (the ones loaded) | 58 | 64 | 165 | 444 | 10,426 |

At 1 m, 58 cores (all at exactly 0 m) get no habitat. Cores with only a GEBCO depth also get none. In all, 133,775 of 147,121 facts carry
`habitat_from_scope` and 13,346 have a NULL habitat.
- **Setting** comes from `geomorphological_site` (91% blank). Only `shelf`, `estuary` and `fjord` map to a setting. All 17 labels have a row in the
  label map.

## Samples, dates and references

- **Samples (34,913):** depth min and max are the sample's upper and bottom depths. The source's average depth is the representative depth.
  13,467 samples have only an average depth, loaded with min and max NULL.
- **Dates:** year and month are core attributes. Where they are NULL and a full date exists, the date is used (16 cores). 20 cores have a date that
  disagrees with the year or month columns, and the columns win (listed in the report). 29,082 facts have no year (`year_missing`).
- **References:** `article:<article_id>`, the dump's own id. 293 of 368 articles are cited by a loaded fact (331 citations, 316 with a DOI). The
  dump has no author-order column, so authors are ordered by `author_id` and may not match the paper.

## Not loaded

| What | Cells | Reason |
|---|---|---|
| isotopes, 14C, biomarker tables, rock-eval | 55,104 | phase 2 of the plan, which needs its own attribute design |
| `surface_area_m2_g` and its error | 1,461 | not in the plan's phase-1 list |
| `cumulative_dry_mass_g_cm2` | 93 | no analysis row, so no metadata link |
| GEBCO-derived water depths | 3,243 cores | derived layer |

**172 value cells have no metadata row** (composition 124, sediment 48). The replicate numbering of the value table and the metadata disagree
(169 have metadata only under another replicate, 3 have none), so no article or method can be attached without guessing. They are skipped
(`no_metadata_row`) and nothing is borrowed.

## Flags

`zero_value` 4,171, `negative_value` 18, `outside_source_qc_range` 38 (outside the authors' own min and max thresholds), `calculated_by_source`
22,290, `replicate` 1,143, `non_bulk_material` 4,577, `habitat_from_scope` 133,775, `year_missing` 29,082, `error_type_missing` 1,140.
Filter on `calculated_by_source`, `non_bulk_material` and `outside_source_qc_range` before analysis.

## Row accounting

| | Cells |
|---|---|
| non-null cells in the 13 value tables | 205,313 |
| facts | 147,121 |
| error cells attached as `sd` | 1,140 |
| skipped (`no_metadata_row` 172, `material_unit_unclear` 108, `error_without_fact` 114) | 394 |
| not loaded: 11 other families | 55,104 |
| not loaded: surface area and error / cumulative mass | 1,461 / 93 |
| **sum** | 205,313 |

This closes per column and per table family, asserted in `extract.py`.

## Verification

An independent parse of the dump (not `extract.py`'s) matches all 147,121 values, keys and fact keys, and all 1,140 `sd` values. Two runs give
identical tables. A one-article subset (articles 1 and 62) predicted 147 facts and 67 attached errors by SQL, and the extract produced 147 and 67.
The full-run prediction also matched.

## Overlap

None by design with SCOC or CCN (marine margins, not wetlands). Possible overlap with dbSEABED and SCOC shelf sites in a future join.

Back to the [compilations index](../README.md).
