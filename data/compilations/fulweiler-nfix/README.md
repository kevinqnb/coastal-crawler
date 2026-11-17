# fulweiler-nfix (aquatic nitrogen fixation rates, inland and coastal)

Fulweiler, R.W., Berberich, M.E., Rinehart, S.A., Taylor, J.M., Kelly, M.C., Ray, N.E., Oczkowski, A., Benavides, M., Church,
M.J., Loeks, B., Newell, S., Olofsson, M., Oppong, J., Roley, S.S., Vizza, C., Wilson, S.T., Scott, J.T., Marcarelli, A.M. (2025).
*Global dataset of nitrogen fixation rates across inland and coastal waters based on a coordinated synthesis effort*, ver 2
[Dataset]. Environmental Data Initiative, https://doi.org/10.6073/pasta/333f651ca721da657d5fd0c393d26cf8 (package `edi.1759.2`).

- **Version:** `EDI edi.1759.2 (ver 2)`. **Access date:** 2026-11-15. **License:** CC BY 4.0, read from the package's EML
  (`edi.1759.2.xml`, `intellectualRights`).
- **Raw files used:** `aquatic_N2fix_rates.csv` (4,793 rate rows), `aquatic_N2fix_ancillary.csv` (3,238 rows keyed by
  `nfix_rate_id`, a subset of the rates), `aquatic_N2fix_references.csv` (267 references, every one cited);
  `data_dictionary.csv` is the authority for meanings and units (read, not parsed). Not loaded: `..._with_abstracts.csv`,
  `edi.1759.2.xml`, `edi.1759.2.report.xml`, `edi.1759.2.txt`, `manifest.txt`, `summarize.R`, `variable_summary.csv`.

Nitrogen fixation rates (acetylene reduction, 15N2 labelling, N2/Ar) from 267 papers and datasets published to April 2022,
for streams and rivers, lakes, freshwater wetlands, mangroves, salt marshes, seagrass, estuaries, tidal flats and continental
shelves; water-column, benthic and "other" substrates (plants, stems, epiphytes). The inland rows (lakes, rivers, freshwater
wetlands) are loaded so the database mirrors the compilation; they have no standard habitat.

**Facts: 9,940**, 4,793 rates + 5,147 J1 water concentrations; 4 skips.

| Attribute | Type / subfield | Facts |
|---|---|---|
| `fulweiler_n2fix_water` (`substrate` = wc) | C5 `n2_fixation` | 1,776 |
| `fulweiler_n2fix_benthic` (`benthos` 2,750 + `other` 267) | C7 `n2_fixation` | 3,017 |
| `water_ammonium` / `water_nitrate` / `water_nitrite` / `water_nitrate_nitrite` / `water_din` / `water_phosphate` | J1 | 787 / 664 / 226 / 417 / 244 / 874 |
| `water_o2` / `water_chl_a` / `water_ph` | J1 | 539 / 969 / 427 |

## D1: the fact carries the source's harmonised value

The fact is `nfix_rate_converted`, the source's rate converted to molar units, in `nfix_unit_converted` (four spellings, the same
for both attributes): `umol-n m-2 h-1` (1,755 benthic + 437 water), `umol-n l-1 h-1` (1,338, water), `umol-n g-1 h-1` (1,027 benthic
+ 1 water), `umol-n m-3 h-1` (235, benthic). **Each rate row has its own `method_dim` row** (`method_key` = `nfix_rate_id`; 4,793 of
the 4,802 methods; the other 9 are one `method_id_null` row per J1 attribute) holding what is needed to recover the original:
`params` = original rate, original unit, original and converted error and its type, the ARA conversion factor (3,046 ARA rows),
incubation time and temperature, day, hour, season, substrate, substrate details, the reference's extraction location and its
data-archive DOI; `technique` = `nfix_method` (ARA / 15N2 / N2/Ar); `conversion_applied` = "source converted: <original value
and unit> -> <converted value and unit>" where they differ. **4,647 of 4,793 rows differ** (a unit spelling or scale, N2 to N, an
ARA ratio), so the flag `converted_by_source` is on those facts and says little beyond "see `params`". The 174 N2/Ar rows with a
negative original rate (net N2 flux out of the sediment) are stored **positive** in the converted column (sign flipped by the
source); the extract asserts no converted rate is negative and counts these.

