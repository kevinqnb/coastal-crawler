# strydom-seagrass (Strydom et al., global seagrass structure, biomass and production)

Strydom, Simone; Webster, Chanelle L; McCallum, Roisin et al. (2021). *Global database of key
seagrass structure, biomass and production variables* [Dataset]. PANGAEA,
https://doi.org/10.1594/PANGAEA.929968. Supplement to Strydom et al. (2023), *Earth System Science
Data* 15, 511-519, https://doi.org/10.5194/essd-15-511-2023.

- **Version:** `2025-05-06`. PANGAEA states no version number; this is the date of the last change in the
  file header ("Corrected some longitudes from east to west according to the cited literature"), so the file
  read here is that corrected one. The dataset was first published 2021-04-01.
- **Access date:** 2026-11-15. **License:** CC-BY-4.0 (read from the file header, asserted by the extract).
- **Raw file:** `Seagrass_db.tab` (PANGAEA tab format: a 409-line `/* ... */` header with the citation, a
  list of 379 "Related to" source citations and the parameter list, then a tab-separated table).

A meta-analysis table of the same design as Foster (`foster-seagrass`): 6,658 rows (study x site x genus x
year), 405 distinct `Reference` strings, 12 seagrass genera, six bioregions, `Habitat` Coastal (4,092 rows)
or Estuarine (2,566), 848 locations (after the label split below), sampled 1972-2019.

**Facts:** 14,266, one per non-blank measurement cell (equal to the 14,266 cells counted from the raw
file), units as in the PANGAEA headers (`units_raw`). The attributes, types and subfields are Foster's;
only the unit spelling differs. The project's `plan.md` quotes "14,271 values from 390 papers" for this
dataset; the file read here has 14,266 cells and 400 distinct reference strings with facts (405 over all rows).
The difference cannot be resolved from the file itself (this is the 2025-05-06 corrected version), so the
extract's own counts are the ones used.

| Attribute | Type / subfield | Facts | Units |
|---|---|---|---|
| `seagrass_biomass_above` / `_below` / `_total` | B2 `above_ground_biomass` / `below_ground_biomass` / `total_biomass` | 3,386 / 2,008 / 2,696 | g/m**2 |
| `seagrass_shoot_density` | B3 `shoot_or_stem_density` | 3,209 | #/m**2 |
| `seagrass_leaf_density` | B3 `leaf_density` (extension) | 146 | #/m**2 |
| `seagrass_percent_cover` | B3 `percent_cover` | 1,285 | `%` (kept as a percentage) |
| `seagrass_production_above` / `_below` | B6 `above_ground_npp` / `below_ground_npp` | 265 / 125 | g/m**2/day |
| `seagrass_production_total` / `_shoot` / `_leaf` | B6 `total_npp` / `shoot_production` / `leaf_production` (extensions) | 258 / 151 / 737 | g/m**2/day |

PANGAEA's parameter names say dry mass ("dm"). The unit strings are declared beside Foster's
(`g DW m-2`, `shoots m-2`, `g DW m-2 d-1`); equivalent spellings are not merged and nothing is converted,
so comparing the two compilations on one axis is a downstream conversion. Production is per day.

## Mapping

- `Latitude` / `Longitude` are the source decimal degrees, unchanged (asserted in range).
- `habitat_type` is the constant `seagrass` (dataset scope), standard habitat = seagrass meadow. The label
  map declares `geomorphic_setting` = `Habitat`: Estuarine -> setting estuary, Coastal -> blank.
  `bioregion` = `Seagr bioregion` (six bioregions; Short et al. 2007 per Foster's README, not stated in this
  file). No site name, country, water body, salinity, ecoregion, position method or elevation (not in the
  source).
- `location_key` = latitude | longitude | Habitat | bioregion, so a coordinate carrying two labels stays two
  locations (as Foster).
- `sample`: one per source data row (the file has no row id). `taxon` = `Genus` (12 genera; an unknown
  one raises), `taxon_rank` = `genus` (there is no `Mixed` here). `sampling_year` = `Year obs [a AD]`;
  month and sediment depth are NULL.
- `method`: the source has no method information; one `method_dim` row per attribute (11),
  `method_status` = `method_id_null`, `params` = the source column (the totals add Foster's note that a total
  may be the sum of its parts). `sd`, `se`, `n` are NULL.
- `reference_key` = `ref:<Reference string>`, whitespace-normalised. See "References".

## References

The data rows cite `Author(s) year` strings only (no DOI column). 400 distinct strings occur in the 6,626
rows with facts, so there are 400 `reference_dim` and 400 `citation_dim` rows (`bibliography_id` = the string).
The file header lists 379 full citations (355 with a `https://doi.org/` link). Each string is matched
to one header entry by author and year (`match_reference` in `extract.py`): `A year` needs a one-author
entry, `A and B year` / `A & B year` a two-author entry with both surnames in that order, `A et al. year` an
entry with two or more authors and that first surname; the year must be equal; a year letter (`2010a`), or
more than one candidate, is *ambiguous* and not matched; surnames are compared ignoring case, accents and
hyphens, and a trailing full stop or comma on the name part is ignored. Nothing is matched fuzzily and no string is
matched by hand.

