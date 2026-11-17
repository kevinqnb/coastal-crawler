# Flag glossary

Every fact has a list of flags (`measurement_fact.flags`). A flag marks something about the value, never changes it, and never drops a row. Filter on flags before analysis. Counts are facts in the current build (695,589 facts, 21 sources). The registry is `FLAGS` in `compilation_vocab.py`, and each compilation's README says how it applies its flags.

Examples:

```sql
-- leave out every value the source calculated or converted
SELECT * FROM measurement_fact
WHERE NOT list_contains(flags, 'calculated_by_source')
  AND NOT list_contains(flags, 'converted_by_source');

-- facts with no flags at all
SELECT * FROM measurement_fact WHERE len(flags) = 0;
```

Skip reasons are different: they are rows left out of a compilation, recorded in its `skip_report.jsonl`. They are listed in the index README.

## How the value was obtained

Whether a number is a direct measurement or something the source worked out.

| Flag | Meaning | Facts (where) | What to do |
|---|---|---|---|
| `calculated_by_source` | The source authors calculated this value from other parameters instead of measuring it (for example organic carbon from fractions, or a sum of grain-size classes). | **22,392**: paradis-mosaic 22,290, davis-calcification 102 | Leave out if you want direct measurements only. |
| `converted_by_source` | The source converted the value, or its unit, from what was originally reported. The original value and unit are kept in the method row (`params`). | **10,161**: fulweiler-nfix 4,647, shao-nfix 2,821, stratma-bio 1,677, pessarrodona-seaweed 1,016 | Check `params` if the original basis matters. It marks a conversion, not an error. |
| `assigned_by_source` | The authors assigned the value from a rule instead of measuring it. USGS set canopy height and biomass to 0 for salt flats. | **46**: usgs-wetlands 46 | Leave out before averaging height or biomass. |
| `digitized_from_figure` | The value was read off a graph, so it is less precise than a tabulated value. | **260**: pessarrodona-seaweed 260 | Treat as approximate. |
| `potential_rate` | The rate is a potential rate (for example an acetylene-block incubation), not an ambient rate. | **4**: ray-oyster 4 | Do not pool with ambient rates. |
| `source_site_averaged` | The value is an average over several sites. | **1,815**: boynton-flux 1,815 | Not a single-site measurement. |
| `source_time_averaged` | The value is an average over time. | **659**: boynton-flux 659 | Not a single-date measurement. |
| `source_average_row` | The row is itself an average of other rows of the same site, and those rows are loaded too. | **440**: jorgensen-oxygen 440 | Leave out to avoid double counting. |
| `source_median` | The value is a median, not a mean or single measurement. | **163**: cloern-phytoplankton 163 | Keep the statistic in mind when comparing. |
| `source_mean_reported` | The source printed `mean (sd)` in one cell. `value` is the mean and `sd` is the bracket, so it is an aggregate, not one measurement. | **1,052**: shao-nfix 1,052 | Treat as a mean. |
| `inequality` | The source printed a limit such as `<0.02`. `value` is the printed number and `value_comparator` holds the sign (`<` or `>`). | **114**: shao-nfix 114 | The true value is below or above `value`, not equal to it. |
| `non_bulk_material` | The analysis was on a sediment fraction (a size or density class), not bulk sediment. | **4,577**: paradis-mosaic 4,577 | Leave out before comparing with bulk sediment. |
| `replicate` | The row is a replicate (replicate number above 1). | **1,143**: paradis-mosaic 1,143 | Average or select as needed. |

## Errors and spread

Flags on `sd`, `se` and how the error was reported.

| Flag | Meaning | Facts (where) | What to do |
|---|---|---|---|
| `error_type_missing` | The source gives an error value but not whether it is SD or SE. By project rule it is stored in `sd` as an assumed SD, and the method note says so. | **1,379**: paradis-mosaic 1,140, fulweiler-nfix 237, davis-calcification 2 | Leave out if you do not want to rely on the SD assumption. |
| `error_type_other` | The error column is neither SD nor SE (for example a confidence interval or a range). It is not stored in `sd` or `se`, and the value stays in `params`. | **41**: fulweiler-nfix 41 | No usable `sd` or `se` for this fact. |
| `error_propagated` | The error is a propagated standard error, calculated from other errors. | **24**: davis-calcification 24 | Treat as an approximate SE. |
| `negative_sd` | The source's SD is negative, which is impossible. Kept as reported. | **6**: davis-calcification 5, ray-oyster 1 | Do not use this `sd`. |
| `se_units_differ` | The SE column is documented in a different unit from the value (CCN accretion: value in cm/yr, SE in mm/yr). Not converted. | **43**: smithsonian-coastal-carbon 43 | Convert before using the SE. |

