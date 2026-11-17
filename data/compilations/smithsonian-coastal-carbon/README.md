# smithsonian-coastal-carbon (Coastal Carbon Network Data Library v1.7.0)

Smithsonian Coastal Carbon Network, DOI 10.25573/serc.21565671. Tidal-wetland soil carbon profiles:
16,715 cores, 117,518 depth intervals, about 550 studies. Mostly marsh and mangrove, some seagrass.
It has no in-situ water data and no geomorphic setting.

## Facts (217,181)

| Type | Attribute | Units | Facts |
|---|---|---|---|
| C1 | `dry_bulk_density` | g/cm3 | part of 217,099 |
| C1 | `fraction_organic_matter`, `fraction_carbon` | fraction | part of 217,099 |
| G2 | `pb210_cic_accretion_rate` | `centimeterPerYear` | 82 |

- The accretion rate is one fact per core, with no depth interval. Its method is the 210Pb
  constant-initial-concentration model (`method_status` = `method_id_null`); max depth, r2 and notes are in `params`.
- Its SE column is documented in `millimetersPerYear`. It is carried unconverted in `se` with the flag
  `se_units_differ` (43 facts).

## How it maps

- **Locations and samples:** salinity, vegetation and tidal position use the core value, falling back to the site value
  when unambiguous (the `*_level` columns say which). `tidal_position` is CCN's `inundation_class`.
- **References:** `reference_dim` is CCN's `study_id`. A study can have several citations.
- **Standard labels:**
  - Habitat: `marsh`, `mangrove`, `seagrass`, `mudflat` map to tidal marsh, mangrove forest, seagrass meadow, tidal flat.
    `swamp`, `scrub/shrub`, `upland`, `sabkha`, mats and `unvegetated` stay blank.
  - Salinity zone: `fresh` to `brine` map to limnetic to hyperhaline. `saline`, `brackish`, `mixoeuhaline` and ranges stay blank.
  - Setting: `estuarine` and `deltaic` map to estuary and river delta.
  - Modification: `natural`; `restored`, `tidally restored`, `revegetated` map to restored; `wetlands built` to created;
    everything else is blank (no rule gives `artificial`).

## Not loaded

Other radiometric and dating columns, and compaction.

## Flags

`negative_value`, `fraction_gt_1`, `dbd_gt_max` (above 3.0 g/cm3), `depth_missing`, `depth_inverted`,
`duplicate_source_row`, `carbon_type_unknown` (46% of carbon facts), `se_units_differ`.
Bulk density runs from -81 to 11.3 and organic matter goes above 1, so filter on `flags` before analysis.

## Row accounting

185 depth rows (277 measurements) are excluded because their (study, site, core) matches no core, mostly key typos.
Suggested matches are in `skip_report.jsonl` and were not applied.

## Things to know

- **`fraction_carbon` mixes quantities** (organic vs total carbon, measured vs modeled from loss on ignition).
  The subfield follows the method row's `carbon_type`; otherwise it is `carbon_unspecified`.
- **Method information is partial:** 69% of facts have a matched method row. The others have NULL technique fields.
- **Sparse context:** month is 42% filled and elevation 17%. `sd`, `se` and `n` are NULL.
- `modification_status` leaves out blank `impact_class` rows.

Back to the [compilations index](../README.md).
