# francesco-phytoplankton (global marine phytoplankton production and chlorophyll profiles)

Mattei, F., Scardi, M. (2021). *Global marine phytoplankton production dataset* [Dataset]. PANGAEA,
https://doi.org/10.1594/PANGAEA.932417 (paper: *Earth System Science Data* 13, 4967-4985, https://doi.org/10.5194/essd-13-4967-2021).

- **Version:** `PANGAEA 932417 (published 2021-06-09; no version number)`: the file has no PANGAEA header, so version and license are copied
  from the dataset page (INTEGRATION_PLAN 1.4).
- **Access date:** 2026-11-15. **License:** CC-BY-4.0 (PANGAEA page).
- **Raw file:** `Global_marine_phytoplankton_production_dataset.txt` (tab-separated, 37,722 data rows x 49 columns, quoted fields; the
  `Short reference` of 14,300 rows carries a CR LF inside its quotes).

A compilation of depth-resolved primary production and chlorophyll a profiles, 1958-2017, 6,084 profiles (the file's `Profile number` is
unique, asserted), 204 distinct `Event` labels after whitespace normalisation (original cruises or programmes; blank on 850 rows), 34 `Short reference` strings. One data row is one
sampling depth of a profile.

**Facts:** 99,779.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `francesco_pp_volumetric` | B8 `primary_production_per_volume` | 37,722 | mg C m^-3 day^-1 |
| `water_chl_a` | J1 `chlorophyll_a` | 37,721 | mg m^-3 |
| `francesco_pp_integrated` | B8 `depth_integrated_primary_production` | 6,084 | mg C m^-2 day^-1 |
| `francesco_pbopt` | B8 `chlorophyll_specific_production` | 6,084 | mg C mg Chla-1 h-1 |
| `water_chl_a_integrated` (Depth-integrated chl a) | J1 `chlorophyll_a` | 6,084 | mg m^-2 |
| `water_chl_a_total` (Total Chl a) | J1 `chlorophyll_a` | 6,084 | mg m^-2 |

Units are the header spellings; nothing is converted. Every numeric column holds numbers (no missing codes occur; anything else raises).

**Two chlorophyll integrals, two attributes.** The plan puts `Depth-integrated chl a` and `Total Chl a` under one attribute. They differ on every profile (none of
the 6,084 is equal), so under one attribute each profile would carry a meaningless `covariate_conflict`. They are `water_chl_a_integrated` and `water_chl_a_total`.

## Profile-level values are loaded once per profile

`Depth-integrated primary production`, `Pbopt`, `Depth-integrated chl a` and `Total Chl a` repeat on every row of a profile. They are loaded once per profile
(24,336 facts) and the extract asserts they are exactly constant within the profile (it raises otherwise); the 126,552 repeated cells are counted as collapsed.
The profile-level facts sit on a `profile<n>` sample (no sampling depth; `depth_notes` = `profile-level value; max sampling depth <x> m`) at the station of the profile's
**first row**; their `source_row_ref` points at that row. **Eight profile numbers span two stations** (different latitude, longitude or date): 2279, 2797, 4487, 4488,
5084, 5123, 5136, 5137: the profile-level facts of these sit at the first row's station, and `depth_notes` says so.

## J1: one fact per sample and attribute

The row-level sample key is profile | sampling depth | date | latitude | longitude (as written). Two pairs of rows share a key: profile 1711 at 3 m has two chlorophyll values (12.1 and
10.4 mg m^-3) and both are loaded with `covariate_conflict` (2 facts); profile 2282 at 25 m has equal values (0.78), collapsed into one fact (1 collapsed). Primary production
is not J1, so all 37,722 production facts load (the two duplicated samples give two production facts each). 49,890 J1 cells = 49,889 facts + 1 collapsed.

## Context and mapping

