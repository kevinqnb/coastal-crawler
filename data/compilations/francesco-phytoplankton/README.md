# francesco-phytoplankton (global marine phytoplankton production and chlorophyll profiles)

Mattei, F., Scardi, M. (2021). *Global marine phytoplankton production dataset.* PANGAEA, DOI 10.1594/PANGAEA.932417 (paper: *Earth System
Science Data* 13, 4967-4985, DOI 10.5194/essd-13-4967-2021).

- **Version:** `PANGAEA 932417 (published 2021-06-09)`, copied from the dataset page along with the license, since the file has no PANGAEA
  header. **Access date:** 2026-11-15. **License:** CC-BY-4.0.
- **Raw file:** `Global_marine_phytoplankton_production_dataset.txt` (tab-separated, 37,722 rows, 49 columns).

Depth-resolved primary production and chlorophyll a profiles, 1958 to 2017: 6,084 profiles, 204 `Event` labels (original cruises or programmes)
and 34 `Short reference` strings. One row is one sampling depth of a profile.

## Facts (99,779)

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `francesco_pp_volumetric` | B8 `primary_production_per_volume` | 37,722 | mg C m^-3 day^-1 |
| `water_chl_a` | J1 `chlorophyll_a` | 37,721 | mg m^-3 |
| `francesco_pp_integrated` | B8 `depth_integrated_primary_production` | 6,084 | mg C m^-2 day^-1 |
| `francesco_pbopt` | B8 `chlorophyll_specific_production` | 6,084 | mg C mg Chla-1 h-1 |
| `water_chl_a_integrated` | J1 `chlorophyll_a` | 6,084 | mg m^-2 |
| `water_chl_a_total` | J1 `chlorophyll_a` | 6,084 | mg m^-2 |

Units are the header spellings. The plan put the two chlorophyll integrals under one attribute, but they differ on every profile, so one attribute
would give 6,084 false conflicts. They are separate attributes.

## Profile-level values are loaded once per profile

Four columns (integrated production, `Pbopt` and the two chlorophyll integrals) repeat on every row of a profile. They are loaded once per
profile (24,336 facts), and the extract asserts they are constant within the profile. The 126,552 repeats are counted as collapsed. These facts
sit on a profile sample (no depth) at the station of the profile's first row. Eight profiles span two stations, and `depth_notes` says so.

## How it maps

- **Habitat** is Water column for every location (from scope, `habitat_from_scope` on all facts). 3,635 locations, keyed on coordinates, with no
  depth.
- **Water chemistry (J1):** the sample is profile, depth, date and coordinates. Profile 1711 at 3 m has two different chlorophyll values (12.1 and
  10.4), both loaded with `covariate_conflict`. Profile 2282 at 25 m has equal values, collapsed into one.
- **Temperature is not loaded.** The file's `SST` is a sea-surface value, not the temperature at the sampling depth, so `water_temperature_c`
  stays NULL.
- **Samples:** `Sampling depth` is `sample_depth_m`. The date gives year and month. `Event` is `source_sample_id`.
- **Longitude** below -180 (one profile, down to -188.5) is wrapped by +360 and flagged `lon_wrapped` (14 facts).
- **Methods:** none stated, so one `method_id_null` row per attribute (6).
- **References:** `Short reference` names the source the compilers took a profile from, and many are databases, not papers. The key is
  `doi:<doi>` where `Paper doi` is given, otherwise `ref:<string>`: 34 references and 35 citations. `Kudela et al. 2015` carries two DOIs and
  becomes a combined key with two citations. **Every fact carries `secondary_compilation`**, because no per-string list of databases exists.

## Not loaded

`SST`, `surface PAR`, `Day length`, `Mixed Layer Depth`, `Euphotic zone depth`, `Bottom depth` (GEBCO), `Distance from coastline` (a grid),
`Production to biomass ratio` (derived), the hemisphere and season labels, and every `* magnitude` class.

## Flags

`habitat_from_scope` and `secondary_compilation` 99,779, `zero_value` 1,455, `lon_wrapped` 14, `covariate_conflict` 2.

## Row accounting

226,332 cells = 99,779 facts + 126,553 collapsed repeats, nothing skipped. All facts were checked back to the raw file by an independent
parse. A one-study check (Imai 2008) predicted 64 facts and matched, and two runs give identical output.

## Overlap

Estuarine production may overlap `cloern-phytoplankton` through regional studies. None checked.

Back to the [compilations index](../README.md).
