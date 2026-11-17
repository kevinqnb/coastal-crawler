# rosentreter-ghg (coastal greenhouse-gas fluxes: estuaries and vegetated coast)

Rosentreter, J.A., Laruelle, G.G., Bange, H.W., Bianchi, T.S., Busecke, J.J.M., Cai, W.-J., et al. (2023).
*Coastal GHG data base (Rosentreter et al. 2023)* [Dataset]. figshare,
https://doi.org/10.6084/m9.figshare.22351267.v1 (paper: *Nature Climate Change*, 2023).

- **Version:** `v1 (2023-05-05)`, read from the figshare record by the integration plan; the workbook states no
  version.
- **Access date:** 2026-11-15. **License:** CC BY 4.0 (figshare record; the workbook states none).
- **Raw file:** `database_coastalGHG_Rosentreter et al._figshare.xlsx`: sheets `Metadata`, `Estuaries_CO2`,
  `Estuaries_CH4`, `Estuaries_N2O`, `Coastal_vegetation_CO2`, `Coastal_vegetation_CH4`, `Coastal_vegetation_N2O`,
  `Surface_areas`.

A literature compilation of site-level greenhouse-gas fluxes, one row per site and gas: 738 rows from 397 distinct
cited works (see References). The Metadata sheet says each value is data "extracted from publications / data
publisher and averaged per site over spatiotemporal gradients", so every value is a site average and carries no
year or depth.

**Facts:** 738, one per data row (every row has exactly one flux; asserted). Units are the header of each flux
column (asserted); nothing is converted.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `ghg_co2_estuary` | C8 `co2_flux` | 204 | g CO2 m2 d-1 |
| `ghg_ch4_estuary` | C8 `ch4_flux` | 157 | mg CH4 m2 d-1 |
| `ghg_n2o_estuary` | C8 `n2o_flux` | 123 | mg N2O m2 d-1 |
| `ghg_co2_vegetation` | C8 `co2_flux` | 37 | g C m2 yr-1 |
| `ghg_ch4_vegetation` | C8 `ch4_flux` | 162 | mg CH4 m2 d-1 |
| `ghg_n2o_vegetation` | C8 `n2o_flux` | 55 | mg N2O m2 d-1 |

Estuary and vegetation fluxes are **separate attributes** because the exchange interface differs: the estuary sheets
are water-air exchange over estuary water; the vegetation sheets are ecosystem-atmosphere exchange over mangroves,
salt marshes and seagrasses (recorded in `method.params.interface`). The vegetation CO2 unit is per year and per
carbon, unlike every other column (per day, per molecule): do not pool without conversion, which is downstream.

**Sign.** The workbook does not state a sign convention. The vegetation CO2 sheet is mostly negative (30 of 37), which
reads as uptake by the ecosystem, so positive is taken to mean a flux to the atmosphere; that is an inference, not a
statement of the source. Negative values are real fluxes and carry no flag (17 of 204 estuary CO2, 9 of 157 estuary
CH4, 11 of 123 estuary N2O, 4 of 162 vegetation CH4, 20 of 55 vegetation N2O). Values are not screened; the extremes are
vegetation CO2 -1,105.15 and 170.6 g C m2 yr-1 and vegetation CH4 1,474.24 mg CH4 m2 d-1.

## Mapping

- `lat` / `long` are decimal degrees as given (asserted in range). `site_name` is stripped of whitespace (the file has
  trailing spaces). `location_key` = lat | long | site_name | habitat_type | geomorphic_setting, so one site seen in the
  CO2, CH4 and N2O estuary sheets is one location: **702 locations** (456 estuary, 246 vegetated coast).
- **Type codes** are read from the Metadata sheet's own legend and asserted equal to the expected one: estuary 1 Tidal
  systems and deltas, 2 Lagoons, 3 Fjords (-> `geomorphic_setting`, raw; 358 / 45 / 53 locations); vegetation 1 Mangroves,
  2 Saltmarshes, 3 Seagrasses (-> `habitat_type`, raw; 100 / 110 / 36 locations).
- **Standard labels** (label map `rosentreter-ghg`): vegetation `Mangroves` -> mangrove forest, `Saltmarshes` -> tidal marsh,
  `Seagrasses` -> seagrass meadow. Estuary rows get the constant raw `habitat_type` `water column` (the scope of the sheet:
  water-air exchange over estuary water) -> **water column** (`bottom_type` NULL); this is a scope, not a label in the
  file, so all 484 estuary facts carry `habitat_from_scope`. Settings: Lagoons -> coastal lagoon, Fjords -> fjord, and
  `Tidal systems and deltas` is left blank (the class mixes two settings; nothing is guessed). Vegetation rows have no
  setting.
- `sample`: one per data row (keyed `<sheet>:row<n>`); no year, depth or taxon.
- `method`: the source gives none per row, so one `method_dim` row per attribute (6), `method_id_null`. `params` holds the
  interface, the flux column header and the Metadata statement that values are averages per site. That statement holds
  for every row, so it is recorded here and not as a per-fact flag (the registered `source_site_averaged` means an
  average over sites, which is not what the source says); `sd`, `se` and `n` are NULL.
- `region` (RECCAP2 region, codes 1 to 10) is not loaded; it is recoverable via `source_row_ref`.

