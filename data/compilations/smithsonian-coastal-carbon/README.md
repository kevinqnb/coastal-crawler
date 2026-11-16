# smithsonian-coastal-carbon (Coastal Carbon Network Data Library v1.7.0)

Smithsonian Coastal Carbon Network, DOI 10.25573/serc.21565671. Tidal-wetland soil
carbon profiles: 16,715 cores, 117,518 depth intervals, ~550 studies; mostly marsh
and mangrove, some seagrass. No in-situ water data and no geomorphic setting.

**Facts:** 217,181.
- C1, 217,099 facts: `dry_bulk_density` (g/cm3), `fraction_organic_matter` and
  `fraction_carbon` (dimensionless).
- G2, 82 core-level facts: `pb210_cic_accretion_rate` (`centimeterPerYear`) for the
  cores that report one. No depth interval; one method row per core (`technique` =
  210Pb constant-initial-concentration model, `method_status` = `method_id_null`; max
  depth, r2 and notes in `method.params`). Its SE column is documented in a different
  unit (`millimetersPerYear`): carried unconverted in `fact.se` with flag
  `se_units_differ` (43 facts). Other radiometric/dating columns and compaction are not
  loaded.

Things to know:

- **`fraction_carbon` mixes quantities:** organic vs total carbon, measured vs modeled
  from loss on ignition. `fact.subfield` separates them by the method row's
  `carbon_type` (`organic_carbon`, `total_carbon`, else `carbon_unspecified`, which
  also covers "not specified" and unmatched methods). Check `method_dim.carbon_type`,
  `carbon_measured_or_modeled`, `conversion_applied`; 46% of carbon facts have unknown
  type (`carbon_type_unknown`).
- **Method info is partial:** only 69% of facts have a matched methods row. The rest
  have NULL technique fields (see `method_status`).
- **Filter on `flags` before analysis:** values include bulk density from -81 to 11.3
  and organic matter above 1. Flags: `negative_value`, `fraction_gt_1`, `dbd_gt_max`
  (`--max-dry-bulk-density`, 3.0), `depth_missing`, `depth_inverted`,
  `duplicate_source_row`, `carbon_type_unknown`, `se_units_differ`.
- **185 depth rows (277 measurements) excluded:** their (study, site, core) matches no
  core, mostly key typos (e.g. Osland_2016 site-name case). Suggested matches are in
  `skip_report.jsonl` and deliberately not applied.
- **Raw labels are CCN's own.** Salinity/vegetation/tidal position use the core value,
  falling back to the site value only when unambiguous (`*_level` columns say which).
  `tidal_position` is CCN `inundation_class`; `marsh_zone` and `vegetated_or_bare` are
  derived by the rules in `extract.py`.
- **Sparse context:** month is 42% filled, elevation 17%. `sd`, `se`, `n` are NULL.
- **Provenance:** `reference_dim` = CCN `study_id`; studies can have several citations
  (see `citation_dim.publication_type`).
- **Standard labels:**
  - Habitat: `marsh`, `mangrove`, `seagrass`, `mudflat` map to tidal marsh, mangrove
    forest, seagrass meadow, tidal flat. `swamp`, `scrub/shrub`, `upland`, `sabkha`,
    mats and `unvegetated` have none (blank, raw kept).
  - Salinity zone: `fresh`, `oligohaline`, `mesohaline`, `polyhaline`, `brine` map to
    limnetic through hyperhaline. `saline`, `brackish`, `mixoeuhaline` and ranges are
    blank.
  - Setting: `estuarine` and `deltaic` (from `salinity_class`) map to estuary and river
    delta.
  - Modification: `natural`; `restored`, `tidally restored`, `revegetated` -> restored;
    `wetlands built` -> created; everything else (ditched, impounded, farmed,
    degraded, ...) is blank, since no rule yields `artificial`.
- `modification_status` omits blank `impact_class` rows (they would otherwise become
  the string `nan`).

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
