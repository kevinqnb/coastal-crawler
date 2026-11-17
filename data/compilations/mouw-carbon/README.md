# mouw-carbon (global ocean particle fluxes from sediment traps and 234Th)

Mouw, C.B., Barnett, A., McKinley, G.A., Gloege, L., Pilcher, D. (2016). *Global ocean particulate organic carbon flux merged with
satellite parameters* [dataset publication series]. PANGAEA, https://doi.org/10.1594/PANGAEA.855600; the flux dataset used here is
https://doi.org/10.1594/PANGAEA.855594 (supplement to *Earth System Science Data* 8(2), 531-541,
https://doi.org/10.5194/essd-8-531-2016).

- **Version:** `PANGAEA 855594 (DOI registered 2016-01-11; no version number)`: PANGAEA states none, so the registration date is recorded.
- **Access date:** 2026-11-15. **License:** CC-BY-3.0 (stated in the file header).
- **Raw files:** `datasets/GO_flux.tab` (PANGAEA tab file: a comment header ending in `*/`, then 15,792 data rows x 41 columns) and
  `summary.txt` (the collection description). The other five `datasets/*.tab` (`GO_chl_2010`, `GO_chl_2014`, `GO_microplankton`,
  `GO_mixed_layer`, `GO_sst`; 61 k-290 k rows) are satellite and climatology products the authors matched to the trap locations: they
  are **not loaded and not opened** (cross-cutting rule 3.5: context only from values measured beside the fact).

A compilation of sediment-trap and 234Th-derived particle fluxes from 43 studies (reference IDs), 1976-2012, 15,205 trap rows and 587
234Th rows, from the shelf to the abyss. One row is one deployment (or one 234Th station) with the fluxes of several analytes.

**Facts:** 57,045, one per numeric cell of seven flux columns, all `mg/m**2/day` (the header unit; nothing is converted). `sd` is the
paired std-dev column where the file has one (834 facts); every std-dev has a flux value beside it (asserted).

| Attribute | Type / subfield | Facts |
|---|---|---|
| `mouw_poc_flux` (POC flux) | C6 `poc_flux` | 14,055 |
| `mouw_pon_flux` (PON flux) | C6 `pon_flux` | 12,067 |
| `mouw_total_mass_flux` (Flux tot) | C6 `total_mass_flux` | 13,418 |
| `mouw_caco3_flux` | C6 `biogenic_silica_carbonate_flux` | 5,627 |
| `mouw_psi_flux` (particulate Si) | C6 `biogenic_silica_carbonate_flux` | 8,046 |
| `mouw_psio2_flux` | C6 `biogenic_silica_carbonate_flux` | 3,515 |
| `mouw_psioh4_flux` (PSi(OH)4) | C6 `biogenic_silica_carbonate_flux` | 317 |

The four CaCO3 / Si analytes share the standard subfield, one attribute per analyte (17,505 facts), as the plan asks. The totals reproduce
the plan's 14,055 / 12,067 / 13,418 / 17,505 exactly. Particulate silicon (`PSi`) and silica (`PSiO2`) are different columns and are not
converted into each other.

## Mapping

- **Habitat** `Water column` for every location (raw `habitat_type` constant `water column`), derived from the dataset's scope (open-ocean
  trap and 234Th fluxes) and never stated by the source: flag `habitat_from_scope` on all 57,045 facts. Setting and bottom type NULL.
- **Samples:** one per source row with a loaded fact (15,397 of 15,792 rows; the other 395 hold only analytes that are not loaded).
  `sample_depth_m` = `Depth water` (the trap deployment depth, 10-5,847 m). `sampling_year/month` from `Date/Time (Deployed)`
  (8 rows have none: flag `year_missing`, 16 facts). The deployment duration varies per row and method rows are not per row, so it is
  written as text in `sample.depth_notes`: `duration_days=<as written>; retrieved=<as written>`. 372 rows have a duration of 0 or less
  (kept as written).
