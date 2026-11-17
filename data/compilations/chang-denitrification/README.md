# chang-denitrification (benthic nitrogen loss: denitrification and anammox)

Chang, Y., Tan, E., Gao, D., Liu, C., Zhang, Z., Huang, Z., et al. (2024). *Global database of actual nitrogen loss
rates in coastal and marine sediments* [Dataset]. figshare, https://doi.org/10.6084/m9.figshare.27745770.v3
(paper: *Earth System Science Data* 17, 3521, 2025).

- **Version:** `v3 (2024-11-15)`, read from the figshare record by the integration plan; the workbook states no version.
- **Access date:** 2026-11-15. **License:** CC BY 4.0 (figshare record).
- **Raw files:** `Upload_data.xlsx` (sheets `Raw_data` and `Experiment information`, 473 rows each, aligned by position;
  the extract asserts reference number, location and site agree row by row) and `Raw_data references.docx` (69 numbered
  references).

A literature compilation of rates of nitrogen loss from coastal and marine sediments, 1996 to 2024 studies. Per the paper
(ESSD 17, 3521) the rates are from intact-core incubations in the dark, with the 15N isotope-pairing family of methods;
the workbook does not state this per row (see Methods). 473 rows, 69 studies, 71 distinct location names.

**Facts:** 2,495, one per numeric cell of nine value columns. Units are the header's, with thin and no-break spaces read as
plain spaces; nothing is converted.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `chang_denitrification` | C4 `denitrification` | 466 | μmol N m−2 h−1 |
| `chang_anammox` | C4 `anammox` | 255 | μmol N m−2 h−1 |
| `chang_total_n2_production` (`P14`) | C4 `total_n2_production` (extension) | 473 | μmol N m−2 h−1 |
| `chang_organic_carbon` | C1 `organic_carbon` | 283 | % |
| `chang_c_n_ratio` | C1 `c_n_ratio` | 136 | mol:mol |
| `chang_o2_penetration_depth` | C2 `o2_penetration_depth` | 67 | mm |
| `water_nitrate` | J1 `nitrate` | 301 | μM |
| `water_ammonium` | J1 `ammonium` | 246 | μM |
| `water_o2` | J1 `dissolved_oxygen` | 268 | μM |

`P14` is total N2 production: on all 255 rows with both rates it equals denitrification + anammox to rounding (largest
difference 0.042, asserted within 0.05), and on the other rows it equals denitrification. Seven rows have `P14` only
(blank denitrification and `Process`): these are why `P14` has 473 facts and denitrification 466. The anammox cell is
filled exactly when `Process` is `Denitrification and anammox` (asserted). 41 facts are zeros, mostly anammox (34
measured zeros).

**J1 and the oxygen choice.** The paper calls nitrate, ammonium, dissolved oxygen, temperature, salinity and depth "water
physicochemical factors" but does not say which water layer. They are water-medium values, so nitrate, ammonium and
dissolved oxygen are J1 (one sample per source row, so one fact per sample and attribute, no collapsing needed). **The plan
put dissolved oxygen in the `bottom_water_o2` sample slot; it is J1 `water_o2` here** because nothing in the source says
bottom water. Temperature and salinity (units `psu` from the header) are sample slots (`water_temperature_c`,
`water_salinity`). **Water depth** is the depth of the water over the sediment, so it is `location.water_depth_m` (as in
SCOC and BenBioDen) and not the water-column `sample_depth_m` the plan named; it is part of `location_key`.

## Mapping

- `location_key` = reference no | Locations | Site | lat | lon | water depth: **367 locations** (21 without coordinates, 66
  without depth). `site_name` = `Locations`, `source_site_id` = `Site` (a number is written as text). 27 rows have no
  coordinates (both blank, asserted): their facts carry `coords_missing`. No row is flagged for a missing depth.
- **Habitat is NULL** for every location: the source gives no habitat label and states no scope for the whole dataset
  (D3), so there is no `habitat_from_scope` flag either. The label map declares the habitat column with one empty-label
  row (`chang-denitrification,habitat_type,,habitat,`), because the build requires every source to declare it; there is no
  raw label to map.
