# shao-nfix (global ocean diazotroph database v2: N2 fixation rates)

Shao, Z., Xu, Y., Wang, H., Luo, W., Wang, L., Huang, Y., et al. (2023). *Version 2 of the global ocean diazotroph database.* figshare, DOI
10.6084/m9.figshare.21677687.v3 (paper: *Earth System Science Data*, 2023).

- **Version:** `2024-01-09` (figshare v3, `DiazotrophsDatabase-20240109.xlsx`). Only this workbook is read. **Access date:** 2026-11-15.
  **License:** CC BY 4.0.
- **Loaded sheets:** `N2_Fixation (Incub. 24 h)` (6,971 rows), `N2_Fixation (Incub.<24 h)` (1,583), `N2_Fixation_Integral (24 h)` (1,806),
  `N2_Fixation_Integral (<24 h)` (411) and `Cell_Specific_N2_Fixation_Rate` (449). The gene and cell-count sheets are counted but not loaded
  (phase 2). `Reference List` only supplies citation titles.

## Facts (32,224)

33,141 non-blank cells = 32,224 facts + 840 skips + 77 collapsed repeats.

| Group | Attributes | Facts |
|---|---|---|
| Rates by fraction (C5 `n2_fixation`) | `shao_n2fix_total` 10,479, `_whole_seawater` 9,306, `_trichodesmium` 1,027, `_lt10um` 663, `_heterocyst` 342, `_gt10um` 235, `_light` 149, `_dark` 162 | 22,363 |
| Per-cell rates (Cell_Specific sheet) | `shao_n2fix_per_cell` 512, `shao_n2fix_per_cellular_n` 537 | 1,049 |
| Cell nitrogen content (`other`) | `shao_cellular_n_content` | 515 |
| Water chemistry (J1) | `water_nitrate` 3,014, `water_phosphate` 2,912, `water_chl_a` 2,371 | 8,297 |

- **Units** are the sheet headers': `μmol N m-3 d-1` and `m-3 h-1` (volumetric sheets), `μmol N m-2 d-1` and `m-2 h-1` (integral sheets, with the
  U+03BC micro sign), and `fmol N cell-1 d-1` and `fmol N fmol N-1 d-1` (per-cell sheet). The J1 nitrate and phosphate use `µM` (U+00B5), a
  separate declared string from Chang and Tang's `μM`. The `Total` and the fractions sit in the same row, so do not sum them. The light and dark
  attributes also set `method.light_dark`.
- **Cell_Specific sheet:** each organism has three columns: a rate per cell, the cell's nitrogen content, and the rate relative to that
  nitrogen. The organism is `sample.taxon`, with `taxon_rank` `genus` (Trichodesmium, Richelia, Calothrix), `clade` (UCYN-A, A1, A2, B, C),
  `host_of_clade`, `cell_type`, `phylum` or `functional_group`. Nitrogen content is `other`, with a note. Its Notes cells (organism names and
  numbers) are copied verbatim and not parsed.
- **Water chemistry (J1):** one fact per sample and attribute, using the shared collapse code. 8,374 cells give 8,297 facts and 77 collapsed
  repeats. 64 sample-attribute groups hold different values, so 133 facts carry `covariate_conflict`. The sample key is reference,
  coordinates, date, depth and sheet family. On the integral sheets, the near-surface nitrate, phosphate and chlorophyll are used.

## Cell rules

| Cell | Result | Cells |
|---|---|---|
| number (including 2 stored as text) | fact | |
| `mean (sd)`, e.g. `2.38 (0.64)` | value is the mean, `sd` is the bracket, flag `source_mean_reported` | 1,052 |
| `<0.02`, `< 29` | value is the number, `value_comparator` `<`, flag `inequality` | 114 |
| `n.d.`, `bdl`, `bld`, `bd`, `dnq`, `u.d.`, `< DL`, `DL` and similar | skip `below_detection_limit` | 646 |
| `nm`, `n.a.`, `na`, `n`, `/`, `-`, `nan` and similar | skip `not_measured` | 185 |
| malformed, e.g. `0.49 (0.02` | skip `unparseable_value`, listed in the report | 9 |

Anything else raises. `n` (16 cells) and `DL` (16 cells) are accepted as skips.

## Notes column

The Notes column holds the database authors' comments. They are copied verbatim into `method.notes` (831 method rows, 708 with notes).
Only two things become flags:
- `converted_by_source` (2,821 facts) where the note says hourly rates were multiplied to daily, or daily divided to hourly.
- `qc_rejected_by_source` (8 facts, 3 rows) where it says `REJECTED by Quality Control`. These values are loaded and flagged.

55 Notes cells are bare numbers (misplaced data). They stay as note text and are never read as values.

## How it maps

- **Habitat** is Water column for every location (`habitat_from_scope` on all facts).
- **Depth:** volumetric depth is `sample_depth_m`. On the integral sheets it is an integration depth and goes to `depth_notes`. Date ranges give
  the year when both ends agree, with the month NULL.
- **Temperature and salinity** go to sample slots on the volumetric and per-cell sheets. 24 samples have rows that disagree. They are named in
  an explicit list in the code and in the report, the disagreeing slot is NULL for those only, and any new disagreement raises. The integral
  sheets' near-surface temperature and salinity are not loaded (surface values for an integral sample).
- **References:** **every non-blank `SOURCE` cell is used as the reference exactly as written.** It starts a new block and is the reference
  for the rows after it. There is no classification into references and comments, and no use of "the reference above". E-mail personal
  communications, `Fig.2` and `Foster et al. Unpublish` are all references here. A row before the first `SOURCE` cell raises.
  280 references from 442 cells. The key is `doi:<doi>` where the string contains a DOI (154), otherwise `ref:<string>`. Titles come from the
  `Reference List` where the DOI matches (132). 100 strings have neither a DOI nor an author-year pattern (listed in the report).

## Not loaded

The gene and cell-count sheets, `Fe`, `Net Primary Production`, the C fixation column and the unnamed 65th column of the per-cell sheet.

## Flags

`habitat_from_scope` 32,224, `converted_by_source` 2,821, `zero_value` 2,579, `source_mean_reported` 1,052, `covariate_conflict` 133,
`inequality` 114, `qc_rejected_by_source` 8.

## Row accounting

33,141 cells = 32,224 facts + 840 skips + 77 collapsed, confirmed by an independent parse of the workbook. Every fact was re-derived from its
raw cell, including its reference. A 194-row test on a block starting with an e-mail string predicted 388 facts and matched. Two runs give
identical output.

## Overlap

`fulweiler-nfix` water-column rows and `tang-nitrification` share some stations (a different process). Never merged.

Back to the [compilations index](../README.md).
