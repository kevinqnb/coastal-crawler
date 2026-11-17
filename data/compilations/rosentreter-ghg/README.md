# rosentreter-ghg (coastal greenhouse-gas fluxes: estuaries and vegetated coast)

Rosentreter, J.A., Laruelle, G.G., Bange, H.W., Bianchi, T.S., Busecke, J.J.M., Cai, W.-J., et al. (2023). *Coastal GHG data base
(Rosentreter et al. 2023).* figshare, DOI 10.6084/m9.figshare.22351267.v1 (paper: *Nature Climate Change*, 2023).

- **Version:** `v1 (2023-05-05)`, from the figshare record. **Access date:** 2026-11-15. **License:** CC BY 4.0 (figshare record).
- **Raw file:** `database_coastalGHG_Rosentreter et al._figshare.xlsx`, with sheets `Metadata`, three `Estuaries_*` sheets (CO2, CH4,
  N2O), three `Coastal_vegetation_*` sheets and `Surface_areas`.

Site-level greenhouse-gas fluxes from the literature, one row per site and gas: 738 rows from 397 cited works. Each value is a site
average over space and time, so it has no year or depth.

## Facts (738)

One fact per row, in the header's unit. Nothing is converted.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `ghg_co2_estuary` | C8 `co2_flux` | 204 | g CO2 m2 d-1 |
| `ghg_ch4_estuary` | C8 `ch4_flux` | 157 | mg CH4 m2 d-1 |
| `ghg_n2o_estuary` | C8 `n2o_flux` | 123 | mg N2O m2 d-1 |
| `ghg_co2_vegetation` | C8 `co2_flux` | 37 | g C m2 yr-1 |
| `ghg_ch4_vegetation` | C8 `ch4_flux` | 162 | mg CH4 m2 d-1 |
| `ghg_n2o_vegetation` | C8 `n2o_flux` | 55 | mg N2O m2 d-1 |

Estuary and vegetation fluxes are separate attributes because the exchange differs: estuary rows are water-air exchange, vegetation
rows are ecosystem-atmosphere exchange over mangroves, marshes and seagrass. Vegetation CO2 is per year and per carbon, unlike the others,
so do not pool without converting.

**Sign:** the workbook states no convention. Vegetation CO2 is mostly negative (30 of 37), which reads as uptake, so positive is taken to
mean a flux to the atmosphere. That is an inference. Negative values are real and are not flagged.

## How it maps

- **Locations:** 702 (456 estuary, 246 vegetated coast). The key is latitude, longitude, site name, habitat and setting, so one site in
  the CO2, CH4 and N2O sheets is one location.
- **Type codes** come from the Metadata legend. Estuary: 1 Tidal systems and deltas, 2 Lagoons, 3 Fjords (raw `geomorphic_setting`).
  Vegetation: 1 Mangroves, 2 Saltmarshes, 3 Seagrasses (raw `habitat_type`).
- **Standard labels:** Mangroves, Saltmarshes and Seagrasses map to mangrove forest, tidal marsh and seagrass meadow. Lagoons map to
  coastal lagoon and Fjords to fjord. "Tidal systems and deltas" stays blank, since it mixes two settings.
- **Estuary rows** get habitat "Water column" from the sheet's scope, so all 484 estuary facts carry `habitat_from_scope`.
- **Samples:** one per row. **Methods:** none stated, so one `method_id_null` row per attribute (6), with the interface and the column
  header in `params`. `sd`, `se` and `n` are NULL. The RECCAP2 `region` column is not loaded.
- **References:** the same author-year `reference` string can stand for different papers (23 of 380 strings), so the study is identified
  by the full citations. `full_citation` is split on `; ` into one piece per paper, and a row citing several papers gets the piece keys
  joined by `+`. Result: 397 references, 483 citations. Only 10 pieces carry a DOI, mostly datasets. The piece count must equal the number
  of papers the author-year string names. Nine strings disagree and are declared explicitly in the extract (comma-joined papers, a damaged
  separator, two dataset citations), and any other mismatch raises.
- A reference naming a personal communication (14 facts) gets `pers_comm`. The Metadata says these have since been published.

## Not loaded

The `Surface_areas` sheet (regional areas used to scale fluxes, not measurements), the RECCAP2 `region`, and the Metadata sheet beyond its
legends.

## Flags

`habitat_from_scope` 484, `coords_integer_degree` 58 (both coordinates whole degrees), `zero_value` 9, `pers_comm` 14.

## Row accounting

738 rows = 738 facts, nothing skipped. All facts were checked back to their raw rows by independent code, a one-study check (Banerjee)
predicted 2 facts and gave 2, and two runs give identical tables.

## Overlap

No DOI is shared with another compilation. `ray-oyster` also reports CO2, CH4 and N2O at oyster sites, so overlap is only through shared
papers. Join by DOI and coordinates.

Back to the [compilations index](../README.md).
