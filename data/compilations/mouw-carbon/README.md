# mouw-carbon (global ocean particle fluxes from sediment traps and 234Th)

Mouw, C.B., Barnett, A., McKinley, G.A., Gloege, L., Pilcher, D. (2016). *Global ocean particulate organic carbon flux merged with satellite
parameters.* PANGAEA, DOI 10.1594/PANGAEA.855600. The flux dataset used here is DOI 10.1594/PANGAEA.855594 (supplement to *Earth System
Science Data* 8(2), 531-541, DOI 10.5194/essd-8-531-2016).

- **Version:** `PANGAEA 855594 (DOI registered 2016-01-11)`. PANGAEA gives no version number, so the registration date is recorded.
  **Access date:** 2026-11-15. **License:** CC-BY-3.0 (file header).
- **Raw files:** `datasets/GO_flux.tab` (15,792 rows, 41 columns) and `summary.txt`. The other five `.tab` files (chlorophyll, microplankton,
  mixed layer, SST) are satellite and climatology products matched to the traps. They are **not opened** or loaded.

Particle fluxes from sediment traps and 234Th stations: 43 studies, 1976 to 2012, 15,205 trap rows and 587 234Th rows, from the shelf to the
abyss. One row is one deployment with the fluxes of several analytes.

## Facts (57,045)

One fact per numeric cell of seven flux columns, all `mg/m**2/day`. `sd` comes from the paired column where the file has one (834 facts).

| Attribute | Type / subfield | Facts |
|---|---|---|
| `mouw_poc_flux` | C6 `poc_flux` | 14,055 |
| `mouw_pon_flux` | C6 `pon_flux` | 12,067 |
| `mouw_total_mass_flux` | C6 `total_mass_flux` | 13,418 |
| `mouw_caco3_flux` | C6 `biogenic_silica_carbonate_flux` | 5,627 |
| `mouw_psi_flux` (particulate Si) | same | 8,046 |
| `mouw_psio2_flux` | same | 3,515 |
| `mouw_psioh4_flux` | same | 317 |

The four CaCO3 and Si analytes share one subfield with one attribute each (17,505 facts). Particulate Si and silica are different columns and
are not converted into each other. The totals match the plan exactly.

## How it maps

- **Habitat** is Water column for every location, from the dataset's scope, so `habitat_from_scope` is on all facts.
- **Samples:** one per row with a loaded fact (15,397). `sample_depth_m` is the trap depth. Year and month come from the deployment date (8 rows
  have none). The deployment duration varies per row, so it is text in `depth_notes`.
- **Locations (716):** keyed on latitude, longitude and elevation. The file's own location identifier is not used, because it is shared across
  studies with different coordinates. `water_depth_m` is minus `Elevation` (the file stores depth as negative elevation, and the sign flip is
  recoverable). 469 locations have none. The ETOPO1 `Bathy depth` is a model layer and is not loaded.
- **Methods (234):** one per attribute, type and trap type. `technique` is `<Type>; <trap type>` in the file's spelling.
- **References:** `mouw_id:<ID>` for 43 IDs. The file's `Reference` text is the same for every row of an ID except IDs 2 and 14.
  - **ID 14 is BATS, by inference** (820 rows, flag `reference_inferred` on 2,364 facts). Its text is blank, but the rows are one location and
    date range that matches the BATS entry in the paper's Table 2 (`http://bats.bios.edu`, a data archive, not a paper). The source does not
    say so, and the extract asserts the premise.
  - **ID 2 is unresolved** (3,613 rows, 77 locations, flag `reference_unresolved` on 12,576 facts). No text and no Table 2 entry matches. It
    has a reference row and no citation.
  - **ID 7** cites two papers in one string and is split into two citations.
  - **DOIs:** 23 of 43 citations get a DOI from the file header's related-citation list, matched only when author and year identify exactly one
    entry. Data archives and personal communications are never matched. 16 strings are unmatched and listed in the report.
  - 10,506 facts cite a personal communication (`pers_comm`).

## Not loaded

Total carbon flux (419 cells, no plan decision), and phase-2 `other` analytes: PIC, POP, particulate Al, chlorophyll, phaeopigments, Fe, Mn, Ba,
detrital and Ti. Trap `Area` has no field. All are counted per column in the report.

## Flags

`habitat_from_scope` 57,045, `reference_unresolved` 12,576, `pers_comm` 10,506, `reference_inferred` 2,364, `zero_value` 891, `year_missing` 16,
`negative_value` 3 (particulate Si, kept).

## Row accounting

57,045 numeric cells = 57,045 facts, nothing skipped or collapsed. All facts were checked back to the raw file by an independent parse. A one-study
check predicted 128 facts and matched, and two runs give identical output.

## Overlap

None expected (open ocean). Nothing was checked against `rosentreter-ghg` or `fulweiler-nfix`.

Back to the [compilations index](../README.md).
