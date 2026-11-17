# jorgensen-oxygen (global benthic oxygen uptake: TOU, DOU and oxygen penetration depth)

Jørgensen, B.B., Wenzhöfer, F., Egger, M., Glud, R.N. (2022). Sediment oxygen consumption: role in the global marine carbon cycle. *Earth-Science
Reviews* 228, 103987, DOI 10.1016/j.earscirev.2022.103987 (Supplementary Table S1).

- **Version:** `Earth-Science Reviews 228:103987 supplement (2022)`, no version number. **Access date:** 2026-11-15. **License:** CC BY (from
  OpenAlex; the workbook states none).
- **Raw file:** `1-s2.0-S001282522200071X-mmc2.xlsx`, one sheet (`total data base`), 4,343 rows, 16 columns.

Measured sediment oxygen fluxes from 0 to 11,000 m water depth, in three sections: in situ 0 to 6,500 m (2,034 data rows), in situ hadal
(17) and ex situ below 1,000 m (2,281).

## Facts (4,623)

One fact per numeric cell of three columns. 1 cell is skipped.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `jorgensen_tou` (total O2 uptake) | B5 `total_o2_uptake` | 3,518 | mmol m-2 d-1 |
| `jorgensen_dou` (diffusive O2 uptake) | B5 `diffusive_o2_uptake` | 816 | mmol m-2 d-1 |
| `jorgensen_opd` (oxygen penetration depth) | C2 `o2_penetration_depth` | 289 | mm |

The units row gives no unit under DOU. The description says oxygen fluxes are in mmol m-2 d-1, so DOU takes TOU's unit. `N` (472 rows) becomes
`n` for every fact of its row, and is the number of averaged points on the averaged rows. One OPD cell reads `3,8`, which could be a decimal comma
or a list. It is the single skip (`unparseable_value`).

## The plan's stray-row assumption was wrong

The plan expected about 230 stray rows holding only a code in column 0. There are none. Besides the description, the headers, blank rows and
three section titles, all 4,332 rows are full data rows. The "codes" are the **Selection-ID cells of the data rows themselves**: 3,641 are plain
numbers and 691 are text with several codes (`0, 3`, `1, 4, 5`). `Station` is blank on 1,756 full rows, because stations repeat.

## Selection-ID

The codes are: 0 multiple data from one site, 1 single data from one site, 2 averaged data points, 3 in situ shallower than 10 m, 4 methodical
exclusion, 5 bottom water below 25 µM O2. They become flags: 2 gives `source_average_row` (440 facts; the average and its members are both
loaded, so **filter downstream**), 3 gives `shallow_lt10m` (612), 4 gives `method_excluded` (95) and 5 gives `low_o2_bottom_water` (63).
The cell text is also in `depth_notes`.

## How it maps

- **Samples (4,331):** one per data row with a loaded fact. `Station` is `source_sample_id`.
- **Bottom-water O2** goes to the `bottom_water_o2` slot where the cell is a number (1,276 rows). Its units string is the file's own spelling,
  `µM or % saturatiion`, because the column mixes µM and % saturation, so those facts carry `o2_units_ambiguous` (1,422). 775 rows hold text
  (ranges, `no info`, `> 40%`, `oxic`): the slot is NULL and the text is in `depth_notes`.
- **Locations (2,440):** keyed on coordinates, depth and the `Depth range` label (25 coordinates carry different labels). 22 rows have no
  coordinates (`coords_missing`).
- **Habitat:** every `Depth range` label (11 spellings) gives "Subtidal soft sediment" by the dataset's scope, with `habitat_from_scope` on 4,622
  facts. 7 rows have no label and so no habitat. Setting is shelf for the shelf labels, open ocean for rise, abyss and hadal, and blank for slope
  and near-shore.
- **Methods (9):** one per attribute and section. `in_situ_ex_situ` is in situ, in situ hadal or ex situ. No technique is given.
- **References:** filled on the first row of a study group, and a row belongs to the last one above it. `ref:<string>` gives **319 references**
  with the year parsed (7 have none) and no DOI. Three strings contain a `;` or line break and are kept whole.

## Not loaded

`NPP` (632 cells, a satellite production model), and the `Ocean` and `Region` text.

## Flags

`habitat_from_scope` 4,622, `o2_units_ambiguous` 1,422, `shallow_lt10m` 612, `source_average_row` 440, `method_excluded` 95,
`low_o2_bottom_water` 63, `coords_missing` 22.

## Row accounting

4,624 cells = 4,623 facts + 1 skip, nothing collapsed. All facts were checked back to the raw sheet by an independent pass. A 48-row test predicted
50 facts and matched, and two runs give identical output.

## Overlap

Major with `stratma-scoc` (3,540 TOU and DOU values) and `boynton-flux`: the same sediment oxygen measurements appear in several compilations.
They are not merged, and overlap is quantified downstream by DOI and coordinates. Averaged rows double-count their members.

Back to the [compilations index](../README.md).
