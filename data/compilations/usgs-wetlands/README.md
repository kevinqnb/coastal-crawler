# usgs-wetlands (USGS tidal saline wetland ecosystem properties)

Feher, L.C., Osland, M.J., Grace, J.B., Buffington, K.J., Carr, J.A., Guntenspergen, G.R., Martinez, M.,
Neville, J.A., Rankin, L.L., Thorne, K.M. (2025). *North American dataset of ecosystem properties in tidal
saline wetlands -- Canopy height, aboveground biomass, productivity, soil carbon density, and soil carbon
accumulation* [Data release]. U.S. Geological Survey, https://doi.org/10.5066/P133WKRO.

- **Version:** `2025-05-07`, the FGDC `pubdate` in the release's metadata (read from the xml by the extract). The
  release states no edition number, and the workbook is named `..._2023`. The ScienceBase landing page could not be
  read (403), so nothing beyond the local files was used.
- **Access date:** 2026-11-15. **License:** none stated: the FGDC use constraint reads "None. Users are advised to
  read the dataset's metadata thoroughly to understand appropriate use and data limitations." (recorded verbatim in
  `source.license`, asserted by the extract).
- **Raw files:** `ecosystem_attribute_data_2023.xlsx` (sheets `Height`, `Biomass`, `Productivity`, `soil_C_density`,
  `soil_C_accumulation`, `Citations`) and `ecosystem_attributes_2023.xml` (FGDC metadata).

A literature-derived compilation for the United States, Canada and Mexico: each data row is one site-level average
taken from a primary study (and, for 781 rows, from a secondary compilation named in `source_review`). 1,485 rows,
828 locations at 765 distinct coordinate pairs, 262 distinct `source_primary` strings.

**Facts:** 1,485, one per data row of the five data sheets (every row has exactly one value; asserted). Units are the
sheet's `units` column (asserted equal to the declared unit); nothing is converted.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `usgs_canopy_height` | B3 `canopy_height` | 258 | m |
| `usgs_aboveground_biomass` | B2 `above_ground_biomass` | 225 | t/ha |
| `usgs_productivity` | B6 `litterfall` 36, `above_ground_npp` 142, NULL 13 | 191 | t/ha/yr |
| `usgs_soil_c_density` | C1 `soil_carbon_density` (extension) | 532 | g/cm3 |
| `usgs_soil_c_accum` | G2 `carbon_burial` | 279 | g/m2/yr |

The release defines the quantities as *average* canopy height, *aboveground* biomass and *aboveground* productivity
of the site. Soil carbon density is carbon mass per soil volume (g/cm3) over the core depth given in the soil sheet;
soil carbon accumulation is per area and year.

**Productivity subfield (written rule, an exhaustive table over the 14 `method` strings; an unknown string raises).**
`litterfall, recorded monthly over at least 1 year` -> `litterfall`. Every other named method -> `above_ground_npp`
(`peak standing crop`, `Smalley 1959`, `Wiegert and Evans 1964`, `Milner and Hughes 1968`, `summation method for NPP
(litterfall + wood growth)`, `non-destructive`, `Valiela et al. 1975`, `Rey's method`, `Shew et al. 1981`, `Lomnicki et
al. 1968`, `Wiegert and McGinnis 1975`, `Pierce 1983; Dickerman et al. 1986`), because the release defines the whole
sheet as aboveground productivity. `not reported` -> NULL subfield (13 facts): the method is unknown, so nothing is
guessed. The integration plan's table listed only four of these strings; the other seven (26 rows) were added here
from the data.

## Mapping

- `location_key` = lat | long | `type` | `veg_type` | `location` (stripped), shared by all sheets; rows at one
  coordinate with different labels are separate locations (e.g. a salt flat and a marsh at one point).
  `site_name` = `location`; `habitat_type` = `type` raw; `vegetation_class` = `veg_type` raw (`G` 489 locations, `M`
  220, `S` 91, `F` 26, `G/M` 2). No country, water body or setting is derived (the `location` string is free text).
- **Standard labels** (label map `usgs-wetlands`): `salt marsh` -> tidal marsh (580 locations), `mangrove` -> mangrove
  forest (220); `salt flat` (26) and `salt marsh mangrove` (2) have no equivalent and stay blank, so those 28
  locations have NULL habitat. The habitat comes from the source's own `type` label, so **no** `habitat_from_scope`
  flag is used here. Salt flats are "tidal saline wetland site with no vegetation" in the release's own words; they
  are not mapped to tidal flat.
