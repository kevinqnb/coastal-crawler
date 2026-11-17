# stratma-bio (BenBioDen: benthic biomass and density)

Stratmann, T., van Oevelen, D., Martinez Arbizu, P. et al. (2021). *The BenBioDen database, a global
database for meio-, macro- and megabenthic biomass and densities* [Dataset]. Dryad,
https://doi.org/10.5061/dryad.gb5mkkwm6 (paper: *Scientific Data* 2020).

- **Version:** `4 (2021-06-07)`, Dryad version 4 (read from the Dryad record by the integration plan; the files
  carry no version of their own; their file modification times are 2020-05-25 for `BenBIO_database.csv` and 2021-06-07
  for `BenDEN_database.csv`).
- **Access date:** 2026-11-15. **License:** CC0-1.0 (as recorded in the integration plan from the Dryad record).
- **Raw files** (Windows-1252): `BenBIO_database.csv` (11,792 rows), `BenDEN_database.csv` (51,559 rows) and the
  screening record of each database, `List_of_studies_for_BenBio_database.txt` (1,447 rows, 1,445 non-blank) and
  `List_of_studies_for_BenDen_database.txt` (2,086). One source, two data files, two study lists.

Global meio-, macro- and megabenthos from the shore to the abyssal plain (a literature compilation, mostly not
coastal wetlands), one row per community value at a station and size class. The same contract design as
`stratma-scoc` (same group, same station columns), which this extract reuses.

**Facts:** 63,350, one per data row with a value: 11,792 biomass + 51,558 density.

| Attribute | Type / subfield | Facts | Units (as in the source) |
|---|---|---|---|
| `benbio_biomass` | B10 `total_biomass` | 11,792 | the per-row `unit` column: `g WM/ m2` 7,051, `g DM/ m2` 2,715, `g C/ m2` 1,115, `g AFDM/ m2` 911 |
| `benden_density` | B10 `total_density` | 51,558 | `ind/ m2` (from the header `Density (ind/ m2)`, asserted) |

Biomass is in four different bases (wet mass, dry mass, carbon, ash-free dry mass); they are one attribute with the
unit on every fact (declared in `ATTRIBUTES`), so filter on `units_raw` before comparing. Nothing is converted. A
BenBIO row is a biomass *as the source reports it*: 1,677 are `converted data` (the authors converted them from
another basis, see flags) and the rest `raw data`.

## Mapping

- `Region` -> `site_name`; `Ocean` -> `water_body`; `Location` (exact / approximated) -> `position_method`; `Depth (m)` ->
  `water_depth_m` (positive down, unchanged); `Depth range` -> `habitat_type` (raw depth class).
- **Standard labels** (label map `stratma-bio`, as SCOC): every `Depth range` label -> habitat subtidal soft sediment
  (dataset scope); setting shelf for the continental-shelf label, open ocean for the abyssal label, blank for near-shore
  and slope. **A row whose `Depth range` is `NA` has no habitat and no `habitat_from_scope` flag** (the label is
  not inferred from the depth number; on every such row the depth is blank anyway): 3,346 BenBIO and 38,345 BenDEN
  rows. This leaves 74% of the density facts with a NULL habitat although the dataset's scope is the same; the
  integration plan chose this (D3) and it can be revisited by changing the label map's source (not done).
- `sample`: one per data row (no sediment depth interval); `taxon` = `Benthos` (macro- / meio- / megabenthos),
  `taxon_rank` = `size_class`. No sampling year or month in the source.
- `method`: one per (reference, attribute, sampling gear, sieve lower, sieve upper, raw/converted, conversion
  reference): 1,125 rows. `technique` = the sampling gear; the sieve mesh limits are kept as the source's text
  (e.g. `0.300 and 0.500`, `0.200 or 0.500`, not parsed) in `params`, with the raw/converted label and, for BenBIO, the
  `Reference for biomass conversion factors`. `method_status` = `matched` where a gear is stated (1,062) and
  `method_id_null` where it is `NA` (63; the technique is unknown, not absent by design). The source has no method id.
- `sd`, `se`, `n`: NULL (not in the source).
- `location_key` = reference | region | ocean | latitude | longitude | depth | location label | depth range, shared by
  both files, so the same station of one reference in BenBIO and BenDEN is one location: 44,058 locations. Ocean is
  in the key because two stations (BenDEN rows 1210/1211 and 1285/1286, a Bering Sea region) appear with both
  `Pacific Ocean` and `Arctic Ocean` at identical coordinates; they are kept as separate locations.

## References and the study lists

- `reference_key` = `ref:<Reference string>` with whitespace collapsed and one trailing full stop dropped; the same
  key in both files. **777 references**, 206 of them cited in both files (the plan's 203 compared the raw strings;
  the 3 more are strings that differ only in whitespace or a trailing full stop); one `citation_dim` row each
  (`bibliography_id` = `title` = the string; the year and, for 43 strings, the DOI are parsed by regex only when the
  string is a single citation). Nine groups of strings are equal after dropping case and punctuation (for example
  `Epifauna Sampling Data` vs `Epifauna sampling data`) but not equal as given; they stay separate references and
  are listed in `extract_report.json`.
- **37 strings are composite** (several citations joined by `; `). They stay one reference each, with no attempt to
  split them into papers: the split is not reliable (one splits mid-citation) and the file has no count to check it
  against. Their list entries are resolved part by part.