- **Locations (716):** `location_key` = latitude | longitude | elevation, as written in the file. The file's own `unique location
  identifier` is not used: it is shared by up to four reference IDs and the coordinates and depths under one identifier differ. `water_depth_m`
  = minus `Elevation` (the file gives total water depth as a negative elevation; sign flipped, the raw number recoverable through
  `source_row_ref`); 469 locations have none (6,663 rows have an elevation; 38 coordinate pairs occur with more than one elevation value (29 of them blank on some rows) and stay
  separate locations). `Bathy depth` (ETOPO1 gridded bathymetry, 15,723 cells) is derived from a model layer and is **not loaded**.
  Longitudes are within +-180 (180.0 occurs); latitudes -77.8 to 88.0.
- **Methods (234):** one per (attribute, `Type`, `Sediment trap type`); `technique` = `<Type>; <trap type>` in the file's spelling
  (`sediment_trap; MARK 7`, `th234; unknown`, ...), both strings also in `params`. `unknown` and `Not specified` trap types are kept as written.
- **Not loaded** (counted per column in `extract_report.json`): total carbon flux (419 cells; the plan lists no decision for it), PIC (5,870),
  POP (683), particulate Al (3,741), chlorophyll (109), phaeopigments (114, ug), Fe (487), Mn (423, ug), Ba (377, ug), detrital (209) and Ti
  (480, ug) with their std-dev columns: phase-2 `other` attributes. Trap `Area` (6,057 cells) has no field.

## References

`reference_key` = `mouw_id:<ID>` for the 43 IDs, one `reference_dim` row each. The file's `Reference` text is the same on every row of an ID except IDs 2 and 14.

- **ID 14 = BATS by inference** (820 rows, flag `reference_inferred` on 2,364 facts): the `Reference` text is blank, the rows are one location
  (31.613 N, 64.314 W) and span 1988-12-16 to 2011-12-07, which matches the single BATS entry of Table 2 of the ESSD paper (`http://bats.bios.edu`,
  a data archive, not a paper). The source does not say so; the extract asserts the location and date premise. The citation is that web page.
- **ID 2 unresolved** (3,613 rows, 77 locations, global, 1983-2011, trap type `unknown`; flag `reference_unresolved` on 12,576 facts): no `Reference`
  text and no entry of Table 2 matches. `reference_dim` row only, no citation.
- **ID 7** cites two papers in one string (`Collier and Dymond, 1994, BCO-DMO, Honjo and Dymond, 1994, BCO-DMO,`): split into two citations by an
  explicit rule (asserted). Strings are carried as written, including the file's typos (`Collier and Cymond`, `Mohiuddin et all.`, `Honjo and Maganini`).
- **DOIs.** 23 of the 43 citations carry a DOI and URL taken from the header's `Related to:` list, matched only where first-author surname and year
  (and the second author, if named) identify exactly one header citation; strings naming a data archive (`BCO-DMO`, `NOAA`, `JAMSTEC`, `US JGOFS`,
  `CARIACO`) or a personal communication are never matched. 16 strings are unmatched and listed in the report (for example `Wassmann et al., 2003`
  against a 2004 chapter in the header: the year disagrees, so it is not matched).
- **Personal communications** (`Conte, 2015, pers. comm.`, `Timothy et al. 2013, pers. comm.`): 10,506 facts carry `pers_comm`.

## Flags

`habitat_from_scope` 57,045, `reference_unresolved` 12,576, `pers_comm` 10,506, `reference_inferred` 2,364, `zero_value` 891, `year_missing` 16,
`negative_value` 3 (particulate Si fluxes below zero; kept). No fact is skipped: every flux cell is a number (anything else raises).

## Row accounting

15,792 source rows; the seven loaded columns hold 57,045 numeric cells = 57,045 facts, 0 skips; no J1 values, so nothing is collapsed. After the central build
every fact was checked against the raw file by an independent line parse (value, unit, `sd`, reference, latitude, longitude, water depth with the sign flip,
trap depth, year, month, and the zero / negative / year-missing / reference flags recomputed), and every non-blank cell of the seven columns is a fact:
57,045 of 57,045 agree. A one-study check (reference 18 in full plus the first three rows of IDs 2 and 14, predicted by the independent parse: 24 rows, 128
facts) matched. Adding this source left the other ten sources' content unchanged. Two runs of `extract.py` give byte-identical parquet and report.
`source_row_ref` is `datasets/GO_flux.tab#data_row=<0-based row below the "*/" line>:<attribute>`.

## Overlap with other compilations

None expected (open ocean). `rosentreter-ghg` and `fulweiler-nfix` are coastal; nothing was checked against them.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
