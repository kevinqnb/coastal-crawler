# stratma-scoc (SCOC database v1)

Stratmann, Soetaert, Wei et al. (2022), Dryad, DOI 10.5061/dryad.25nd083 (accessed
2026-11-15). Global marine **sediment community oxygen consumption** rates: 3,543 source
rows (3 fully blank, excluded as `empty_row`), 231 studies, from the shore to the abyssal
plain; mostly not coastal wetlands. Raw files: `SCOC_database_version_1.csv`
(measurements) and `List_of_studies_version_1.csv` (315 candidate studies and their
screening outcome).

**Facts:** 3,907.
- B5, 3,540 oxygen-uptake facts, units `mmol O2 m-2 d-1` as reported. The attribute is
  set by the source's flux-type column; the three are different quantities:
  `scoc_tou` (total, 2,859; subfield `total_o2_uptake`), `scoc_dou` (diffusive, 650;
  `diffusive_o2_uptake`), `scoc_aou` (advective, 11; `advective_o2_uptake`, an
  extension subfield), plus `scoc_unspecified` (20 rows of one study with no flux type;
  NULL subfield; flag `flux_type_missing`).
- G1, 367 `scoc_median_grain_size` facts (`um`; subfield `median_grain_size`). No method
  information: one method row per study. Flags are the station/row flags of the row,
  not method or flux flags. The flag counts below are for the 3,540 oxygen-uptake facts.

Things to know:

- **Mapping onto the contract:** `Region` -> `site_name`; `Ocean` -> `water_body`;
  `Depth range` label -> `habitat_type` (raw; it is a depth class); `Location` label
  (exact / approximated) -> `position_method`; depth -> `water_depth_m` (m, positive
  down; `elevation` is NULL); device -> `method.technique`; in/ex situ and dark/light
  as raw labels; sediment type and photosynthetic-sediment label -> `sample` columns.
  Country is not parsed from `Region`. `sd`/`se`/`n` NULL; no dates (`sampling_year`
  NULL); no sediment depth interval (the flux is for the whole community).
- **Standard labels:** every `Depth range` label -> habitat subtidal soft sediment
  (dataset scope: SCOC is sediment); `setting` = shelf for the continental-shelf label,
  open ocean for the abyssal label, blank for near-shore and slope.
- **Longitude** mixes 0-360 and +-180 conventions: values above 180 are wrapped
  (exact decimal subtraction, flag `lon_wrapped`, 2 rows).
- **Raw labels are the source's own:** `not investigated` is kept (distinct from NULL =
  not stated).
- **References:** the data file and study list share no id. A data reference string
  matches a list entry on its case- and punctuation-insensitive form (215 of 231);
  the other 16 are resolved by an explicit `CROSSWALK` in `extract.py` (typos such as
  "accessed"/"assessed", dropped initials, a journal-name variant, and two cells citing
  two studies, which become one reference `study_a+study_b`). `reference_key` =
  `study_<0-based row of the list file>` (joined with `+`). Anything unresolved raises.
  Authors/publication type are NULL (no parsing); year and DOI come from the citation
  string by regex.
- **`study_screening_dim`** holds all 315 list entries with their raw screening labels;
  231 link to a reference, 84 (excluded studies) do not. One data row cites a study the
  list marks as not in the database (Smith & Teal 1973, `study_251`): kept, flag
  `study_marked_excluded`.
- **Flags:** `zero_value` (15), `lon_wrapped`, `coords_missing` (4), `coords_approximate`
  (800), `depth_missing` (38), `method_missing` (20), `flux_type_missing` (20),
  `duplicate_source_row` (every member of a group of fully identical rows, 62 rows),
  `study_marked_excluded` (1), `lat_lon_zero` (0 rows in v1). Rows that agree on station
  and value but differ in some other column may be real replicates and are not flagged.
- **`method_status`:** the source has no method id; `matched` = device and in/ex situ
  present, `method_id_null` = both NA.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
