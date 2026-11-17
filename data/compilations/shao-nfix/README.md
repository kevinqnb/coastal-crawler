# shao-nfix (global ocean diazotroph database v2: N2 fixation rates)

Shao, Z., Xu, Y., Wang, H., Luo, W., Wang, L., Huang, Y., et al. (2023). *Version 2 of the global ocean diazotroph database* [Dataset].
figshare, https://doi.org/10.6084/m9.figshare.21677687.v3 (paper: *Earth System Science Data*, 2023).

- **Version:** `2024-01-09 (figshare v3; DiazotrophsDatabase-20240109.xlsx)`. Only this workbook is read (the 20230626 workbook and the update notes are not).
- **Access date:** 2026-11-15. **License:** CC BY 4.0 (figshare record).
- **Sheets loaded (phase 1):** `N2_Fixation (Incub. 24 h)` (6,971 rows), `N2_Fixation (Incub.<24 h)` (1,583), `N2_Fixation_Integral (24 h)` (1,806),
  `N2_Fixation_Integral (<24 h)` (411) and `Cell_Specific_N2_Fixation_Rate` (449). Counted, **not loaded** (phase 2): `Cell_Count` (6,765 rows), `Cell_Count_Integral` (1,411),
  `nifH_Gene` (5,580), `nifH_Gene_Integral` (688), `NCD_Gene` (4,449). `Reference List` (239 entries) only supplies citation titles.

**Facts: 32,224** from 33,141 non-blank cells of the loaded columns = 32,224 facts + 840 skips + 77 collapsed J1 repeats (asserted).

| Attribute | Facts |
|---|---|
| `shao_cellular_n_content` | 515 |
| `shao_n2fix_dark` | 162 |
| `shao_n2fix_gt10um` | 235 |
| `shao_n2fix_heterocyst` | 342 |
| `shao_n2fix_light` | 149 |
| `shao_n2fix_lt10um` | 663 |
| `shao_n2fix_per_cell` | 512 |
| `shao_n2fix_per_cellular_n` | 537 |
| `shao_n2fix_total` | 10,479 |
| `shao_n2fix_trichodesmium` | 1,027 |
| `shao_n2fix_whole_seawater` | 9,306 |
| `water_chl_a` | 2,371 |
| `water_nitrate` | 3,014 |
| `water_phosphate` | 2,912 |

- **Rates** (C5 `n2_fixation`), one attribute per fraction as plan 5.12 (`shao_n2fix_total`, `_whole_seawater`, `_trichodesmium`, `_heterocyst`, `_lt10um`, `_gt10um`, `_light`, `_dark`),
  23,412 facts including the Cell_Specific ones. Units are the sheet headers' (U+03BC): `μmol N m-3 d-1`, `μmol N m-3 h-1` (volumetric), `μmol N m-2 d-1`, `μmol N m-2 h-1`
  (integral). The `Total` and the component fractions coexist in a row (double counting, not summed). The light/dark attributes set `method.light_dark`.
- **Cell_Specific sheet:** 16 organism triples. The organism is `sample.taxon` with `taxon_rank` (free text; values used: `genus` Trichodesmium, Richelia, Calothrix; `clade` UCYN-A, A1, A2, B, C; `host_of_clade` UCYN-A host, A1, A2, A3, B;
  `cell_type` Heterocyst; `phylum` Cyanobacteria; `functional_group` Non-cyanobacteria diazotroph). Attributes: `shao_n2fix_per_cell` (`fmol N cell-1 d-1`), `shao_n2fix_per_cellular_n` (`fmol N fmol N-1 d-1`) and
  `shao_cellular_n_content` (`other`, `fmol N cell-1`, with a note: the divisor of the N-specific rate). Its Notes (organism names, numbers) are copied verbatim to `method.notes`, not parsed.
