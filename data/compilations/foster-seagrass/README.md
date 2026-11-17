# foster-seagrass (Foster et al. 2025)

Foster, N., Gomis, E., Montemayor, D. et al. (2025). *Global patterns and drivers of seagrass biomass, net primary
production and meadow structure.* Dryad, DOI 10.5061/dryad.kprr4xhh1. Accessed 2026-11-15. Version `2025` (the Dryad
year; the source states none). License not stated in the files.

A meta-analysis table of 5,903 rows (one per study, site, genus and year), 463 distinct `Reference` strings and 1,131 locations,
sampled 1972 to 2022, 12 seagrass genera plus `Mixed`, coastal and estuarine.

## Facts (12,980)

One fact per non-blank measurement cell, units as in the source headers.

| Attribute | Type / subfield | Units |
|---|---|---|
| `seagrass_biomass_above`, `_below`, `_total` | B2 above, below, total biomass | g DW m-2 |
| `seagrass_shoot_density` | B3 `shoot_or_stem_density` | shoots m-2 |
| `seagrass_leaf_density` | B3 `leaf_density` (extension) | leaves m-2 |
| `seagrass_percent_cover` | B3 `percent_cover` | `%` |
| `seagrass_production_above`, `_below` | B6 above, below ground NPP | g DW m-2 d-1 |
| `seagrass_production_total`, `_shoot`, `_leaf` | B6 `total_npp`, `shoot_production`, `leaf_production` (extensions) | g DW m-2 d-1 |

The `seagrass_` prefix keeps these apart from other compilations' biomass attributes. Production is per day, as reported.

## How it maps

- **Locations:** `habitat_type` is the constant `seagrass` (dataset scope), so habitat is "Seagrass meadow". `Geotype` is
  `geomorphic_setting` (Estuarine gives setting estuary, Coastal stays blank), `Bioregion` is `bioregion`, and `ECOREGION` is
  `marine_ecoregion` (it looks like Marine Ecoregions of the World, but the source does not say). The location key includes all three
  labels, so the same coordinates with different labels stay separate (12 pairs differ in Geotype, 2 in Bioregion).
- **Samples:** `taxon` is `Genera`, rank `genus` (or `mixed` for `Mixed`, 104 rows). An unknown genus raises.
- **Methods:** none stated. One method row per attribute, `method_id_null`.
- **References:** `reference_key` is `doi:<doi>` when the DOI cell is a real DOI (467 references), otherwise
  `ref:<Reference string>` (91). 567 non-blank DOI cells are free-text citations and fall back to the string. `Hitchcock et al. 2017`
  has both kinds of row, so it is two references. Nothing is merged.
- No `sd`, `se` or `n`, and no sediment depth.

## Not loaded

The 19 `BO22_*` columns (temperature, pH, PAR, nutrients, chlorophyll, salinity, O2, current velocity, productivity). They are a
Bio-ORACLE model and satellite climatology (2000 to 2014) looked up at each coordinate, not seagrass measurements.

## Flags

`habitat_from_scope` (all facts), `year_missing` 285, `duplicate_source_row` 236 (rows identical except `Unique.ID`),
`total_ne_sum_of_parts` 981.

## Row accounting

5,903 rows = 5,856 with facts + 47 with none of the 11 measurement columns (skipped as `no_seagrass_measurement`).

## Things to know

- **Totals are partly derived upstream.** The source says total = above + below "if not reported", and the data cannot show which
  is which (734 rows have a total with no parts, 51 have both parts but no total). Totals are carried as given.
- **`total_ne_sum_of_parts`** marks the three facts of a triple where total differs from above + below by more than 1e-3
  (relative and absolute). It depends on the tolerance (726 facts at 1e-2, 1,473 at 1e-6), so use it as a screen.
- There are no zeros in the source (minimum 0.001), so absence cannot be told from "not measured". Values are not limited or clipped.

Back to the [compilations index](../README.md).
