---
id: 2026-10-20-warehouse-parsed-fields-01
kind: build
---

## Session 2026-10-20

### Prompts
Summary of this session's requests (not verbatim):

- After the scholarlm pin bump, investigate what broke in the table-cleaning and extraction adapters.
- Are the new parsed fields (qualifiers, point_value, lower, upper, …) added to every measurement, non-null, and recorded in the databases?
- Switch the warehouse to the model's parsed fields: for every measurement keep the attribute, unit, raw string value, and all parsed value fields. Previously extracted measurements are invalidated and will be re-extracted.
- Commit, write the devlog, then prepare to reset the databases and rerun extraction after table-cleaning.

### Implemented
The adapters were already ported to the new scholarlm pin; the investigation found no remaining breakage but noted that extraction now makes an extra parse call per record. The DuckDB warehouse now stores scholarlm's parsed fields (`qualifiers`, `point_value`, `lower`, `upper`, `list_values`, `tolerance`, `standard_deviation`) as columns on `extractions_fact`, alongside `attribute`, `quantity_raw` and `units_raw`. `qualifier_dim`, the regex parser and canonical-unit conversion are removed from the warehouse build (`src/coastal_crawler/warehouse.py`, `scripts/build_warehouse.py`). Rows without a parse are skipped and logged as `missing_parse`; partial or mistyped parses raise. Unit tests pass; no smoke run or real-model check yet.

### Commits
0acb3f1 Warehouse: carry model-parsed value fields instead of regex qualifiers
