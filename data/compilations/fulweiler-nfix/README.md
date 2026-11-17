# fulweiler-nfix (aquatic nitrogen fixation rates, inland and coastal)

Fulweiler, R.W., Berberich, M.E., Rinehart, S.A., Taylor, J.M., Kelly, M.C., Ray, N.E., Oczkowski, A., Benavides, M., Church, M.J.,
Loeks, B., Newell, S., Olofsson, M., Oppong, J., Roley, S.S., Vizza, C., Wilson, S.T., Scott, J.T., Marcarelli, A.M. (2025). *Global
dataset of nitrogen fixation rates across inland and coastal waters based on a coordinated synthesis effort*, ver 2. Environmental Data
Initiative, DOI 10.6073/pasta/333f651ca721da657d5fd0c393d26cf8 (package `edi.1759.2`).

- **Version:** `EDI edi.1759.2 (ver 2)`. **Access date:** 2026-11-15. **License:** CC BY 4.0 (from the package's EML).
- **Raw files used:** `aquatic_N2fix_rates.csv` (4,793 rows), `aquatic_N2fix_ancillary.csv` (3,238 rows, a subset of the rates, keyed
  by `nfix_rate_id`) and `aquatic_N2fix_references.csv` (267 references). `data_dictionary.csv` is the authority for meanings and units.

Nitrogen fixation rates (acetylene reduction, 15N2 labelling, N2/Ar) from 267 papers and datasets to April 2022, for streams, lakes,
freshwater wetlands, mangroves, marshes, seagrass, estuaries, tidal flats and shelves. Water-column, benthic and "other" substrates
(plants, stems, epiphytes) are all included. The inland rows are loaded so the database mirrors the compilation, and they have no standard
habitat.

## Facts (9,940)

4,793 rates + 5,147 water-chemistry (J1) facts, with 4 skips.

| Attribute | Type / subfield | Facts |
|---|---|---|
| `fulweiler_n2fix_water` (substrate `wc`) | C5 `n2_fixation` | 1,776 |
| `fulweiler_n2fix_benthic` (`benthos` and `other`) | C7 `n2_fixation` | 3,017 |
| `water_ammonium`, `water_nitrate`, `water_nitrite`, `water_nitrate_nitrite`, `water_din`, `water_phosphate` | J1 | 787 / 664 / 226 / 417 / 244 / 874 |
| `water_o2`, `water_chl_a`, `water_ph` | J1 | 539 / 969 / 427 |

## The fact carries the source's harmonised value

The fact is `nfix_rate_converted`, the source's rate converted to molar units. Its units are `umol-n m-2 h-1`, `umol-n l-1 h-1`,
`umol-n g-1 h-1` and `umol-n m-3 h-1`. **Each rate row has its own method row** (`method_key` is `nfix_rate_id`) holding what is needed
to recover the original: original rate and unit, original and converted error, the ARA conversion factor (3,046 ARA rows), incubation time
and temperature, substrate, and the reference's extraction location. `technique` is the method (ARA, 15N2 or N2/Ar). 4,647 rows differ
from their original, so `converted_by_source` is on those facts and mostly means "see `params`". 174 N2/Ar rows with a negative original
rate are stored positive by the source. The extract asserts no converted rate is negative.

**Errors and n:** `stdev.` goes to `sd` (1,316 facts) and `std. err` to `se` (975). `C.I.` (34) and `range` (7) are neither, so they
get no `sd` or `se`, stay in `params`, and carry `error_type_other`. **237 error values have no type** (the file's code is `NA`). By your rule
(default to SD, flag it, note it) they are stored in `sd`, flagged `error_type_missing`, and noted in `method.notes`. Any other type string
raises. `n` comes from `nfix_n` (68 blank).

## How it maps

- **Habitat:** the raw label is the `habitat` column, and `<habitat> | wc` for water-column rows, so a water-column and a benthic row at
  one coordinate are two locations. Salt marshes, mangroves, tidal flats and seagrasses map to the matching habitat. Every `... | wc`
  maps to **Water column** with `habitat_from_scope` (4,843 facts). Estuaries and shelves set the setting. Lakes, rivers and freshwater
  wetlands map to nothing.
- **Locations (1,530):** habitat label, site, latitude, longitude.
- **Samples (3,608):** the plan's grain (reference, coordinates, date, depth, substrate) gives 2,696. The sample row also stores salinity,
  its unit, the substrate details and the location, so the key adds those. `sample_depth` is ambiguous ("depth of site, sample, or sediment"),
  so it is numeric `sample_depth_m` only for water-column rows (868) and text in `depth_notes` for the rest. Year is NULL on 95 rows
  (`year_missing`).
- **Salinity** goes to `water_salinity` with its raw unit. 497 values have no unit, so they get NULL units and `salinity_units_missing`.
- **References:** `R1` to `R267` is the key. DOIs (262 of 267), authors, title and year come from the references file. The DOI of an
  associated data archive is in the method params.

## Water chemistry (J1)

Nine ancillary columns: ammonium, nitrate, nitrite, nitrate+nitrite, DIN, phosphate, oxygen, chlorophyll a and pH (`unitless` from the data
dictionary). Units are the source's own spellings, so each attribute holds 2 to 13 of them, and some are areal or percent. They are **not
unit-homogeneous**, and conversion is downstream. 6,036 cells: 4 `chla_wc` cells have no unit and are skipped (`units_missing`).

One fact per sample, attribute, value and unit: 885 repeats collapse into the first row's fact, giving 5,147 facts. Under the plan's coarser
key the count is 4,708, which is the plan's prediction. A sample with two different values keeps both with `covariate_conflict` (268 facts,
118 pairs).

## Not loaded

Conductivity, PAR, flow, residence time, water-table depth, primary production, DIN flux, particulate C and N, and every `*_sed` column
(phase 2, counted in the report).

## Flags

`converted_by_source` 4,647, `habitat_from_scope` 4,843, `salinity_units_missing` 1,796, `covariate_conflict` 268, `zero_value` 366,
`duplicate_source_row` 173 (every copy of 68 groups of identical rows), `error_type_missing` 237, `error_type_other` 41, `year_missing` 114.

## Row accounting

4,793 rate rows = 4,793 rate facts. 6,036 J1 cells = 5,147 facts + 885 collapsed + 4 skipped. All 9,940 facts were checked back to their raw
cells, the J1 count was predicted independently (5,147) and matched, three one-study checks matched, and two runs give identical tables.

## Overlap

`shao-nfix` (ocean N2 fixation), `ray-oyster` (some oyster studies are co-authors' own), `boynton-flux` (nutrient fluxes in some of the same
estuaries) and `chang-denitrification` (benthic N loss).

Back to the [compilations index](../README.md).
