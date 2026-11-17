# pessarrodona-seaweed (global seaweed net primary productivity)

Pessarrodona, A., Filbee-Dexter, K., Krumhansl, K.A., Moore, P.J., Pedersen, M.F., Wernberg, T. (2021). *A global dataset of
seaweed net primary productivity* [Dataset]. figshare, https://doi.org/10.6084/m9.figshare.14882322.v5
(paper: *Scientific Data*, 2022).

- **Version:** `figshare v5 (2022-08-04)`, read from the figshare record by the integration plan (the CSV states none).
- **Access date:** 2026-11-15. **License:** CC BY 4.0 (figshare record).
- **Raw files:** `NPP Dataset.csv` (latin-1; 1,121 data rows; 80 columns, 36 named and 44 empty `Unnamed`), `References from
  database.pdf` (261 reference entries, read with `pypdf`). `NPP dataset Sci Data code.R` is not read.

Net primary productivity (NPP) of seaweed (macroalgal) assemblages from 229 author-year sources, one row per site, species and
study: 1,121 rows at 628 distinct locations, in rocky reefs, coral reefs, sand and some floating assemblages.

**Facts: 1,121**, no skips: one `pessarrodona_npp` fact per row (B6 `seagrass_seaweed_npp`), the source's **harmonised**
`Avg_NPP_kg_C_m2_y`, unit `kg C m-2 y-1` (the header's). Values run from -0.102 to 5.47; **3 are negative** (`negative_value`)
and none is exactly 0. `sd` = `stdev_NPP_kg_C_m2_y` on **389 facts** (2 are exactly 0); blank and `NA` give NULL; `se` and
`n` are NULL.

## D1: the fact carries the source's harmonised value

Each source row has its own `method_dim` row (`method_key` = `row<i>`, 1,121). Its `params` hold the **original** value
(`Avg_NPP`), original SD (`sted_NPP`, 390 values; one fewer than the harmonised column), `NPP_units` (29 spellings, such as
`g DW 25 month-1`, `mmol O2 gDW-1 yr-1`, `gC plant-1 y-1`), `Matter_Unit` (DW 587, C 312, FW 179, O 43), the FW:DW (177) and
DW:C (777) conversion factors, the production method (`Prod_method_general`), the free-text `Description`, `Data_mining_method`,
`Aggregation_required`, `Multispecies`, phylum / order / family / genus, `Seasons`, annual sampling frequency, start and end year,
`Person_entering_data`, `Originalcoordinates` and `Level`. `technique` = `Prod_method_group` (12 values; `Unknown` on 8 rows gives a
NULL technique and status `method_id_null`). `conversion_applied` is the text "source harmonised: <original value, unit,
matter> -> <harmonised value and unit>" and the fact carries `converted_by_source` where the original is not already the same
number in `kg C m-2 yr-1`: **1,016 of 1,121 rows**. A conversion factor that is not in the file (the DW:C factor is blank on 344
rows, and some harmonised values were derived with factors the file does not give) cannot be told apart from no conversion.

## Mapping

- **Habitat.** `location.habitat_type` = `<Vegetation_category> | <Substrate_category>`, the composite approved in the plan; all 20
  combinations are in the label map and an unlisted one raises. Marine forest, brown / red / green algal beds on **Rocky Reef**
  (and red / green on **Sand**) -> *Kelp and macroalgal bed*; Algal turfs on Rocky Reef -> *Rocky shore and reef*; **anything on
  Coral Reef** (algal turfs, marine forest, brown and red algal beds, rhodolith beds and coralline algae) -> *Coral reef*; blank:
  Marine forest on Salt-marsh and on Sand, Green algal beds on Salt-marsh, Red algal beds on Floating, Rhodolith beds and
  coralline algae on Rhodolith beds and on Rocky Reef, Floating Sargassum, Free floating algae. The labels come from the source
  (no `habitat_from_scope`).
- **Locations: 628** (`Site | Site_ID_within_study | lat | lon | habitat label | Level | Originalcoordinates | depth min | depth max`).
  `tidal_position` = `Level` (Intertidal 418 rows, Subtidal 672, `NA` 31 -> NULL). `position_method`: `Originalcoordinates` YES (658
  rows) -> `reported`, NO (463) -> `approximated by compiler` (flag `coords_approximate`, 454 facts: the 9 more have no
  coordinates). **9 rows have no coordinates** (`coords_missing`).
- **Depth.** `Depth_min_m` and `Depth_max_m` (`NA` for both on 61 rows). **`water_depth_m` is set only when min = max and the value is >= 0**
  (549 rows); every other case (a range, or a negative value) is text in the sample's `depth_notes` (841 rows with notes, also
  holding multi-year ranges). **A decision to check:** the plan reads a negative depth as "below the surface per source", but the
  file states no sign convention and 192 of the 195 rows with a negative bound are intertidal (so the sign looks like elevation above
  the water level, the opposite reading). No sign is interpreted: nothing negative reaches the numeric slot; the raw text is kept.
