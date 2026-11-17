# boynton-flux (World Flux Data Set: sediment-water oxygen and nutrient fluxes)

Boynton, W., Ceballos, M.A. (2017). *World Flux Data Set 2016* [Dataset]. Mendeley Data, V3,
https://doi.org/10.17632/8gc3ys3jx6.3 (created 2016-12-16, updated 2017-05-17; Boynton et al. 2018, *Estuaries and Coasts*).

- **Version:** `Mendeley Data v3 (2017-07-21)`, read from the Mendeley page by the integration plan (the workbook's own
  "Cite as" line says v2 and its Table 1 caption says v3). **v3 only is loaded.** The directory also holds the v1 and v2
  workbooks; they are superseded (same 2,879 `ID#` set; v2 to v3 renamed `SOC` to `O2 or SOC` and `PC/PN` to `POC/PON` and
  changed 33 `RES` values) and are never read.
- **Access date:** 2026-11-15. **License:** CC BY 4.0 (Mendeley page).
- **Raw file:** `Published2016v3WorldFlux DataSet.xlsx`. Sheets used: `WorldFluxDataSet2016v3` (2,879 data rows, one
  sediment-water flux incubation per row, unique `ID#`) and `DataRefs` (177 reference entries). `Table2DataVariables` is a
  picture of the paper's variable and unit table (read by eye for the element basis and the "usually top 1 cm" note).

A compilation of net sediment-water oxygen and nutrient fluxes from 227 estuarine and coastal systems in the peer-reviewed
literature (1969 to 2014), with bottom-water conditions and some sediment characteristics beside each flux.

**Facts: 16,693**, no skips. 13,449 non-J1 numeric cells are 13,449 facts; the 3,247 J1 cells are 3,244 facts plus 3 collapsed
repeats. Units are the header spellings (the element basis of each flux, from Table 2, is not in `units_raw`).

| Attribute | Type / subfield | Facts | Units | Element basis (Table 2) |
|---|---|---|---|---|
| `boynton_soc` (`O2 or SOC`) | B5 `total_o2_uptake` | 2,365 | umol/m2/hr | O2 |
| `boynton_nh4_flux` | C3 `ammonium_flux` | 2,313 | umol/m2/hr | N |
| `boynton_po4_flux` | C3 `phosphate_flux` | 1,823 | umol/m2/hr | P |
| `boynton_no2_flux` | C3 `nitrite_flux` (extension) | 155 | umol/m2/hr | N |
| `boynton_no3_flux` | C3 `nitrate_flux` (extension) | 1,296 | umol/m2/hr | N |
| `boynton_no23_flux` | C3 `nitrate_nitrite_flux` | 585 | umol/m2/hr | N |
| `boynton_dsi_flux` | C3 `silicate_flux` | 1,135 | umol/m2/hr | DSi |
| `boynton_water_respiration` (`RES`) | B9 `community_respiration` | 154 | gO2/m2/day | O2; water-column respiration |
| `boynton_sediment_chl_a` | B1 `sediment_chlorophyll_a` | 1,021 | mg/m2 | usually top 1 cm |
| `boynton_poc` | C1 `organic_carbon` | 1,454 | %DW | usually top 1 cm |
| `boynton_pon` | `other` (particulate organic N) | 721 | %DW | usually top 1 cm |
| `boynton_pp` | `other` (particulate P) | 427 | %DW | usually top 1 cm |
| `water_ammonium` | J1 `ammonium` | 990 | uM | bottom water |
| `water_phosphate` | J1 `phosphate` | 841 | uM | bottom water |
| `water_nitrate` | J1 `nitrate` | 495 | uM | bottom water |
| `water_nitrate_nitrite` | J1 `nitrate_nitrite` | 584 | uM | bottom water |
| `water_silicate` | J1 `silicate` | 285 | uM | bottom water |
| `water_ph` | J1 `ph` | 49 | unitless | bottom water |

