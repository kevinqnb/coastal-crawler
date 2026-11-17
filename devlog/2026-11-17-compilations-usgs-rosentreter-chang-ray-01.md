---
id: 2026-11-17-compilations-usgs-rosentreter-chang-ray-01
kind: build
---

## Session 2026-11-17

### Prompts
- Continue the compilations work: integrate usgs, rosentreter, chang and ray following the integration plan (section 5, cross-cutting rules, J1 rules); one compilation at a time through the full ladder (fixture tests, real-data run, full build and source-row round trip); READMEs written from extract_report.json.
- Stop and ask before changing shared vocabulary or contract code; commit and /devlog after all four; do not touch other compilations.
- (Answers during the session) register assigned_by_source for the USGS salt-flat zeros; register negative_sd and keep the sd as reported for the one negative SD in Ray.

### Implemented
Four new extracts under data/compilations/ (usgs-wetlands 1,485 facts, rosentreter-ghg 738, chang-denitrification 2,495, ray-oyster 241), each with a README and fixture tests (tests/test_compilations_{usgs,rosentreter,chang,ray}.py). Shared edits are two flags, attribute declarations (including the first J1 attributes), label-map rows and the index README; no contract code changed. The nine-source build has 316,643 facts, the five earlier sources unchanged, and every new fact round-trips to its raw cell. No configs/<id>.yaml (not an experiment-contract stage).

### Commits
edf419a Compilations: integrate usgs-wetlands, rosentreter-ghg, chang-denitrification, ray-oyster
