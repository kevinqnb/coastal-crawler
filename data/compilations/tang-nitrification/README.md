# tang-nitrification (ocean nitrification rates, with water nutrients and oxygen)

Tang, W., Ward, B.B., Beman, M., Bristow, L., Clark, D., Fawcett, S., et al. (2023). *Database of nitrification and nitrifiers in the global
ocean.* Zenodo, DOI 10.5281/zenodo.8355912 (paper: *Earth System Science Data* 15, 5039-5077, DOI 10.5194/essd-15-5039-2023).

- **Version:** `v1 (2023-09-18)`, from the Zenodo record. **Access date:** 2026-11-15. **License:** CC BY 4.0.
- **Raw files:** `Database of nitrification and nitrifiers in the global ocean.xlsx` (8 sheets). The empty submission template is not read.

Ammonia-oxidation (2,417 rows) and nitrite-oxidation (1,010 rows) rates in the water column, each with the sample's temperature, salinity,
nutrients and oxygen. 72 reference strings, 2,589 distinct samples.

## Facts (9,890)

2,941 rates + 6,949 water-chemistry (J1) facts. Units are the header's. Nothing is converted.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `tang_ammonia_oxidation` | C5 `ammonia_oxidation` | 2,065 | nmol N L-1 d-1 |
| `tang_nitrite_oxidation` | C5 `nitrite_oxidation` | 876 | nmol N L-1 d-1 |
| `water_ammonium` | J1 `ammonium` | 1,824 | μM |
| `water_nitrite` | J1 `nitrite` | 1,890 | μM |
| `water_nitrate` | J1 `nitrate` | 2,062 | μM |
| `water_o2` | J1 `dissolved_oxygen` | 1,107 | μM |
| `water_ph` | J1 `ph` | 66 | unitless |

`sd` is the sheet's rate SD where it is a number (1,612 facts). 34 rows have an SD but no rate fact (the rate is `BDL` or `NM`), and that SD is
not loaded on its own.

## Water chemistry: one fact per sample and attribute

The ammonia and nitrite sheets repeat the same samples with the same ambient values, so a J1 value belongs to the **sample**. A sample is
latitude, longitude, depth and date. 9,235 nutrient and oxygen cells give 6,883 facts and 2,352 collapsed repeats, and the 66 pH cells are
66 more facts. Where one sample has two different values (57 sample-attribute pairs), every value is loaded with `covariate_conflict`
(141 facts) and nothing is averaged.

- **The data source is not in the sample key,** unlike the plan's wording. Only without it do the plan's counts reproduce. The two sheets
  spell the same paper differently, and the same stations appear under two papers. The reference stays on the fact. Strings are never merged.
- **J1 beside a skipped rate still loads:** 1,044 J1 facts sit on rows whose rate is `BDL` or `NM`.
- **pH** has 66 numbers (the plan said empty) and no unit anywhere. By your decision they are `water_ph` with units `unitless`.
- **One literal NaN** in the oxygen column (ammonia sheet, row 516) is read as blank.
- **Slots:** depth is `sample_depth_m`, with 14 samples lacking one (`depth_missing`), temperature is `water_temperature_c`, and salinity is
  `water_salinity` (`psu`). Rows of one sample must agree or the extract raises. One sample disagrees (-67.774, -69.2762, 80 m, 2011-01-17, two
  replicate casts). Its slots are NULL through an explicit list in the code, and the values are recoverable by `source_row_ref`.

## How it maps

- **Habitat** is Water column for every location, from the dataset's scope, so `habitat_from_scope` is on all facts. Setting is NULL.
- **Locations (603):** latitude and longitude only. There are no names or water depths.
- **Dates:** a datetime gives year and month. The 29 text forms are an explicit table. An in-year range gives the year with month NULL. The
  cross-year range `2008 Dec-2009 Jan` gives both NULL and `year_missing` (419 facts), with the text kept in `depth_notes`.
- **Methods:** the two metadata sheets describe each experiment per `Data source`. A rate fact gets one method row per attribute and
  reference. `technique` is the `Method` column and everything else (tracer, enrichment, incubation, volume, time, termination, rate calculation,
  detection limit) is in `params`. 60 are matched exactly and 33 are `method_id_unmatched`, since the metadata sheets spell 23 strings
  differently and nothing is matched by similarity.
- **The `Data source` column** holds the reference on the first row of each block, and **comment lines are written into the same column**
  (`Data shown in Figure 3`, detection-limit notes). Explicit prefix and exact-string tables recognise the comments. Anything else must look
  like a reference (a year and a full stop, or `unpublished`) or the extract raises. A row belongs to the last reference above it. That gives
  **72 references**, not the plan's 130 + 80, which counted the comments.
- **References:** `ref:<string>` with year parsed (5 `unpublished` entries have none). No title, authors or DOI.

## Not loaded

`PAR`, `Light level` and `Potential density` (phase-2 `other`), and the gene-abundance sheets (`ammonia oxidizer` 2,249 rows, `NOB` 658 rows,
and their qPCR metadata). All are counted in the report.

## Missing codes and skips (938)

Blank and `NA` give no value. `BDL` (below detection) and `NM` (not measured) in a rate or J1 column are skips.

| Skip | Cells |
|---|---|
| rates `BDL` / `NM` | ammonia 266 / 86, nitrite 119 / 15 |
| J1 `BDL` / `NM` | 387 / 65 |

Each `BDL` skip carries the detection-limit comments and the metadata limit where they exist. 155 of the 772 have neither.

## Flags

`habitat_from_scope` 9,890, `year_missing` 419, `depth_missing` 66, `zero_value` 264, `covariate_conflict` 141, `negative_value` 1 (a nitrate of
-0.000214 uM, Clark 2008).

## Row accounting

13,180 cells = 9,890 facts + 938 skips + 2,352 collapsed repeats, asserted. All 9,890 facts were checked back to their raw cells by independent
code. A one-study check (Baer 2017 plus two nitrite rows) predicted 26 facts and 2 skips and matched, and two runs give identical tables.

## Overlap

`shao-nfix` is a different process but shares some stations. `fulweiler-nfix` water-column rows may report the same water chemistry.

Back to the [compilations index](../README.md).