- **Coordinates** are as given. The release says that where a publication gave none, the authors placed the site
  from its text description with Google Earth; this is not marked per row, so no per-fact flag exists. No row has both
  coordinates in whole degrees (`coords_integer_degree` is checked and unused).
- `sample`: one per data row (keyed `<sheet>:row<n>`), no year or taxon. Only `soil_C_density` has a depth:
  `core_depth_cm` is parsed strictly as `0-N` (`0-7.5` occurs) into `depth_min_cm` / `depth_max_cm`; `not reported`
  (186 of 532) gives NULL depths and the flag `depth_missing`. The raw text is kept in `depth_notes`. The other sheets
  have no sediment depth interval.
- `method`: one row per distinct (attribute, method string, `*_range` text, `source_review`, `included_Feher_2017`):
  166 rows. `technique` = the `method` string on `Biomass` and `Productivity`; `method_status` = `matched` where one is
  stated, `method_id_null` where it is `not reported` (41 facts, flag `method_missing`) and for the three sheets that
  have no method column (no `method_missing` flag there). `params` holds the `*_range` text (verbatim, whitespace-
  stripped), `source_review` and `included_Feher_2017`. **The range is not an sd**: the release gives it as the range of
  values recorded at the site; `sd`, `se` and `n` are NULL.

## References

`source_primary` is the study. Both the data cells and the `Citations` tab contain NBSP characters; every citation
string is whitespace-normalised first. A cited paper is looked up in the tab by the exact normalised string, else by
the one entry equal after accent and case folding (this matches `Castañeda-Moya et al. 2013` to the tab's
`Castaneda-Moya et al. 2013`; it raises if several entries fold equal).

- `reference_key` = `doi:<lower-case doi>` where the tab's url is a DOI (`10.NNNN/...`), else `ref:<string>`. The tab
  has 281 rows for 278 distinct strings; 203 strings carry a valid DOI (the plan's 252 was an estimate). **Three
  `doi.org` urls are not DOIs** (`https://doi.org/c_accumulation` for Arias-Ortiz et al. 2021 and
  `https://doi.org/productivity` for Balling and Resh 1983 and Cameron 1972): kept as `citation.url`, DOI NULL, key
  `ref:`.
- 262 distinct `source_primary` strings cover 262 cited papers (251 found in the tab, 180 of them with a DOI) and give
  **261 `reference_dim` rows** (177 `doi:`-only, 80 `ref:`-only, 4 composite) and 267 `citation_dim` rows. One
  reference is shared by two strings: `Dai and Weigert 1996` and `Dai and Wiegert 1996` have one DOI in the tab (a
  spelling variant in the source) and so one reference with two citation rows. This merge is by the source's own
  DOI, not by hand.
- `citation_dim.bibliography_id` = the citation string, `title` = the tab's `full_citation` text (whitespace-
  normalised, not parsed into title and journal; SCOC / BenBioDen precedent), `year` = the last year in the string,
  `url` = the tab's url. The tab's `Cahoon 1994` appears twice with the **same DOI but different `full_citation`
  text** (volume `41` vs `4`): both texts are kept as two citation rows of one reference. `Marin-Vásquez 1988` and
  `Marín-Vásquez 1988` are two tab entries that differ only by an accent (same url, no DOI); they stay two references
  because the source gives two strings and has no DOI to tie them.
- **Composite strings (4):** `Anisfeld et al. 1999; Anisfeld 1995 unpublished data`, `Chastain et al. 2022; Kohfield
  et al. 2022`, `Howes et al. 1985; Redfield 1972` (split on `;`) and, as an explicit declared split,
  `Mahall and Park 1976, Balling and Resh 1983`. Each gets the part keys joined by `+` and one citation row per part;
  the number of parts equals the number of citation rows (asserted). No general comma rule is used.
- **Cited but not in the tab (11 papers), declared in the extract and asserted as exactly this set:**
  7 "unpublished data" strings (`Josselyn`, `Meling-López et al.`, `Cahoon` 1993, `Cahoon and Lynch` 1993 and 1994,
  `Chmura` 1997, `Anisfeld` 1995; 68 facts) and 4 strings for which the tab has an entry for a *different year*:
  `Lagomasino et al. 2020` (tab: 2013), `Luk et al. 2020` (2021), `Kemp et al. 2020` (2012); `Breithaupt et al. 2020`
  has no near entry (tab: 2014, 2017). They are **not matched**: the data side has no title or journal to confirm one
  paper, so they keep a string-only `ref:` citation and the near misses are listed in `extract_report.json`.