## Missing or unclear information

The source left something out. The fact is still loaded.

| Flag | Meaning | Facts (where) | What to do |
|---|---|---|---|
| `depth_missing` | The sediment or sample depth is missing in the source. | **46,151**: stratma-bio 42,639, smithsonian-coastal-carbon 3,222, usgs-wetlands 186, tang-nitrification 66, 1 more | Cannot be placed in a depth profile. |
| `year_missing` | The sampling year is missing or is a range the extract did not read as one year. | **30,308**: paradis-mosaic 29,082, tang-nitrification 419, foster-seagrass 285, boynton-flux 237, 6 more | Cannot be placed in time. |
| `method_missing` | The source gives no method information for this row, so the method fields are NULL because they are unknown. | **44,925**: stratma-bio 44,864, usgs-wetlands 41, stratma-scoc 20 | Method-based filters will not include it. |
| `flux_type_missing` | The flux type (total, diffusive or advective oxygen uptake) is not stated. | **20**: stratma-scoc 20 | Do not combine with typed fluxes. |
| `carbon_type_unknown` | CCN does not say whether the carbon is organic or total. | **23,290**: smithsonian-coastal-carbon 23,290 | The subfield is `carbon_unspecified`. |
| `salinity_units_missing` | A salinity value has no unit in the source. | **1,796**: fulweiler-nfix 1,796 | Do not compare with salinities of known unit. |
| `o2_units_ambiguous` | The bottom-water oxygen column mixes µM and % saturation, so the unit is not certain. | **1,422**: jorgensen-oxygen 1,422 | Check before using the oxygen value. |

## Position and depth

Problems with where a measurement was made.

