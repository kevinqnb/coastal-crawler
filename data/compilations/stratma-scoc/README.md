# stratma-scoc (SCOC database v1)

Stratmann, Soetaert, Wei et al. (2022), Dryad, DOI 10.5061/dryad.25nd083 (accessed 2026-11-15).
Global rates of **sediment community oxygen consumption** from the shore to the abyssal plain, mostly not coastal wetlands.
3,543 source rows (3 fully blank, skipped as `empty_row`) from 231 studies. Raw files: `SCOC_database_version_1.csv`
(measurements) and `List_of_studies_version_1.csv` (315 candidate studies with their screening outcome).

## Facts (3,907)

| Type | Attribute | Units | Facts |
|---|---|---|---|
| B5 | `scoc_tou` (total uptake) | `mmol O2 m-2 d-1` | 2,859 |
| B5 | `scoc_dou` (diffusive) | same | 650 |
| B5 | `scoc_aou` (advective) | same | 11 |
| B5 | `scoc_unspecified` (no flux type, 20 rows of one study) | same | 20 |
| G1 | `scoc_median_grain_size` | `um` | 367 |

The three uptake attributes are different quantities, set by the source's flux-type column. There is one method row per study.

## How it maps

- **Locations:** `Region` is `site_name`, `Ocean` is `water_body`, depth is `water_depth_m` (positive down). The `Depth range`
  label is the raw `habitat_type` (it is really a depth class) and `Location` (exact or approximated) is `position_method`.
- **Samples:** sediment type and the photosynthetic-sediment label go to the sample. There is no date and no sediment depth interval.
- **Methods:** the device is `technique`; in/ex situ and light/dark are raw labels. The source has no method id, so
  `matched` means device and in/ex situ are present and `method_id_null` means both are NA.
- **Standard labels:** every `Depth range` label gives habitat "Subtidal soft sediment" (the dataset is sediment). Setting is
  shelf for the continental-shelf label, open ocean for the abyssal label, and blank for near-shore and slope.
- **Longitude** mixes 0 to 360 and -180 to 180. Values above 180 are wrapped (2 rows, flag `lon_wrapped`).
- **References:** the data file and the study list share no id. 215 of 231 reference strings match a list entry after
  ignoring case and punctuation. The other 16 are resolved by an explicit `CROSSWALK` in `extract.py`, and anything
  unresolved raises. `reference_key` is `study_<row in the list file>`, joined with `+` when a cell cites two studies.
- **`study_screening_dim`** holds all 315 list entries. 231 link to a reference and 84 (excluded studies) do not.

## Flags

`habitat_from_scope` (all 3,907 facts), `zero_value` 15, `lon_wrapped` 2, `coords_missing` 4, `coords_approximate` 800,
`depth_missing` 38, `method_missing` 20, `flux_type_missing` 20, `duplicate_source_row` 62 (every member of a group of
identical rows), `study_marked_excluded` 1 (Smith and Teal 1973, which the list marks as not in the database).
These counts are for the 3,540 oxygen facts. Rows that agree on station and value but differ elsewhere may be real
replicates and are not flagged.

## Things to know

- `sd`, `se` and `n` are NULL. `not investigated` is kept as a raw label, which is different from NULL (not stated).
- Authors and publication type are NULL. Year and DOI come from the citation string.

Back to the [compilations index](../README.md).
