---
id: 2026-10-19-table-clean-stage-01
kind: build
config: configs/2026-10-19-extract-remaining-01.yaml
---

## Session 2026-10-19

### Prompts
The table-clean stage itself was built in earlier sessions whose prompts are not
included here. This session's requests were operational: re-orient on the repo,
restart the shared Postgres, requeue stranded `processing` papers, draft and submit
the remaining-extraction configs, investigate papers that extracted zero
measurements, and commit the accumulated changes with a devlog.

### Implemented
Commits the table-clean pipeline stage as a fourth, independently-served model role
between OCR and extraction: `table_clean_worker.py`, `TableCleanAdapter` and its
builder, new `table_clean_*` paper statuses with their claim/mark/requeue store
functions, a `table-clean` CLI command, and the `TABLE_CLEAN_*` settings.
Extraction (and the judge's upstream check) now read cleaned text and claim
`table_clean_done` papers. `scripts/run_experiment.py` and `scripts/submit.sh` gain
the `table_clean` stage, and `submit.sh` can pin a GPU model via `params.gpu_type`.
Adds the run configs for the OCR, table-clean and extraction runs, including
`configs/2026-10-19-extract-remaining-01.yaml` and `-02.yaml`. Rung 1 passed (248
change-scoped tests on a throwaway Postgres); a 3-paper extraction smoke passed
before the remaining-extraction jobs were submitted.

### Commits
933df2d Add table-clean pipeline stage between OCR and extraction
ddeb378 Add run configs for the 2026-09 OCR/table-clean/extract runs and remaining extraction
