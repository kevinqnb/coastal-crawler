# Compilations

Existing coastal-ecosystem data compilations (not extracted from papers by a model), cleaned into one common set of **extraction records** and
joined in a DuckDB star schema, `compilations.duckdb`. It is separate from the literature warehouse (`data/warehouse.duckdb`): compilation values
are curated source values and are never mixed into the literature tables. Joining the two is a deliberate downstream step.

The aim is to record what has already been extracted, and how much, in a common language. Every fact has a standard measurement type, and every
location has standard habitat, setting, salinity and modification labels. Nothing is limited to that vocabulary: what it has no place for is
loaded as `measurement_type = other` (with a note) or as a NULL label.

## Layout

```
data/compilations/
  README.md                      this file
  compilation_vocab.py           standard vocabulary, attribute declarations, checks
  compilation_label_maps.csv     raw label -> standard label, per source
  FLAGS.md                       glossary of every fact flag (generated from the registry and the build)
  compilations.duckdb            built output (ignored by git)
  <compilation>/
    README.md                    ours: citation, mapping, flags, things to know (tracked)
    extract.py                   raw files -> parquet (tracked)
    <raw files>                  as downloaded (ignored by git)
    extracted/                   extract.py output (ignored by git): eight parquet tables,
                                 skip_report.jsonl and extract_report.json
```

Code: `src/coastal_crawler/compilations.py` (table contract, validation, key assignment, DuckDB write) and `scripts/build_compilations.py`.
Tests: `tests/test_compilations_*.py` (one per compilation, plus the contract tests) and `tests/test_compilation_vocab.py`.

## Workflow

```bash
# 1. per compilation (writes <compilation>/extracted/)
python -I data/compilations/smithsonian-coastal-carbon/extract.py --access-date 2026-11-15 --max-dry-bulk-density 3.0

# 2. central build: validates every extracted dir, assigns global ids, writes the file
python scripts/build_compilations.py --out data/compilations/compilations.duckdb \
    --allow-factless ribeiro-mangrove data/compilations/*/extracted
```

Every `extract.py` takes `--access-date`. A few need more (CCN `--max-dry-bulk-density`, Foster and Strydom `--sum-rel-tol` and `--sum-abs-tol`,
MOSAIC `--pg-restore` and `--min-subtidal-depth-m`), and each README gives the command it was built with. In a git worktree, use
`PYTHONPATH=<worktree>/src python -P`, because `-I` ignores `PYTHONPATH`.

The build is a **full rebuild**, written to a temp file and renamed atomically. The same inputs give the same tables, because ids are assigned in
sorted key order. Anything that breaks the contract raises, and the builder never skips and logs. Exclusions happen only in a compilation's own
`extract.py`, are written to its `skip_report.jsonl`, and need a reason from the registry `SKIP_REASONS`. Fact flags are a closed registry too
(`FLAGS`).

## Extraction records

One **fact** is one measured variable at one core and depth interval. A source row with three variables becomes three facts.