- **Each file is resolved against its own list.** The two lists have different screening labels and different studies,
  so BenBio list status is never applied to BenDEN facts. `study_screening_dim` holds all 3,531 non-blank list rows
  (`benbio_<row>` 1,445 and `benden_<row>` 2,086; the two trailing blank BenBio list rows are dropped, asserted fully
  blank), with the raw screening, eligibility and database labels. **974 are linked to a reference** (382 + 592): a list
  entry is linked only when exactly one reference string resolves to it as a whole. The others are NULL: 1,061 + 1,493
  entries no data row cites (mostly studies screened out), and 3 entries (2 + 1) that are cited only inside composite
  strings or resolved to by two different strings (`study_lists` in `extract_report.json`).
- **Resolution** is by the case- and punctuation-insensitive form of the string, then of each part, then by an explicit
  `CROSSWALK` of 17 data prefixes (19 parts resolved: 8 BenBio, 11 BenDEN): an encoding loss in the list (`alpha-` and
  `beta-` as `?`, `Cvitkovi?`, `Sezg?n`), an accent, a journal abbreviation (`Deep. Res.` vs `Deep-Sea Res.`), a typo
  (`assessmsent`), a thesis wording, and two papers whose **year in the list differs** from the data and from the paper
  (Piepenburg and Schmid, Mar. Biol. 125: data 1996, list 2004; Grassle et al., Deep-Sea Res. 22: data 1975, list
  2003; title, journal, volume and pages agree). The plan expected 14 BenBio and 18 BenDEN strings to be unmatched;
  under the SCOC normalisation there are 9 and 13 (the plan's counts used another normalisation). Of their parts, 8
  (BenBio) and 11 (BenDEN) go through the crosswalk and 4 and 2 are `UNRESOLVED` (next point). Every other part must
  match an entry or an explicit `UNRESOLVED` prefix, or the extract raises.
- **No list entry** (declared `UNRESOLVED`, the nearest entries are different papers): Gilkinson et al. 2005 (ICES J. Mar.
  Sci.; the lists' Gilkinson 2003 is a different paper), Gilkinson (DFO 2013), Wei et al. 2019 together with
  Archambault (unpublished 2009), and Bluhm HLY-05-02 Macrofauna Data. Their facts keep the reference; no list entry
  links to them.
- **Cited but not `included`:** one BenDEN list entry (Relexans et al., Oceanol. Acta 15, list row 1456; screening
  `included`, eligibility `excluded`, database `previously excluded`) is cited by 30 BenDEN rows: kept, flag
  `study_marked_excluded`. **Included but never cited:** 8 BenDEN list entries (Danovaro et al. Agip Abruzzo,
  Galeron DI226/DI229/DI231, Gilkinson 2003, Moreno et al., Pfannkuche et al. BENGAL, Rutgers van der Loeff and
  Lavaleye), declared as an exact set; BenBio has none.

## Flags

(Facts; BenBIO / BenDEN.)

- `habitat_from_scope`: 8,446 / 13,213 (rows with a `Depth range` label).
- `depth_missing`: 3,613 / 39,026. `method_missing` (no sampling gear): 5,456 / 39,408.
- `coords_approximate` (`Location` = approximated): 1,486 / 2,201.
- `coords_missing`: 19 / 19. One study has a latitude but no longitude on these rows; the latitude is kept as
  reported, the longitude is NULL.
- `zero_value`: 73 / 112. `duplicate_source_row`: 143 / 1,255, every member of a group of rows identical in all
  columns of that file (some may be genuine replicates). The integration plan's 74 and 668 count the *extra* copies
  (all but the first of each group: BenBIO 143 rows in 69 groups, 74 extra; BenDEN 1,255 rows in 587 groups, 668
  extra); the flag marks every member.
- `converted_by_source`: 1,677 (BenBIO `converted data`; 1,485 name a conversion-factor reference, 192 have `NA`; one
  `raw data` row also carries a conversion reference, kept in `params`).
- `study_marked_excluded`: 0 / 30.
- **Latitude beyond the pole:** BenDEN data row 48707 (station `North Pole`) has latitude 90.012. The value is kept as
  reported and its fact carries the flag `lat_beyond_pole` (registered for this, on your decision); the extract declares
  this one row and raises on any other latitude outside [-90, 90]. Filter on the flag before any geographic analysis.
- Not used: `lon_wrapped`, `lat_lon_zero` (no longitude above 180, no 0/0 pair).

## Not loaded

Nothing in the data files is dropped except the one BenDEN row with no density, and no column is ignored (every
column is a value, a label, a method field or the reference). Not stored: the study lists' full text beyond the
citation string and three labels; the sieve limits as numbers. The BenDEN list's database column is named
`BenDEN database`, BenBio's `BenBio database`; both map to `database_status`.

## Row accounting

BenBIO 11,792 rows = 11,792 facts. BenDEN 51,559 rows = 51,558 facts + 1 row with a blank density (data row 1810,
reason `no_density`, in `skip_report.jsonl` with its file). 63,350 samples. After the central build every fact was
checked against the raw row it points to (value, unit, coordinates, depth, region, ocean, location label, depth class,
benthos, reference, gear, sieve strings, conversion reference, and every flag recomputed from the raw row):
63,350 of 63,350 agree, the set of raw rows with a value equals the set of facts, and rows = facts + skipped. Two runs
of `extract.py` give identical tables.

## Overlap with other compilations

Never merged. **SCOC** (`stratma-scoc`, same group): 19 of this file's references have the same citation text as a SCOC
citation (case- and punctuation-insensitive) and 2 DOIs are shared, so a few studies report both oxygen consumption and
biomass; the variables differ. Join downstream by DOI and coordinates. No overlap with CCN or the seagrass
compilations by DOI. Other compilations (Jorgensen, Boynton) are not built yet.

**J1 (joined water-chemistry fields):** not applicable; the file has no in-situ water measurements, so there are no J1
facts.

Back to the [compilations index](../README.md) for the table contract, conventions and vocabulary.
