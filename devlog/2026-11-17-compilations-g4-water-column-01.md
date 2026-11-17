---
id: 2026-11-17-compilations-g4-water-column-01
kind: build
---

## Session 2026-11-17

### Prompts
- Continue the compilations work previously started on a remote machine, split into four separate agent sessions, each on its own branch and reconciled at the end; agents do not touch other compilations outside their group; follow INTEGRATION_PLAN.md section 5, the cross-cutting rules in section 3 and the J1 rules in 1.2; take one compilation at a time through the full ladder; write each README from extract_report.json; commit and /devlog after all compilations in the subset.
- Group 4 (water-column sources): tang-nitrification, shao-nfix, mouw-carbon, francesco-phytoplankton, jorgensen-oxygen. Habitat `Water column` from scope; J1 sample-keyed collapse implemented in this group's own module; stop and report on anything the plan does not settle.

### Implemented
Four new extracts under data/compilations/ (tang-nitrification 9,824 facts, mouw-carbon 57,045, francesco-phytoplankton 99,779, jorgensen-oxygen 4,623), each with a README and fixture tests (tests/test_compilations_{tang,mouw,francesco,jorgensen}.py), plus a shared group helper data/compilations/_g4_common.py (the J1 sample-keyed collapse). shao-nfix was stopped before any code: the plan does not settle its mixed reference/comment column, its cell codes and `mean (sd)` strings, the free-text Notes (including stray numbers in the Notes column) or the Cell_Specific sheet; the findings are in the session report. Shared edits are attribute declarations (a merge-safe `_g4_declare_j1` helper), one skip reason (`unparseable_value`) and label-map rows; no contract code changed. The thirteen-source build on this branch has 487,914 facts, the nine earlier sources unchanged, and every new fact round-trips to its raw cell. No configs/<id>.yaml (not an experiment-contract stage).

### Commits
b67b64c Compilations: integrate tang-nitrification
6a78180 Compilations: integrate mouw-carbon
11f5cd0 Compilations: integrate francesco-phytoplankton
6e383c6 Compilations: integrate jorgensen-oxygen
5596037 tests: register the unparseable_value skip reason in the vocab known-answer test
