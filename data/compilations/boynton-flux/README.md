# boynton-flux (World Flux Data Set: sediment-water oxygen and nutrient fluxes)

Boynton, W., Ceballos, M.A. (2017). *World Flux Data Set 2016.* Mendeley Data, V3, DOI 10.17632/8gc3ys3jx6.3 (paper: Boynton et al.
2018, *Estuaries and Coasts*).

- **Version:** `Mendeley Data v3 (2017-07-21)`. Only v3 is loaded. The v1 and v2 workbooks in the directory are superseded and never
  read. **Access date:** 2026-11-15. **License:** CC BY 4.0.
- **Raw file:** `Published2016v3WorldFlux DataSet.xlsx`, using the sheets `WorldFluxDataSet2016v3` (2,879 rows, one incubation each) and
  `DataRefs` (177 reference entries). `Table2DataVariables` is a picture of the paper's variable table, read by eye.

Net sediment-water oxygen and nutrient fluxes from 227 estuarine and coastal systems (1969 to 2014), with bottom-water conditions and some
sediment properties beside each flux.

## Facts (16,693)

No skips. 13,449 non-J1 cells are 13,449 facts, and 3,247 J1 cells are 3,244 facts plus 3 collapsed repeats. Units are the header spellings.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `boynton_soc` | B5 `total_o2_uptake` | 2,365 | umol/m2/hr |
| `boynton_nh4_flux` | C3 `ammonium_flux` | 2,313 | umol/m2/hr |
| `boynton_po4_flux` | C3 `phosphate_flux` | 1,823 | umol/m2/hr |
| `boynton_no2_flux` | C3 `nitrite_flux` (extension) | 155 | umol/m2/hr |
| `boynton_no3_flux` | C3 `nitrate_flux` (extension) | 1,296 | umol/m2/hr |
| `boynton_no23_flux` | C3 `nitrate_nitrite_flux` | 585 | umol/m2/hr |
| `boynton_dsi_flux` | C3 `silicate_flux` | 1,135 | umol/m2/hr |
| `boynton_water_respiration` | B9 `community_respiration` | 154 | gO2/m2/day |
| `boynton_sediment_chl_a` | B1 `sediment_chlorophyll_a` | 1,021 | mg/m2 |
| `boynton_poc` | C1 `organic_carbon` | 1,454 | %DW |
| `boynton_pon`, `boynton_pp` | `other` (particulate organic N, particulate P) | 721 / 427 | %DW |
| `water_ammonium`, `water_phosphate`, `water_nitrate`, `water_nitrate_nitrite`, `water_silicate` | J1 bottom-water concentrations | 990 / 841 / 495 / 584 / 285 | uM |
| `water_ph` | J1 `ph` | 49 | unitless |

POC, PON and PP are percent of dry weight (headers and Table 2 agree; the plan's "mg C m-2" was wrong). PON and PP are `other` because
particulate N and P are not total N or P. "Usually top 1 cm" is text in `depth_notes`. 24 rows have only `ND` values and give no fact.

## Sign convention

Fluxes are carried as given. **Negative means into the sediment and positive means out of it**, by the compilers' entry rule. Table 2
does not say this, but the `Edit Notes` of individual rows do (for example "data was entered in our method (negative into sediment)", on 32
rows, and "All SOC made negative"). The data agree: SOC is never positive (the extract asserts it) and nutrient fluxes are mostly
positive. Studies the notes do not mention are assumed to follow the same entry rule. Because the sign is meaningful, `negative_value` is
not put on fluxes.

## How it maps

- **Samples (2,731).** The plan's key (reference, station, coordinates, date) cannot be stored, because rows inside it differ in salinity,
  temperature, oxygen, month and season, which all sit on one sample row. So the key adds raw `Month`, `Year`, `SEASON`, `Salinity`,
  `Temp` and `DO` (2,715 samples under the plan's key). Each J1 attribute gets one fact per sample and distinct value. Repeats collapse (3),
  and a sample with two different values keeps both with `covariate_conflict` (36 facts, 16 pairs). Under the plan's key the J1 count would be
  3,241, as the plan predicted.
- **Sample slots:** `Salinity` gives `water_salinity`, `Temp` gives `water_temperature_c`, and `DO` gives `bottom_water_o2` (a slot, not J1,
  because it sits beside a benthic rate). Water depth is `location.water_depth_m`.
- **Locations (1,208):** keyed on region, estuary, station, coordinates, depth, state, country and system description. There are 713
  estuary-station pairs. The free-text system description is kept as raw `geomorphic_setting` but not mapped to a setting. **Habitat is
  NULL** and unflagged, because the source gives no label and states no scope.
- **Dates:** `sampling_year` is `Year` when numeric, otherwise NULL with `year_missing` (75 rows). `sampling_month` is `Month` when it is
  a single month name. The `Date` column is not loaded (blank on 95 rows and inconsistent with `Year` and `Month` on some).
- **Methods (964):** the seven rate attributes get one method row per technique and incubation setup. `technique` is the raw technique
  (30 spellings). `in_situ_ex_situ` follows a written rule (`in situ` gives in situ, `lab` or `shipboard` gives ex situ, anything else
  raises). `light_dark` follows `Photic/Aphotic`. Core size, incubation hours, stirring and the `Edit Notes` (source errata, verbatim) go
  in `params`. Other attributes have no method information.
- `sd`, `se` and `n` are NULL.
- **Missing codes:** `ND` gives no value. `OSN` ("other reason, see notes") gives no value either (3 NH4 cells, 27 DO cells); no fact exists
  for a flag to sit on. Any other text in a numeric column raises.
- **References:** 180 strings give `ref:<string>` keys. Two combine several papers and are split into one citation each (183 citations).
  Full text and DOIs come from `DataRefs` by a unique first-author-and-year rule plus an explicit table for misspellings and ambiguous
  years. 176 of 183 citations have the entry text and 130 have a DOI. Seven pieces have no entry (Hartwig 1974, Hopkinson 1987, Hopkinson
  2001, Giblin 1997, Munoz 2005 to 2007) and keep text-only citations.

## Not loaded

`Temp. Range` and `Date`. `Wet/Dry/Normal` and other incubation text appear only in method `params`.

## Flags

`zero_value` 454, `year_missing` 237, `source_site_averaged` 1,815, `source_time_averaged` 659, `covariate_conflict` 36, `negative_value` 1
(bottom-water NH4 of -0.3 uM).

## Row accounting

2,879 rows; 13,449 non-J1 cells = 13,449 facts; 3,247 J1 cells = 3,244 facts + 3 collapsed; the `ND` and `OSN` cells give neither. All facts
were checked back to their raw cells, and every J1 cell has a fact. Three one-study checks (Grenz 2000: 243 facts, Fisher 1982: 29, Bailey
2003: 900) matched their predictions, and two runs give identical tables.

## Overlap

SCOC and Jorgensen (oxygen uptake in estuaries), Chang and Ray (nutrient fluxes) overlap in systems and sometimes in studies, and
`fulweiler-nfix` shares some Narragansett Bay work. The `Fluxzilla` rows are Chesapeake Bay monitoring data, not a paper.

Back to the [compilations index](../README.md).
