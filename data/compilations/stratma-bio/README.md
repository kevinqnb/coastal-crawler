# stratma-bio (BenBioDen: benthic biomass and density)

Stratmann, T., van Oevelen, D., Martinez Arbizu, P. et al. (2021). *The BenBioDen database, a global database for meio-, macro-
and megabenthic biomass and densities.* Dryad, DOI 10.5061/dryad.gb5mkkwm6 (paper: *Scientific Data* 2020).

- **Version:** Dryad version 4 (2021-06-07), from the Dryad record. **Access date:** 2026-11-15. **License:** CC0-1.0.
- **Raw files** (Windows-1252): `BenBIO_database.csv` (11,792 rows), `BenDEN_database.csv` (51,559 rows), and one study list per
  database (`List_of_studies_for_BenBio_database.txt`, `..._BenDen_database.txt`).

A global compilation of meio-, macro- and megabenthos from the shore to the abyssal plain, mostly not coastal wetlands. One row is
one community value at a station and size class. It follows the same design as `stratma-scoc` and reuses its code.

## Facts (63,350)

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `benbio_biomass` | B10 `total_biomass` | 11,792 | per-row `unit` column: `g WM/ m2` 7,051, `g DM/ m2` 2,715, `g C/ m2` 1,115, `g AFDM/ m2` 911 |
| `benden_density` | B10 `total_density` | 51,558 | `ind/ m2` |

Biomass comes in four bases (wet mass, dry mass, carbon, ash-free dry mass). It is one attribute with the unit on every fact, so
filter on `units_raw` before comparing. Nothing is converted. 1,677 biomass rows are `converted data` (the authors converted them
from another basis).

## How it maps

- **Locations:** `Region` is `site_name`, `Ocean` is `water_body`, `Location` (exact or approximated) is `position_method`, depth is
  `water_depth_m`, and `Depth range` is the raw `habitat_type`. The key includes reference, region, ocean, coordinates, depth and both
  labels, so a station shared by the two files is one location (44,058 in all).
- **Standard labels:** every `Depth range` label gives habitat "Subtidal soft sediment" (dataset scope). Setting is shelf for the
  shelf label, open ocean for the abyssal label, and blank for near-shore and slope.
- **Blank depth range:** a row whose `Depth range` is `NA` gets no habitat and no `habitat_from_scope` flag (3,346 biomass and
  38,345 density rows). The depth number is not used to infer a label, so 74% of the density facts have a NULL habitat.
- **Samples:** one per row. `taxon` is `Benthos` (macro, meio or megabenthos), rank `size_class`. No date and no sediment depth.
- **Methods:** one per (reference, attribute, gear, sieve limits, raw or converted, conversion reference), 1,125 in all. `technique`
  is the gear. Sieve limits (for example `0.300 and 0.500`) and the conversion reference stay as text in `params`. 63 methods have no
  gear stated and are `method_id_null`. `sd`, `se` and `n` are NULL.
- **References:** `ref:<Reference string>`, the same key in both files. 777 references, 206 cited in both. 37 strings combine several
  citations and stay one reference each, because the split is not reliable. `citation_dim` holds the string, plus a year and (for 43) a DOI.

## Study lists

Each file is resolved against its own list, because the screening labels differ. `study_screening_dim` holds all 3,531 non-blank list
rows with their raw labels. 974 link to a reference (382 BenBio, 592 BenDEN). The rest are cited by no data row, or only inside combined
strings.

Strings match a list entry after ignoring case and punctuation, and otherwise through an explicit `CROSSWALK` of 17 prefixes
(encoding loss, accents, journal abbreviations, typos, and two papers whose list year differs from the paper). Anything else must be
declared `UNRESOLVED` or the extract raises. Four references have no list entry (Gilkinson 2005, Gilkinson 2013, Wei 2019 with Archambault,
and Bluhm HLY-05-02) and keep their reference.

One BenDEN entry (Relexans, *Oceanol. Acta* 15) is marked excluded but cited by 30 rows. It is kept with `study_marked_excluded`. Eight
included BenDEN entries are cited by no row.

## Flags (BenBIO / BenDEN)

`habitat_from_scope` 8,446 / 13,213, `depth_missing` 3,613 / 39,026, `method_missing` 5,456 / 39,408, `coords_approximate` 1,486 / 2,201,
`coords_missing` 19 / 19, `zero_value` 73 / 112, `duplicate_source_row` 143 / 1,255 (every member of a group of identical rows; some
may be real replicates), `converted_by_source` 1,677, `study_marked_excluded` 0 / 30.
BenDEN row 48707 (station `North Pole`) has latitude 90.012. It is kept and flagged `lat_beyond_pole`, and the extract raises on any other
latitude outside the range.

## Row accounting

BenBIO 11,792 rows = 11,792 facts. BenDEN 51,559 rows = 51,558 facts + 1 row with no density (data row 1810, `no_density`). No other
column is dropped. All 63,350 facts were checked back to their raw row, and two runs give identical tables.

## Overlap

SCOC (same authors): 19 reference texts and 2 DOIs are shared, but the variables differ. Join downstream by DOI and coordinates. No
overlap with CCN or the seagrass compilations by DOI.

Back to the [compilations index](../README.md).
