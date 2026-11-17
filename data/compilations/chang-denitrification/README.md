# chang-denitrification (benthic nitrogen loss: denitrification and anammox)

Chang, Y., Tan, E., Gao, D., Liu, C., Zhang, Z., Huang, Z., et al. (2024). *Global database of actual nitrogen loss rates in coastal and
marine sediments.* figshare, DOI 10.6084/m9.figshare.27745770.v3 (paper: *Earth System Science Data* 17, 3521, 2025).

- **Version:** `v3 (2024-11-15)`, from the figshare record. **Access date:** 2026-11-15. **License:** CC BY 4.0.
- **Raw files:** `Upload_data.xlsx` (sheets `Raw_data` and `Experiment information`, 473 rows each, aligned by position and asserted to
  agree on reference, location and site) and `Raw_data references.docx` (69 numbered references).

Rates of nitrogen loss from coastal and marine sediments: 473 rows, 69 studies, 71 location names. Per the paper the rates come from
dark, intact-core incubations with 15N isotope pairing, but the workbook does not say this per row.

## Facts (2,495)

Units are the header's, with thin and no-break spaces read as plain spaces. Nothing is converted.

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

- `P14` is total N2 production. Where both rates exist it equals denitrification + anammox to rounding (asserted within 0.05), and
  otherwise it equals denitrification. Seven rows have `P14` only, which is why it has 473 facts and denitrification 466.
- Nitrate, ammonium and oxygen are J1 water values, because the paper does not say which water layer. The plan had oxygen in the
  `bottom_water_o2` slot, but nothing says bottom water.
- Temperature and salinity are sample slots. Water depth is the depth over the sediment, so it is `location.water_depth_m`.

## How it maps

- **Locations:** 367 (the key includes reference, location, site, coordinates and depth). 27 rows have no coordinates and carry
  `coords_missing`.
- **Habitat is NULL** for every location. The source gives no label and states no scope for the whole dataset, so there is no
  `habitat_from_scope` flag either. The label map declares the column with one empty-label row because the build requires it.
- **Samples:** one per row, with year (20 blank, `year_missing`) and month. No sediment depth.
- **Methods:** for the three rates, one per distinct combination of 15NO3 concentration, incubation environment, temperature, time,
  termination and rate calculation (212 rows in all). `technique` is the rate-calculation string, `light_dark` is `dark` where stated,
  and all six strings are in `params`. `incubation_type` is NULL, since the intact-core statement is the paper's, not a per-row field.
- `sd`, `se` and `n` are NULL.
- **References:** `References No.` 1 to 69 gives `ref_no:<n>`, with the docx text as the citation title and the year parsed. The file has
  no DOIs.

## Missing codes

The paper says NM means not measured and blank or NA means not available. Blanks, `na` and `n/a` give no fact and are counted in the
report. `nm` in a value column is a skip with reason `not_measured` (2 cells, both in data row 269). Any other text raises.

## Not loaded

`ra (%)` (the anammox share, derived by the authors and not always consistent), `Process` (used for a consistency check) and the `Month`
text. The marsh, mangrove or shelf setting of a site is not in the file.

## Flags

`coords_missing` 117, `year_missing` 75, `zero_value` 41. Temperature is below zero on 17 rows (polar water) and water depth is 0 on 4;
neither is flagged.

## Row accounting

2,495 numeric cells = 2,495 facts, and 2 `nm` cells = 2 skips. All facts were checked back to their raw cells by independent code, a
one-study check (reference 24) predicted 18 facts and gave 18, and two runs give identical tables.

## Overlap

The file has no DOIs, so overlap is found downstream. Benthic N removal also appears in `ray-oyster` and nutrient fluxes in
`boynton-flux`; a few studies may be shared.

Back to the [compilations index](../README.md).
