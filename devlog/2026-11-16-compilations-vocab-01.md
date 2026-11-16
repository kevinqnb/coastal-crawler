---
id: 2026-11-16-compilations-vocab-01
kind: build
---

## Session 2026-11-16

### Prompts
Summary of this session's requests (not verbatim):

- Review the extraction plan (habitats, measurement types, extraction-record context fields) against the compilations aggregation strategy and the current database (CCN, SCOC, Foster); decide what must change, propose a plan, and update the compilations README. Compilations must not be limited to the plan's types, but every reasonable attempt should map them onto it as a common language.
- Approved moving SCOC grain size to facts and the CCN/SCOC label mappings; go ahead. DOI resolution, the coverage report and new compilations are out of scope (later builds); just keep the structure amenable to them.
- Keep the vocabulary module and label map under `data/compilations/`, not `src/coastal_crawler/`.
- Clean up: delete unused code, drop history-of-events comments and README notes, don't reference the plan document in the README (describe what we do), and make the README more concise.
- Commit and /devlog.

### Implemented
Added `data/compilations/compilation_vocab.py` (21 measurement types with subfields, per-attribute declarations of type, subfields and allowed units, habitat/setting/salinity/modification vocab, label-map loading and checks) and `data/compilations/compilation_label_maps.csv`. The contract in `src/coastal_crawler/compilations.py` gains `fact.measurement_type`/`subfield`, derived location labels (written by `write_extracted`, recomputed by `read_extracted`), water depth and in-situ water context, incubation/experimental status, and nullable DOI-resolution columns; the build raises on undeclared attributes, units or unlisted labels. The three `extract.py` scripts set types and subfields; CCN gains core-level 210Pb accretion facts, SCOC grain size becomes facts, and the README is rewritten around the standard vocabulary. Tests: `tests/test_compilation_vocab.py` plus extended CCN/SCOC/Foster tests (74 pass); the rebuilt database keeps all earlier facts unchanged and adds 449. No `configs/<id>.yaml` (not an experiment-contract stage).

### Commits
c7c724e Compilations: standard measurement types and location labels