| Table | Grain | Holds |
|---|---|---|
| `measurement_fact` | one measurement | `attribute`, `measurement_type` (standard code or `other`), `subfield`, `value` (as reported), `value_comparator` (`<` or `>` for a censored value such as `<0.02`, with the flag `inequality`), `units_raw`, `sd`, `se`, `n`, `flags`, `source_row_ref` |
| `source_dim` | compilation | name, version, DOI, access date, license, notes |
| `reference_dim` | contributing study | `reference_key`, the compilation's own study id |
| `citation_dim` | study and citation | bibliography id, publication type, DOI, title, authors, year, URL, and `openalex_id`, `doi_resolved`, `doi_resolution_source` (empty after the build; filled by the scripts in "DOI resolution" below) |
| `study_screening_dim` | candidate study | citation string and the compilation's own screening labels. `reference_id` is NULL for studies with no facts |
| `location_dim` | core or site | coordinates, names, raw labels (`habitat_type`, `geomorphic_setting`, `salinity_class`, ...), water depth, and the **standard labels** derived from them: `habitat`, `bottom_type`, `setting`, `salinity_zone`, `modification_class[]` |
| `sample_dim` | depth interval or sample | depths, sampling year and month, `taxon` and `taxon_rank` (what was sampled), sediment descriptors, and water context measured beside the value: `sample_depth_m`, `water_temperature_c`, `water_salinity`, `bottom_water_o2` |
| `method_dim` | study, method, attribute | `technique`, in situ or ex situ, light or dark, `incubation_type`, `experimental_status`, `method_status`, `params` (structured details, original values and conversion factors, source column names) and `notes` (the source's own free-text comments, verbatim) |

`incubation_type` and `experimental_status` say whether a value is from a control, a natural site, an intact core, an in-situ incubation, a
treatment or a slurry. Filtering on them is downstream.

Three patterns appear in the later compilations:
- **Harmonised value with the original kept.** Where a source reports one harmonised value and also the original (Pessarrodona, Fulweiler), the
  fact carries the harmonised value and the method row is **per source row**, with the original value, unit and conversion factors in `params`.
- **Water chemistry stored once per sample.** A water-chemistry value measured beside several rates (the `J1` fields, below) belongs to the
  sample. The sample follows the source's own grain (each README states it), so a repeated ambient value is stored once. Collapsed repeats are
  counted in `extract_report.json`, and two different values for one sample and attribute are both kept with `covariate_conflict`.
- **Reference-list-only sources.** A source built with `--allow-factless <source_key>` (Ribeiro) may have reference and citation rows with no
  fact. The flag exempts only the named source from the empty-fact and no-habitat-map checks, and raises if that source has any fact.

New nullable contract columns (`value_comparator`, `method.notes`) are listed in `OPTIONAL_COLUMNS`. An `extract.py` that does not supply one
gets NULL, but the build demands the full schema, so older parquet must be regenerated and is never built silently.

## Conventions

- **Values are carried as reported.** No unit conversion and no cleaning. Units are the source's own (`units_raw`). Conversions (per mass to per
  area, N vs N2, O2 vs C, DW vs AFDW, per day to per year) are downstream.
- **Standardisation is minimal.** Allowed: longitude in [-180, 180], elevation in metres (negative below the surface), whitespace stripping, and
  "NA" or blank to NULL. Each is written in the `extract.py` docstring, and the raw value stays recoverable through `source_row_ref`.
- **Suspicious values are flagged, never changed or dropped.** Filter on `flags` before analysis.
- **Raw labels stay, and standard labels sit beside them.** A label with no equivalent maps to blank, never to a guess.
- **Context fields come only from values measured beside the fact**, never from model or satellite layers.
- **Unstated error types default to SD.** Where a source gives an error but not its type, the value is stored in `sd`, flagged `error_type_missing`
  and noted in `method.notes`. Filter on the flag to avoid the assumption.
- **Attribute names** are snake_case, prefixed with the compilation's namespace where they could be confused (`scoc_*`, `seagrass_*`). A statistic
  (mean, max) is part of the name. Percentages stay percentages.
- **Study keys:** the compilation's own study id where it has one, otherwise `doi:<bare lower-case doi>`, otherwise `ref:<source text>`. Near-duplicates
  are never merged by hand.
- **Provenance:** `source_row_ref` is `<file>#data_row=<0-based row>:<attribute>`, indexed as the raw file was read (not a line number). Every
  fact was round-tripped to its raw row after the build.
- **`method_status`** says how method information was obtained: `matched`, `method_id_null`, `method_id_unmatched` or
  `ambiguous_duplicate_method_id`. Anything but `matched` means the technique fields are NULL because they are unknown.

## Standard vocabulary

Defined in `compilation_vocab.py`. Raw attributes, labels and units are never replaced.

**Measurement types** (`measurement_type`), each with allowed `subfield`s. Extra subfields are declared in `EXTENSION_SUBFIELDS` with a reason.

| Domain | Types |
|---|---|
| Geological | G1 sediment texture; G2 accretion and carbon burial |
| Chemical | C1 sediment organic matter; C2 porewater; C3 sediment-water nutrient fluxes; C4 benthic nitrogen removal; C5 water-column nitrogen transformations; C6 sinking particle flux; C7 benthic N2 fixation; C8 greenhouse-gas fluxes; J1 joined fields |
| Biological | B1 benthic microalgal biomass; B2 foundation-organism biomass; B3 foundation-organism density and canopy; B4 benthic cover by group; B5 benthic metabolism; B6 macrophyte production; B7 calcification; B8 pelagic primary production; B9 pelagic respiration and net community production; B10 macrofauna |
| Physical | P1 light attenuation |

