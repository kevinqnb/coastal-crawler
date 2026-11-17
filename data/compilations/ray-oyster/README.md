# ray-oyster (oyster meta-analysis: sediment and oyster fluxes)

Ray, N.E., Fulweiler, R.W. (2020). *Meta-analysis of oyster impacts on coastal biogeochemistry.* figshare, DOI
10.6084/m9.figshare.12488753.v1 (paper: *Nature Sustainability* 4, 261-269, 2021, DOI 10.1038/s41893-020-00644-9).

- **Version:** `v1 (2020-10-08)`, from the figshare record. **Access date:** 2026-11-15. **License:** CC BY 4.0.
- **Raw file:** `oyster_meta - excel for figshare.xlsx`, with `Overview` (empty), `sediment fluxes` (37 rows) and `oyster fluxes` (17 rows).

The data behind a meta-analysis of how oysters change nutrient and gas fluxes: 54 rows from 52 study labels, each a study-level mean, SD
and n. The paper screened 1,012 studies, but that list is not in the file, so `study_screening_dim` is empty.

## Facts (241)

One fact per (row, solute, arm) with a mean. The sediment sheet pairs a **control** and an **oyster** arm for 10 solutes (200 facts), and
the oyster sheet gives the oysters' own flux for 8 solutes with no control (41 facts). `value` is the mean, with `sd` and `n` from the
paired columns.

| Solute | Type / subfield | Facts (sediment / oyster) |
|---|---|---|
| N2 | C4 `denitrification` | 56 / 6 |
| NH4 | C3 `ammonium_flux` | 50 / 12 |
| PO4 | C3 `phosphate_flux` | 24 / 5 |
| NOx | C3 `nitrate_nitrite_flux` | 24 / 5 |
| NO3 | C3 `nitrate_flux` (extension) | 18 / 2 |
| NO2 | C3 `nitrite_flux` (extension) | 4 / 3 |
| Si | C3 `silicate_flux` | 4 / none |
| N2O | C8 `n2o_flux` | 8 / 5 |
| CH4 | C8 `ch4_flux` | 6 / 3 |
| CO2 | C8 `co2_flux` | 6 / none |

Attributes are `ray_sediment_<solute>` and `ray_oyster_<solute>`, kept separate because the unit and the object differ.

**Units are not in the file.** They come from the paper's Methods: every sediment flux is **µmol m−2 h−1** and every oyster flux is
**µmol ind−1 h−1** (per-gram rates were converted using 2.93 g per individual). The paper's Table 1 prints N2O and CH4 oyster fluxes
in nmol, which are the file's numbers times 1,000, so the file is in µmol.

## How it maps

- **Control or oyster** is `method.experimental_status`: `control`, `oyster`, or `oyster (the oysters' own flux; no control)`. Both arms
  are loaded, and filtering is downstream.
- **N2 technique:** the denitrification method (`n2_ar`, `n2/ar`, `IPT`, `acetylene`) is `technique` for N2 facts only. Other solutes
  are `method_id_null`.
- **Notes:** five rows have a free-text note, copied verbatim into `method.params.note`. One says the denitrification is really a
  potential rate (acetylene block), so its 4 N2 and NH4 facts get `potential_rate`.
- **`n`:** stored as an integer. The authors give 10.5 (a median of a range) on 8 facts, which an integer cannot hold, so those facts have
  `n` NULL and the number is in `method.params.n_reported`.
- **SD:** the paper converted every SE to an SD and every per-gram rate to per individual, but the file does not say which rows, so there
  is no per-fact `sd_converted_from_se` or `converted_by_source` flag. `method.params.sd_note` says it for every fact. `se` is NULL.
- **Locations (47):** `Coordinates` is one `lat, lon` string, parsed strictly. 6 sediment rows have none (28 facts, `coords_missing`).
  `Habitat`: `Reef` gives Shellfish reef / bed, and `Aquaculture` and `Mesocosm` stay blank. The oyster sheet has no habitat column.
- **Samples (54):** one per row. `taxon` is `Species` (rank `species`). There is no sampling year.
- **IPT rows:** three sediment rows named `... - IPT` hold only the isotope-pairing N2 result of the study named before them, with no species
  or coordinates. They stay separate and are not merged into that study.
- **References:** `ref:<abbreviated_name>`, 52 references and citations with title, authors and year. The file has no DOIs. Different
  labels are different references even when they are one paper (six pairs share a title). Nothing is merged.

## Not loaded

The `Journal` text (recoverable through `source_row_ref`) and the empty `Overview` sheet.

## Flags

`coords_missing` 28, `zero_value` 1, `potential_rate` 4, `negative_sd` 1. The oyster sheet gives an SD of -0.022 for the Erler et al. 2017
nitrate flux. It is kept and flagged, and the extract raises on any other negative SD. Negative fluxes are real and unflagged.
Notable extremes are kept: oyster NH4 of 1,016.81 µmol ind−1 h−1 (Srna and Baggaley 1976; the paper's Table 1 mean for that species is
9.44, so the unit may differ), sediment N2 up to 4,199, and CO2 up to 30,468 µmol m−2 h−1.

## Row accounting

54 rows; 241 mean cells = 241 facts, nothing skipped. All facts were checked back to their raw cells, two one-study checks (Caffrey 2016:
7 facts; Smyth 2013a: 6) matched their predictions, and two runs give identical tables.

## Overlap

Sediment nutrient fluxes also appear in `boynton-flux`, benthic N removal in `chang-denitrification`, and GHG fluxes in `rosentreter-ghg`.
The file has no DOIs, so shared studies are found downstream by title.

Back to the [compilations index](../README.md).
