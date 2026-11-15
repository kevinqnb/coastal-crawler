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

Source: Smithsonian Coastal Carbon Network, DOI 10.25573/serc.21565671. Tidal-wetland
soil carbon profiles: 16,715 cores, 117,518 depth intervals, ~550 studies (US 38%
of cores, then South Africa, UK, Indonesia, Australia). Habitat is mostly marsh
(8,746) and mangrove (5,801), with seagrass (1,294) and small numbers of others.
No shellfish reefs; tidal flat appears only as `mudflat` (60 cores). No in-situ water
column data (no water depth, temperature, salinity or O2) and no geomorphic setting.

**Facts extracted (in scope):** `dry_bulk_density` (g/cm3), `fraction_organic_matter`
and `fraction_carbon` (dimensionless), 217,099 facts in the current build. Units are
read from `CCN_database_structure.html` and asserted. **Out of scope for now:**
radiometric and dating columns (Cs-137, Pb-210, Ra-226, Pb-214, Bi-214, Be-7, C-14,
delta-13C, ages, markers), accretion rates, compaction.

Nuances:

- **`fraction_carbon` is not one quantity.** It is *organic* carbon for some studies and
  *total* carbon for others, and *modeled* from loss on ignition (Craft or local
  regression) for some. Use `method_dim.carbon_type`,
  `carbon_measured_or_modeled`, `conversion_applied`. 46% of carbon facts have
  unknown type (flag `carbon_type_unknown`).
- **Method coverage is partial.** Only 69% of facts have a matched methods row
  (322 studies have none; 561 facts sit on a duplicated method id whose rows
  conflict, and none is chosen). Technique is NULL for the rest.
- **Flags used:** `negative_value`, `fraction_gt_1`, `dbd_gt_max` (bulk density above
  `--max-dry-bulk-density`, 3.0 in the current build), `depth_missing`,
  `depth_inverted`, `duplicate_source_row` (flags the later copy only),
  `carbon_type_unknown`. Observed ranges include bulk density -81 to 11.3 and
  organic matter up to 1.59, so unflagged filtering is unsafe.
- **Excluded: 185 depth rows (277 measurements, 0.13%).** Their (study, site, core)
  key matches no core, so there are no coordinates or labels. Mostly key typos
  (Osland_2016 `Lake_pontchartrain` vs `Lake_Pontchartrain`, Noe_2016 `Bulter_Island`).
  Suggested matches are in `skip_report.jsonl` but deliberately **not applied**.
- **Site-level label fallback.** `sites.csv` repeats some site ids (e.g. one row per
  habitat). Core labels are used first; a site value is used only when every site row
  for that (study, site) is non-null and identical. `salinity_class_level`,
  `vegetation_class_level`, `tidal_position_level` say which was used (core 5,050 /
  site 83 for salinity). Salinity classes are CCN's own (estuarine, saline,
  mesohaline, ...), not Venice classes.
- **`tidal_position` is CCN `inundation_class`** (low/mid/high/back/levee/...) and
  `low`/`high` also appear on mangrove cores. `marsh_zone` is filled only when
  habitat is marsh and the value is low/mid/high. `vegetated_or_bare` is derived
  from `vegetation_class` (`unvegetated` -> bare; emergent, forested, scrub shrub,
  seagrass and mixed -> vegetated; `mudflat` -> NULL). Habitat and vegetation class
  disagree for some cores (e.g. marsh cores labelled forested); both raw values are kept.
- **Time:** year is 85% filled, month 42%; day is not carried.
- **Elevation** is 17% filled, with datum (mostly NAVD88) and method as reported.
- **Statistics:** CCN reports single values; `sd`, `se`, `n` are NULL.
- **Method `params`** carries drying/combustion temperature, time and sample size etc.
  Reported ranges such as `"1-5"` stay strings.
- **Provenance:** `reference_dim` = CCN `study_id`; a study can have several citations
  (dataset, source article, synthesis source). No primary DOI is chosen; use
  `citation_dim.publication_type`. The compilation's own DOI is in `source_dim`.
- Small data-quality items handled as-is: 3 cores with no coordinates (location kept
  with NULL lat/lon), 3 exact-duplicate citation rows collapsed, `species` and
  `impacts` rows for cores not in `cores.csv` ignored (1,073 and 37 keys).
- A CCN raw-file read uses only the literal `NA` and empty string as missing, and
  parses numbers with Python `float()` (not `pd.to_numeric`, which is off by 1 ULP
  on 17-digit strings).

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