**J1 joined fields.** Water concentrations measured beside a rate (nitrate, nitrite, ammonium, phosphate, silicate, nitrate+nitrite, DIN, pH, dissolved
oxygen, chlorophyll a), kept as their own category so they can later be set beside, or replaced by, joined layers (WOD, GLODAP). **Only water
concentrations go here** (water column, overlying or bottom water). Sediment values are C2 or `other`, and bottom-water O2 beside a benthic rate
stays in the `bottom_water_o2` slot. J1 attributes are shared across compilations, and each adds its own verbatim unit strings. Equivalent spellings
stay separate (`μM` U+03BC, `µM` U+00B5, `uM`), and pH is `unitless`. Model or satellite climatologies are never J1.

**Attribute declarations** (`ATTRIBUTES`): every attribute names its type, its allowed subfields (`None` means NULL, never a guess) and its exact
allowed `units_raw` strings. The build raises on an undeclared attribute, a type or subfield outside the declaration, or an unlisted unit. `other`
needs a note.

**Location labels** (`compilation_label_maps.csv`: source, raw column, raw label, mapped column, mapped label):

| Column | Values |
|---|---|
| `habitat` | tidal marsh, mangrove forest, tidal flat, sandy beach, seagrass meadow, shellfish reef / bed, subtidal soft sediment, rocky shore and reef, kelp and macroalgal bed, coral reef, water column |
| `bottom_type` | follows from `habitat`: soft, biogenic, hard, hard_biogenic; NULL for water column |
| `setting` | estuary, coastal lagoon, fjord, river delta, embayment, open coast, shelf, open ocean |
| `salinity_zone` | Venice classes: limnetic, oligohaline, mesohaline, polyhaline, euhaline, hyperhaline |
| `modification_class[]` | natural, restored, created, artificial |

**`habitat` is NULL unless the source labels it or states a scope for the whole dataset.** Where it is filled from scope (SCOC and MOSAIC are
sediment, Foster is all seagrass, water-column sources get "water column"), the facts carry `habitat_from_scope`, so label-derived and scope-derived
habitat can be told apart. Where it is NULL, there is no flag. A source with neither a label nor a scope (Chang, Boynton) declares the habitat column
with one empty-label row (`<source>,habitat_type,,habitat,`), because the build requires every source to declare it.

Every distinct raw label needs a row in the label map (blank means no equivalent), and an unlisted label raises. `write_extracted` adds the derived
columns (an `extract.py` must not supply them), and `read_extracted` recomputes them and raises if the parquet is stale against the map.

## Compilations

One README per compilation: citation, what it holds, facts, mapping, flags and things to know. Counts are as built, and each `extract_report.json` has
the authoritative numbers. 21 sources, **695,589 facts**.

