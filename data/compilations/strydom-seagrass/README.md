# strydom-seagrass (Strydom et al., global seagrass database)

Strydom, S., Webster, C.L., McCallum, R. et al. (2021). *Global database of key seagrass structure, biomass and production
variables.* PANGAEA, DOI 10.1594/PANGAEA.929968. Supplement to Strydom et al. (2023), *Earth System Science Data* 15, 511-519,
DOI 10.5194/essd-15-511-2023.

- **Version:** `2025-05-06`, the last change date in the file header (longitudes corrected from east to west). First
  published 2021-04-01. PANGAEA gives no version number.
- **Access date:** 2026-11-15. **License:** CC-BY-4.0 (from the file header).
- **Raw file:** `Seagrass_db.tab` (a 409-line header with the citation and 379 related citations, then a tab-separated table).

The same design as Foster: 6,658 rows (study, site, genus, year), 405 distinct `Reference` strings, 12 genera, six bioregions,
848 locations (548 Coastal, 300 Estuarine), sampled 1972 to 2019.

## Facts (14,266)

One fact per non-blank measurement cell. Attributes, types and subfields are Foster's; only the unit spelling differs.
The plan quoted 14,271 values, but this file has 14,266.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `seagrass_biomass_above`, `_below`, `_total` | B2 above, below, total biomass | 3,386 / 2,008 / 2,696 | g/m**2 |
| `seagrass_shoot_density` | B3 `shoot_or_stem_density` | 3,209 | #/m**2 |
| `seagrass_leaf_density` | B3 `leaf_density` | 146 | #/m**2 |
| `seagrass_percent_cover` | B3 `percent_cover` | 1,285 | `%` |
| `seagrass_production_above`, `_below` | B6 above, below ground NPP | 265 / 125 | g/m**2/day |
| `seagrass_production_total`, `_shoot`, `_leaf` | B6 total, shoot, leaf production | 258 / 151 / 737 | g/m**2/day |

PANGAEA's names say dry mass. Unit spellings are not merged with Foster's (`g DW m-2` vs `g/m**2`), so comparing the two is a
downstream conversion.

## How it maps

- **Locations:** coordinates are the source's decimal degrees. `habitat_type` is the constant `seagrass` (habitat "Seagrass meadow").
  `Habitat` is `geomorphic_setting` (Estuarine gives estuary, Coastal stays blank) and `Seagr bioregion` is `bioregion`. The location
  key includes both labels.
- **Samples:** one per source row. `taxon` is `Genus` (rank `genus`) and `sampling_year` is `Year obs`.
- **Methods:** none stated. One method row per attribute (11), `method_id_null`. `sd`, `se` and `n` are NULL.
- **References:** `ref:<Reference string>`. The rows cite `Author year` text only, so 400 strings give 400 references and citations.
  Each string is matched to one of the 379 header citations by author and year (no fuzzy matching, nothing by hand).

| Result | Strings |
|---|---|
| matched (312 of them to an entry with a DOI) | 332 |
| ambiguous (Cullain 2018, Namba 2018, Samper-Villarreal 2018) | 3 |
| no header entry | 65 |

Unmatched strings keep only the text and a year, and are listed in `extract_report.json`. 22 of the 65 are a source defect:
`Lamit and Tanaka 2020` to `2033` and `Jensen and Gibson 1987` to `1994` count up one year per row, as a spreadsheet auto-fill would.
They stay separate references. Three strings contain a replacement character that is in the source file, and are kept as given.

## Not loaded

- 32 rows with none of the 11 measurements (see below).
- The header's other content, and the full citation text of matched entries (`citation_dim.title` is NULL).
- 5 reference strings that occur only in the 32 excluded rows have no reference row.

## Flags

`habitat_from_scope` 14,266 (all), `duplicate_source_row` 283 (every member of a group of identical rows; some may be genuine
replicates), `total_ne_sum_of_parts` 1,077 (same 1e-3 tolerances as Foster; a screen, not a verdict), `year_missing` 13.

## Row accounting

6,658 rows = 6,626 with facts + 32 with none (`no_seagrass_measurement`). 14,266 cells = 14,266 facts. Every fact was checked
back to its raw cell, and two runs give identical tables.

## Overlap

Foster has the same design and an overlapping literature: 291 of the 400 reference strings, and 247 of the 310 DOIs here, also occur
in Foster. Shared papers can report different values (this file ends in 2019, Foster's in 2022). Join by DOI and coordinates. This
`extract.py` imports Foster's at load time, so editing Foster's can change this one.

Back to the [compilations index](../README.md).