| Result | Strings |
|---|---|
| matched | 332 (312 of them to an entry with a DOI link; their `citation_dim` row carries `doi`, `authors`, `year`) |
| ambiguous (several candidates) | 3: Cullain et al. 2018, Namba et al. 2018, Samper-Villarreal et al. 2018 |
| no header entry | 65 |

The 65 "no entry" strings are kept with only the string and a year parsed from it. They are listed in
`extract_report.json`, with, for each, the header entries that share its first author (`near_misses_...`),
for review. Of the 65, **22 are a source defect**: `Lamit and Tanaka 2020` to `2033` (14 strings, one row
each) and `Jensen and Gibson 1987` to `1994` (8 strings, one row each) count up one year per row, as a
spreadsheet auto-fill would; the header has one Lamit and Tanaka entry (2019, the 2019 string matches it) and
one Jensen and Gibson entry (1986, one row). They are different `reference_dim` rows here because the source gives different strings; they are not
merged. Of the other 43, many differ from a header entry by one year (e.g. `Adi et al. 2018` vs the 2019
entry), by the number of authors, or by spelling. Three strings contain U+FFFD, a replacement character that is
in the source file (`Ekl?f et al. 2009`, `Marb? and Duarte 2001`, `Uku and Bj?rk 2005`); they are kept as given.
Two header entries are matched by two strings each (`Cambridge and Hocking 1997` / `Cambridge et al. 1997`;
`Lee and Dunton 2000` / `Lee et al. 2000`): two reference rows, one DOI. The DOI-resolution job can attach
DOIs to the unmatched strings later; the source's own DOI is never overwritten.

## Not loaded

- **Nothing in the table is dropped except 32 rows** with none of the 11 measurements (below). Every
  column is either loaded or a label.
- **Header-only information:** the 49 of 379 header citations that no `Reference` string of a row with facts
  matched, and the header's coverage,
  abstract and parameter descriptions. The full citation text (title, journal) of matched entries is not
  stored: `citation_dim.title` is NULL (the entry text does not split reliably into title and journal);
  authors, year and DOI are.
- **5 reference strings occur only in the 32 excluded rows** and have no `reference_dim` row:
  Alexandre et al. 2006, Bryars 2009, Caye and Meinesz 1985, Churchill and Riner 1978, Dos Santos et al. 2017.

## Flags

(Facts: `extract_report.json`.)

- `habitat_from_scope`: all 14,266 facts (`habitat_type` is the constant `seagrass`, the dataset's scope).
- `duplicate_source_row`: 283 facts, from 261 rows with facts. Every member of a group of rows identical in
  all columns, measurement values included. Strydom's rows have no id, so some may be genuine replicates that
  happen to match; the flag marks them, nothing is dropped. The integration plan's 146 counts the *extra* copies
  (all but the first of each group; 269 rows in 123 groups in the file, 146 extra); the flag marks every member.
- `total_ne_sum_of_parts`: 1,077 facts (all three facts of an above/below/total biomass or production triple
  where the total differs from above + below by more than 1e-3 relative and 1e-3 absolute, the Foster
  tolerances). A screen, not a verdict: the source does not say whether a total is reported or summed.
- `year_missing`: 13 facts (10 source rows have no year).

## Row accounting

6,658 source rows = 6,626 rows with facts + 32 rows with none of the 11 measurements
(`no_seagrass_measurement`, in `skip_report.jsonl`). 14,266 non-blank measurement cells = 14,266 facts. 6,626
samples, 848 locations (548 Coastal, 300 Estuarine), 11 methods, 400 references and citations. Every fact
was round-tripped to its raw cell after the central build: 14,266 of 14,266 match on value (exact float),
unit, coordinates, `Habitat`, bioregion, genus, year and reference string, and the set of non-blank raw
cells equals the set of facts. Two runs of `extract.py` give identical tables.

## Overlap with other compilations

Never merged here. **Foster** has the same design and an overlapping literature: 291 of this file's 400
reference strings are also Foster `Reference` strings (exact string equality), and 247 of the 310 distinct DOIs
attached here also occur in Foster. The measured values of a shared paper can differ between the two (this
file's sampling years end in 2019; Foster's in 2022). Join downstream by DOI and coordinates.

**J1 (joined water-chemistry fields):** not applicable; the file has no in-situ water measurements, so there are no J1
facts.

**Coupling:** `extract.py` imports Foster's `foster-seagrass/extract.py` at load time (its number parser, the
above/below/total triples, the genus list and the reference-string normaliser), so a later edit to Foster's extract can
change this one; the unit tests would show it.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
