# usgs-wetlands (USGS tidal saline wetland ecosystem properties)

Feher, L.C., Osland, M.J., Grace, J.B., Buffington, K.J., Carr, J.A., Guntenspergen, G.R., Martinez, M., Neville, J.A., Rankin, L.L.,
Thorne, K.M. (2025). *North American dataset of ecosystem properties in tidal saline wetlands: canopy height, aboveground biomass,
productivity, soil carbon density, and soil carbon accumulation.* U.S. Geological Survey data release, DOI 10.5066/P133WKRO.

- **Version:** `2025-05-07`, the metadata publication date (the release has no edition number; the workbook is named `..._2023`).
- **Access date:** 2026-11-15. **License:** none stated. The metadata says "None. Users are advised to read the dataset's metadata
  thoroughly", recorded verbatim in `source.license`.
- **Raw files:** `ecosystem_attribute_data_2023.xlsx` (sheets `Height`, `Biomass`, `Productivity`, `soil_C_density`,
  `soil_C_accumulation`, `Citations`) and the FGDC metadata `ecosystem_attributes_2023.xml`.

A literature-derived compilation for the US, Canada and Mexico. Each row is a site-level average from a primary study (781 rows
also name a secondary compilation). 1,485 rows, 828 locations, 262 cited studies.

## Facts (1,485)

One fact per data row, in the sheet's own unit. Nothing is converted.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `usgs_canopy_height` | B3 `canopy_height` | 258 | m |
| `usgs_aboveground_biomass` | B2 `above_ground_biomass` | 225 | t/ha |
| `usgs_productivity` | B6 `litterfall` 36, `above_ground_npp` 142, none 13 | 191 | t/ha/yr |
| `usgs_soil_c_density` | C1 `soil_carbon_density` (extension) | 532 | g/cm3 |
| `usgs_soil_c_accum` | G2 `carbon_burial` | 279 | g/m2/yr |

**Productivity subfield:** the method `litterfall, recorded monthly over at least 1 year` gives `litterfall`. Every other named method
gives `above_ground_npp`, because the release defines the sheet as aboveground productivity. `not reported` leaves the subfield
empty (13 facts). An unknown method string raises.

## How it maps

- **Locations:** the key is latitude, longitude, `type`, `veg_type` and `location`. `habitat_type` is the source's `type`:
  `salt marsh` gives Tidal marsh (580 locations) and `mangrove` gives Mangrove forest (220). `salt flat` (26) and
  `salt marsh mangrove` (2) have no equivalent, so those 28 locations have NULL habitat. The habitat comes from the source label, so
  there is no `habitat_from_scope` flag. `veg_type` is the raw `vegetation_class`.
- **Coordinates** are as given. Where a paper gave none, the authors placed the site from its description, which is not marked per row.
- **Samples:** one per row. Only the soil sheet has a depth: `core_depth_cm` is parsed as `0-N` into the depth range. `not reported`
  (186 rows) gives no depth and the flag `depth_missing`.
- **Methods:** 166 rows. `technique` is the `method` string on the biomass and productivity sheets; `not reported` (41 facts) is
  `method_id_null`. The `*_range` text and the secondary source go in `params`. **The range is not an `sd`**, and `sd`, `se` and `n`
  are NULL.
- **References:** `doi:<doi>` where the `Citations` tab's url is a DOI, otherwise `ref:<string>`. 262 strings give 261 references and
  267 citations. Exceptions:
  - Three `doi.org` urls in the tab are not DOIs and are kept as plain urls.
  - 4 combined strings are split into parts joined by `+`, one citation row per part.
  - `Dai and Weigert 1996` and `Dai and Wiegert 1996` share one DOI, so they are one reference.
  - 11 cited papers are not in the tab (7 "unpublished data" strings, and 4 whose tab entry has a different year). They are not matched
    and keep a text-only citation. The near misses are in `extract_report.json`.
- **Secondary sources** (25 strings, such as `Sanderman et al. 2018`) go in `params`, with the flag `secondary_source` on 781 facts.

## Not loaded

Nothing in a data row is dropped. The metadata is stored only for version, DOI, authors and use constraint.

## Flags

`zero_value` 46 and `assigned_by_source` 46: the release says height and biomass "were estimated as 0" in salt flats, and the 25 height
and 21 biomass zeros are exactly those rows. They are the authors' assignment, not measurements, so filter on the flag before
averaging. `secondary_source` 781, `method_missing` 41, `depth_missing` 186, `duplicate_source_row` 9.

## Row accounting

1,485 rows = 1,485 facts, nothing skipped. All 1,485 facts were checked back to their raw cells by code independent of `extract.py`.
A one-study check (Giani et al. 1996) predicted 11 facts and gave 11. Two runs give identical tables.

## Overlap

Coastal Carbon Network: 60 of this release's 179 DOIs also occur in CCN (533 of the 1,485 facts), and both draw on Sanderman 2018,
Holmquist 2018 and the Coastal Carbon Atlas. The quantities differ in form (site averages here, depth series there). Soil carbon
accumulation here is not CCN's vertical `pb210_cic_accretion_rate`. Join by DOI and coordinates. Ribeiro's litterfall paper list may
overlap the productivity sheet.

Back to the [compilations index](../README.md).
