---
id: 2026-11-17-compilations-g6-cloern-ribeiro-01
kind: build
---

## Session 2026-11-17

### Prompts
- Continue the compilations work previously started on a remote machine, split into four separate agent sessions, each on its own branch, reconciled at the end; agents do not touch other compilations outside their group; follow INTEGRATION_PLAN.md section 5, the cross-cutting rules in section 3 and the J1 rules in 1.2; take one compilation at a time through the full ladder; write each README from extract_report.json; commit and /devlog after all compilations in the subset.
- Group 6: cloern-phytoplankton (PDF parse, checked-in hand verification, sum of n and ecosystem count, no coordinates, references by explicit crosswalk to the supplement's own list), then ribeiro-mangrove (reference list only); own contract change C1, an explicit --allow-factless flag on the build with unit tests for both exemptions and the raise.

### Implemented
C1: `--allow-factless <source_key>` on scripts/build_compilations.py and a keyword on build_compilations / read_extracted / validate_tables / write_extracted / add_derived_labels (and derive_location_labels in compilation_vocab.py): exempts a named source from the empty-fact-table check and, for an empty location table, from the no-habitat-label-map error; raises if the named source has facts or is not being built (tests/test_compilations_factless.py). cloern-phytoplankton: 163 facts (not the plan's ~250; 131 ecosystems; the n column sums to 1,147, one short of the 1,148 in the supplement's own caption, verified three ways), no coordinates, references matched to the supplement's reference list (144 entries, all cited) by an explicit crosswalk, independent re-reading and database round trip in verify_table.py. ribeiro-mangrove: 87 references and 87 citations, no measurements. The build with all eleven sources has 316,806 facts (316,643 + 163); the nine earlier sources are unchanged. No configs/<id>.yaml (not an experiment-contract stage).

### Commits
6c5e36f Compilations: --allow-factless build flag (C1)
50fc3c6 Compilations: integrate cloern-phytoplankton
80d0bc9 Compilations: integrate ribeiro-mangrove
