# davis-calcification (reef net calcification and net production)

Davis, K., Colefax, A., Tucker, J., Kelaher, B., Santos, I. (2021). *Spatiotemporal drivers of hydrochemical ecosystem calcification and
organic productivity: the meta-data* [Dataset]. SEANOE, https://doi.org/10.17882/80022.

- **Version:** `SEANOE 80022 (2021-11-26), last version` (the quality-controlled version; the first had errors), read from the SEANOE
  page by the integration plan. **Access date:** 2026-11-15. **License:** CC BY 4.0 (SEANOE page).
- **Raw file:** `89830.xlsx`, sheet `Metadata1` (the sheet `_xltb_storage_` is junk and not read). Layout asserted by the extract:
  row 1 is a legend, row 2 the headers, row 3 how each column was used in the paper, 116 data rows in sheet rows 4 to 119, nothing
  below and nothing in columns AR to AV. 147 cells are formulas (for example `=6.875*24`); the **cached values** are read.

A meta-data table of reef-scale net calcification (`Gnet`) and net production (`Pnet`) measurements from 53 studies (1971 to 2019), with
reef type, season, method, cover and (sometimes) chemistry. 116 rows at 85 locations in 40 named places, mostly Australia, the USA, Japan and
French Polynesia.

**Facts: 347**, one per numeric value cell of four columns, no skips. Units are the header spellings; nothing is converted.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `davis_gnet` (`Gnet`) | B7 `net_ecosystem_calcification` | 116 | mmol m-2 d-1 |
| `davis_pnet` (`Pnet`) | B5 `net_ecosystem_production` | 105 | mmol m-2 d-1 |
| `davis_coral_cover` (`% coral cover`) | B4 `live_hard_coral_cover` | 95 | % |
| `davis_coralline_cover` (`% Coralline`) | B4 `turf_cca_invertebrate_cover` | 31 | % |

**`Pnet` basis.** The sheet does not say whether `Pnet` is on an oxygen or a carbon basis; the unit string is kept verbatim and the
conversion question is downstream. Both `Gnet` and `Pnet` take negative values (net dissolution, net heterotrophy), which are not flagged.

## Mapping

- **Errors.** `Gnet error` with its `error type` -> `sd` / `se` of the `Gnet` fact; `Pnet error` with its `error type` -> the `Pnet` fact
  (explicit table, an unlisted type raises). `SE` and `se` -> `se`; `SD` and `SD ` -> `sd` (25 facts); `EP-SE` -> `se` plus flag
  `error_propagated` (24); in all 104 facts carry an `se`. **`S` (one `Pnet` cell, Davis et al. 2019) is not interpreted** (it could be SE or
  SD): no `sd`/`se`, flag `error_type_missing`, value in the params. One more error value has no type at all (same flag). **Five `Pnet`
  errors are negative** (the Kinsey 1977 rows, where `Pnet` is negative and the error is signed): kept as given, flag `negative_sd`.
  `% Coral Error` has no type column: its 22 values are in the params of the cover fact's method row, not in `sd`/`se`.
- **Colour provenance (flags).** The legend in row 1 is a font colour code: purple text (`FF7030A0`) is "calculated from information
  provided", blue text (theme colour 4) is "sourced from other literature compilations". The font colour of each loaded value cell gives
  `calculated_by_source` (**102 facts**: Gnet 48, Pnet 42, cover 10, coralline 2) and `secondary_source` (1 fact, a coral cover). The cell
  fills mean "used in Fig. 3 / Fig. 4", not provenance, and are not read. Five loaded cells carry other font colours that the
  legend does not explain (no flag; counted in the report). The extract checks the legend text and its two colours before using them.