- `source_review` (the secondary compilation a value was taken from; 25 distinct strings such as `Sanderman et al.
  2018`, `Coastal Carbon Atlas 2023`, `Holmquist et al. 2018`) is kept as a string in `method.params` with the flag
  `secondary_source` on the 781 facts that have one; every review string is in the tab (asserted). A review citation
  is not a `reference_dim` row.
- 3 tab strings are used by no row (`Kemp et al. 2012`, `Lagomasino et al. 2013`, `Luk et al. 2021`); they are the
  near misses above.

## Flags

(Facts; `extract_report.json` has the split by attribute.)

- `zero_value`: 46 and `assigned_by_source`: 46. The release says canopy height and aboveground biomass "were estimated
  as 0 ... in areas identified as salt flats": the 25 height and 21 biomass zeros are exactly the salt-flat rows of those
  two sheets (asserted in both directions, and no other sheet has a zero). They are the authors' assignment, not
  measurements; `assigned_by_source` (registered for this source on your decision) marks them. Filter on it before
  averaging height or biomass.
- `secondary_source`: 781. `method_missing`: 41 (28 biomass, 13 productivity). `depth_missing`: 186.
- `duplicate_source_row`: 9 (3 height, 4 soil carbon density, 2 accumulation): every member of a group of rows
  identical in all columns of its sheet. Nothing is dropped.
- Not used: `negative_value` (none), `coords_integer_degree` (none), `habitat_from_scope`, `year_missing` (the source has
  no year).
- Values are not otherwise screened; the largest are height 40 m, biomass 857.9 t/ha, productivity 44 t/ha/yr,
  accumulation 1,713 g/m2/yr.

## Not loaded

Nothing in a data row is dropped; every column is a value, a label, a method field, a reference or the range text.
Not stored: the `Citations` tab's `full_citation` is stored as `title` but not parsed; the `Citations` entries used by
no row (3) are not rows; the FGDC metadata beyond version, DOI, authors and use constraint is not stored. The `*_range`
text is a string in `params`, not a number.

## Row accounting

1,485 source rows (Height 258, Biomass 225, Productivity 191, soil_C_density 532, soil_C_accumulation 279) = 1,485
facts + 0 excluded; `skip_report.jsonl` is empty. 1,485 samples, 828 locations, 166 methods, 261 references, 267
citations. After the central build every fact was checked against the raw cell it points to by code that does not use
`extract.py` (value, unit, coordinates, site, type and vegetation labels, habitat, the cited papers, technique,
`params`, productivity subfield, depth, and every flag recomputed from the raw row): 1,485 of 1,485 agree and the set of
raw rows equals the set of facts. The other five sources' content in `compilations.duckdb` is unchanged by adding
this one (313,169 facts = 311,684 + 1,485). A one-study check (Giani et al. 1996, predicted from the raw sheets alone:
2 height + 2 biomass + 7 soil carbon density = 11) matched. Two runs of `extract.py` give identical tables.
`source_row_ref` is `ecosystem_attribute_data_2023.xlsx[<sheet>]#data_row=<0-based row after the header>:<attribute>`
(the row counts within its sheet).

## Overlap with other compilations

Never merged. **Coastal Carbon Network** (`smithsonian-coastal-carbon`): 60 of this release's 179 distinct DOIs also occur
in CCN citations (61 references; 533 of the 1,485 facts: 269 soil carbon density, 178 accumulation, 44 biomass,
42 height), and the release names `Sanderman et al. 2018`, `Holmquist et al. 2018` and `Coastal Carbon Atlas 2023`, which
CCN also draws on, as secondary sources. The measured quantities differ in form (site averages here, core depth series
there). Join downstream by DOI and coordinates. Soil carbon accumulation here is a different quantity from CCN's
`pb210_cic_accretion_rate` (vertical accretion). `ribeiro-mangrove` (a litterfall paper list) is not built.

**J1 (joined water-chemistry fields):** not applicable; the release has no in-situ water measurements, so there are no J1
facts.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
