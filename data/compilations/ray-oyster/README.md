# ray-oyster (oyster meta-analysis: sediment and oyster fluxes)

Ray, N.E., Fulweiler, R.W. (2020). *Meta-analysis of oyster impacts on coastal biogeochemistry* [Dataset]. figshare,
https://doi.org/10.6084/m9.figshare.12488753.v1 (paper: Ray & Fulweiler, *Nature Sustainability* 4, 261-269, 2021,
https://doi.org/10.1038/s41893-020-00644-9).

- **Version:** `v1 (2020-10-08)`, read from the figshare record by the integration plan; the workbook states no version.
- **Access date:** 2026-11-15. **License:** CC BY 4.0 (figshare record).
- **Raw file:** `oyster_meta - excel for figshare.xlsx`: `Overview` (empty; asserted), `sediment fluxes` (37 rows) and
  `oyster fluxes` (17 rows).

The data behind a literature meta-analysis of how oysters change nutrient and gas fluxes: 54 rows from 52 study labels, each
a study-level mean, SD and n (the paper's text says 45 studies met the criteria out of 1,012 screened; the screening list is
not in the file, so `study_screening_dim` is empty).

**Facts:** 241, one per (row, solute, arm) with a mean: the `sediment fluxes` sheet pairs a **control** and an **oyster**
arm for 10 solutes (200 facts, 100 per arm), the `oyster fluxes` sheet gives the oysters' own flux for 8 solutes with no
control (41 facts). `value` = the mean, `sd` and `n` the paired columns (every mean has both; asserted).

| Attribute prefix | Type / subfield | Facts (sediment / oyster) | Units |
|---|---|---|---|
| `ray_*_n2` | C4 `denitrification` | 56 / 6 | µmol m−2 h−1 / µmol ind−1 h−1 |
| `ray_*_nh4` | C3 `ammonium_flux` | 50 / 12 | same |
| `ray_*_po4` | C3 `phosphate_flux` | 24 / 5 | same |
| `ray_*_nox` | C3 `nitrate_nitrite_flux` | 24 / 5 | same |
| `ray_*_no3` | C3 `nitrate_flux` (extension) | 18 / 2 | same |
| `ray_*_no2` | C3 `nitrite_flux` (extension) | 4 / 3 | same |
| `ray_sediment_si` | C3 `silicate_flux` | 4 / no oyster column | µmol m−2 h−1 |
| `ray_*_n2o` | C8 `n2o_flux` | 8 / 5 | same |
| `ray_*_ch4` | C8 `ch4_flux` | 6 / 3 | same |
| `ray_sediment_co2` | C8 `co2_flux` | 6 / no oyster column | µmol m−2 h−1 |

**Units are not in the file.** They are taken from the paper's Methods and recorded in `method.params`: "We converted all flux
rates to either µmol g dry tissue weight−1 h−1 (oysters) or µmol m−2 h−1 (sediments)" and then, for oysters, to µmol per
individual and hour (per-gram rates were multiplied by 2.93 g per individual). So **sediment fluxes are µmol m−2 h−1 for every
solute** and **oyster fluxes µmol ind−1 h−1 for every solute**. The plan expected N2O and CH4 oyster fluxes in nmol; that is the
paper's Table 1, which prints them multiplied by 1,000: the file's values are the micromole numbers (the *Saccostrea glomerata* N2O
of 0.067662 is 67.66 in Table 1, *Ostrea edulis* 0.000469 and 0.000205 are 0.47 and 0.21). The sheets' magnitudes agree with this
(sediment N2O within ±0.3, CO2 up to 30,468). Sediment and oyster fluxes are separate attributes (`ray_sediment_*`,
`ray_oyster_*`) because the unit and the object differ.

## Mapping

- **Control vs oyster** is `method.experimental_status`: `control` and `oyster` (sediment sheet: the paper compares control sediment
  with sediment beneath or adjacent to oysters; the file does not say whether control is bare), and `oyster (the oysters' own flux; no
  control)` for the oyster sheet. Both arms are loaded; filtering is downstream.
- **N2 technique.** `n2_method` / `Denitrification Method` (`n2_ar`, `n2/ar`, `IPT`, `acetylene`; blank on 9 sediment and 11 oyster
  rows) is `technique` of the N2 facts only (sediment N2 facts: `n2_ar` 40, `IPT` 14, `acetylene` 2; oyster N2 facts: `n2/ar` 5, `IPT` 1); every other
  solute is `method_id_null`. Raw spellings are kept.
- **Notes.** Five sediment rows have a free-text `notes` field; it is carried verbatim in `method.params.note` on every fact of that
  row. One note (Hoellein & Zarnoch 2014) says the denitrification is really a *potential* (acetylene block) and NH4 comes from
  ammonification assays: the 4 N2 and NH4 facts of that row carry the flag `potential_rate`. The others (a pooled-figure study,
  ambient vs elevated nitrate rows, a study that also used acetylene) are not flags; the elevated-nitrate row is identifiable
  from its note.
