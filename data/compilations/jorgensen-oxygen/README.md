# jorgensen-oxygen (global benthic oxygen uptake: TOU, DOU and oxygen penetration depth)

Jørgensen, B.B., Wenzhöfer, F., Egger, M., Glud, R.N. (2022). Sediment oxygen consumption: Role in the global marine carbon cycle.
*Earth-Science Reviews* 228, 103987, https://doi.org/10.1016/j.earscirev.2022.103987 (Supplementary Table S1).

- **Version:** `Earth-Science Reviews 228:103987 supplement (2022); no version number`.
- **Access date:** 2026-11-15. **License:** CC BY (OpenAlex: hybrid open access, CC BY; read by the integration plan, the workbook states none).
- **Raw file:** `1-s2.0-S001282522200071X-mmc2.xlsx`, one sheet (`total data base`), 4,343 rows x 16 columns.

A compilation of measured sediment oxygen fluxes from 0 to 11,000 m water depth, in three sections: in situ 0 - 6,500 m (2,034 data rows), in situ hadal
> 6,500 m (17) and ex situ < 1,000 m (2,281).

**Facts:** 4,623, one per numeric cell of three columns; 1 cell is a skip.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `jorgensen_tou` (total O2 uptake) | B5 `total_o2_uptake` | 3,518 | mmol m-2 d-1 |
| `jorgensen_dou` (diffusive O2 uptake) | B5 `diffusive_o2_uptake` | 816 | mmol m-2 d-1 |
| `jorgensen_opd` (oxygen penetration depth) | C2 `o2_penetration_depth` | 289 | mm |

The units row has a unit under TOU (`mmol m-2 d-1`) and under OPD (`mm`) but **none under DOU**; the description says "O2 fluxes are presented in
mmol m-2 d-1" (the extract asserts the sentence), so DOU takes TOU's unit. The plan counts (3,518 / 816 / 289) are reproduced exactly. `N` (472 rows) is `n`
for every fact of its row (500 facts): it is the number of averaged points on the averaged rows (selection code 2). **One OPD cell reads `3,8`** (a decimal
comma or a list; undecidable): it is the single skip, reason `unparseable_value` (new, registered), in `skip_report.jsonl`.

## The plan's stray-row assumption was wrong

INTEGRATION_PLAN 5.13 expected "about 230 stray rows that contain only a code in column 0". **There are none.** Besides row 0 (description), rows 1-2 (names, units),
blank rows and three section rows (text in column 0 only), every one of the 4,332 remaining rows is a full data row (asserted: a row with only column 0 filled that is
not one of the three section titles raises). The "codes" are the **Selection-ID cells of the data rows themselves**: 3,641 are plain numbers (0, 1, 2) and 691 are
text holding several codes (`0, 3`, `1, 4, 5`, ...; one row reads `0, 3,, 4`, handled as 0, 3, 4). `Station` is blank on 1,756 data rows, which are full rows
(stations repeat).

## Selection-ID

The description: 0 multiple data from one site, 1 single data from one site, 2 averaged multiple data points, 3 in situ < 10 m water depth, 4 methodical
exclusion, 5 bottom water < 25 µM O2. A row's codes are the numbers in its cell (exactly one of 0/1/2, optionally 3, 4, 5; asserted). Flags: 2 -> `source_average_row`
(440 facts; the averaged row is an average of other rows of the same site, which are loaded too: **filter downstream**), 3 -> `shallow_lt10m` (612), 4 -> `method_excluded` (95),
5 -> `low_o2_bottom_water` (63). The cell text is also in `sample.depth_notes` as `selection_id=<text>`, so codes 0 and 1 are not lost.

## Mapping

- **Samples:** one per data row with a loaded fact (4,331; one row has no TOU, DOU or OPD). `Station` -> `source_sample_id`.
- **Bottom-water O2** -> the `bottom_water_o2` slot where the cell is a number (1,276 rows), with the units string of the units row, `µM or % saturatiion`
  (U+00B5, **the file's own spelling**; the column mixes µM and % saturation, so every fact of such a row carries `o2_units_ambiguous`, 1,422 facts). 775 rows hold text
  (ranges such as `170-180` and `75 - 260`, `no info` 331, `> 40%`, `oxic`): the slot is NULL and the text is copied into `depth_notes` as `bottom_water_o2_as_written=<text>`.
  `Comments` (245 rows) is copied there as `comment=<text>`.
- **Locations (2,440):** `location_key` = latitude | longitude | depth | `Depth range` label (the same coordinates carry different labels in 25 cases, so the label is part of the key).
  `water_depth_m` = `Depth` (positive m). 22 rows have no coordinates (flag `coords_missing`).
- **Habitat.** The raw `habitat_type` is the `Depth range` label (11 spellings, `outer shelf` and `Outer shelf` separate raw labels with their own rows); all map to **Subtidal soft sediment by
  the dataset's scope** (flag `habitat_from_scope` on the facts of the rows that have a label: 4,622 facts; 7 rows have no label: NULL habitat, no flag). Setting: `shelf` for
  the inner/outer/continental-shelf labels, `open ocean` for rise, abyss and hadal, none for slope and near-shore (the SCOC mapping).
- **Methods (9):** one per (attribute, section); `in_situ_ex_situ` = `in situ`, `in situ hadal (>6,500m)` or `ex situ`; no technique is given.
- **Not loaded:** `NPP` (632 cells; Oregon State VGPM satellite production, rule 3.5), `Ocean` and `Region` (location text; Ocean 4,332, Region 4,178 cells).

## References

The `Reference` cell is filled on the first row of a study group and blank on the others: a row belongs to the last non-blank reference above it (asserted: every section's first data
row has one). `reference_key` = `ref:<whitespace-normalised string>`: **319 references and citations** (347 filled cells; repeats merge); `bibliography_id` = the string, `year` = its first
four-digit year (7 strings have none), no DOI (the DOI-resolution job can attach them). Three strings contain a `;` or a line break and are kept whole; each may cite more than one paper.

## Flags

`habitat_from_scope` 4,622, `o2_units_ambiguous` 1,422, `shallow_lt10m` 612, `source_average_row` 440, `method_excluded` 95, `low_o2_bottom_water` 63, `coords_missing` 22.

## Row accounting

4,332 data rows; 4,624 numeric-or-text cells in TOU, DOU and OPD = 4,623 facts + 1 skip; no J1 values, nothing collapsed. After the central build every fact was checked against the raw sheet by an
independent pass (value, unit, section and method, forward-filled reference, location coordinates, depth and label, selection and other flags recomputed, bottom-water O2 and `n`):
4,623 of 4,623 agree. A tiny end-to-end check (the first 40 data rows of the in-situ section plus the first 4 rows of each of the other two sections, predicted by an independent count: 48 rows, 50 facts) matched. Adding
this source left the other eleven sources' content unchanged. Two runs of `extract.py` give byte-identical parquet and reports. `source_row_ref` is
`<file>[<sheet>]#sheet_row=<0-based sheet row, read with header=None>:<attribute>`.

## Overlap with other compilations

Major with `stratma-scoc` (3,540 TOU/DOU) and `boynton-flux`: the same sediment oxygen measurements appear in several compilations. The layer never merges them; overlap is quantified
downstream by DOI and coordinates, and the averaged rows (`source_average_row`) double-count their members.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
