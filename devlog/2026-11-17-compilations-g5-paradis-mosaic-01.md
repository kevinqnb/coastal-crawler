---
id: 2026-11-17-compilations-g5-paradis-mosaic-01
kind: build
---

## Session 2026-11-17

### Prompts
- Continue the compilations work previously started on a remote machine, split into four separate agent sessions on their own branches, reconciled at the end; agents do not touch other compilations outside their group; follow INTEGRATION_PLAN.md section 5, the cross-cutting rules in section 3 and the J1 rules in 1.2; one compilation at a time through the full ladder; READMEs from extract_report.json; commit and devlog after all compilations in the subset.
- Group 5: paradis-mosaic. The pg_restore binary path is an argument (no plan-derived version gate; fail loudly on error or unexpected COPY tables/row counts); read DatasetS1/S2 for what the error columns mean (store in params if unstated); --min-subtidal-depth-m required, no default (1 m); README explains every choice with counts; row accounting closes for every table family.

- (Follow-up) Decisions: store the unstated-type error columns as sd (assume SD) with flag error_type_missing and a method.notes sentence; threshold 1 m, non_bulk_material and no coords_approximate accepted; merge main (value_comparator, method.notes) first.

### Implemented
data/compilations/paradis-mosaic/extract.py (pg_restore wrapper, COPY-text parser, 147,121 facts from sample_composition and sample_sediment_properties, errors stored as sd (assumed SD, flag error_type_missing, method.notes) with the raw error in params, 394 skips, 55,104+1,554 cells not loaded, accounting asserted per column) with README and tests/test_compilations_mosaic.py. Shared vocabulary: 26 mosaic_* attributes, G1 mode_grain_size, flags non_bulk_material and outside_source_qc_range, skip reasons no_metadata_row, error_without_fact, material_unit_unclear, 18 label-map rows. Full build: 463,764 facts; full-run SQL prediction, independent round trip and two-run determinism all matched.

### Commits
5bb1928 Compilations: integrate paradis-mosaic
