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
`tests/test_compilations_*.py` (one per compilation, plus the contract tests), `tests/test_compilation_vocab.py`.

## Workflow

```bash
# 1. per compilation (writes <compilation>/extracted/)
python data/compilations/smithsonian-coastal-carbon/extract.py \
    --access-date YYYY-MM-DD --max-dry-bulk-density 3.0

# 2. central build: validates every extracted dir, assigns global ids, writes the file
python scripts/build_compilations.py --out data/compilations/compilations.duckdb \
    --allow-factless ribeiro-mangrove data/compilations/*/extracted
```

Every `extract.py` takes `--access-date`; a few need more (CCN `--max-dry-bulk-density`; Foster and Strydom `--sum-rel-tol` /
`--sum-abs-tol`; MOSAIC `--pg-restore <binary>` and `--min-subtidal-depth-m`): each compilation's README gives the command it was
built with. Run them with `python -I` from the repository (the editable `coastal_crawler` install must be the checkout whose
contract code you mean to use; a git worktree needs `PYTHONPATH=<worktree>/src python -P`, because `-I` ignores `PYTHONPATH`).

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
| `measurement_fact` | one measurement | `attribute`, `measurement_type` (standard code or `other`), `subfield`, `value` (DOUBLE, exactly as reported), `value_comparator` (`<` or `>` when the source prints a censored value such as `<0.02`; `value` is then the printed number and the fact carries the flag `inequality`; NULL otherwise), `units_raw`, `sd`, `se`, `n`, `flags` (VARCHAR[]), `source_row_ref`; FKs to the five dims below |
| `source_dim` | compilation | name, version, DOI, access date, license, notes |
| `reference_dim` | contributing study | `reference_key` (the compilation's own study id) |
| `citation_dim` | study x citation | bibliography id, publication type, DOI, title, authors, year, URL; `openalex_id`, `doi_resolved`, `doi_resolution_source` (NULL until a DOI-resolution job fills them) |
| `study_screening_dim` | candidate study | citation string and the compilation's own screening labels (`screening_phase`, `eligibility_phase`, `database_status`); `reference_id` is NULL for studies that contributed no facts (the only nullable FK) |
| `location_dim` | core / site | lat, lon, position accuracy and method, site name, country, `water_body`, `habitat_type`, `geomorphic_setting`, `bioregion`, `marine_ecoregion`, `salinity_class`, `vegetation_class`, `tidal_position` (each with a `*_level` of core vs site), `modification_status[]`, `dominant_species[]`, elevation + units + datum + method, `water_depth_m` (positive down), `distance_to_shore_km` (all raw labels); **standard labels derived from the raw ones by the label map:** `habitat`, `bottom_type`, `setting`, `salinity_zone`, `modification_class[]` |
| `sample_dim` | depth interval | depth min/max (cm), representative depths, notes, sampling year/month, `taxon`, `taxon_rank` (what was sampled, raw label), `vegetated_or_bare`, `marsh_zone`, `sediment_type`, `photosynthetic_sediment` (raw), in-situ water context beside the value: `sample_depth_m`, `water_temperature_c`, `water_salinity` (+ units), `bottom_water_o2` (+ units) |
| `method_dim` | (study, method id, attribute) | `technique`, coring method, carbon type, measured/modeled, carbon method, carbonate removal, `conversion_applied`, in-situ/ex-situ, light/dark, `incubation_type` and `experimental_status` (raw labels), `method_status`, `params` (JSON text: structured method details, original values and conversion factors, source column names), `notes` (free text: the source's own comments, verbatim) |

Three patterns that appear in the later compilations:

- **Source's harmonised value, original in the method row.** Where a source reports one harmonised value and keeps the original
  (Pessarrodona, Fulweiler), the fact carries the harmonised value and `method_dim` is **per source row**: the original value,
  unit and conversion factors sit in `params`.
- **Samples at the source's natural grain, with covariates attached once.** `sample_dim` is keyed by the source's own sample
  grain (each README states the key). A water-chemistry value measured beside several rates (the `J1` fields, below) attaches
  to the sample, so a repeated ambient value is stored once; repeats collapsed this way are counted in the compilation's
  `extract_report.json`, and two different values for one sample and attribute are both kept with the flag `covariate_conflict`.
- **Reference-list-only sources.** A source built with `--allow-factless <source_key>` (Ribeiro) may have `reference_dim` /
  `citation_dim` rows that no fact refers to. The flag exempts only the named source from the empty-fact-table and
  no-habitat-label-map checks and raises if that source has any fact.

Contract columns added after the first compilations (`value_comparator`, `method.notes`) are nullable and listed in
`OPTIONAL_COLUMNS` (`compilations.py`): an `extract.py` that does not supply one gets NULL when it writes its parquet, but the
build demands the full schema, so parquet written before a column existed must be regenerated, never built silently.

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
reports them). J1 attributes are shared across compilations; each compilation adds its own verbatim unit strings to the shared
attribute (pH is `unitless`). Equivalent spellings stay separate declared strings (`μM` U+03BC, `µM` U+00B5, `uM`):
conversion is downstream. Values from gridded model or satellite climatologies are never J1.

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
| [boynton-flux](boynton-flux/README.md) | 16,693 | World Flux Data Set 2016 v3 (Boynton and Ceballos): sediment-water O2 and nutrient fluxes (negative = into the sediment, per the Edit Notes), sediment chlorophyll, POC, PON, PP, with bottom-water covariates (J1) |
| [fulweiler-nfix](fulweiler-nfix/README.md) | 9,940 | Fulweiler et al. (EDI edi.1759.2 v2): water-column and benthic N2 fixation rates (source-converted values, originals in per-row methods) with water chemistry (J1) |
| [pessarrodona-seaweed](pessarrodona-seaweed/README.md) | 1,121 | Pessarrodona et al. (figshare v5): seaweed net primary production, harmonised to kg C m-2 y-1, originals in per-row methods |
| [davis-calcification](davis-calcification/README.md) | 347 | Davis et al. (SEANOE 80022): reef net calcification and net production, with coral and coralline cover |
| [tang-nitrification](tang-nitrification/README.md) | 9,890 | Tang et al. (Zenodo v1, 2023): ocean ammonia- and nitrite-oxidation rates (2,941) with water ammonium, nitrite, nitrate, pH and oxygen (J1, one fact per sample) |
| [shao-nfix](shao-nfix/README.md) | 32,224 | Shao et al. (figshare v3, 2024-01-09): ocean N2 fixation rates by size fraction, light/dark and per cell for individual organisms, with nitrate, phosphate and chlorophyll a (J1) |
| [mouw-carbon](mouw-carbon/README.md) | 57,045 | Mouw et al. (PANGAEA 855594, 2016): sediment-trap and 234Th fluxes of POC, PON, total mass, CaCO3 and silica |
| [francesco-phytoplankton](francesco-phytoplankton/README.md) | 99,779 | Mattei and Scardi (PANGAEA 932417, 2021): depth-resolved and depth-integrated primary production and chlorophyll a (J1) |
| [jorgensen-oxygen](jorgensen-oxygen/README.md) | 4,623 | Jorgensen et al. (2022): benthic total and diffusive O2 uptake and O2 penetration depth, in situ and ex situ |
| [paradis-mosaic](paradis-mosaic/README.md) | 147,121 | MOSAIC v2.0 (Paradis et al., dump mosaic_2_ESSD of 2023-04-25): marine sediment cores, organic and inorganic carbon, nitrogen, CaCO3, grain size, bulk density and porosity (shelf to abyssal) |
| [cloern-phytoplankton](cloern-phytoplankton/README.md) | 163 | Cloern et al. 2014 supplement Table S1 (Biogeosciences 11): median annual phytoplankton primary production for 131 estuarine-coastal ecosystems, no coordinates; n sums to 1,147 (the caption says 1,148) |
| [ribeiro-mangrove](ribeiro-mangrove/README.md) | 0 | Ribeiro et al. 2019 Appendix S1 (Ecosphere 10(8) e02841): reference list only, 87 studies behind the neotropical litterfall model, no measurements (build with `--allow-factless ribeiro-mangrove`) |

A source's own README, if it ships one, is a raw file kept beside ours as `README_<origin>.md`
(for example `foster-seagrass/README_dryad.md`); only `README.md` is ours and tracked.

## Flags and skip reasons

Both are closed registries in `compilation_vocab.py` (`FLAGS`, `SKIP_REASONS`, each name with
its meaning). The build raises on a fact flag that is not registered; every `extract.py`
raises on an unregistered skip reason. A flag is added deliberately, with a meaning, when a
source needs it; the compilation's README says how that source applies it.

- **In use** (fact counts over all 21 sources, from the 695,589-fact build; each compilation's README says how it applies
  its flags): `habitat_from_scope` 395,637; `secondary_compilation` 99,779 (Francesco: every fact); `depth_missing` 46,151;
  `method_missing` 44,925; `year_missing` 30,308; `carbon_type_unknown` 23,290; `calculated_by_source` 22,392;
  `reference_unresolved` 12,576 and `reference_inferred` 2,364 (Mouw IDs 2 and 14); `pers_comm` 10,524; `zero_value` 10,482;
  `converted_by_source` 10,161; `coords_approximate` 5,055; `non_bulk_material` 4,577 (MOSAIC size or density fractions: filter
  before comparing with bulk sediment); `duplicate_source_row` 2,176; `total_ne_sum_of_parts` 2,058; `source_site_averaged`
  1,815; `salinity_units_missing` 1,796; `o2_units_ambiguous` 1,422; `error_type_missing` 1,379 (MOSAIC 1,140: the source never says SD or SE, so the error is stored as `sd` by assumption, noted
  in `method.notes`; Fulweiler 237 and Davis 2: an error value with no stated type); `replicate` 1,143; `source_mean_reported` 1,052 (Shao `mean (sd)` cells,
  value = the mean, sd = the bracket); `secondary_source` 782; `source_time_averaged` 659; `shallow_lt10m` 612;
  `covariate_conflict` 580 (one sample with two different values of one J1 attribute; both kept); `source_average_row` 440;
  `digitized_from_figure` 260; `coords_missing` 218; `source_median` 163; `dbd_gt_max` 148; `inequality` 114 (censored values
  such as `<0.02`: `value` is the printed number and `value_comparator` the sign); `method_excluded` 95; `low_o2_bottom_water`
  63; `coords_integer_degree` 58; `assigned_by_source` 46; `se_units_differ` 43; `cited_secondary` 43; `error_type_other` 41;
  `outside_source_qc_range` 38; `negative_value` 37; `study_marked_excluded` 31; `error_propagated` 24; `flux_type_missing` 20;
  `lon_wrapped` 16; `qc_rejected_by_source` 8 (Shao: the authors' own quality control rejected the value; loaded, flagged);
  `negative_sd` 6; `potential_rate` 4; `depth_inverted` 4; `fraction_gt_1` 2; `lat_beyond_pole` 1.
- **Registered, not used by any current compilation:** `sd_converted_from_se`, `lat_lon_zero` (considered and not used; see the
  Ray and Rosentreter READMEs for why).
- **Skip reasons in use:** `depthseries_core_not_in_cores`, `empty_row`, `no_seagrass_measurement`, `no_density`,
  `not_measured` (Chang 2, Tang 166, Shao 185), `below_detection_limit` (Tang 772, Shao 646: results printed as `BDL`, `n.d.`
  and the like; the detection limit is copied into the report where the source gives one), `units_missing` (Fulweiler 4),
  `unparseable_value` (Jorgensen 1, Shao 9), and for MOSAIC `no_metadata_row` (172), `error_without_fact` (114) and
  `material_unit_unclear` (108).

## Not yet built

The table structure already has room for each of these.

- **DOI resolution.** `citation_dim` has `openalex_id`, `doi_resolved` and
  `doi_resolution_source` (NULL everywhere) for a separate script to fill via OpenAlex,
  so compiled and extracted values can be joined by DOI. `extract.py` never calls a
  network service and the source's own DOI is never overwritten. DOI coverage now (citations with a DOI / citations): CCN
  959/1,012, Foster 476/567, SCOC 17/231, Strydom 312/400, BenBioDen 43/777, USGS 185/267, Rosentreter 10/483,
  Chang 0/69, Ray 0/52, Boynton 130/183, Fulweiler 262/267, Pessarrodona 7/229, Davis 0/53, Tang 0/72, Shao 154/280,
  Mouw 23/43, Francesco 11/35, Jorgensen 0/319, MOSAIC 316/331, Cloern 33/159, Ribeiro 0/87.
- **Coverage report.** A habitat x `measurement_type` count of facts, references and
  locations per source, answering what has been extracted and to what extent. Its
  inputs (`location_dim.habitat`, `measurement_fact.measurement_type`) are in place.
- **More compilations.** None are waiting: all 21 sources in `data/compilations/` are built. Phase-2 material that was
  deliberately not loaded (isotopes, 14C and biomarkers in MOSAIC; gene and cell counts in Shao and Tang; PAR, light level and
  other `other`-type columns) is counted in each compilation's `extract_report.json` and listed in its README. Expect overlap
  between compilations (CCN/USGS, SCOC/Jorgensen/Boynton, Foster/Strydom, SCOC/BenBioDen, Fulweiler/Shao); the layer never merges
  them.
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
