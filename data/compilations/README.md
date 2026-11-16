# Compilations

Raw, pre-existing coastal-ecosystem data compilations (not extracted from papers by
a model), cleaned into a common set of **extraction records** and joined into one
DuckDB star schema, `compilations.duckdb`. Distinct from the literature warehouse
(`data/warehouse.duckdb`): compilation values are curated source values, not model
output, and are never mixed into the literature tables. Joining the two is a
deliberate downstream step.

The aim is to record what has already been extracted, and to what extent, in a
common language: every fact carries a standard measurement type and every location
carries standard habitat, setting, salinity and modification labels. Nothing is
limited to that vocabulary; what it has no place for is loaded as
`measurement_type = other` (with a written note) or a NULL label.

## Layout

```
data/compilations/
  README.md                      this file
  compilation_vocab.py           standard vocabulary, attribute declarations, checks
  compilation_label_maps.csv     raw label -> standard label, per source
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
`tests/test_compilations_{ccn,scoc,foster}.py`, `tests/test_compilation_vocab.py`.

## Workflow

```bash
# 1. per compilation (writes <compilation>/extracted/)
python data/compilations/smithsonian-coastal-carbon/extract.py \
    --access-date YYYY-MM-DD --max-dry-bulk-density 3.0

# 2. central build: validates every extracted dir, assigns global ids, writes the file
python scripts/build_compilations.py --out data/compilations/compilations.duckdb \
    data/compilations/smithsonian-coastal-carbon/extracted [other extracted dirs...]
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
| `measurement_fact` | one measurement | `attribute`, `measurement_type` (standard code or `other`), `subfield`, `value` (DOUBLE, exactly as reported), `units_raw`, `sd`, `se`, `n`, `flags` (VARCHAR[]), `source_row_ref`; FKs to the five dims below |
| `source_dim` | compilation | name, version, DOI, access date, license, notes |
| `reference_dim` | contributing study | `reference_key` (the compilation's own study id) |
| `citation_dim` | study x citation | bibliography id, publication type, DOI, title, authors, year, URL; `openalex_id`, `doi_resolved`, `doi_resolution_source` (NULL until a DOI-resolution job fills them) |
| `study_screening_dim` | candidate study | citation string and the compilation's own screening labels (`screening_phase`, `eligibility_phase`, `database_status`); `reference_id` is NULL for studies that contributed no facts (the only nullable FK) |
| `location_dim` | core / site | lat, lon, position accuracy and method, site name, country, `water_body`, `habitat_type`, `geomorphic_setting`, `bioregion`, `marine_ecoregion`, `salinity_class`, `vegetation_class`, `tidal_position` (each with a `*_level` of core vs site), `modification_status[]`, `dominant_species[]`, elevation + units + datum + method, `water_depth_m` (positive down), `distance_to_shore_km` (all raw labels); **standard labels derived from the raw ones by the label map:** `habitat`, `bottom_type`, `setting`, `salinity_zone`, `modification_class[]` |
| `sample_dim` | depth interval | depth min/max (cm), representative depths, notes, sampling year/month, `taxon`, `taxon_rank` (what was sampled, raw label), `vegetated_or_bare`, `marsh_zone`, `sediment_type`, `photosynthetic_sediment` (raw), in-situ water context beside the value: `sample_depth_m`, `water_temperature_c`, `water_salinity` (+ units), `bottom_water_o2` (+ units) |
| `method_dim` | (study, method id, attribute) | `technique`, coring method, carbon type, measured/modeled, carbon method, carbonate removal, `conversion_applied`, in-situ/ex-situ, light/dark, `incubation_type` and `experimental_status` (raw labels), `method_status`, `params` (JSON text) |

`incubation_type` / `experimental_status` record whether a value comes from a control,
a natural site, an intact core or an in-situ incubation, or from a treatment or slurry;
filtering on them is downstream.

Conventions that hold for every compilation:

- **Values are carried as reported.** No unit conversion, no cleaning. Units are the
  compilation's own (`units_raw`), taken from its documentation where it has one.
  Conversions (per mass to per area, N vs N2, O2 vs C, DW vs AFDW, per day to per
  year) are downstream.
- **Standardisation is limited to what is needed to put compilations side by side.**
  Allowed, and written in the compilation's `extract.py` docstring: sign/unit changes
  for coordinates and elevation (longitude in [-180, 180], elevation in metres,
  negative below the surface/water level; the raw value stays recoverable via
  `source_row_ref`), whitespace stripping, and mapping the source's "NA"/blank to NULL.
  The measured value, its units and categorical labels are never converted.
- **Suspicious values are flagged, never changed or dropped.** Flags are a list on the
  fact (empty list = none). Filter on `flags` before any analysis.
- **Raw labels stay; standard labels sit beside them.** Controlled-vocabulary fields
  (salinity, habitat, vegetation) are whatever the source says. The standard labels
  are separate columns filled only from the label map; a label with no equivalent maps
  to blank, never a guess. Fields a compilation does not have are NULL.
- **Context fields are filled only from values the compilation measured beside the
  fact**, never from model or satellite layers.
- **Attribute naming:** snake_case, prefixed with the compilation's own namespace where
  the quantity could be confused with another compilation's (`scoc_*`, `seagrass_*`).
  A statistic (mean/max/min) is part of the attribute name, not a separate field.
  Percentages stay percentages (`units_raw` = `%`); fractions stay fractions.
- **Study keys:** `reference_key` is the compilation's own study id where it has one;
  otherwise the DOI (`doi:<bare lower-case doi>`), falling back to the source's
  author/year text (`ref:<string>`). Never a hand-made merge of near-duplicates.
- **Derived fields have one written rule** (in the compilation's `extract.py`
  docstring) and the raw value sits next to the derived one.
- **Provenance:** `source_row_ref` is `<file>#data_row=<0-based data row>:<attribute>`;
  it indexes the raw file as read by pandas (not a line number, because quoted
  newlines exist). Every fact in the build was round-tripped to its raw row.
- `method_dim.method_status` says how method information was obtained:
  `matched`, `method_id_null`, `method_id_unmatched`, or
  `ambiguous_duplicate_method_id`. Anything but `matched` means the technique fields
  are NULL because they are unknown, not because they were absent by design.

## Standard vocabulary

Defined in `compilation_vocab.py`; raw attributes, labels and units are never replaced.

**Measurement types** (`fact.measurement_type`), with a list of allowed `subfield`s
each (for example `organic_carbon`, `loi_organic_matter` and `dry_bulk_density` under
C1). Subfields beyond the standard list are declared in `EXTENSION_SUBFIELDS` with a
reason.

| Domain | Types |
|---|---|
| Geological | G1 sediment texture; G2 accretion and carbon burial |
| Chemical | C1 sediment organic matter; C2 porewater; C3 sediment-water nutrient fluxes; C4 benthic nitrogen removal; C5 water-column nitrogen transformations; C6 sinking particle flux; C7 benthic N2 fixation; C8 greenhouse-gas fluxes |
| Biological | B1 benthic microalgal biomass; B2 foundation-organism biomass; B3 foundation-organism density and canopy; B4 benthic cover by group; B5 benthic metabolism; B6 macrophyte production; B7 calcification; B8 pelagic primary production; B9 pelagic respiration and net community production; B10 macrofauna |
| Physical | P1 light attenuation |

**Attribute declarations** (`ATTRIBUTES`): every fact attribute names its type, the
subfields it may carry (`None` = no subfield, kept NULL rather than guessed) and the
exact `units_raw` strings it may carry. `extract.py` sets `measurement_type` and
`subfield` (where the subfield depends on method information, as for CCN
`carbon_type`, the rule is in its docstring). The build raises on an undeclared
attribute, a type or subfield outside the declaration, or a unit string not on the
list; a new unit is a deliberate addition. `other` needs a note.

**Location labels** (`compilation_label_maps.csv`: `source_key, raw_column, raw_label,
mapped_column, mapped_label`):

| Column | Values |
|---|---|
| `habitat` | tidal marsh, mangrove forest, tidal flat, sandy beach, seagrass meadow, shellfish reef / bed, subtidal soft sediment, rocky shore and reef, kelp and macroalgal bed, coral reef |
| `bottom_type` | follows from `habitat`: soft, biogenic, hard, hard_biogenic |
| `setting` | estuary, coastal lagoon, fjord, river delta, embayment, open coast, shelf, open ocean |
| `salinity_zone` | Venice classes: limnetic, oligohaline, mesohaline, polyhaline, euhaline, hyperhaline |
| `modification_class[]` | natural, restored, created, artificial |

Within a declared (source, raw column, mapped column) every distinct raw label needs a
row (blank = no equivalent); an unlisted label raises, and every source must declare
`habitat`. `write_extracted` adds the five derived columns (an `extract.py` must not
supply them), and `read_extracted` recomputes them and raises if the parquet is stale
against the map.

## Compilations

### smithsonian-coastal-carbon (Coastal Carbon Network Data Library v1.7.0)

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

### stratma-scoc (SCOC database v1)

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

### foster-seagrass (Foster et al. 2025, seagrass biomass, production and structure)

Foster, Nicole; Gomis, Enric; Montemayor, Diana et al. (2025). *Global patterns and
drivers of seagrass biomass, net primary production and meadow structure* [Dataset].
Dryad, DOI 10.5061/dryad.kprr4xhh1 (accessed 2026-11-15; `source.version` = `2025`, the
Dryad year, because the source states no version; license not stated in the files).
A meta-analysis table: 5,903 rows (one per study x site x genus x year), 463 distinct
`Reference` strings, 1,131 locations (after the label split below), sampled 1972-2022,
12 seagrass genera plus `Mixed`, coastal and estuarine, all bioregions.

**Facts:** 12,980, one per non-blank measurement cell, units as in the source headers
(`units_raw`):

| Attribute | Type / subfield | Units |
|---|---|---|
| `seagrass_biomass_above` / `_below` / `_total` | B2 `above_ground_biomass` / `below_ground_biomass` / `total_biomass` | g DW m-2 |
| `seagrass_shoot_density` | B3 `shoot_or_stem_density` | shoots m-2 |
| `seagrass_leaf_density` | B3 `leaf_density` (extension) | leaves m-2 |
| `seagrass_percent_cover` | B3 `percent_cover` | `%` (kept as a percentage) |
| `seagrass_production_above` / `_below` | B6 `above_ground_npp` / `below_ground_npp` | g DW m-2 d-1 |
| `seagrass_production_total` / `_shoot` / `_leaf` | B6 `total_npp` / `shoot_production` / `leaf_production` (extensions) | g DW m-2 d-1 |

The `seagrass_` prefix keeps these apart from other compilations' biomass-like
attributes. Production is per day here and carried as reported. `habitat` = seagrass
meadow, `setting` = estuary for `Geotype` Estuarine (Coastal -> blank). Overlap with
other seagrass compilations is never merged here; it is found downstream through
`reference_key` (DOI-based) and coordinates. No `sd`/`se`/`n`; no sediment depth
(`depth_notes` says so).

Things to know:

- **Bio-ORACLE environmental columns are not loaded.** The 19 `BO22_*` columns
  (sea-surface temperature, pH, PAR, nutrients, chlorophyll, salinity, O2, current
  velocity, primary productivity; 5,628 rows populated, 275 empty) are a gridded
  model/satellite climatology (2000-2014) looked up at each coordinate, not seagrass
  measurements. They are asserted present and listed in `extract_report.json` but
  create no facts. If added later they should be a distinct attribute family with a
  method row marking them as modelled; the dataset README's units for
  `BO22_ppmax/ppmean` (mmol m-3) should be checked against Bio-ORACLE's own
  documentation first.
- **No value limits.** Values are taken as is: no thresholds, no clipping. There are no
  zeros in the source (minimum 0.001), so absence cannot be told from "not measured".
- **Totals are partly derived upstream.** The source README says total = above + below
  "if not reported"; the data cannot distinguish reported from summed totals (734 rows
  have a total with no parts, 51 have both parts but no total). Totals are carried as
  given. Flag `total_ne_sum_of_parts` goes on all three facts of a biomass or production
  triple where above, below and total are all present and total differs from
  above+below by more than `--sum-rel-tol` relative and `--sum-abs-tol` absolute (run
  with 1e-3 / 1e-3: 981 facts). The flag is tolerance-sensitive (726 facts at 1e-2,
  1,473 at 1e-6), so treat it as a screen, not a verdict.
- **Reference keys.** `reference_key` = `doi:<doi>` when the DOI cell is a DOI (bare, or
  a doi.org URL; lower-cased, inner whitespace removed: four cells had stray spaces),
  otherwise `ref:<Reference string>` (the source's "Author et al. year" text). 467
  references are DOI-keyed and 91 text-keyed; 567 non-blank DOI cells are free-text
  citations (some with encoding damage) and fall back to the Reference string. One
  Reference string (`Hitchcock et al. 2017`) has both DOI and non-DOI rows, so it is two
  references; no merging is attempted. `citation_dim`: `bibliography_id` = the
  Reference string, `year` parsed from it, title/authors NULL.
- **Labels.** `habitat_type` = constant `seagrass` (derived from the dataset's scope);
  `geomorphic_setting` = `Geotype` (`Coastal` / `Estuarine`); `bioregion` = `Bioregion`
  (Short et al. 2007 bioregions per the source README); `marine_ecoregion` = `ECOREGION`
  (not documented in the source README; the names look like Marine Ecoregions of the
  World, which is presumed, not stated). The location key includes all three labels, so
  coordinates that carry conflicting labels (12 pairs differ in Geotype, 2 in Bioregion)
  stay separate locations.
- **Taxon.** `sample_dim.taxon` = `Genera` as given; `taxon_rank` = `genus`, or `mixed`
  for the label `Mixed` (104 source rows). An unknown genus raises.
- **Method info is absent:** one `method_dim` row per attribute, `method_status` =
  `method_id_null`, `params` = the source column.
- **Excluded:** 47 rows with none of the 11 measurement columns filled
  (`no_seagrass_measurement`, in `skip_report.jsonl`). 5,903 = 5,856 rows with facts + 47.
- **Flags:** `year_missing` (the source has `NA` in Year.of.sampling; 285 facts),
  `duplicate_source_row` (rows identical in every column except `Unique.ID`; 236
  facts), `total_ne_sum_of_parts`.

## Not yet built

The table structure already has room for each of these.

- **DOI resolution.** `citation_dim` has `openalex_id`, `doi_resolved` and
  `doi_resolution_source` (NULL everywhere) for a separate script to fill via OpenAlex,
  so compiled and extracted values can be joined by DOI. `extract.py` never calls a
  network service and the source's own DOI is never overwritten. DOI coverage now: CCN
  959/1,012 citations, Foster 476/567, SCOC 17/231.
- **Coverage report.** A habitat x `measurement_type` count of facts, references and
  locations per source, answering what has been extracted and to what extent. Its
  inputs (`location_dim.habitat`, `measurement_fact.measurement_type`) are in place.
- **More compilations.** Raw files for 15 more sit in `data/compilations/` with no
  `extract.py`: strydom-seagrass, usgs-wetlands, boynton-flux, stratma-bio,
  chang-denitrification, fulweiler-nfix, ray-oyster, pessarrodona-seaweed,
  rosentreter-ghg, tang-nitrification, francesco-phytoplankton, mouw-carbon,
  paradis-mosaic (cloern-phytoplankton is only a PDF supplement; davis-calcification is
  an empty directory). Expect overlap between compilations (CCN/USGS,
  SCOC/Boynton, Foster/Strydom); the layer never merges them.
- **Open label questions.** CCN `saline`, `brackish`, `mixoeuhaline` have no Venice
  class; CCN has no rule yielding `artificial`; SCOC near-shore and slope have no
  setting.

## Adding a compilation

1. Put the raw files in `data/compilations/<name>/` and add `extract.py` there.
2. **Map to the standard vocabulary first.** Declare each attribute the compilation
   will emit in `ATTRIBUTES` (measurement type, subfields, the exact unit strings;
   anything outside the standard types is `other` with a note; a new subfield goes in
   `EXTENSION_SUBFIELDS` with a reason), and add a row to
   `compilation_label_maps.csv` for every distinct raw habitat / salinity / setting /
   modification label (blank when there is no equivalent). Write the mapping rules in
   the `extract.py` docstring.
3. `extract.py` builds the eight contract tables (columns and types in
   `coastal_crawler.compilations.TABLES`) with compilation-local `*_key` strings. It
   supplies the raw labels and `write_extracted` derives the standard ones. It calls
   `write_extracted(tables, out_dir)` and must assert its own row accounting (every
   source measurement is either a fact or a listed exclusion).
4. Follow the ladder: unit tests on a hand-built fixture, a real-data run, a
   one-study end-to-end run with a predicted count, then the full build and a
   source-row round-trip check.
5. Note any new vocabulary, units or derived-field rules in this file and in the
   `extract.py` docstring. `.gitignore` already tracks `data/compilations/*/extract.py`.
6. The write steps insert row by row (~5 min for 217k facts); switch to a bulk load
   before adding many large compilations.
