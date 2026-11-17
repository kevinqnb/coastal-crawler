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
  24 samples have rows that disagree on temperature or salinity: those slots are NULL (reported).
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

Per-code counts are in `extract_report.json`. Anything else raises. `n` and `DL` are the least certain codes (`DL` may mean 'at the detection limit'): both are skips, never values.

## Notes column

Copied verbatim into `method.notes`; there is one method row per distinct (attribute, METHODS string, incubation time, notes): 831 method rows, 708 with notes. Flags from notes only: `converted_by_source` on the rate facts of rows whose note
matches an explicit pattern (rows: `daily NFR divided by 24 h (to hourly)` 1068, `hourly rates multiplied (to 12/24 h or daylength)` 163; 2,821 facts) and `qc_rejected_by_source` (new)
on the rate facts of the 3 rows saying `REJECTED by Quality Control` (8 facts; the values are loaded, the note says which fraction). 55 Notes cells are bare numbers (misplaced data: {'Cell_Specific_N2_Fixation_Rate': 25, 'N2_Fixation_Integral (24 h)': 30}): kept only as note text.

## References

The `SOURCE` column mixes references and comment lines. A cell is a **reference** if it contains a DOI or an author-year pattern (capital initial, four-digit year); the rest are comments, and a row belongs to the last reference above it. 180 references, 154 with a DOI (`doi:<lower-case>` key; the others `ref:<string>`);
132 citation titles come from the `Reference List` by DOI.
**Review needed:** 100 strings are not classified as references; nothing was guessed. Several are personal-communication references without a year (an e-mail address) or `Foster et al. Unpublish`; for those the rows
that follow are attributed to the reference ABOVE them, so check the counts below before using the affected rows (the list is also in `extract_report.json`):

