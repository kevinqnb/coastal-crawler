# foster-seagrass (Foster et al. 2025, seagrass biomass, production and structure)

Foster, Nicole; Gomis, Enric; Montemayor, Diana et al. (2025). *Global patterns and
drivers of seagrass biomass, net primary production and meadow structure* [Dataset].
Dryad, DOI 10.5061/dryad.kprr4xhh1 (accessed 2026-11-15; `source.version` = `2025`, the
Dryad year, because the source states no version; license not stated in the files).
A meta-analysis table: 5,903 rows (one per study x site x genus x year), 463 distinct
`Reference` strings, 1,131 locations (after the label split below), sampled 1972-2022,
12 seagrass genera plus `Mixed`, coastal and estuarine, all bioregions.

**Facts:** 12,980, one per non-blank measurement cell, units as in the source headers
(`units_raw`):

| Attribute | Type / subfield | Units |
|---|---|---|
| `seagrass_biomass_above` / `_below` / `_total` | B2 `above_ground_biomass` / `below_ground_biomass` / `total_biomass` | g DW m-2 |
| `seagrass_shoot_density` | B3 `shoot_or_stem_density` | shoots m-2 |
| `seagrass_leaf_density` | B3 `leaf_density` (extension) | leaves m-2 |
| `seagrass_percent_cover` | B3 `percent_cover` | `%` (kept as a percentage) |
| `seagrass_production_above` / `_below` | B6 `above_ground_npp` / `below_ground_npp` | g DW m-2 d-1 |
| `seagrass_production_total` / `_shoot` / `_leaf` | B6 `total_npp` / `shoot_production` / `leaf_production` (extensions) | g DW m-2 d-1 |

The `seagrass_` prefix keeps these apart from other compilations' biomass-like
attributes. Production is per day here and carried as reported. `habitat` = seagrass
meadow, `setting` = estuary for `Geotype` Estuarine (Coastal -> blank). Overlap with
other seagrass compilations is never merged here; it is found downstream through
`reference_key` (DOI-based) and coordinates. No `sd`/`se`/`n`; no sediment depth
(`depth_notes` says so).

Things to know:

- **Bio-ORACLE environmental columns are not loaded.** The 19 `BO22_*` columns
  (sea-surface temperature, pH, PAR, nutrients, chlorophyll, salinity, O2, current
  velocity, primary productivity; 5,628 rows populated, 275 empty) are a gridded
  model/satellite climatology (2000-2014) looked up at each coordinate, not seagrass
  measurements. They are asserted present and listed in `extract_report.json` but
  create no facts. If added later they should be a distinct attribute family with a
  method row marking them as modelled; the dataset README's units for
  `BO22_ppmax/ppmean` (mmol m-3) should be checked against Bio-ORACLE's own
  documentation first.
- **No value limits.** Values are taken as is: no thresholds, no clipping. There are no
  zeros in the source (minimum 0.001), so absence cannot be told from "not measured".
- **Totals are partly derived upstream.** The source README says total = above + below
  "if not reported"; the data cannot distinguish reported from summed totals (734 rows
  have a total with no parts, 51 have both parts but no total). Totals are carried as
  given. Flag `total_ne_sum_of_parts` goes on all three facts of a biomass or production
  triple where above, below and total are all present and total differs from
  above+below by more than `--sum-rel-tol` relative and `--sum-abs-tol` absolute (run
  with 1e-3 / 1e-3: 981 facts). The flag is tolerance-sensitive (726 facts at 1e-2,
  1,473 at 1e-6), so treat it as a screen, not a verdict.
- **Reference keys.** `reference_key` = `doi:<doi>` when the DOI cell is a DOI (bare, or
  a doi.org URL; lower-cased, inner whitespace removed: four cells had stray spaces),
  otherwise `ref:<Reference string>` (the source's "Author et al. year" text). 467
  references are DOI-keyed and 91 text-keyed; 567 non-blank DOI cells are free-text
  citations (some with encoding damage) and fall back to the Reference string. One
  Reference string (`Hitchcock et al. 2017`) has both DOI and non-DOI rows, so it is two
  references; no merging is attempted. `citation_dim`: `bibliography_id` = the
  Reference string, `year` parsed from it, title/authors NULL.
- **Labels.** `habitat_type` = constant `seagrass` (derived from the dataset's scope);
  `geomorphic_setting` = `Geotype` (`Coastal` / `Estuarine`); `bioregion` = `Bioregion`
  (Short et al. 2007 bioregions per the source README); `marine_ecoregion` = `ECOREGION`
  (not documented in the source README; the names look like Marine Ecoregions of the
  World, which is presumed, not stated). The location key includes all three labels, so
  coordinates that carry conflicting labels (12 pairs differ in Geotype, 2 in Bioregion)
  stay separate locations.
- **Taxon.** `sample_dim.taxon` = `Genera` as given; `taxon_rank` = `genus`, or `mixed`
  for the label `Mixed` (104 source rows). An unknown genus raises.
- **Method info is absent:** one `method_dim` row per attribute, `method_status` =
  `method_id_null`, `params` = the source column.
- **Excluded:** 47 rows with none of the 11 measurement columns filled
  (`no_seagrass_measurement`, in `skip_report.jsonl`). 5,903 = 5,856 rows with facts + 47.
- **Flags:** `year_missing` (the source has `NA` in Year.of.sampling; 285 facts),
  `duplicate_source_row` (rows identical in every column except `Unique.ID`; 236
  facts), `total_ne_sum_of_parts`.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