## References

The data columns are `reference` (free author-year text such as `Barr et al. (2010,2012)`) and `full_citation`
(Nature-style entries joined by `; `). **The same `reference` string can stand for different papers**: 23 of the 380
distinct `reference` strings occur with more than one `full_citation` text (e.g. `Alongi et al. (2008)` with three), and 5
`full_citation` texts occur under more than one `reference` string. So the study is identified by the full citations, not by the
author-year string:

- `full_citation` is whitespace-normalised (the file has NBSP) and split on `; ` into one **citation piece** per cited paper; a
  piece with a DOI in its text (`doi:10...` / `doi.org/10...`) gets `doi:<lower-case doi>`, else `ref:<piece text>`.
  A row citing several papers gets the piece keys joined by `+` (rule 3.4) and one `citation_dim` row per piece
  (`title` = the piece text, `year` = the parenthesised year when there is one, `bibliography_id` = the row's `reference`
  string). The result is **397 references** and **483 citations** from 465 distinct pieces; 679 rows cite one paper, 59 cite two
  to ten. Only 10 pieces carry a DOI (10 distinct), mostly datasets (PANGAEA, AmeriFlux, EDI, USGS); the rest are left for the
  DOI-resolution job.
- The number of pieces must equal the number of papers the `reference` string names (`count_papers`: `;` parts, a year list
  `(2010,2012)` counting each year). **Nine `reference` strings disagree** and are declared in the extract with the rule that
  resolves each; the set of disagreeing strings must equal the declared set exactly (an extra or a missing one raises):
  - `Miyajima et al. (2009); T. Miyajima (pers. comm.)` (two spellings): two parts, one citation; the personal-communication
    part has none.
  - `Burgos et al. (2018), Ferron et al. (2007)`, `Linto et al. (2014), Barnes et al. (2006)`, `Dutta et al. (2017), Ganguly et
    al. (2008)` and `Kreuzwieser et al. (2003), Allen et al. (2007,2010)`: papers joined by a comma in `reference`; the split is
    asserted to give 2, 2, 2 and 3 pieces.
  - `Adiyanti et al. (2016); Jeffrey et al. (2018); Wells et al. (2020a)`: the text has `(2016;.` where a separator should be, so the
    three papers cannot be split reliably; the whole text is one citation piece (one reference key, three papers).
  - Two `CH4 data available at PANGAEA, DOI: ...` strings: a PANGAEA citation whose authors are themselves joined by `;`; the
    whole text is one piece and its DOI gives the key.
- A `reference` that names a personal communication (`pers.comm.`, ten strings, 14 facts) gets the flag `pers_comm`. The Metadata
  says such data were personal communications in 2020 and have been published since 2021, so the citation is then the published
  source (an AmeriFlux, EDI or USGS dataset, or a paper).
- The `MEMENTO database, contact person` row is not a personal communication (no flag).

## Flags

(Facts; `extract_report.json` has the split by sheet.)

- `habitat_from_scope`: 484 (all estuary facts). `coords_integer_degree`: 58 (both coordinates whole degrees: 53 estuary CO2 rows
  and 5 estuary CH4 rows; the plan expected "many").
- `zero_value`: 9 (5 estuary CO2, 2 vegetation CH4, 2 vegetation N2O). `pers_comm`: 14 (2 estuary CO2, 9 vegetation CO2, 3 vegetation N2O).
- `duplicate_source_row`: 0 (checked). Not used: `negative_value` (a flux can be negative), `source_site_averaged` (above).

## Not loaded

Nothing in a data row is dropped; every column is a value, a label, a reference or the not-loaded `region`. The workbook's
`Surface_areas` sheet is **not measurements**: RECCAP2 regional surface areas in km2 (with uncertainties) of the three estuary
types and three vegetation types, used by the paper to scale fluxes; it is not loaded. The `Metadata` sheet is read only for the
`type` legends.

## Row accounting

738 source rows (estuary 204 + 157 + 123, vegetation 37 + 162 + 55) = 738 facts + 0 excluded; `skip_report.jsonl` is empty. 738
samples, 702 locations, 6 methods, 397 references, 483 citations. After the central build every fact was checked against the raw
row it points to by code that does not use `extract.py` (value, unit from the header, coordinates, site, type decode, habitat and
setting, the re-joined citation pieces equal to the raw `full_citation`, and every flag recomputed): 738 of 738 agree and the set of
raw rows equals the set of facts. A one-study check (the Banerjee et al. entry, predicted from the raw sheets alone: 1 estuary CH4 + 1
vegetation CH4 = 2) matched. Adding this source left the other six sources' content in `compilations.duckdb` unchanged
(313,907 facts = 313,169 + 738). Two runs of `extract.py` give identical tables. `source_row_ref` is
`<file>[<sheet>]#data_row=<0-based row after the header>:<attribute>`.

## Overlap with other compilations

Never merged. No DOI is shared with another built compilation (only 10 pieces carry one). **Ray and Fulweiler** (`ray-oyster`, built
after this one) reports CO2, CH4 and N2O fluxes at oyster sites; any overlap would be through shared papers. Join downstream by DOI and
coordinates.

**J1 (joined water-chemistry fields):** not applicable; the file has no in-situ water concentrations, so there are no J1 facts.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