| Compilation | Facts | What it is |
|---|---|---|
| [smithsonian-coastal-carbon](smithsonian-coastal-carbon/README.md) | 217,181 | Coastal Carbon Network v1.7.0: tidal-wetland soil carbon profiles and 210Pb accretion rates |
| [stratma-scoc](stratma-scoc/README.md) | 3,907 | SCOC v1: marine sediment community oxygen consumption, and grain size |
| [foster-seagrass](foster-seagrass/README.md) | 12,980 | Foster et al. 2025: seagrass biomass, production and meadow structure |
| [strydom-seagrass](strydom-seagrass/README.md) | 14,266 | Strydom et al. (PANGAEA): the same seagrass measures as Foster, in PANGAEA's units |
| [stratma-bio](stratma-bio/README.md) | 63,350 | BenBioDen: meio-, macro- and megabenthic biomass and density, shore to abyssal |
| [usgs-wetlands](usgs-wetlands/README.md) | 1,485 | USGS data release: tidal wetland canopy height, biomass, productivity, soil carbon density and accumulation |
| [rosentreter-ghg](rosentreter-ghg/README.md) | 738 | CO2, CH4 and N2O fluxes of estuaries and vegetated coasts |
| [chang-denitrification](chang-denitrification/README.md) | 2,495 | Benthic denitrification, anammox and total N2 production, with water chemistry (J1) |
| [ray-oyster](ray-oyster/README.md) | 241 | Oyster meta-analysis: sediment (control vs oyster) and oyster fluxes of N2, nutrients and GHGs |
| [boynton-flux](boynton-flux/README.md) | 16,693 | World Flux Data Set: sediment-water O2 and nutrient fluxes, with bottom-water covariates (J1) |
| [fulweiler-nfix](fulweiler-nfix/README.md) | 9,940 | Water-column and benthic N2 fixation rates, originals kept in per-row methods, with water chemistry (J1) |
| [pessarrodona-seaweed](pessarrodona-seaweed/README.md) | 1,121 | Seaweed net primary production, harmonised to kg C m-2 y-1, originals in per-row methods |
| [davis-calcification](davis-calcification/README.md) | 347 | Reef net calcification and net production, with coral and coralline cover |
| [tang-nitrification](tang-nitrification/README.md) | 9,890 | Ocean ammonia and nitrite oxidation rates, with water ammonium, nitrite, nitrate, pH and oxygen (J1) |
| [shao-nfix](shao-nfix/README.md) | 32,224 | Ocean N2 fixation rates by size fraction, light and dark, and per cell, with nitrate, phosphate and chlorophyll (J1) |
| [mouw-carbon](mouw-carbon/README.md) | 57,045 | Sediment-trap and 234Th fluxes of POC, PON, total mass, CaCO3 and silica |
| [francesco-phytoplankton](francesco-phytoplankton/README.md) | 99,779 | Depth-resolved and integrated primary production and chlorophyll a (J1) |
| [jorgensen-oxygen](jorgensen-oxygen/README.md) | 4,623 | Benthic total and diffusive O2 uptake and O2 penetration depth |
| [paradis-mosaic](paradis-mosaic/README.md) | 147,121 | MOSAIC v2.0: marine sediment cores with carbon, nitrogen, CaCO3, grain size, bulk density and porosity |
| [cloern-phytoplankton](cloern-phytoplankton/README.md) | 163 | Median annual phytoplankton production of 131 estuarine-coastal ecosystems, no coordinates |
| [ribeiro-mangrove](ribeiro-mangrove/README.md) | 0 | Reference list only: 87 studies behind a mangrove litterfall model, built with `--allow-factless` |

A source's own README, if it ships one, is a raw file kept as `README_<origin>.md`. Only `README.md` is ours.

## Flags and skip reasons

Both are closed registries in `compilation_vocab.py` (`FLAGS`, `SKIP_REASONS`), each name with its meaning. The build raises on an unregistered fact
flag, and every `extract.py` raises on an unregistered skip reason. A new one is added deliberately, with a meaning, when a source needs it.

The full glossary, with every flag explained in plain words, its counts and what to do about it, is in [FLAGS.md](FLAGS.md).

**Flags in use** (facts over all 21 sources). Each compilation's README says how it applies its flags. The commonest and most important:

| Flag | Facts | Meaning |
|---|---|---|
| `habitat_from_scope` | 395,637 | habitat comes from the dataset's scope, not a label |
| `secondary_compilation` | 99,779 | the reference is itself a database (Francesco) |
| `depth_missing`, `method_missing`, `year_missing` | 46,151 / 44,925 / 30,308 | the field is unknown |
| `carbon_type_unknown` | 23,290 | CCN carbon type not stated |
| `calculated_by_source` | 22,392 | the source calculated the value from other parameters |
| `reference_unresolved`, `reference_inferred` | 12,576 / 2,364 | Mouw IDs 2 and 14 |
| `pers_comm` | 10,524 | the reference is a personal communication |
| `converted_by_source` | 10,161 | the source converted the value |
| `non_bulk_material` | 4,577 | MOSAIC size or density fraction, not bulk sediment |
| `error_type_missing` | 1,379 | error type unstated, stored in `sd` as SD (MOSAIC 1,140, Fulweiler 237, Davis 2) |
| `source_mean_reported` | 1,052 | Shao `mean (sd)` cell: value is the mean, `sd` the bracket |
| `covariate_conflict` | 580 | one sample with two different values of one J1 attribute, both kept |
| `inequality` | 114 | censored value such as `<0.02`: `value` is the number, `value_comparator` the sign |
| `qc_rejected_by_source` | 8 | the authors' own quality control rejected the value (Shao); loaded, flagged |