- `*other data shown in Figure 3 is included in Church et al.2019` (NF; x2; 274 rows follow before the next reference)
- `*value of 0 meters is value of 5 meters` (NF; x2; 2 rows follow before the next reference)
- `Church, M.J., University of Hawaii, mjchurch@hawaii.edu` (NF; x2; 174 rows follow before the next reference)
- `Correspondence: Berman-Frank` (NF_Integral; x1; 20 rows follow before the next reference)
- `ETSP cruise` (NF; x1; 13 rows follow before the next reference)
- `ETSP cruise/ NERC cruise` (NF; x1; 21 rows follow before the next reference)
- `Fig.2` (NF; x1; 24 rows follow before the next reference)
- `Foster et al. Unpublish` (NF; x4; 4 rows follow before the next reference)
- `Matthew J. Church, Correspondence: mjchurch@hawaii.edu` (NF; x2; 2 rows follow before the next reference)
- `Mulholland, M.R., Old Dominion University, mmulholl@odu.edu` (NF; x4; 428 rows follow before the next reference)
- `NERC cruise` (NF; x1; 9 rows follow before the next reference)
- `NFR data come from Fig. 4` (NF; x1; 13 rows follow before the next reference)
- `Norbert Wasmund, Leibniz Institute for Baltic Sea Research, Seestr. Norbert.wasmund@io-warnemuende.de` (NF_Integral; x1; 1 rows follow before the next reference)
- `Rees, A.P, Plymouth Marine Laboratory, apre@pml.ac.uk` (NF; x1; 91 rows follow before the next reference)
- `Three papers describe the same project in ETSP` (NF_Integral; x1; 1 rows follow before the next reference)
- `White, A.E., Oregon State University, awhite@coas.oregonstate.edu` (NF; x4; 167 rows follow before the next reference)
- `all cruise NFR reported daily rate` (NF; x1; 25 rows follow before the next reference)
- `and in supplementary table` (NF_Integral; x1; 78 rows follow before the next reference)
- `areal rates shown in figure 1` (NF_Integral; x1; 1 rows follow before the next reference)
- `average data shown in table 1 and 2` (NF; x1; 59 rows follow before the next reference)
- `calculate depth-integrated NFR` (NF_Integral; x3; 10 rows follow before the next reference)
- `combined data shown in figure 2, 3 and supplement TableS1` (NF; x1; 110 rows follow before the next reference)
- `data come from 7 Cruise, sampling date come from:` (NF_Integral; x1; 1 rows follow before the next reference)
- `data come from FIGURE 4.` (NF; x1; 67 rows follow before the next reference)
- `data come from Fig.1、Fig.2 and Fig.3` (NF; x1; 2 rows follow before the next reference)
- `data come from Fig.3a` (NF; x1; 1 rows follow before the next reference)
- `data come from Figure 2.` (NF; x3; 63 rows follow before the next reference)
- `data come from Figure 3.` (NF; x2; 31 rows follow before the next reference)
- `data come from Figure 7.` (NF; x2; 22 rows follow before the next reference)
- `data come from Supplement` (NF; x3; 327 rows follow before the next reference)
- `data come from Supplement Table S1.` (NF; x1; 45 rows follow before the next reference)
- `data come from Supplement Table S2.` (NF_Integral; x1; 9 rows follow before the next reference)
- `data come from Table 1 and Figure 4.` (NF; x2; 20 rows follow before the next reference)
- `data come from Table 1.` (NF; x3; 65 rows follow before the next reference)
- `data come from Table 2.` (NF; x2; 24 rows follow before the next reference)
- `data come from Table 3.` (NF_Integral; x1; 17 rows follow before the next reference)
- `data come from author` (NF; x1; 12 rows follow before the next reference)
- `data come from author.` (NF; x2; 109 rows follow before the next reference)
- `data come from http://scope.soest.hawaii.edu/data/hot/` (NF; x2; 75 rows follow before the next reference)
- `data come from supplement` (NF; x1; 220 rows follow before the next reference)
- `data come from supplement Data S1` (NF; x2; 158 rows follow before the next reference)
- `data come from supplement Table Data Set S1` (NF; x2; 87 rows follow before the next reference)
- `data come from supplement Table S3` (NF; x2; 103 rows follow before the next reference)
- `data come from supplement Table.S1-S7` (NF; x1; 178 rows follow before the next reference)
- `data come from table 2` (NF; x1; 6 rows follow before the next reference)
- `data comes from Fig.6` (NF_Integral; x1; 13 rows follow before the next reference)
- `data comes from Figure 2.` (NF; x1; 36 rows follow before the next reference)
- `data comes from Figure 5.` (NF; x1; 31 rows follow before the next reference)
- `data comes from authors (ljx2ljx@scsio.ac.cn)` (NF_Integral; x1; 3 rows follow before the next reference)
- `data comes from fig.3 &table.1.` (NF; x1; 30 rows follow before the next reference)
- `data comes from fig.4 &table.1/2.` (NF; x1; 3 rows follow before the next reference)
- `data comes from http://www.obs-vlfr.fr/proof/ftpfree/outpace/db/data/BOTTLE/` (NF; x1; 51 rows follow before the next reference)
- `data comes from table 1` (NF_Integral; x1; 3 rows follow before the next reference)
- `data comes from table S1` (NF_Integral; x1; 6 rows follow before the next reference)
- `data comes from table.1` (NF; x5; 72 rows follow before the next reference)
- `data comes from table.1&2` (NF_Integral; x1; 6 rows follow before the next reference)
- `data comes from table.2` (NF_Integral; x1; 14 rows follow before the next reference)
- `data comes from table.S2` (NF; x1; 46 rows follow before the next reference)
- `data from Fig. 4` (NF_Integral; x1; 4 rows follow before the next reference)
- `data from Fig.4(a)` (NF_Integral; x1; 10 rows follow before the next reference)
- `data from Fig.5` (NF_Integral; x1; 1 rows follow before the next reference)
- `data from Figure1` (NF; x1; 3 rows follow before the next reference)
- `data from author` (NF; x6; 137 rows follow before the next reference)
- `data from supplement table S1` (NF; x1; 70 rows follow before the next reference)
- `data from table.1` (NF_Integral; x1; 16 rows follow before the next reference)
- `data from table.2` (NF_Integral; x1; 24 rows follow before the next reference)
- `data in table 1 and figure 1,2` (NF; x2; 4 rows follow before the next reference)
- `data shown in figure 2 and table 1` (NF; x1; 40 rows follow before the next reference)
- `data shown in figure 3` (NF; x3; 113 rows follow before the next reference)
- `data shown in figure 3,5,6 and table 2` (NF; x1; 2 rows follow before the next reference)
- `data shown in figure 4` (NF; x3; 37 rows follow before the next reference)
- `data shown in figure 5,6` (NF; x2; 31 rows follow before the next reference)
- `data shown in figure 6` (NF_Integral; x1; 19 rows follow before the next reference)
- `data shown in figure 7 and supplementary` (NF_Integral; x1; 4 rows follow before the next reference)
- `data shown in figure 8, 9` (NF; x2; 90 rows follow before the next reference)
- `data shown in firgure 3` (NF; x1; 1 rows follow before the next reference)
- `data shown in supplementary material` (NF_Integral; x1; 10 rows follow before the next reference)
- `data shown in table 1` (NF_Integral; x1; 1 rows follow before the next reference)
- `data shown in table 1 and supplementary` (NF_Integral; x1; 33 rows follow before the next reference)
- `data shown in table 1 and supplementary table 1` (NF_Integral; x1; 1 rows follow before the next reference)
- `data shown in table 1 without lat, lon` (NF_Integral; x1; 4 rows follow before the next reference)
- `data shown in table 1. but coordinates are not available` (NF_Integral; x1; 1 rows follow before the next reference)
- `date from introduction` (NF; x1; 94 rows follow before the next reference)
- `depth from supplementary table S3` (NF_Integral; x1; 3 rows follow before the next reference)
- `depth from volumetric` (NF_Integral; x1; 19 rows follow before the next reference)
- `depth-integrated rates from Fig.3` (NF_Integral; x1; 69 rows follow before the next reference)
- `during El Nino (2010) and La Nina (2011)` (NF_Integral; x1; 5 rows follow before the next reference)
- `hourly rates*24 hrs` (Cell_Specific_NF_Rate; x1; 18 rows follow before the next reference)
- `integral data shown in figure 1` (NF_Integral; x1; 1 rows follow before the next reference)
- `integrated rates calculated from volumetric rates` (NF_Integral; x1; 1 rows follow before the next reference)
- `other cruise date are estimated` (NF_Integral; x1; 2 rows follow before the next reference)
- `read them mannually from figure 1` (NF_Integral; x1; 4 rows follow before the next reference)
- `show integrated rates in table 3` (NF; x1; 1 rows follow before the next reference)
- `shown supplementary table 1` (NF; x1; 225 rows follow before the next reference)
- `supplementary tables` (NF; x2; 92 rows follow before the next reference)
- `surface rates shown in table 3` (NF; x1; 19 rows follow before the next reference)
- `table 4` (NF_Integral; x1; 57 rows follow before the next reference)
- `vertical profiles shown in figure 1 and` (NF; x1; 1 rows follow before the next reference)
- `volumetric rates was shown in figure 6d` (NF; x1; 47 rows follow before the next reference)
- `计算得depth-integrated` (NF_Integral; x1; 11 rows follow before the next reference)

## Flags

`habitat_from_scope` 32,224, `converted_by_source` 2,821, `zero_value` 2,579, `source_mean_reported` 1,052 (new), `covariate_conflict` 133, `inequality` 114,
`qc_rejected_by_source` 8 (new).

## Row accounting

Verified by an independent parse of the workbook after the build: the non-blank cells of the loaded columns number 33,141 (the extract's own count agrees) and equal facts + skips + collapsed; every fact's value, comparator, `sd`, unit (header), mean/inequality flags and the method note were re-derived from its raw cell (32,224 of 32,224 agree).
A tiny end-to-end check (the first 12 rows of the 24 h sheet, predicted by an independent cell count: 76 facts, 12 samples) matched. Two runs give byte-identical parquet, skip report and report. `source_row_ref` is `<file>[<sheet>]#data_row=<0-based row below the header>:<attribute>` (Cell_Specific: `<attribute>[<taxon>]`).

## Overlap with other compilations

Fulweiler (`wc` rows) and Tang share some stations (a different process). Never merged.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