- **Habitat** `Water column` for every location (raw `habitat_type` constant `water column`), from the dataset's scope: flag `habitat_from_scope` on all facts. 3,635 locations
  (`location_key` = latitude | longitude as written); no depth (`Bottom depth` is a GEBCO value: derived, not loaded).
- **Temperature.** `SST` where `SST_flag` is 0 (in situ, 11,452 rows) -> the sample's `water_temperature_c`; flag 1 (reconstructed from MODIS / Copernicus, 26,270 rows)
  is not loaded. **SST is a sea-surface temperature**: following the plan (1.3) it is written to the temperature slot of every in-situ row, including samples taken below the
  surface, so for deep samples it is not the temperature at the sampling depth. Profile-level samples carry none.
- `Sampling depth` -> `sample_depth_m`; `Date` (dd/mm/yyyy, asserted equal to `Year` and `Month`) -> year and month; `Event` -> `sample.source_sample_id`.
- **Longitude** below -180 (5 rows of one profile, down to -188.5) is wrapped by +360 and flagged `lon_wrapped` (14 facts: 10 row-level, 4 profile-level).
- **Methods:** one `method_id_null` row per attribute (6): the file gives no method per row.
- **Not loaded:** `surface PAR` (both flags; phase 2: 15,092 in-situ rows), `Day length`, `Day of the year`, `Mixed Layer Depth`, `Euphotic zone depth`, `Max production depth`,
  `Max sampling depth` (written into the profile samples' `depth_notes`), `Bottom depth` and its sd (GEBCO), `Distance from coastline` (NASA OBPG grid), `Production to biomass ratio`
  (derived, D7), `hemisphere`, `Northern hemisphere season` and every `* magnitude` category (Jenks classes).

## References

`Short reference` names the source the compilers took a profile from (many are databases, not papers: `calcofi.org`, `hahana.soest.hawaii.edu`, `Behrenfeld and Falkowski 1997`);
the original cruise is `Event`. `reference_key` = `doi:<lower-case doi>` where `Paper doi` is given (17,484 rows), else `ref:<short reference>`; **34 references, 35 citations**.
The 311 rows of `Kudela et al. 2015` carry two DOIs (`a; b`): composite key `doi:a+doi:b` and one citation per DOI. One DOI (`10.1029/2006gl026772`) is shared by `Kudela and Edwards 2012` and
`Kudela et al. 2012`: one reference, both strings in the citation's `bibliography_id`; the 24 `Kudela and Edwards 2012` rows with no DOI keep `ref:Kudela and Edwards 2012`. 11 citations carry a
DOI (`url` = the link). The file's mojibake in one string (`NÃ¶thig and Cornils 2019`) is kept as written. **Every fact carries `secondary_compilation`**: no per-string list
of which short references are databases exists, so the flag is blanket (the whole file is a compilation of compilations).

## Flags

`habitat_from_scope` and `secondary_compilation` 99,779, `zero_value` 1,455 (zero chlorophyll or production in a profile), `lon_wrapped` 14, `covariate_conflict` 2.

## Row accounting

226,332 cells (75,444 row-level and 150,888 profile-level occurrences) = 99,779 facts + 126,553 collapsed repeats (1 J1 sample repeat + 126,552 profile repeats); 0 skips.
After the central build every fact was checked against the raw file by an independent parse (value, unit, reference key, location, wrapped longitude, sample key and depth, temperature
rule, year, month, flags), every row's production cell is a fact, every profile has its four profile-level facts at its first row, and the profile-level cells are constant within every
profile: 99,779 of 99,779 agree. A one-study check (Imai et al. 2008, predicted by an independent parse as 2 x 24 rows + 4 x 4 profiles = 64 facts) matched. Adding this source left the other
eleven sources' content unchanged. Two runs of `extract.py` give byte-identical parquet and report. `source_row_ref` is `<file>#data_row=<0-based row>:<attribute>`.

## Overlap with other compilations

Estuarine production may overlap `cloern-phytoplankton` through regional studies; none checked. Never merged.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
