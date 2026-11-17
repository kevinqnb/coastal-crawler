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
    README.md                    ours: citation, mapping, flags, things to know
    <raw files>                  as downloaded, incl. the source's own README_<origin>.md (ignored by git)
    extract.py                   raw files -> parquet (tracked, with README.md)
    extracted/                   extract.py output (ignored by git)
      source|reference|citation|study_screening|location|sample|method|fact.parquet
      skip_report.jsonl          rows excluded, with reason
      extract_report.json        counts, flag counts, method coverage
```

Code: `src/coastal_crawler/compilations.py` (table contract, validation, key
assignment, DuckDB write), `scripts/build_compilations.py` (CLI),
`tests/test_compilations_{ccn,scoc,foster,strydom,bio,usgs,rosentreter,chang,ray}.py`, `tests/test_compilation_vocab.py`.

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
with a reason from the central registry `SKIP_REASONS` in `compilation_vocab.py` (an unlisted
reason raises). Fact flags are a closed registry too (`FLAGS`, below).

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

*Planned (not built yet):* for sources whose fact carries the source's harmonised value
(Pessarrodona, Fulweiler), `method_dim` will be per source row, so original value, unit and
conversion factors sit in `params`; and `sample_dim` will be keyed by the source's natural
sample grain, so water-column covariates (J1, below) attach to the sample and a repeated
ambient value is stored once. Neither applies to the five compilations built so far.
Likewise a `reference_dim` row with no facts (a reference-list-only source, Ribeiro) needs an
explicit `--allow-factless` build flag that does not exist yet: today the build raises on an
empty fact table.

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
| Chemical | C1 sediment organic matter; C2 porewater; C3 sediment-water nutrient fluxes; C4 benthic nitrogen removal; C5 water-column nitrogen transformations; C6 sinking particle flux; C7 benthic N2 fixation; C8 greenhouse-gas fluxes; J1 joined fields |
| Biological | B1 benthic microalgal biomass; B2 foundation-organism biomass; B3 foundation-organism density and canopy; B4 benthic cover by group; B5 benthic metabolism; B6 macrophyte production; B7 calcification; B8 pelagic primary production; B9 pelagic respiration and net community production; B10 macrofauna |
| Physical | P1 light attenuation |

**J1 Joined fields** (chemical, state; 22 types in all). Water-medium in-situ values reported
beside a rate, kept as their own category so they can be set beside, or replaced by, joined
layers (WOD, GLODAP) later. **Only water concentrations go here** (water column, overlying or
bottom water); sediment-medium values are C2 (porewater) or `other`, and bottom-water O2
measured beside a benthic rate stays in the `bottom_water_o2` sample slot. Subfields:
`nitrate`, `nitrite`, `ammonium`, `phosphate`, `silicate`, `nitrate_nitrite`, `din`, `ph`,
`dissolved_oxygen`, `chlorophyll_a` (carbonate-chemistry subfields are added when a source
reports them). Each J1 attribute is declared, with its source's verbatim unit strings, by the compilation that
first emits it: `water_nitrate`, `water_ammonium` and `water_o2` (all `μM`) by Chang; later sources add their own unit
spellings. Values from gridded model or satellite climatologies are never J1.

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
| `habitat` | tidal marsh, mangrove forest, tidal flat, sandy beach, seagrass meadow, shellfish reef / bed, subtidal soft sediment, rocky shore and reef, kelp and macroalgal bed, coral reef, water column (11 labels) |
| `bottom_type` | follows from `habitat`: soft, biogenic, hard, hard_biogenic; NULL for water column, the only habitat without a bottom |
| `setting` | estuary, coastal lagoon, fjord, river delta, embayment, open coast, shelf, open ocean |
| `salinity_zone` | Venice classes: limnetic, oligohaline, mesohaline, polyhaline, euhaline, hyperhaline |
| `modification_class[]` | natural, restored, created, artificial |

**`habitat` is NULL unless the source labels it or states a scope for the whole dataset.**
Where it is filled from such a scope (every SCOC depth class is subtidal soft sediment because
the dataset is sediment; Foster is all seagrass; water-column sources get `water column`), the
facts carry the flag `habitat_from_scope`, so label-derived and scope-derived habitat can be
told apart. Where it is NULL (no label, no scope) there is no flag. `water column` is mapped by
Rosentreter's estuary sheets (raw `habitat_type` = the constant `water column`). SCOC and Foster were rebuilt once to add the flag;
fact counts and every other column were unchanged. A source with no habitat label and no stated scope (Chang) declares the habitat
column with one empty-label row (`<source>,habitat_type,,habitat,`) because the build requires every source to declare it; its
locations then have NULL habitat and no flag.

Within a declared (source, raw column, mapped column) every distinct raw label needs a
row (blank = no equivalent); an unlisted label raises, and every source must declare
`habitat`. `write_extracted` adds the five derived columns (an `extract.py` must not
supply them), and `read_extracted` recomputes them and raises if the parquet is stale
against the map.

## Compilations

One README per compilation (citation, what it holds, facts, mapping, flags, things to know).
Counts are as built; the extract's own `extract_report.json` has the authoritative numbers.

| Compilation | Facts | What it is |
|---|---|---|
| [smithsonian-coastal-carbon](smithsonian-coastal-carbon/README.md) | 217,181 | Coastal Carbon Network Data Library v1.7.0: tidal-wetland soil carbon profiles and 210Pb accretion rates |
| [stratma-scoc](stratma-scoc/README.md) | 3,907 | SCOC database v1: marine sediment community oxygen consumption (and grain size) |
| [foster-seagrass](foster-seagrass/README.md) | 12,980 | Foster et al. 2025: seagrass biomass, production and meadow structure |
| [strydom-seagrass](strydom-seagrass/README.md) | 14,266 | Strydom et al. (PANGAEA 929968, last change 2025-05-06): the same seagrass measures as Foster, in PANGAEA's unit spellings |
| [stratma-bio](stratma-bio/README.md) | 63,350 | BenBioDen (Stratmann et al., Dryad v4): meio-, macro- and megabenthic biomass (11,792) and density (51,558), shore to abyssal |
| [usgs-wetlands](usgs-wetlands/README.md) | 1,485 | Feher et al. (USGS data release, 2025-05-07): tidal saline wetland canopy height, aboveground biomass and productivity, soil carbon density and accumulation |
| [rosentreter-ghg](rosentreter-ghg/README.md) | 738 | Rosentreter et al. (2023, figshare v1): CO2, CH4 and N2O fluxes of estuaries (water-air) and vegetated coasts |
| [chang-denitrification](chang-denitrification/README.md) | 2,495 | Chang et al. (figshare v3): benthic denitrification, anammox and total N2 production, with sediment covariates and water nitrate, ammonium and oxygen (J1) |
| [ray-oyster](ray-oyster/README.md) | 241 | Ray and Fulweiler (2020, figshare v1): sediment (control vs oyster) and oyster fluxes of N2, nutrients and GHGs |

A source's own README, if it ships one, is a raw file kept beside ours as `README_<origin>.md`
(for example `foster-seagrass/README_dryad.md`); only `README.md` is ours and tracked.

## Flags and skip reasons

Both are closed registries in `compilation_vocab.py` (`FLAGS`, `SKIP_REASONS`, each name with
its meaning). The build raises on a fact flag that is not registered; every `extract.py`
raises on an unregistered skip reason. A flag is added deliberately, with a meaning, when a
source needs it; the compilation's README says how that source applies it.

- Used by the built compilations: `negative_value`, `zero_value`, `fraction_gt_1`,
  `dbd_gt_max`, `depth_missing`, `depth_inverted`, `duplicate_source_row`,
  `carbon_type_unknown`, `se_units_differ`, `coords_missing`, `coords_approximate`,
  `lon_wrapped`, `lat_lon_zero`, `method_missing`, `flux_type_missing`,
  `study_marked_excluded`, `year_missing`, `total_ne_sum_of_parts`, `converted_by_source` and `lat_beyond_pole`
  (stratma-bio; the latter on 1 fact) and
  `habitat_from_scope` (SCOC, Foster and Strydom: on all their facts, 3,907, 12,980 and 14,266; stratma-bio: on the facts
  whose `Depth range` label is present, 21,659 of 63,350; Rosentreter: its 484 estuary facts),
  `assigned_by_source` (USGS: the 46 salt-flat height and biomass zeros the authors assigned), `secondary_source`,
  `method_missing` and `depth_missing` (USGS), `coords_integer_degree` and `pers_comm` (Rosentreter; USGS checks the first),
  `potential_rate` and `negative_sd` (Ray: 4 facts and the one negative SD in the file).
- Registered ahead of use, for compilations not yet built (names only; defined in each
  source's README when it is built): `covariate_conflict`, `reference_inferred`,
  `reference_unresolved`, `sd_converted_from_se`,
  `calculated_by_source`, `source_site_averaged`, `source_time_averaged`,
  `source_average_row`, `source_median`, and others listed in `FLAGS`. (`sd_converted_from_se`, `converted_by_source` and
  `source_site_averaged` were considered for Ray and Rosentreter and not used: the files do not say which rows, or the flag's
  meaning does not fit; see their READMEs.)
- Skip reasons in use: `depthseries_core_not_in_cores`, `empty_row`,
  `no_seagrass_measurement`, `no_density` (stratma-bio; its skip report entries carry a `file` key because the source has
  two data files), `not_measured` (Chang: 2 cells coded `nm`; its entries carry `data_row` and `attribute`). Registered
  ahead of use: `below_detection_limit`, `units_missing`.

## Not yet built

The table structure already has room for each of these.

- **DOI resolution.** `citation_dim` has `openalex_id`, `doi_resolved` and
  `doi_resolution_source` (NULL everywhere) for a separate script to fill via OpenAlex,
  so compiled and extracted values can be joined by DOI. `extract.py` never calls a
  network service and the source's own DOI is never overwritten. DOI coverage now: CCN
  959/1,012 citations, Foster 476/567, SCOC 17/231, Strydom 312/400, BenBioDen 43/777, USGS 185/267, Rosentreter 10/483,
  Chang 0/69, Ray 0/52.
- **Coverage report.** A habitat x `measurement_type` count of facts, references and
  locations per source, answering what has been extracted and to what extent. Its
  inputs (`location_dim.habitat`, `measurement_fact.measurement_type`) are in place.
- **More compilations.** Raw files for 12 more sit in `data/compilations/` with no
  `extract.py`: boynton-flux, fulweiler-nfix, davis-calcification,
  pessarrodona-seaweed, tang-nitrification, shao-nfix, jorgensen-oxygen, mouw-carbon,
  francesco-phytoplankton, paradis-mosaic, cloern-phytoplankton (a PDF supplement) and
  ribeiro-mangrove (a reference list only, no measurements). Expect overlap between
  compilations (CCN/USGS, SCOC/Jorgensen/Boynton, Foster/Strydom, SCOC/BenBioDen); the layer never merges them.
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
5. Register any new flag or skip reason in `FLAGS` / `SKIP_REASONS`, and note new
   vocabulary, units or derived-field rules in the compilation's `README.md` and in the
   `extract.py` docstring. `.gitignore` tracks `data/compilations/*/extract.py` and
   `data/compilations/*/README.md`; rename a source's own README to `README_<origin>.md` first.
6. The central DuckDB write (`write_duckdb`) is a bulk load (about 6 s for 234k facts).
   `write_extracted`, which writes each compilation's parquet, still inserts row by row
   (~5 min for 217k facts).
