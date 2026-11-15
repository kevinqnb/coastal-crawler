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
      source|reference|citation|location|sample|method|fact.parquet
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
| `location_dim` | core / site | lat, lon, position accuracy and method, site name, country, `habitat_type`, `geomorphic_setting`, `salinity_class`, `vegetation_class`, `tidal_position` (each with a `*_level` of core vs site), `modification_status[]`, `dominant_species[]`, elevation + units + datum + method |
| `sample_dim` | depth interval | depth min/max (cm), representative depths, notes, sampling year/month, `vegetated_or_bare`, `marsh_zone` |
| `method_dim` | (study, method id, attribute) | `technique`, coring method, carbon type, measured/modeled, carbon method, carbonate removal, `conversion_applied`, in-situ/ex-situ, light/dark, `method_status`, `params` (JSON text) |

Conventions that hold for every compilation:

- **Values are carried as reported.** No unit conversion, no cleaning. Units are the
  compilation's own (`units_raw`), taken from its documentation where it has one.
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

## Adding a compilation

1. Put the raw files in `data/compilations/<name>/` and add `extract.py` there.
2. `extract.py` builds the seven contract tables (columns and types in
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