- **n.** `n` is stored as an integer; the authors give **10.5** (the median of a range, per the paper) on 8 facts, which an integer
  column cannot hold: those facts have `n` NULL and the reported number in `method.params.n_reported`.
- **SD.** The paper converted every SE to an SD and every per-gram rate to per individual, but the file does not say which rows, so
  there is **no per-fact `sd_converted_from_se` or `converted_by_source` flag** (it would be wrong for the rows reported as SD);
  `method.params.sd_note` says it for every fact. The plan called for both flags; they are registered but unused here. `se` is
  NULL.
- **Locations (47).** `Coordinates` / `Location` is one `lat, lon` string, parsed strictly; 6 sediment rows have none
  (28 facts flagged `coords_missing`). `habitat_type` = `Habitat` raw on the sediment sheet: `Reef` (14 locations) -> shellfish
  reef / bed; `Aquaculture` (14) and `Mesocosm` (2) have no equivalent and stay blank; the oyster sheet has no habitat column (NULL).
  No site name, country or setting. `location_key` = lat | lon | habitat (rows without coordinates:
  `nocoords|<label>|<habitat>`).
- **Samples (54):** one per row; `taxon` = `Species` (rank `species`). The `Year` is the paper's publication year, so no sampling
  year.
- **IPT rows.** Three sediment rows named `... - IPT` (Higgins et al. 2013, Hoellein et al. 2015, Hasset 2015) hold only the
  isotope-pairing N2 pair of the study named before them and have no species, coordinates, habitat or title. They are kept as
  their own references and locations without those labels, not merged into the study they follow.

## References

`reference_key` = `ref:<abbreviated_name>` (the file's own label, whitespace-normalised): **52 references and 52 citations**. The
paper title, authors and year go to `citation_dim` (the `Journal` column has no contract column and is not loaded; it is
recoverable through `source_row_ref`); the 3 IPT rows have no title (3 citations without one). A label that occurs twice (Caffrey et
al. 2016 and McCarthy et al. 2019, both on the oyster sheet for two species) must have identical title, authors and year (asserted).
There are no DOIs. **Different labels are different references even for one paper**: six pairs share a title (Smyth et al. 2015
`- low`/`- high`; Humphries et al. 2016 `Aqua`/`Reef`; Jackson 2019 `Ch.4`/`ch. 5`; Ray et al. 2019b / `Ray et al., 2019`; Smyth et al.
2013 / 2013b; Erler et al. 2017 on the two sheets, spelled differently). Nothing is merged; the DOI-resolution job can tie them.

## Flags

(Facts; `extract_report.json` has the rest.) `coords_missing`: 28. `zero_value`: 1. `potential_rate`: 4. `negative_sd`: 1.
**`negative_sd`** (registered for this source on your decision): the oyster sheet gives SD = **-0.022** for the Erler et al. 2017
nitrate flux (mean -0.027, n = 5). The value is kept as reported, the fact carries the flag, and the extract declares this one case
and raises on any other negative SD (and if this one disappears). `duplicate_source_row`: 0. Negative fluxes are real and unflagged
(5 sediment N2, 13 each of nitrate and NOx, ...).

Values are not otherwise screened. Notable extremes (kept): oyster NH4 **1,016.81** µmol ind−1 h−1 (Srna & Baggaley 1976; the paper's
Table 1 gives a *C. virginica* mean of 9.44, so this value may be in other units or excluded there), sediment N2 up to 4,199 (the
acetylene row) and CO2 up to 30,468 µmol m−2 h−1.

## Not loaded

Nothing in the numeric columns is dropped. Not stored: the `Journal` text; the `Overview` sheet (empty; the plan's mention of a
drawing text box is not readable by the extract); the study screening (1,012 studies) which is not in the file.

## Row accounting

54 rows (37 + 17); 241 mean cells = 241 facts, 0 excluded (`skip_report.jsonl` empty). 54 samples, 47 locations, 61 methods (one per
attribute, arm and technique, plus one per row with a note or non-integer n), 52 references and citations. After the central build
every fact was checked against the raw cells it points to by code that does not use `extract.py` (value, SD, n, units, arm status,
coordinates, habitat, taxon, reference, title, authors, year, technique, the mean / SD / n column names, the note, and every flag
recomputed): 241 of 241 agree and the set of mean cells equals the set of facts. A two-study check (Caffrey et al. 2016: 7; Smyth et
al., 2013a: 6; both predicted from the raw sheets alone) matched. Adding this source left the other eight sources' content in
`compilations.duckdb` unchanged (316,643 facts = 316,402 + 241). Two runs of `extract.py` give identical tables. `source_row_ref` is
`<file>[<sheet>]#data_row=<0-based row>:<attribute>:<arm>` (`ctrl`, `oys`, or `oyster`).

## Overlap with other compilations

Never merged. Sediment nutrient fluxes also appear in `boynton-flux` (estuaries, not built yet) and benthic N removal in
`chang-denitrification`; GHG fluxes in `rosentreter-ghg`. The file has no DOIs, so shared studies are found downstream by title.

**J1 (joined water-chemistry fields):** not applicable; the file has no in-situ water concentrations, so there are no J1 facts.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