**POC, PON, PP.** Table 2 gives these as `%DW`, usually top 1 cm; the column headers say `%DW`; the values (POC maximum 13.7)
agree. (The integration plan's "Table 2 says mg C m-2" is not in the picture: there is no unit conflict.) PON and PP are
`other` with a note, not `total_nitrogen`: particulate organic N and P are not total N or P. "Usually top 1 cm" is text in
`sample.depth_notes`, not numeric depths. 24 source rows have no numeric value in any fact column (all `ND`) and give no fact,
sample or location.

## Sign convention (checked against the source)

Fluxes are carried as given and **not normalised**. **Negative = into the sediment, positive = out of the sediment**, by the
compilers' own entry rule. This is **not** stated in Table 2 or elsewhere on the sheets; it is stated in the `Edit Notes` of
individual rows: "in paper positive fluxes were into sediment, but data was entered in our method (negative into sediment)"
(32 rows); "SOC plotted as positive but represented sediment uptake of O2 ... We changed flux sign to negative to indicate O2
uptake by sediments" (24 rows); "All SOC made negative" (1 row) and "All SOC flux entered as negative values" (11 rows). The
data agree: SOC is negative or zero on every row (2,340 negative, 25 zero, none positive; the extract asserts SOC <= 0 and
raises on a positive value), and the nutrient fluxes are mostly positive (release): NH4 flux 1,981 positive, 258 negative, 74 zero; PO4
1,309 / 358 / 156; DSi 1,077 / 46 / 12; NO2 97 / 46 / 12; NO3 843 / 427 / 26; NO23 246 / 268 / 71. (The original papers'
conventions differ; the compilers converted to this one. The sign of studies the Edit Notes do not mention is taken as the
compilers' entry convention, not independently verified.) Because the sign is meaningful, `negative_value` is **not** put on
fluxes or SOC.

## Mapping

- **Sample grain and J1 (a refinement of the plan).** The plan keys a sample on reference, station, lat, lon and date. That
  key cannot be stored: inside it, rows can differ in salinity, temperature, bottom-water O2, month and season, all of which
  sit on the one `sample_dim` row (for example Grenz et al. 2000 changes salinity every row; two Jorgensen and Sorensen rows
  share a Date but differ in Month, Season and temperature). So `sample_key` = the plan's key plus raw `Month`, `Year`,
  `SEASON`, `Salinity`, `Temp (°C)`, `DO(uM)`: 2,731 samples (2,715 under the plan's key). The sample row is asserted
  single-valued. Each J1 attribute then gets **one fact per (sample, distinct value)**: repeats collapse (3 collapsed; 6 under
  the plan's key, giving 3,241 facts as the plan predicted), and a sample with two different values of one attribute keeps both
  with `covariate_conflict` (36 facts in 16 sample-attribute pairs, nothing averaged). Each J1 fact's `source_row_ref` is the
  first raw row of the sample that carries the value.
- **Slots** (Table 2 "Bottom water ..."): `Salinity` (unitless) -> `water_salinity`; `Temp (°C)` -> `water_temperature_c`;
  `DO(uM)` -> `bottom_water_o2` (`uM`), the slot (not J1) because Table 2 says bottom water and the O2 sits beside a benthic
  rate. **Water depth** (2,665 rows; `ND` on 214) is `location.water_depth_m` (as Chang and SCOC), not the water-column
  `sample_depth_m`, and is part of `location_key`.
- **Locations: 1,208**, `location_key` = REGION | Estuary Name | Station Name | lat | lon | water depth | state/area | country |
  general characteristics (every stored field, so one key never carries two values). `site_name` = Estuary Name,
  `source_site_id` = Station Name, `admin_division` = state/area, `bioregion` = REGION (the source's continent / world-region
  label), `geomorphic_setting` = general characteristics (free text, 146 blank or ND; raw, **not mapped** to `setting`). There
  are 713 estuary x station pairs (the plan's "171" was a different count). Coordinates are present and valid on every row.
  **Habitat is NULL, unflagged** (D3: the source labels no habitat and states no scope): the label map declares the habitat
  column with one empty-label row (`boynton-flux,habitat_type,,habitat,`).
- **Dates (one rule).** `sampling_year` = the `Year` column when it is a number, else NULL with `year_missing` (75 rows: ranges
  such as `1979-1981` and `OSN, averaged several yrs`); `sampling_month` = the `Month` column when it is one month name (92 rows
  are not: ranges, `ND`, `Spring/Summer`). The `Date` column is not loaded: it is `ND` on 95 rows and disagrees with `Year` on 8
  and with `Month` on 16 (counted in the report; where it does, `Month`, `Season` and `Year` agree with each other).
- **Methods: 964.** The seven rate attributes get one method row per distinct (attribute, technique, incubation fields):
  `technique` = the raw `Method/measurement technique` (30 spellings); `in_situ_ex_situ` by the written rule `in situ ...` ->
  `in situ`, `lab ...` or `shipboard ...` -> `ex situ` (anything else raises); `light_dark` from `Photic/Aphotic` (the
  incubation condition, Table 2): `Aphotic` -> `dark` (2,630 rows), `Photic` -> `light` (41), `Aphotic&Photic` -> `dark and light`
  (8), `ND` and `typically dark at sediment surface` -> NULL. Params (`ND` stored as null): measurement frequency, incubation
  hours, stirring, flow-through or batch, the raw photic text, light-reaches-bottom, core diameter, length, sediment height,
  volume, area, wet/dry/normal, both `pub or us` columns and the `Edit Notes` text verbatim (they document source errata, for
  example "core area was calculated wrong in paper, correction made"). The other attributes (RES, sediment variables, J1) have
  no method information in the file: one `method_id_null` row each. `incubation_type` is NULL.
- `sd`, `se`, `n` are NULL (the file has none).

## Missing codes

`ND` (not determined) -> no value, counted per column in the report. `OSN` ("other reason, see notes") in a value column: 3 cells
(`NH4(uM)`) and 27 in the `DO(uM)` slot; no value, counted in the report, no fact. (The plan said "NULL + a flag", but there is
no fact for a flag to sit on; no flag was invented. The reason is in the row's `Edit Notes`.) Any other text in a numeric column
raises.

## References

180 `Reference` strings -> `ref:<string>` keys (all used). Two are composite cites, split into one citation per paper under
a `a+b` key: `Hopkinson 1985 and 1987` and `Tucker et al 2014, Hopkinson et al 2001, Giblin et al 1997`; 183 citation rows in
all. Full text and DOIs come from the `DataRefs` sheet by an explicit crosswalk in `extract.py`: the unique first-author surname
plus first year (and, for "A and B" strings, a second-author check), and an explicit table for every string the rule cannot
settle (misspellings: Balzar, Fergurson, Fluxzilla, Samuellsson, Sosrensen; two-author vs et-al pairs: Cowan, Smith and Teal).
Same author and year twice (Cook 2004 / 2004a, Rysgaard 1996 / 1996a) is settled by the entry's title naming the rows' own
system (Bremer River; Huon estuary; Arcachon and Etang du Prevost lagoons; Young Sound). **176 of 183 citations carry the entry
text, 130 carry a DOI.** Seven pieces have no `DataRefs` entry and get text-only citations (`bibliography_id` = the string,
year from the string): `Hartwig 1974` (the only Hartwig entry is dated 1976; not assumed to be the same paper, and that entry is
the one DataRefs line no string cites), `Hopkinson 1987`, `Hopkinson et al 2001`, `Giblin et al 1997`, `Munoz et al 2005`,
`2006` and `2007` (DataRefs lists Munoz 2004 only). The extract raises on any string that is neither resolved nor listed.

## Flags

(Facts; `extract_report.json` has the rest.) `zero_value`: 454; `year_missing`: 237 (75 rows); `source_site_averaged`: 1,815
(`sites averaged` = Y on 305 rows) and `source_time_averaged`: 659 (`time averaged` = Y on 91); `covariate_conflict`: 36;
`negative_value`: 1 (bottom-water NH4 = -0.3 uM; fluxes and SOC are never flagged for sign). No row repeats another once `ID#` is
ignored (asserted), so no `duplicate_source_row`.

## Not loaded

`Temp. Range` (an annual range, text), `Date` (see above), `SEASON` beyond its place in the sample key, `Wet/Dry/Normal` and the
other incubation text beyond method params; `pH` is loaded. Nothing else is dropped.

## Row accounting

2,879 source rows; 13,449 non-J1 numeric cells = 13,449 facts; 3,247 J1 numeric cells = 3,244 J1 facts + 3 collapsed repeats;
0 skips; the `ND` and `OSN` cells give neither. 2,731 samples, 1,208 locations, 964 methods, 180 references, 183 citations. After the
central build every fact was checked against the raw cell it points to by code that does not use `extract.py` (value, unit,
reference, coordinates, estuary, station, depth, year, the three sample slots, technique and in-situ/light rules, and every flag
recomputed): 16,693 of 16,693 agree; the set of non-J1 numeric cells equals the set of non-J1 facts, and every one of the 3,247 J1
cells has a fact with the same sample, attribute and value. One-study checks, predicted from the raw sheet alone (non-J1 cells
plus distinct J1 values per sample): Grenz et al. 2000 135 + 108 = 243, Fisher et al. 1982 29 + 0 = 29, Bailey et al. 2003 660
+ 240 = 900; all matched. Adding this source left the other nine sources' content in `compilations.duckdb` unchanged
(333,336 facts = 316,643 + 16,693). Two runs of `extract.py` give identical tables. `source_row_ref` is
`Published2016v3WorldFlux DataSet.xlsx[WorldFluxDataSet2016v3]#data_row=<0-based row>:<attribute>`.

## Overlap with other compilations

Never merged. SCOC and Jorgensen (oxygen uptake in estuaries), Chang and Ray (nutrient fluxes and nitrogen removal) overlap in
systems and sometimes in studies; `fulweiler-nfix` co-authors some of the same Narragansett Bay work. The `Fluxzilla` rows are
Chesapeake Bay monitoring data from the Gonzo group's own web site, not a paper.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
