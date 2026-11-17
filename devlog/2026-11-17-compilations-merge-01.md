---
id: 2026-11-17-compilations-merge-01
kind: build
---

## Session 2026-11-17

### Prompts
- Continue the compilations work started on a remote machine: check this machine can continue the build, then split the four remaining subsets (boynton/fulweiler/pessarrodona/davis; tang/shao/francesco/mouw/jorgensen; paradis-mosaic; cloern/ribeiro) into four agent sessions, each on its own branch, and reconcile them to main at the end; agents must not touch other compilations; follow INTEGRATION_PLAN section 5, the cross-cutting rules in section 3 and the J1 rules in 1.2; one compilation at a time through the full ladder; READMEs from extract_report.json.
- Pause before merging and go through each group's decisions one by one. Shao: mix DOI and non-DOI references (blank DOI is fine); drop missing values; flag `mean (sd)` as an aggregate and store the bracket in the standard-deviation field; flag censored values as an inequality with a separate field for the sign; copy the Notes column into a methods field (not `params`: add a `notes` column); organism in `taxon`; use every SOURCE string as the reference exactly as written; accept `n` and `DL` as skipped.
- Other decisions: samples differing in salinity, temperature or month are separate samples; drop Francesco's temperature; pH is always `unitless`; keep MOSAIC non-bulk fractions flagged; MOSAIC errors default to SD with a method note and `error_type_missing`; Cloern 1,147 vs 1,148 accepted; Ribeiro license CC BY 3.0; all other small items accepted. Contract changes as their own commits.

### Implemented
Set-up check on the new machine (nine built compilations rebuild to 316,643 facts; Ray re-extracts identically; `pg_restore` 16.14 reads the MOSAIC archive, so no version gate). Four git worktrees with per-group symlinked raw files. Two contract commits on main: `fact.value_comparator` with the registered flag `inequality` (the two must appear together; only `<` and `>`), and a free-text `method.notes` column, both nullable and listed in `OPTIONAL_COLUMNS` so an older extract.py gets NULL while the build still demands the full schema; the nine earlier extracts were regenerated and are identical apart from the two new NULL columns. Merged the four group branches (conflicts only in the shared vocabulary, label-map and vocabulary-test files, resolved additively; CRLF label map normalised). All twelve new extracts re-run on main and equal the branch outputs table by table. Full 21-source build with `--allow-factless ribeiro-mangrove`: 695,589 facts (316,643 earlier + boynton 16,693, fulweiler 9,940, pessarrodona 1,121, davis 347, tang 9,890, shao 32,224, mouw 57,045, francesco 99,779, jorgensen 4,623, paradis-mosaic 147,121, cloern 163, ribeiro 0). Top-level README rewritten for the new patterns, registries with real counts and DOI coverage; `.gitignore` exceptions for the helper modules and the PDF review artifacts; `pdfplumber` added to `pyproject.toml` (already in `uv.lock`). Two tests in tests/test_adapter.py fail on main and on the commit before this work (a `.env` comment value cannot be parsed as an integer); unrelated.

### Commits
e157fb3 Compilations contract: fact.value_comparator and the inequality flag
bf99dd6 Compilations contract: method.notes for the source's free-text comments
7674427 Compilations: reconcile groups 3-6; top-level README, gitignore exceptions, pdfplumber
(group branch merges f3f134a, b513398, 6fc4b9d, 24ab6b2; per-group devlogs g3 to g6 carry the compilation commits)