**Error and n.** `nfix_error_type` `stdev.` (1,316 facts with a value) -> `sd`; `std. err` (975) -> `se`; `nfix_n` -> `n` (4,725; 68
blank). Error values the file gives under `C.I.` (34) or `range` (7 with a value) are not an SD or SE: no `sd`/`se`, the value and type are in
`params`, and the fact carries `error_type_other` (41). **Error values with no type at all (237; the file's code is the literal `NA`)
are assumed to be a standard deviation**, by the user's rule of 2026-11-17 (default to SD, flag it, note it): the value is stored in `sd`,
the fact carries `error_type_missing` (237), and its method row has `notes` saying so. Any other type string raises. Filter on
`error_type_missing` if you do not want to rely on the assumption.

## Mapping

- **Habitat (D3), a composite raw label.** `location.habitat_type` = the `habitat` column for `benthos` / `other` rows and
  `<habitat> | wc` for `wc` rows, both in `location_key` (rule 3.10), so a water-column and a benthic row at one coordinate are
  two locations. Label map: salt marshes -> Tidal marsh, mangroves -> Mangrove forest, tidal flats -> Tidal flat, seagrasses ->
  Seagrass meadow; every `... | wc` -> **Water column** (these facts carry `habitat_from_scope`, 4,843, since no source states it);
  `estuaries` and `continental shelves` (and their wc forms) -> `setting` estuary / shelf only; lakes, rivers, freshwater
  wetlands (both forms) -> nothing. Resulting facts by (habitat, setting): Water column/shelf 2,608, Water column/estuary 707,
  Water column/none 1,528; NULL/estuary 1,353, NULL/shelf 283, NULL/none 2,117 (inland, plus estuaries and shelves with no
  habitat); Seagrass meadow 413, Tidal marsh 406, Tidal flat 334, Mangrove forest 191.
- **Locations: 1,530** (`habitat_type | site_name | lat | lon`); coordinates present and valid on every row. `site_name` is the
  source's text (whitespace normalised).
- **Samples: 3,608.** The plan's grain is reference + lat + lon + date + sample depth + substrate (2,696 samples). The sample
  row also stores salinity and its unit, the substrate details and the location, so `sample_key` adds those (the row is asserted
  single-valued; no sample spans two locations). `sample_depth` ("depth of site, sample, or sediment", ambiguous): numeric
  `sample_depth_m` only for `wc` rows (868 have a value); for the other 1,151 it is text in `depth_notes`
  (`substrate: ...; details: ...; sample_depth (depth of site, sample or sediment, m): ...`). Year is NULL on 95 rows (flag
  `year_missing`), month on 422 (no flag). Day, hour and season are in the method params.
- **Salinity** (ancillary, 1,340 rows) -> `sample.water_salinity` with its unit cell (`unitless` 311, `psu` 302, `ppt` 113, `per
  mille` 95, `g l-1` 18, `pss` 4). **497 values have no unit cell**: NULL units and flag `salinity_units_missing` on that
  row's facts (1,796). Five unit cells have no value (ignored, counted). The maximum, 400, is in `g l-1` (hypersaline systems).

## J1 (water concentrations from the ancillary file)

Nine columns: `NH4_wc` `NO3_wc` `NO2_wc` `NOx_wc` `DIN_wc` `DIP_wc` `DO_wc` `chla_wc` and `pH` (unit `unitless` from the data dictionary).
Units are the unit cells, verbatim and ASCII, so each attribute holds 2 to 13 spellings (`um`, `umol l-1`, `ug l-1`, `mg-n l-1`,
`umol kg-1`, `% sat`, `mg m-2`, `ug cm-2`, ...): **J1 attributes here are not unit-homogeneous, and a few units are areal or a
percentage**; every conversion is downstream. 6,036 cells: **4 `chla_wc` cells have no unit and are skipped** (`units_missing`), 6,032 have
one.

**One fact per (sample, attribute, distinct value and unit).** Repeats in one sample collapse into the first row's fact
(`source_row_ref` = that ancillary row): **885 collapsed, 5,147 facts**. Under the plan's sample key the same rule gives 1,324 collapsed and
**4,708 facts, which is the plan's prediction**; the key used here is finer (see Samples), so there are more. A sample with two
different values of one attribute keeps both with `covariate_conflict`: 268 facts in 118 sample-attribute pairs.
Cells: 6,036 = 5,147 + 885 + 4.

Not loaded (phase 2; non-empty cells in the report): conductivity, PAR, flow, residence time, water-table depth, primary
production, DIN flux, `POC_wc`, `PON_wc` and every `*_sed` column. They are in the file and are not quality checked for
extraction errors (the EML says so of all ancillary variables).

## References

`reference_id` R1..R267 is the study id and `reference_key`. The references file supplies DOI (262 of 267; lower case), URL,
authors, title, year; `bibliography_id` = the shorthand `reference`; the DOI of an associated data archive (`doi_data`) is in
the method params. Journal, volume and pages are not loaded. 267 of 267 references are used by facts.

## Flags

(Facts.) `converted_by_source` 4,647; `habitat_from_scope` 4,843; `salinity_units_missing` 1,796; `covariate_conflict` 268;
`zero_value` 366 (312 rates, 54 J1 values); `duplicate_source_row` 173; `error_type_missing` 237; `error_type_other` 41;
`year_missing` 114. `duplicate_source_row` marks **every copy** in a group of rate rows identical in all columns except
`nfix_rate_id` (as Chang): 68 groups, 173 rows (the plan counted the 105 extra copies). `coords_*` and sign flags are not used
(no negative rate, all coordinates present).

## Row accounting

4,793 rate rows = 4,793 rate facts. 6,036 J1 numeric cells = 5,147 facts + 885 collapsed + 4 skipped (`units_missing`; their raw
rows and cells are in `skip_report.jsonl`). 3,608 samples, 1,530 locations, 4,802 methods, 267 references and citations. After the
central build every fact was checked against the raw cell it points to by code that does not use `extract.py` (value, unit,
reference, coordinates, site, habitat, setting, year, month, n, sd/se from the error type, technique, original rate and unit,
ARA factor, salinity and its unit, sample depth, and every flag recomputed): 9,940 of 9,940 agree; the set of rate rows equals the
set of rate facts, and each of the 6,032 J1 cells with a unit has a fact in the same sample with the same attribute, value and
unit. The J1 fact count was predicted independently from the raw files (5,147) and matched. One-study checks, predicted from the
raw files alone (rows + distinct J1 values per sample): R231 4 + 2 = 6, R147 2 + 10 = 12, R198 13 + 0 = 13; all matched. Adding this
source left the other eleven sources' content unchanged (343,276 facts = 333,336 + 9,940). Two runs of `extract.py` give
identical tables.

## Overlap with other compilations

Never merged. `shao-nfix` (ocean N2 fixation, water column) and `ray-oyster` (some oyster-reef studies are co-authors' own),
`boynton-flux` (benthic nutrient fluxes in some of the same estuaries, e.g. Narragansett Bay), and `chang-denitrification`
(benthic N loss) are the nearest neighbours.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
