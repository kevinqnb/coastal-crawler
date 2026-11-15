---
id: 2026-11-15-compilations-ccn-01
kind: build
---

## Session 2026-11-15

### Prompts
Summary of this session's requests (not verbatim):

- Organize raw coastal-ecosystem data compilations under `data/compilations/` into unified extraction records; start with the Coastal Carbon Library: summarize it and plan the extraction.
- One record per variable measurement, depth interval as context; keep and flag suspicious values rather than cleaning; store results in a separate DuckDB star schema; radiometric/dating data out of scope.
- Run the staged ladder (unit tests, real-data extract, one-study end-to-end, full build); leave unmatched-core depth rows excluded.

### Implemented
Added a shared compilations contract and central builder (`src/coastal_crawler/compilations.py`, `scripts/build_compilations.py`) that validates per-compilation parquet outputs, assigns global keys and writes `compilations.duckdb`. The first compilation's extractor (`data/compilations/coastal-carbon-library/extract.py`) turns dry bulk density, organic-matter fraction and carbon fraction into one fact per measurement, with values as reported and suspicious values flagged. Unit tests use a hand-built fixture; a one-study run matched its predicted counts, and every fact in the full build round-trips to its source row. `.gitignore` now tracks only `extract.py` under `data/compilations/`. No `configs/<id>.yaml` (not an experiment-contract stage).

### Commits
2c3c60f Compilations: DuckDB star-schema contract, central builder, Coastal Carbon Library extract
