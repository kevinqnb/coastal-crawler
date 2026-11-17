---
id: 2026-11-17-compilations-g3-boynton-fulweiler-pessarrodona-davis-01
kind: build
---

## Session 2026-11-17

### Prompts
- Continue the compilations work previously started on a remote machine: split into four separate agent sessions, each on its own branch, reconciled at the end; agents do not touch other compilations outside their group.
- Group 3: integrate boynton-flux, fulweiler-nfix, pessarrodona-seaweed and davis-calcification following INTEGRATION_PLAN section 5, the cross-cutting rules in section 3 and the J1 rules in 1.2; one compilation at a time through the full ladder; write each README from extract_report.json; commit and /devlog after all compilations in the subset.
- Specifics: the harmonised-value (D1) per-row method pattern and the sample-keyed J1 collapse are key choices inside extract.py, no contract code change; Davis has no reference list (record the short references as given); the Pessarrodona label map is approved as proposed; verify the Boynton nutrient-flux sign convention against the source before writing it in the README, load v3 only.

### Implemented
Four new extracts under data/compilations/ (boynton-flux 16,693 facts, fulweiler-nfix 9,940, pessarrodona-seaweed 1,121, davis-calcification 347), each with a README and fixture tests (tests/test_compilations_{boynton,fulweiler,pessarrodona,davis}.py), plus a shared helper module data/compilations/_g3_common.py (J1 collapse, cell helpers). Shared edits are attribute declarations (including the first multi-spelling J1 units), one flag (error_type_missing), and label-map rows; no contract code changed. The 13-source build has 344,744 facts, the nine earlier sources unchanged, and every new fact round-trips to its raw cell (and every J1 cell to a fact). The Boynton sign convention (negative = into the sediment) is documented from the source's Edit Notes, not Table 2. No configs/<id>.yaml (not an experiment-contract stage).

### Commits
d57d940 Compilations: integrate boynton-flux
8ffa163 Compilations: integrate fulweiler-nfix
dc1ff9e Compilations: integrate pessarrodona-seaweed
e214f9d Compilations: integrate davis-calcification