| Flag | Meaning | Facts (where) | What to do |
|---|---|---|---|
| `coords_missing` | The source gives no coordinates (latitude or longitude blank). | **218**: chang-denitrification 117, stratma-bio 38, ray-oyster 28, jorgensen-oxygen 22, 2 more | Cannot be mapped. |
| `coords_approximate` | The source marks the position as approximate (for example approximated from a map or a compiler's estimate). | **5,055**: stratma-bio 3,687, stratma-scoc 914, pessarrodona-seaweed 454 | Use with a coarser spatial tolerance. |
| `coords_integer_degree` | Both coordinates are whole degrees, so the position is probably rounded. | **58**: rosentreter-ghg 58 | Treat as approximate. |
| `lon_wrapped` | The source gave longitude on a 0 to 360 scale (or below -180), and it was wrapped into -180 to 180. The raw value is recoverable from `source_row_ref`. | **16**: francesco-phytoplankton 14, stratma-scoc 2 | None. It records the standardisation. |
| `lat_beyond_pole` | The source latitude is outside -90 to 90 (BenDEN station `North Pole`, 90.012). Kept as reported. | **1**: stratma-bio 1 | Leave out of geographic analysis. |
| `lat_lon_zero` | Latitude and longitude are both exactly 0, usually a placeholder for missing coordinates. No current fact carries it. | none | Registered for future sources. |
| `depth_inverted` | The depth interval's top is deeper than its bottom. | **4**: smithsonian-coastal-carbon 4 | Check the interval. |

## Unusual values

Values that look wrong or that the source itself questions. Values are never changed or dropped.

| Flag | Meaning | Facts (where) | What to do |
|---|---|---|---|
| `negative_value` | A value below zero for a quantity that cannot be negative. Kept as reported. (Fluxes, whose sign is meaningful, are not flagged.) | **37**: paradis-mosaic 18, smithsonian-coastal-carbon 10, pessarrodona-seaweed 3, mouw-carbon 3, 3 more | Check, and probably leave out. |
| `zero_value` | The value is exactly 0. It may mean none found, an assigned zero or a rounded value, and the source rarely says which. | **10,482**: paradis-mosaic 4,171, shao-nfix 2,579, francesco-phytoplankton 1,455, mouw-carbon 891, 10 more | Decide per analysis whether to treat zeros as data. |
| `fraction_gt_1` | A quantity that should be a fraction (0 to 1) is above 1. | **2**: smithsonian-coastal-carbon 2 | Check the unit or leave out. |
| `dbd_gt_max` | Dry bulk density is above the cut-off set for the CCN run (3.0 g/cm3). | **148**: smithsonian-coastal-carbon 148 | Check the value, which may be a unit error. |
| `outside_source_qc_range` | The value is outside the minimum or maximum the MOSAIC authors list for that variable. | **38**: paradis-mosaic 38 | Leave out or check. |
| `total_ne_sum_of_parts` | In a seagrass above-ground, below-ground and total triple, the total differs from the sum of the parts by more than 1e-3 (relative and absolute). A screen, not proof of an error. | **2,058**: strydom-seagrass 1,077, foster-seagrass 981 | Check before using totals. |
| `qc_rejected_by_source` | The source's own note says the value was REJECTED by its quality control. It is loaded and flagged. | **8**: shao-nfix 8 | Leave out. |

## Source's own selection codes

Jorgensen's own labels, passed through as flags.

| Flag | Meaning | Facts (where) | What to do |
|---|---|---|---|
| `shallow_lt10m` | Jorgensen selection code 3: in situ, water depth under 10 m. | **612**: jorgensen-oxygen 612 | Source's own category. |
| `method_excluded` | Jorgensen selection code 4: methodological exclusion in the source. | **95**: jorgensen-oxygen 95 | The authors suggest excluding it. |
| `low_o2_bottom_water` | Jorgensen selection code 5: bottom-water oxygen under 25 µM. | **63**: jorgensen-oxygen 63 | Source's own category. |

## Habitat and scope

| Flag | Meaning | Facts (where) | What to do |
|---|---|---|---|
| `habitat_from_scope` | The location's habitat was filled from the whole dataset's scope (for example every MOSAIC core is a marine sediment core, and water-column sources get Water column), not from a label in that row. Facts with a NULL habitat do not carry the flag. | **395,637**: paradis-mosaic 133,775, francesco-phytoplankton 99,779, mouw-carbon 57,045, shao-nfix 32,224, 9 more | Tells scope-derived habitat from label-derived habitat. |

## Duplicates and conflicts

| Flag | Meaning | Facts (where) | What to do |
|---|---|---|---|
| `duplicate_source_row` | The row is identical to another row in the source (every member of the group is flagged). Some may be genuine replicates. | **2,176**: stratma-bio 1,398, strydom-seagrass 283, foster-seagrass 236, fulweiler-nfix 173, 3 more | Keep one per group if you need to remove duplicates. |
| `covariate_conflict` | One sample has two different values of the same water-chemistry (J1) attribute. Both are loaded. | **580**: fulweiler-nfix 268, tang-nitrification 141, shao-nfix 133, boynton-flux 36, 1 more | Choose one or average, deliberately. |

## References

Flags on where the value came from.

| Flag | Meaning | Facts (where) | What to do |
|---|---|---|---|
| `pers_comm` | The value (or its reference) is a personal communication. | **10,524**: mouw-carbon 10,506, rosentreter-ghg 14, cloern-phytoplankton 4 | Not independently citable. |
| `secondary_source` | The value was taken from a secondary compilation that the source names. | **782**: usgs-wetlands 781, davis-calcification 1 | May be double counted with that compilation. |
| `secondary_compilation` | The reference is itself an aggregated database, not the original study (every Francesco fact). | **99,779**: francesco-phytoplankton 99,779 | The original cruise is in `Event`. |
| `cited_secondary` | The source marks the citation as `cited in` another paper. | **43**: pessarrodona-seaweed 30, cloern-phytoplankton 13 | The first paper is the study, the later one the secondary source. |
| `reference_inferred` | The reference was attached by inference (for Mouw ID 14: one location and date range matching BATS), not stated by the source. | **2,364**: mouw-carbon 2,364 | Treat the citation as probable. |
| `reference_unresolved` | The source gives no resolvable reference for this study id (Mouw ID 2). | **12,576**: mouw-carbon 12,576 | No citation to attribute. |
| `study_marked_excluded` | The source's own study list says the cited study is not in its database, yet a data row cites it. | **31**: stratma-bio 30, stratma-scoc 1 | Check the study list. |
| `sd_converted_from_se` | The source converted a reported SE to an SD. Registered but no current compilation uses it, because no file says which rows. | none | Registered for future sources. |

Back to the [compilations index](README.md).