- **Samples:** one per row. `sampling_year` = `Start_year` when it equals `End_year`; a multi-year range (726 rows) gives NULL
  and `years=<start>-<end>` in `depth_notes`; `year_missing` only where `Start_year` is `NA` (52 rows). `taxon` = `Species` (the
  source's label, including assemblage labels such as `Turf assemblage`); `taxon_rank` = `multiple` where `Multispecies` = YES (248
  rows) else `species`.
- **Data mining.** `Graph digitizer` and `Text & Graph digitizer` give `digitized_from_figure` (260 facts: 258 + 2).

## Not loaded

`Standing biomass_kg_DW_m2` (11 values): **not in the plan**, so not loaded; they could become B2 biomass facts in a later pass. The
44 empty `Unnamed` columns, and **7 stray non-empty cells** in columns 73 and 77 to 79 on two Juanes and Fernandez rows (data rows 1083
and 1084): the extract asserts they are exactly those cells and ignores them.

## References

`Reference` (229 distinct author-year strings, whitespace normalised) -> `ref:<string>`. The PDF's 261 entries (a hanging indent starts
an entry; `———` repeats the previous author) are matched by an explicit rule: first-author surname (ASCII-folded) and the year with its suffix
letter must select exactly one entry (a second author named in the string must be in the entry; where several entries share
author and year, the one-, two- or many-author shape of the string may settle it). **184 of 229 strings are matched** and carry the entry text as
`title`; 7 have a DOI (14 of the PDF's entries have one; the plan expected 13), the other matched entries have a URL or neither.
**45 strings are not matched and are not guessed:** misspellings and variants (`Bu?as 2009`, with a literal `?` in the CSV;
`Chesire`, `Sjotun`, `Luning`), a year or suffix the PDF lacks (`Mann 1972a`, `Miller et al. 2011a/b`, `Vadas et al 2004a/b`,
`Shimabukuro et al. 2007a/b`), unpublished work and theses, secondary citations, and two or more entries for one author and
year (`Kamohara 2009`, `Tait, 2010 (thesis)`). They keep a text-only citation (`bibliography_id` = the string, year from the string), listed in
`extract_report.json`. 77 PDF entries are cited by no string. **30 facts (14 strings of the form `X in Y` / `cited in`) carry
`cited_secondary`**, the source's own marking.

## Flags

(Facts.) `converted_by_source` 1,016; `coords_approximate` 454; `digitized_from_figure` 260; `cited_secondary` 30; `year_missing` 52;
`coords_missing` 9; `negative_value` 3. No row repeats another (asserted), so no `duplicate_source_row`.

## Row accounting

1,121 rows = 1,121 facts, one harmonised value each; 0 skips; the 11 standing-biomass cells and the stray cells are listed above and
in the report. 1,121 samples, 628 locations, 1,121 methods, 229 references and citations. After the central build every fact was
checked against the raw row it points to by code that does not use `extract.py` (value, unit, SD, habitat composite and mapped habitat,
coordinates, site, tidal position, position method, the depth rule, year, taxon and rank, reference key, original value and unit,
technique, and every flag recomputed): 1,121 of 1,121 agree and the set of source rows equals the set of facts. One-study checks,
predicted from the CSV alone (rows and SD count): Akrong et al. 2021 118 facts, 0 SD; Cousens 1984 63, 0; Vadas et al 2004a 48, 24; all
matched. Adding this source left the other twelve sources' content unchanged (344,397 facts = 343,276 + 1,121). Two runs of
`extract.py` give identical tables. `source_row_ref` is `NPP Dataset.csv#data_row=<0-based row>:pessarrodona_npp`.

## Overlap with other compilations

Never merged. Strydom and Foster are seagrass, not seaweed, so they do not overlap; `davis-calcification` (reef algae and corals)
may share a few reef studies; `boynton-flux` and `fulweiler-nfix` are unrelated measurements.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
