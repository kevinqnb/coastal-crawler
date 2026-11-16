---
id: 2026-11-15-scoc-compilation-01
kind: build
---

## Session 2026-11-15

### Prompts
Summary of this session's requests (not verbatim):

- Read the compilations README, then summarize the new SCOC dataset (sediment community oxygen consumption) and plan cleaning, extraction records and the DB build.
- Map onto existing fields where possible, document standardization conventions in the README, and propose new fields/tables only where the fit is poor; include the list of studies and link it to measurements; keep the TOU/DOU/AOU split; load all depths.
- Approved the plan (study-screening table, new sample/location columns); commit and /devlog.

### Implemented
Added `data/compilations/scoc/extract.py`, which turns each SCOC row into one fact (`scoc_tou`/`scoc_dou`/`scoc_aou`, plus `scoc_unspecified` where the flux type is missing) with values as reported and suspicious rows flagged. Reference strings are matched to the list of studies (case/punctuation-insensitive, then an explicit crosswalk that raises on anything unresolved), and the list itself loads into a new `study_screening` table. The shared contract in `src/coastal_crawler/compilations.py` gained that table, a nullable foreign key, `location.water_body` and three sediment columns on `sample`; the CCN extractor emits NULLs for them. Tests are in `tests/test_compilations_scoc.py`; the full build (220,639 facts) round-trips to the source rows. No `configs/<id>.yaml` (not an experiment-contract stage).

### Commits
d4d5da1 Compilations: SCOC extract; study_screening table, nullable FK, new sample/location columns
