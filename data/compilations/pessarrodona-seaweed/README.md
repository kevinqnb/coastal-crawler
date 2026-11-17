# pessarrodona-seaweed (global seaweed net primary productivity)

Pessarrodona, A., Filbee-Dexter, K., Krumhansl, K.A., Moore, P.J., Pedersen, M.F., Wernberg, T. (2021). *A global dataset of seaweed net
primary productivity.* figshare, DOI 10.6084/m9.figshare.14882322.v5 (paper: *Scientific Data*, 2022).

- **Version:** `figshare v5 (2022-08-04)`. **Access date:** 2026-11-15. **License:** CC BY 4.0 (figshare record).
- **Raw files:** `NPP Dataset.csv` (latin-1, 1,121 rows) and `References from database.pdf` (261 entries, read with `pypdf`). The R code
  file is not read.

Net primary productivity of seaweed (macroalgal) assemblages from 229 author-year sources, one row per site, species and study: 1,121 rows at
628 locations, on rocky reefs, coral reefs, sand and floating assemblages.

## Facts (1,121)

One `pessarrodona_npp` fact per row (B6 `seagrass_seaweed_npp`), no skips. The value is the source's **harmonised** `Avg_NPP_kg_C_m2_y`, in
`kg C m-2 y-1`. Values run from -0.102 to 5.47. 3 are negative and flagged. `sd` comes from `stdev_NPP_kg_C_m2_y` (389 facts). `se` and `n` are
NULL.

## The fact carries the source's harmonised value

Each row has its own method row (`row<i>`, 1,121). Its `params` hold the **original** value and SD, the original unit (29 spellings, such as
`g DW 25 month-1` and `mmol O2 gDW-1 yr-1`), the matter basis (DW, C, FW, O), the FW:DW and DW:C conversion factors, the production method,
the free-text description and the data-mining method. `technique` is `Prod_method_group`. `converted_by_source` is on the 1,016 rows where the
original is not already the same number in `kg C m-2 yr-1`. A harmonised value derived with a factor the file does not give cannot be
told apart from no conversion.

## How it maps

- **Habitat** is a composite of vegetation and substrate (`<Vegetation_category> | <Substrate_category>`, 20 combinations, all in the label
  map). Marine forest and brown, red or green algal beds on rocky reef or sand map to Kelp and macroalgal bed. Algal turfs on rocky reef map to
  Rocky shore and reef. Anything on coral reef maps to Coral reef. The rest (salt marsh, floating, rhodolith) stays blank. The labels come
  from the source, so there is no `habitat_from_scope`.
- **Locations (628):** `Level` is the tidal position (418 intertidal, 672 subtidal). `Originalcoordinates` NO (463 rows) means the compiler
  approximated them (`coords_approximate`, 454 facts). 9 rows have no coordinates (`coords_missing`).
- **Depth:** `water_depth_m` is set only when min equals max and the value is not negative (549 rows). Ranges and negative values stay as text in
  `depth_notes`. **Open question:** the plan reads a negative depth as "below the surface", but the file states no sign and 192 of the 195 rows
  with a negative bound are intertidal, so the sign looks like elevation. No sign is interpreted.
- **Samples:** `sampling_year` is `Start_year` when it equals `End_year`. A multi-year range (726 rows) gives NULL and the range in
  `depth_notes`. `year_missing` applies to 52 rows. `taxon` is `Species` (rank `multiple` where `Multispecies` is YES, 248 rows, else `species`).
- `digitized_from_figure` marks values read off a graph (260 facts).
- **References:** `ref:<Reference string>` (229). A string is matched to a PDF entry by first-author surname and year (with its letter suffix) and
  must select exactly one entry. 184 match and carry the entry text, and 7 of those have a DOI. **45 are not matched and not guessed**
  (misspellings, a missing year suffix, unpublished work, theses, ambiguous author-year pairs). They keep a text-only citation and are listed in
  `extract_report.json`. 30 facts cite a secondary source and carry `cited_secondary`, the source's own marking.

## Not loaded

`Standing biomass_kg_DW_m2` (11 values, not in the plan; they could become biomass facts later), the 44 empty columns, and 7 stray cells in two
Juanes and Fernandez rows (asserted to be exactly those cells).

## Flags

`converted_by_source` 1,016, `coords_approximate` 454, `digitized_from_figure` 260, `cited_secondary` 30, `year_missing` 52, `coords_missing` 9,
`negative_value` 3.

## Row accounting

1,121 rows = 1,121 facts, nothing skipped. All facts were checked back to their raw rows, three one-study checks matched their predictions
(Akrong 2021: 118 facts, Cousens 1984: 63, Vadas 2004a: 48), and two runs give identical tables.

## Overlap

Strydom and Foster are seagrass, not seaweed. `davis-calcification` (reef algae and corals) may share a few reef studies.

Back to the [compilations index](../README.md).
