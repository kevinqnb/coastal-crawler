---
id: 2026-10-20-repr-modernbert-01
kind: build
config: configs/2026-10-20-repr-modernbert-full-01.yaml
---

## Session 2026-10-20

### Prompts
Summary of this session's requests (not verbatim):

- scholarlm's RepresentationLM gained mean-pooling, paragraph windowing and bidirectional models; use them to collect representations over the database's documents with `answerdotai/ModernBERT-large`. Will context windows exceeding the model length be a problem for our OCR documents? Investigate and plan.
- Which problems can be fixed upstream? (The user is handling those separately.)
- Meanwhile, submit full runs: skip documents that would hard-error from overflow, references, or unclosed table tags. Overflow raises, so it must be handled somehow.
- Decisions: keep reference-contaminated papers but flag them; context radius 1; layers 24 and 28; plus a separate run embedding each term alone.
- Commit with a devlog, then submit the smoke run.

### Implemented
Bumped scholarlm to 95bde63 and made the repr stage build bidirectional, windowed, mean-pooled `RepresentationLM` runs from required `REPR_LM_*` settings. Seeding now excludes papers that would raise (over-length window, text-cleaning failure) via the model's own cleaning and tokenizer, only for explicitly flagged kinds, and writes a manifest of exclusions with reasons; shards and the index carry window/pooling provenance and per-paper reference flags. Added a `repr_terms` stage that embeds each term alone, and five configs (`configs/2026-10-20-repr-modernbert-{smoke-01,tiny-01,tiny-02,full-01,terms-01}.yaml`). Unit tests pass and a CPU plumbing check on the real model matched an independent measurement of the skip counts; GPU smoke, tiny and full runs are still to come.

### Commits
5ed8571 Repr stage: ModernBERT bidirectional/windowed/mean-pooled collection with explicit overflow and cleaning-error exclusion