- **Locations: 85** (`Location | Country | lat | lon | Reef Type | Depth | Reef state | Health`). `Reef Type` is both `habitat_type`
  and `geomorphic_setting` (whitespace normalised; `reef flat` 72 and `Reef flat` 8 are separate label-map rows, as are `Lagoon` /
  `lagoon`, `Reef Crest` / `Reef crest`, `backreef` / `back reef`); all 19 types map to *Coral reef* **except `Soft Sediments`
  (1 row), which is left blank**: the plan said every value maps to Coral reef, but a soft-sediment site is not a coral reef and no
  other standard label is certain (a question for the reviewer). Labels come from the source (no `habitat_from_scope`). `Depth` (98
  rows, 0.18 to 7 m) -> `location.water_depth_m`. `modification_status` holds the raw text `reef state: ...` and `health: ...` (not
  mapped to a class). Latitude and longitude are numbers except two text latitudes (`-28.5`) and one longitude written `149.9 W`
  (a trailing letter sets the sign: -149.9).
- **Samples:** one per row. `sampling_year` = `Year Studied` (6 blank: `year_missing`, 15 facts). `Temp avg` (84 rows) -> `water_temperature_c`
  (the sheet gives no unit; the slot is degC, so degC is assumed and not verified against the paper).
- **Methods: 235.** For `Gnet` and `Pnet` one row per distinct (attribute, technique, params); `technique` = the first `Method` column (`L`,
  `E`, `S`, `CV`, `OTAA`, `C`, `chamber`, `benthic gradient flux`, raw), the second `Method` column (`FL`, `S`, `CV`, `OTAA`, `GF`) is in
  params with wave action, timescale, duration, season, heat type, night measured, TA:DIC slope, "data found" and the notes. The error value and
  raw error type are in the params. Cover facts have `method_id_null` rows (coral cover: one per distinct `% Coral Error`).
- **References: 53** `Short Reference` strings (author, year, journal text) -> `ref:<string>`. The sheet has no reference list, so each citation
  holds only `bibliography_id` = the string and `year` = `Year Pub`; titles, authors and DOIs are NULL until the DOI-resolution job resolves them
  from the string and the study location. Two source inconsistencies in `Year Pub`: **`Conand, 1997. Proc 8th Intl Coral Reef Symp` has
  1997 on some rows and 1998 on two** (the citation year is the one in the string, 1997), and `Hata et al., 2002. MEPS` has `Year Pub` 2000
  (kept as given).

## Not loaded

Non-blank cells of columns not loaded, from the report: `% calcifiers` 94 (coral + coralline, derived by the authors), `Decade Studied` 116,
`avg Ωarag` 65 and `Stdev Ωarag` 36 (phase 2 `other`), `Temp Stdev` 59, `NO3` 13 and `PO4` 16 (no units in the sheet), `PAR (daytime average)` 42
(the authors: not used, too many reporting units), `TA:DIC SLOPE` 33 (params only for rate facts), `Net dissolution at Night?` 84. The
`Gnet`, `Pnet` and cover cells are all loaded.

## Flags

(Facts.) `calculated_by_source` 102; `year_missing` 15; `error_propagated` 24; `zero_value` 5; `negative_sd` 5; `error_type_missing` 2;
`secondary_source` 1.

## Row accounting

116 rows; 347 numeric value cells = 347 facts; 0 skips. 116 samples, 85 locations, 235 methods, 53 references and citations. After the central
build every fact was checked against the raw cell it points to by code that does not use `extract.py` (value read from the cached cell,
unit, reference, site, coordinates, depth, temperature, year, habitat label and mapped habitat, SD / SE by the error-type table, technique,
the citation year, and every flag recomputed from the cell's font colour): 347 of 347 agree and the set of numeric cells equals the set of facts.
One-study checks, predicted from the sheet alone (rows -> numeric value cells): Kinsey 1977 6 rows -> 18 facts, Gattuso et al. 1996 2 -> 5, Albright
et al. 2013 2 -> 8; all matched. Adding this source left the other thirteen sources' content unchanged (344,744 facts = 344,397 + 347). Two runs of
`extract.py` give identical tables. `source_row_ref` is `89830.xlsx[Metadata1]#data_row=<0-based data row (sheet row - 4)>:<attribute>`.

## Overlap with other compilations

Never merged. Other coral work overlaps only through shared papers; `pessarrodona-seaweed` (reef algae) may share a few reef studies.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