Others (counts in each README): `zero_value`, `negative_value`, `negative_sd`, `duplicate_source_row`, `coords_missing`, `coords_approximate`,
`coords_integer_degree`, `lon_wrapped`, `lat_beyond_pole`, `total_ne_sum_of_parts`, `source_site_averaged`, `source_time_averaged`,
`source_average_row`, `source_median`, `salinity_units_missing`, `o2_units_ambiguous`, `shallow_lt10m`, `method_excluded`, `low_o2_bottom_water`,
`secondary_source`, `cited_secondary`, `study_marked_excluded`, `digitized_from_figure`, `replicate`, `potential_rate`, `assigned_by_source`,
`se_units_differ`, `error_type_other`, `error_propagated`, `flux_type_missing`, `outside_source_qc_range`, `dbd_gt_max`, `depth_inverted`,
`fraction_gt_1`. Registered but unused: `sd_converted_from_se`, `lat_lon_zero`.

**Skip reasons in use:** `depthseries_core_not_in_cores`, `empty_row`, `no_seagrass_measurement`, `no_density`, `not_measured` (Chang 2, Tang 166,
Shao 185), `below_detection_limit` (Tang 772, Shao 646), `units_missing` (Fulweiler 4), `unparseable_value` (Jorgensen 1, Shao 9), and for MOSAIC
`no_metadata_row` (172), `error_without_fact` (114) and `material_unit_unclear` (108).

## DOI resolution

The goal of a DOI here is a pointer to the original **paper**, so a dataset DOI or a website is not enough, and "no DOI because it is not a paper"
is kept apart from "no DOI because we are missing information". The central build leaves the DOI columns empty. These scripts fill them, and
every one is **re-run after each rebuild** (the sidecar `*.parquet` files next to them are derived and ignored by git):

```bash
python data/compilations/openalex_verify_dois.py                 # look up every stored DOI in OpenAlex (cached in _openalex_cache/)
python data/compilations/build_doi_check.py                      # verdict per stored DOI, checked against the citation's own text
python data/compilations/resolve_citations.py --per-page 5       # title search for citations with no / failed DOI
python data/compilations/resolve_from_bibliography.py            # author-year labels via the introducing paper's bibliography
python data/compilations/francesco_source_list.py                # Francesco: the file's own `Data doi/data link` column
python data/compilations/apply_doi_resolution.py --apply         # write doi_resolved (dry run without --apply; backs up first)
python data/compilations/find_paper_for_datasets.py              # companion paper of dataset citations (candidates only)
python data/compilations/build_source_status.py --apply          # write the three status tables below
```

`apply_doi_resolution.py` checks every row's `source_key` and `citation_key` first and aborts if ids drifted. `eval_resolver.py` and
`eval_citation_parser.py` score the matching on citations whose DOI is already verified.