- **J1:** `water_nitrate` and `water_phosphate` (`µM`, **U+00B5**, a separate declared string from Chang/Tang's U+03BC `μM`) and `water_chl_a` (`mg m-3`), one fact per sample and attribute by the shared collapse helper:
  8,374 cells = 8,297 facts + 77 collapsed repeats; 64 sample-attribute groups with differing values, 133 facts flagged `covariate_conflict`. The sample key is reference | latitude | longitude | date | depth | family (volumetric, integral or cell).
  On the integral sheets the near-surface nitrate, phosphate and chlorophyll columns are used (J1 only). `Fe`, `Net Primary Production`, the C fixation column and the unnamed 65th column of Cell_Specific are not loaded (counted in the report).
- **Temperature and salinity** go to the sample slots on the volumetric and Cell_Specific sheets (units `PSU`, the header's). The integral sheets' near-surface temperature (793 + 290 cells) and salinity (703 + 269) are **not** loaded (surface values of an integral sample; the same reason as Francesco's SST).
  **24 samples** have rows that disagree on temperature or salinity: they are named in the explicit allow-list `SLOT_CONFLICT_SAMPLES` in `extract.py` (and in `extract_report.json`); the disagreeing slot is NULL for those only, and any new disagreement, or an allow-listed one that no longer occurs, raises. The integral sheets' near-surface temperature and salinity stay unloaded.
- **Habitat** `Water column` for all locations (flag `habitat_from_scope` on every fact). Depth: volumetric depth -> `sample_depth_m`; the integral sheets' depth is an integration depth -> `depth_notes`; text depths too. Date ranges (`2008/7/19-8/3`) -> year if both ends agree, month NULL,
  the text in `depth_notes`. 29 coordinates written as text with a tab (`25.128`) are read as numbers.

## Cell rules (the user's)

| Cell | Result | Cells |
|---|---|---|
| number (also a number stored as text, 2) | fact | |
| `mean (sd)`, e.g. `2.38 (0.64)` | value = the mean, `sd` = the bracket, flag `source_mean_reported` (new) | 1,052 |
| `<0.02`, `< 29` | value = the printed number, `value_comparator` `<`, flag `inequality` | 114 (all `<`) |
| `n.d.` `n.d` `nd` `bdl` `bld` `bd` `b.d.` `dnq` `u.d.` `< DL` `DL` | skip `below_detection_limit` | 646 |
| `nm` `n.a.` `n.a` `na` `n/a` `n` `/` `-` `nan` | skip `not_measured` | 185 |
| `0.49 (0.02`, `1.98 (0.45`, `0.01 (0.00)*` (malformed) | skip `unparseable_value` | 9 (listed in the report) |
| blank or whitespace | nothing | |

Per-code counts are in `extract_report.json`. Anything else raises. `n` (16 cells) and `DL` (16 cells) are accepted by the user as skips (`n` -> `not_measured`, `DL` -> `below_detection_limit`), never values.

## Notes column

Copied verbatim into `method.notes`; there is one method row per distinct (attribute, METHODS string, incubation time, notes): 831 method rows, 708 with notes. Flags from notes only: `converted_by_source` on the rate facts of rows whose note
matches an explicit pattern (rows: `daily NFR divided by 24 h (to hourly)` 1068, `hourly rates multiplied (to 12/24 h or daylength)` 163; 2,821 facts) and `qc_rejected_by_source` (new)
on the rate facts of the 3 rows saying `REJECTED by Quality Control` (8 facts; the values are loaded, the note says which fraction). 55 Notes cells are bare numbers (misplaced data: {'Cell_Specific_N2_Fixation_Rate': 25, 'N2_Fixation_Integral (24 h)': 30}): kept only as note text.

## References

**Every non-blank `SOURCE` cell is used as the reference exactly as written** (whitespace-normalised only): it starts a new reference block and is the reference of the rows after it until the next non-blank cell. There is no classification into references and comments, and no attribution to "the reference above" for strings that look like comments (`data come from Table 2.`, `Fig.2`, e-mail personal communications, `Foster et al. Unpublish` are all references here). A data row before the first `SOURCE` cell of its sheet raises.
280 references from 442 SOURCE cells. `reference_key` = `doi:<bare lower-case doi>` where the string contains a DOI (154 references), otherwise `ref:<string>`; the citation's `bibliography_id` is the string, DOI NULL for the others, `title` from the `Reference List` where the DOI matches (132).
`pers_comm` only where the string itself says pers. comm. / personal communication (no fact has it; it is not inferred from e-mail addresses). For information: 100 SOURCE strings contain neither a DOI nor an author-year pattern (listed in `extract_report.json`); they are references all the same. Because the sample key includes the reference, a comment-like cell splits what was one station block into two samples (the facts and J1 collapse counts are unchanged from the earlier classification run: 32,224 facts, 77 collapsed; references 180 -> 280, samples 10,518 -> 10,513).

## Flags

`habitat_from_scope` 32,224, `converted_by_source` 2,821, `zero_value` 2,579, `source_mean_reported` 1,052 (new), `covariate_conflict` 133, `inequality` 114,
`qc_rejected_by_source` 8 (new).

## Row accounting

Verified by an independent parse of the workbook after the build: the non-blank cells of the loaded columns number 33,141 (the extract's own count agrees) and equal facts + skips + collapsed; every fact's value, comparator, `sd`, unit (header), mean/inequality flags and the method note were re-derived from its raw cell (32,224 of 32,224 agree).
A tiny end-to-end check (a block starting with an e-mail string, `Church, M.J., University of Hawaii, ...`, plus 12 rows of it and the block before: 194 rows, predicted by an independent cell count: 388 facts) matched; every fact's reference was also re-derived from the forward-filled SOURCE cells. Two runs give byte-identical parquet, skip report and report. `source_row_ref` is `<file>[<sheet>]#data_row=<0-based row below the header>:<attribute>` (Cell_Specific: `<attribute>[<taxon>]`).

## Overlap with other compilations

Fulweiler (`wc` rows) and Tang share some stations (a different process). Never merged.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