- `sample`: one per source row (`row<n>`), `sampling_year` = `Year` (20 rows blank: `year_missing`), `sampling_month` =
  `Month_1` (`Month` names asserted to agree; 31 rows blank, no flag). No sediment depth interval.
- **Methods.** For the three rate attributes one `method_dim` row per distinct (attribute, 15NO3 addition concentration,
  incubation environment, incubation temperature, incubation time, termination, rate calculation) from the aligned
  `Experiment information` row: 83 + 42 + 87 rows. `technique` = the rate-calculation string (e.g. `(Nielsen, 1992)`;
  blank -> `method_id_null`), `light_dark` = `dark` where the incubation environment says so (387 rows; 86 blank), and
  all six strings go to `params` as written (`~100`, `25, 50, 100, 150`, `every 24 h for 4 days` ...). `incubation_type` is
  NULL: the intact-core, dark-only statement is the paper's, for the whole dataset, not a per-row field. Covariates (organic
  carbon, C/N, O2 penetration, J1) have no method information: one `method_id_null` row each.
- `ra (%)` (anammox share of total N2 production) is derived by the authors, does not equal anammox / P14 on every row, and
  is **not loaded** (D7). `sd`, `se`, `n` are NULL.

## Missing codes

The paper: "NM represents parameters that were not measured, and empty or NA indicates data not available." Blank cells
give no fact. `na` and `n/a` give no fact and are counted in the report (organic carbon 9, C/N 4, O2 penetration 3,
dissolved oxygen 1, nitrate 2, ammonium 20). `nm` in a fact-bearing column is a skip with reason `not_measured`: two cells, both
in data row 269 (organic carbon and C/N). Any other text in a value column raises. (The integration plan expected `na` and
`nm` to be NULL; `nm` is a skip so that it enters the row accounting, following the cross-cutting rule 3.3.)

## References

`References No.` 1 to 69 is the study id: `reference_key` = `ref_no:<n>`, one `reference_dim` and one `citation_dim` row each
(69 of 69 numbers used in the data and present in the docx, asserted). `bibliography_id` = the number, `title` = the whole
docx entry (authors, title, journal, year, pages, not parsed), `year` = the `, YYYY.` that ends the journal part (parsed for
all 69). The file has no DOIs; the DOI-resolution job can attach them from the entry text.

## Flags

(Facts; `extract_report.json` has the rest.) `coords_missing`: 117 (27 rows), `year_missing`: 75 (20 rows), `zero_value`: 41.
Not used: `duplicate_source_row` (no two rows identical across both sheets), `negative_value`. Temperature is below zero on 17
rows (polar water; a slot, not flagged); water depth is 0 on 4 rows (kept).

## Not loaded

`ra (%)` (derived), `Process` (a label, used for a consistency check), the `Month` text (checked against `Month_1`). Nothing else
is dropped. The coastal-wetland link of a site (marsh, mangrove, shelf, open sea) is not in the file.

## Row accounting

473 source rows; 2,495 numeric value cells = 2,495 facts; 2 `nm` cells = 2 skips; the `na` cells give neither. 473 samples, 367
locations, 218 methods, 69 references and citations. After the central build every fact was checked against the raw cell it points to
by code that does not use `extract.py` (value, unit, subfield and type, reference, coordinates, site, depth, year, month,
temperature, salinity, the six experiment strings, technique, light/dark, and every flag recomputed): 2,495 of 2,495 agree; the set of
numeric cells equals the set of facts and the set of `nm` cells the set of skips. A one-study check (reference 24, predicted from the raw
sheet alone: 5 denitrification + 5 anammox + 5 P14 + 3 dissolved oxygen = 18) matched. Adding this source left the other seven
sources' content in `compilations.duckdb` unchanged (316,402 facts = 313,907 + 2,495). Two runs of `extract.py` give identical
tables. `source_row_ref` is `Upload_data.xlsx[Raw_data]#data_row=<0-based row>:<attribute>`.

## Overlap with other compilations

Never merged. The file has no DOIs, so overlap is found downstream. Benthic N removal also appears in `ray-oyster` (oyster
sites) and, for nitrate and ammonium fluxes, `boynton-flux`; a few studies may be shared. The J1 values here sit beside a rate so
they can be set beside, or replaced by, the joined WOD/GLODAP layers.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