**`doi_resolved` is the one trusted DOI.** Never key on the raw `doi`: about 200 stored DOIs failed verification. Only strong evidence is
written, and `doi_resolution_source` says which:
`stored_doi:verified_title` / `stored_doi:verified_author_year` (a stored DOI that agrees with the citation text; author+year is weaker),
`openalex_title_search:full|trimmed` and `bibliography:doi|title` (title, first author and year agree with exactly one non-dataset work),
`source_file_data_doi` (Francesco's own data DOIs; datasets, no `openalex_id`). A DOI found only in a compilation's own source list
(`source_attested`), weak, ambiguous and author-year-only matches are not written.

**`doi_resolved` is not always a paper.** Use the status tables, which are added to the file by `build_source_status.py`:

| Object | Grain | Use |
|---|---|---|
| `citation_source_status` | citation | `reference_kind` (paper, thesis, report, book_or_chapter, dataset, website_or_database, personal_or_unpublished, unknown) with its evidence in `kind_basis`, `doi_status`, and companion-paper candidates |
| `reference_source_status` | reference | `best_status` over the reference's citations, `paper_dois`, `dataset_dois` |
| `measurement_source_status` (view) | measurement | the same, one row per fact: filter `best_status = 'paper_doi'` for papers you can download |

`doi_status` values: `paper_doi`, `paper_openalex_only` (a real paper whose record has no DOI), `dataset_doi` (the DOI is a dataset), `not_a_paper`
(a website, database, personal communication or unpublished work, or a dataset with no DOI), `paper_doi_missing` (evidence says it is a paper but
no DOI was found), `unidentified` (cannot tell what it is), `doi_kind_unknown`, and for references with no citation row `no_citation_record`.
The kind comes from the strongest evidence available: OpenAlex's work type, then the source's own `publication_type`, then a data-repository DOI
prefix, then narrow wording in the label. It stays `unknown` when none applies. Companion-paper candidates (`paper_candidate_*`) are only shown when
the title, an author and the year all agree. They are never in `doi_resolved`.

Facts by `best_status` (2026-11-17): `paper_doi` 352,418 (50.7%), `not_a_paper` 126,869 (18.2%), `dataset_doi` 97,041 (14.0%),
`unidentified` 89,397 (12.9%), `no_citation_record` 12,576 (1.8%), `paper_openalex_only` 11,922 (1.7%), `paper_doi_missing` 5,330 (0.8%),
`doi_kind_unknown` 36.

## Still to do

- **DOI resolution, open items.** 89,397 facts (12.9%) are `unidentified` (mostly author-year labels with no text: Mouw, Francesco, Paradis,
  Shao, all of Strydom), and 12,576 facts (Mouw ID 2) have no citation row. Francesco has seven labels with several data DOIs each (about 4,400
  facts); giving them one reference per data DOI needs an extractor change. 516 dataset citations have a companion paper that a title match cannot
  find.
- **Coverage report.** A habitat by `measurement_type` count of facts, references and locations per source, showing what has been extracted and how
  much. The inputs are in place.
- **Phase 2 material** that was deliberately not loaded (isotopes, 14C and biomarkers in MOSAIC; gene and cell counts in Shao and Tang; PAR, light
  level and other `other`-type columns) is counted in each `extract_report.json`.
- **Overlap** exists between compilations (CCN and USGS; SCOC, Jorgensen and Boynton; Foster and Strydom; SCOC and BenBioDen; Fulweiler and Shao). The
  layer never merges them. It is found downstream by DOI and coordinates.
- **Open label questions.** CCN `saline`, `brackish` and `mixoeuhaline` have no Venice class. CCN has no rule yielding `artificial`. SCOC near-shore
  and slope have no setting.

## Adding a compilation

1. Put the raw files in `data/compilations/<name>/` and add `extract.py` there.
2. **Map to the standard vocabulary first.** Declare each attribute in `ATTRIBUTES` (type, subfields, exact units). Anything outside the standard
   types is `other` with a note, and a new subfield goes in `EXTENSION_SUBFIELDS` with a reason. Add a label-map row for every distinct raw habitat,
   salinity, setting and modification label (blank when there is no equivalent). Write the mapping rules in the `extract.py` docstring.
3. `extract.py` builds the eight contract tables (`coastal_crawler.compilations.TABLES`) with compilation-local `*_key` strings, supplies the raw
   labels, and calls `write_extracted(tables, out_dir)`. It must assert its own row accounting: every source measurement is a fact or a listed
   exclusion.
4. Follow the ladder: unit tests on a hand-built fixture, a real-data run, a one-study run with a predicted count, then the full build and a
   source-row round trip.
5. Register any new flag or skip reason in `FLAGS` and `SKIP_REASONS`, and note new vocabulary, units and derived-field rules in the compilation's
   `README.md` and the `extract.py` docstring. `.gitignore` tracks `*/extract.py` and `*/README.md`, so rename a source's own README to
   `README_<origin>.md` first.
6. The central DuckDB write is a bulk load (about 6 s for 234k facts). `write_extracted` still inserts row by row, which is slow for the largest
   sources (about 5 min for 217k facts).
