# davis-calcification (reef net calcification and net production)

Davis, K., Colefax, A., Tucker, J., Kelaher, B., Santos, I. (2021). *Spatiotemporal drivers of hydrochemical ecosystem calcification and
organic productivity: the meta-data.* SEANOE, DOI 10.17882/80022.

- **Version:** `SEANOE 80022 (2021-11-26), last version` (quality-controlled; the first version had errors). **Access date:** 2026-11-15.
  **License:** CC BY 4.0.
- **Raw file:** `89830.xlsx`, sheet `Metadata1` (row 1 is a colour legend, row 2 the headers, then 116 data rows). 147 cells are formulas, and
  the cached values are read.

Reef-scale net calcification (`Gnet`) and net production (`Pnet`) from 53 studies (1971 to 2019), with reef type, season, method, cover and
sometimes chemistry. 116 rows at 85 locations.

## Facts (347)

One fact per numeric value cell of four columns, no skips. Nothing is converted.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `davis_gnet` | B7 `net_ecosystem_calcification` | 116 | mmol m-2 d-1 |
| `davis_pnet` | B5 `net_ecosystem_production` | 105 | mmol m-2 d-1 |
| `davis_coral_cover` | B4 `live_hard_coral_cover` | 95 | % |
| `davis_coralline_cover` | B4 `turf_cca_invertebrate_cover` | 31 | % |

The sheet does not say whether `Pnet` is on an oxygen or carbon basis, so the unit is kept as written. Negative `Gnet` and `Pnet` are real
(net dissolution, net heterotrophy) and not flagged.

## How it maps

- **Errors:** the error column and its type give `sd` or `se`. `SE` and `se` give `se`, `SD` gives `sd`, and `EP-SE` gives `se` plus
  `error_propagated`. An unlisted type raises. **`S` (could be SE or SD) and one error with no type are not stated**, so by your rule they
  are stored in `sd`, flagged `error_type_missing` and noted in `method.notes`. 5 `Pnet` errors are negative (Kinsey 1977) and flagged
  `negative_sd`. `% Coral Error` has no type column and sits in `params`.
- **Provenance from font colour:** the legend in row 1 is a colour code. Purple text means "calculated from information provided"
  (`calculated_by_source`, 102 facts), and blue means "sourced from other literature compilations" (`secondary_source`, 1 fact). Cell fills
  mean "used in a figure" and are ignored. 5 cells carry colours the legend does not explain and get no flag.
- **Locations (85):** `Reef Type` is both the habitat label and the setting. All types map to Coral reef **except `Soft Sediments`
  (1 row), which stays blank**, since it is not a coral reef and no other label is certain. Depth goes to `water_depth_m`. Reef state and
  health stay raw text.
- **Samples:** one per row. `sampling_year` is `Year Studied` (6 blank). `Temp avg` goes to `water_temperature_c`; the sheet gives no unit, so
  degC is assumed.
- **Methods (235):** one per distinct technique and parameter set. `technique` is the first `Method` column; the second, wave action,
  duration, season and notes are in `params`.
- **References (53):** the `Short Reference` strings (author, year, journal) give `ref:<string>`. The sheet has no reference list, so each
  citation holds only the string and `Year Pub`. Titles and DOIs come later from the DOI-resolution job. `Conand 1997` has 1998 on two rows
  (the string's year is used), and `Hata 2002` has `Year Pub` 2000 (kept).

## Not loaded

`% calcifiers` (derived by the authors), `Decade Studied`, aragonite saturation (phase 2 `other`), `Temp Stdev`, `NO3` and `PO4` (no units),
`PAR` (the authors say not used, too many reporting units), `TA:DIC SLOPE` beyond `params`, and `Net dissolution at Night?`.

## Flags

`calculated_by_source` 102, `year_missing` 15, `error_propagated` 24, `zero_value` 5, `negative_sd` 5, `error_type_missing` 2,
`secondary_source` 1.

## Row accounting

116 rows; 347 numeric cells = 347 facts, nothing skipped. All facts were checked back to their raw cells, three one-study checks matched
(Kinsey 1977: 18 facts, Gattuso 1996: 5, Albright 2013: 8), and two runs give identical tables.

## Overlap

Other coral work overlaps only through shared papers. `pessarrodona-seaweed` (reef algae) may share a few reef studies.

Back to the [compilations index](../README.md).
