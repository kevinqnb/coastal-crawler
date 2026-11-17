---
id: 2026-11-16-compilations-strydom-bio-01
kind: build
---

## Session 2026-11-16

### Prompts
- Continue the compilations work: integrate strydom-seagrass and stratma-bio (reusing Foster and SCOC code), following the integration plan, one compilation at a time through the full ladder (unit tests, real-data run, one-study end-to-end with a predicted count, full build and source-row round trip), READMEs written from `extract_report.json`.
- Stop and ask on a count mismatch and before changing shared vocabulary or contract code; do not touch other compilations; commit and devlog after both.
- (Answers during the session) register a flag for a latitude beyond the pole; keep one citation row per composite reference string.

### Implemented
Two new extracts, `data/compilations/strydom-seagrass/extract.py` (14,266 facts, reuses Foster's helpers) and `data/compilations/stratma-bio/extract.py` (63,350 facts, two data files, each resolved against its own study list with asserted crosswalk constants), with READMEs and tests (`tests/test_compilations_strydom.py`, `tests/test_compilations_bio.py`). Shared edits: unit strings on the Foster seagrass attribute declarations, two new attributes, a `lat_beyond_pole` flag, label-map rows and the index README; no contract code changed. The five-source build has 311,684 facts; every new fact round-trips to its raw row and the other compilations are unchanged. No `configs/<id>.yaml` (not an experiment-contract stage).

### Commits
1e6c2cb Compilations: integrate strydom-seagrass and stratma-bio (BenBioDen)
