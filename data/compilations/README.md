# Compilations

Raw, pre-existing coastal-ecosystem data compilations (not extracted from papers by
a model), cleaned into a common set of **extraction records** and joined into one
DuckDB star schema, `compilations.duckdb`. Distinct from the literature warehouse
(`data/warehouse.duckdb`): compilation values are curated source values, not model
output, and are never mixed into the literature tables. Joining the two is a
deliberate downstream step.

## Layout

```
data/compilations/
  README.md                      this file
  compilations.duckdb            built output (ignored by git)
  <compilation>/
    <raw files>                  as downloaded (ignored by git)
    extract.py                   the only tracked file; raw files -> parquet
    extracted/                   extract.py output (ignored by git)
      source|reference|citation|study_screening|location|sample|method|fact.parquet
      skip_report.jsonl          rows excluded, with reason
      extract_report.json        counts, flag counts, method coverage
```

Code: `src/coastal_crawler/compilations.py` (table contract, validation, key
assignment, DuckDB write), `scripts/build_compilations.py` (CLI),
`tests/test_compilations_ccn.py`.

## Workflow

```bash
# 1. per compilation (writes <compilation>/extracted/)
python data/compilations/coastal-carbon-library/extract.py \
    --access-date YYYY-MM-DD --max-dry-bulk-density 3.0

# 2. central build: validates every extracted dir, assigns global ids, writes the file
python scripts/build_compilations.py --out data/compilations/compilations.duckdb \
    data/compilations/coastal-carbon-library/extracted [other extracted dirs...]
```

The build is a **full rebuild**, written to a temp file and atomically renamed; a
rebuild from the same inputs gives the same table contents (ids are assigned in
sorted natural-key order, so input order does not matter). Anything that violates the
contract raises; there is no skip-and-log path in the builder. The only exclusions
happen in a compilation's own `extract.py` and are written to its `skip_report.jsonl`
with a reason from an allow-list (an unlisted reason raises).

## Extraction records

One **fact** (`measurement_fact`) = one variable measurement at one core and depth
interval. A compilation row holding three variables becomes three facts. Context is
in the dimensions:

| Table | Grain | Holds |
|---|---|---|
| `measurement_fact` | one measurement | `attribute`, `value` (DOUBLE, exactly as reported), `units_raw`, `sd`, `se`, `n`, `flags` (VARCHAR[]), `source_row_ref`; FKs to the five dims below |
| `source_dim` | compilation | name, version, DOI, access date, license, notes |
| `reference_dim` | contributing study | `reference_key` (the compilation's own study id) |
| `citation_dim` | study x citation | bibliography id, publication type, DOI, title, authors, year, URL |
| `study_screening_dim` | candidate study | citation string and the compilation's own screening labels (`screening_phase`, `eligibility_phase`, `database_status`); `reference_id` is NULL for studies that contributed no facts (the only nullable FK). Empty for CCN. |
| `location_dim` | core / site | lat, lon, position accuracy and method, site name, country, `water_body` (ocean/sea; open-water compilations), `habitat_type`, `geomorphic_setting`, `salinity_class`, `vegetation_class`, `tidal_position` (each with a `*_level` of core vs site), `modification_status[]`, `dominant_species[]`, elevation + units + datum + method |
| `sample_dim` | depth interval | depth min/max (cm), representative depths, notes, sampling year/month, `vegetated_or_bare`, `marsh_zone`, `sediment_type` (raw), `median_grain_size_um`, `photosynthetic_sediment` (raw) |
| `method_dim` | (study, method id, attribute) | `technique`, coring method, carbon type, measured/modeled, carbon method, carbonate removal, `conversion_applied`, in-situ/ex-situ, light/dark, `method_status`, `params` (JSON text) |

Conventions that hold for every compilation:

- **Values are carried as reported.** No unit conversion, no cleaning. Units are the
  compilation's own (`units_raw`), taken from its documentation where it has one.
- **Standardisation is limited to what is needed to put compilations side by side.**
  Allowed, and written in the compilation's `extract.py` docstring and below: sign/unit
  changes for coordinates and elevation (longitude in [-180, 180], elevation in metres,
  negative below the surface/water level; the raw value stays recoverable via
  `source_row_ref`), whitespace stripping, and mapping the source's "NA"/blank to NULL.
  The measured value, its units and categorical labels are never converted.
- **Suspicious values are flagged, never changed or dropped.** Flags are a list on the
  fact (empty list = none). Cleaning decisions stay downstream. Filter on `flags`
  before any analysis.
- **Labels are raw as reported.** Controlled-vocabulary fields (salinity, habitat,
  vegetation) are whatever the source says; they are *not* mapped onto Venice classes
  or any shared scheme yet. Fields a compilation does not have are NULL
  (`geomorphic_setting`, `in_situ_ex_situ`, `light_dark`, `sd`, `se`, `n` for CCN).
- **Derived fields have one written rule** (in the compilation's `extract.py`
  docstring) and the raw value sits next to the derived one.
- **Provenance:** `source_row_ref` is `<file>#data_row=<0-based data row>:<attribute>`;
  it indexes the raw file as read by pandas (not a line number, because quoted
  newlines exist). Every fact in the build was round-tripped to its raw row.
- `method_dim.method_status` says how method information was obtained:
  `matched`, `method_id_null`, `method_id_unmatched`, or
  `ambiguous_duplicate_method_id`. Anything but `matched` means the technique fields
  are NULL because they are unknown, not because they were absent by design.

## Compilations

### coastal-carbon-library (Coastal Carbon Network Data Library v1.7.0)

Smithsonian Coastal Carbon Network, DOI 10.25573/serc.21565671. Tidal-wetland soil
carbon profiles: 16,715 cores, 117,518 depth intervals, ~550 studies; mostly marsh
and mangrove, some seagrass. No in-situ water data and no geomorphic setting.

**Facts:** `dry_bulk_density` (g/cm3), `fraction_organic_matter` and `fraction_carbon`
(dimensionless): 217,099 facts. Radiometric/dating columns, accretion and compaction
are out of scope for now.

Things to know:

- **`fraction_carbon` mixes quantities:** organic vs total carbon, measured vs modeled
  from loss on ignition. Check `method_dim.carbon_type`, `carbon_measured_or_modeled`,
  `conversion_applied`; 46% of carbon facts have unknown type (`carbon_type_unknown`).
- **Method info is partial:** only 69% of facts have a matched methods row. The rest
  have NULL technique fields (see `method_status`).
- **Filter on `flags` before analysis:** values include bulk density from -81 to 11.3
  and organic matter above 1. Flags: `negative_value`, `fraction_gt_1`, `dbd_gt_max`
  (`--max-dry-bulk-density`, 3.0), `depth_missing`, `depth_inverted`,
  `duplicate_source_row`, `carbon_type_unknown`.
- **185 depth rows (277 measurements) excluded:** their (study, site, core) matches no
  core, mostly key typos (e.g. Osland_2016 site-name case). Suggested matches are in
  `skip_report.jsonl` and deliberately not applied.
- **Labels are CCN's own, not Venice classes.** Salinity/vegetation/tidal position use
  the core value, falling back to the site value only when unambiguous
  (`*_level` columns say which). `tidal_position` is CCN `inundation_class`;
  `marsh_zone` and `vegetated_or_bare` are derived by the rules in `extract.py`.
- **Sparse context:** month is 42% filled, elevation 17%. `sd`, `se`, `n` are NULL.
- **Provenance:** `reference_dim` = CCN `study_id`; studies can have several citations
  (see `citation_dim.publication_type`).

### scoc (SCOC database v1)

Stratmann, Soetaert, Wei et al. (2022), Dryad, DOI 10.5061/dryad.25nd083 (accessed
2026-11-15). Global marine **sediment community oxygen consumption** rates: 3,543 source
rows (3 fully blank, excluded as `empty_row`), 231 studies, from the shore to the abyssal
plain; mostly not coastal wetlands. Raw files: `SCOC_database_version_1.csv`
(measurements) and `List_of_studies_version_1.csv` (315 candidate studies and their
screening outcome).

**Facts:** 3,540, units `mmol O2 m-2 d-1` as reported. Attribute is set by the source's
flux-type column and the three are different quantities: `scoc_tou` (total, 2,859),
`scoc_dou` (diffusive, 650), `scoc_aou` (advective, 11), plus `scoc_unspecified` (20
rows of one study with no flux type; flag `flux_type_missing`).

Things to know:

- **Mapping onto the contract:** `Region` -> `site_name`; `Ocean` -> `water_body`;
  `Depth range` label -> `habitat_type`; `Location` label (exact / approximated) ->
  `position_method`; depth -> `elevation` = -depth (m, datum "water surface");
  device -> `method.technique`; in/ex situ and dark/light as raw labels; sediment type,
  median grain size (um) and photosynthetic-sediment label -> new `sample` columns.
  Country is not parsed from `Region`. `sd`/`se`/`n` NULL; no dates (`sampling_year`
  NULL); no sediment depth interval (the flux is for the whole community).
- **Longitude** mixes 0-360 and +-180 conventions: values above 180 are wrapped
  (exact decimal subtraction, flag `lon_wrapped`, 2 rows).
- **Labels are the source's own:** `not investigated` is kept (distinct from NULL = not
  stated); `Depth range` / photosynthetic / dark-light vocabularies are not mapped.
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

## Adding a compilation

1. Put the raw files in `data/compilations/<name>/` and add `extract.py` there.
2. `extract.py` builds the eight contract tables (columns and types in
   `coastal_crawler.compilations.TABLES`) with compilation-local `*_key` strings and
   calls `write_extracted(tables, out_dir)`; it must assert its own row accounting
   (every source measurement is either a fact or a listed exclusion).
3. Follow the ladder: unit tests on a hand-built fixture, a real-data run, a
   one-study end-to-end run with a predicted count, then the full build and a
   source-row round-trip check.
4. Note any new vocabulary, units or derived-field rules in this file and in the
   `extract.py` docstring. The `.gitignore` already tracks `data/compilations/*/extract.py`.
5. Extraction speed: the write steps insert row by row (~5 min for 217k facts);
   switch to a bulk load before adding many large compilations.
