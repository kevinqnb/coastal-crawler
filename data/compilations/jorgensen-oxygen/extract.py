#!/usr/bin/env python3
"""Jorgensen et al. benthic oxygen uptake database -> compilation-contract tables.

Jorgensen, B.B., Wenzhofer, F., Egger, M., Glud, R.N. (2022): Sediment oxygen consumption: role in the global marine carbon cycle.
Earth-Science Reviews 228, 103987, doi:10.1016/j.earscirev.2022.103987, Supplementary Table S1. Raw file (this directory):
``1-s2.0-S001282522200071X-mmc2.xlsx`` (one sheet, 16 columns).

Layout (asserted): row 0 is the description, row 1 the column names, row 2 the units row, row 3 blank, then three SECTION rows (text in
column 0 only: ``In Situ 0 - 6,500m O2 fluxes``, ``In situ Hadal (>6,500m) O2 fluxes``, ``Ex Situ (<1,000m) O2 fluxes``), each preceded by
blank rows, then the DATA rows. The plan expected about 230 "stray rows holding only a code in column 0". There are none: every row other
than the three section rows and the blank rows is a full data row (``Station`` is blank on many of them, which is normal, since stations
repeat). The selection codes sit in column 0 of the data rows themselves, as numbers (0, 1, 2) or as TEXT holding several codes (``0, 3``,
``1, 4, 5``; one row reads ``0, 3,, 4``, a typo for 0, 3, 4).

Grain: one fact per numeric cell of three columns, with one sample per data row (``row<n>``, n = 0-based index among the data rows, section
and blank rows excluded):
  TOU  jorgensen_tou  B5 total_o2_uptake       ``mmol m-2 d-1`` (header unit under TOU)
  DOU  jorgensen_dou  B5 diffusive_o2_uptake   ``mmol m-2 d-1`` (the DOU unit cell is blank. The description says "O2 fluxes are presented
       in mmol m-2 d-1", asserted to be present, and TOU's unit is applied)
  OPD  jorgensen_opd  C2 o2_penetration_depth  ``mm``
``N`` (the number of values averaged) is the ``n`` of every fact of its row. One OPD cell reads ``3,8`` (a decimal comma, or a list;
undecidable). It is a skip with reason ``unparseable_value``, kept in the skip report. Every other cell of the three columns is a number.

Selection-ID (the description: "0" multiple data from one site, "1" single data from one site, "2" averaged multiple data points, "3" in
situ <10 m water depth, "4" methodical exclusion, "5" bottom water <25 uM O2): a row's codes are the numbers in its cell (exactly one of
0, 1, 2 is asserted). Flags: 2 gives ``source_average_row`` (an average of other rows of the same site, so filter downstream, as the
members are loaded too), 3 gives ``shallow_lt10m``, 4 gives ``method_excluded`` and 5 gives ``low_o2_bottom_water``. The raw cell text is
kept in ``sample.depth_notes`` as ``selection_id=<text>``.

Context: ``Station`` is ``sample.source_sample_id``. ``Depth`` (m, positive) is ``location.water_depth_m``. Rows without coordinates get
``coords_missing``. Bottom-water O2 goes to the ``bottom_water_o2`` slot when the cell is a number, with the units string of the file's units
row, ``µM or % saturatiion`` (U+00B5, the file's own spelling), and every fact of such a row carries ``o2_units_ambiguous`` (the column mixes
uM and % saturation). Text cells (ranges such as ``170-180``, ``no info``, ``> 40%``, ``oxic``) give a NULL slot and are copied into
``depth_notes`` as ``bottom_water_o2_as_written=<text>``. ``Comments`` goes to ``depth_notes`` as ``comment=<text>``. ``NPP`` (Oregon State
VGPM satellite production, INTEGRATION_PLAN 3.5) is not loaded, nor are ``Ocean`` and ``Region`` (counted in the report).

Locations: location_key = latitude | longitude | depth | Depth range label (the same coordinates carry different labels in some cases).

Habitat: the ``Depth range`` label is the raw ``habitat_type`` (spelling variants such as ``outer shelf`` and ``Outer shelf`` are separate raw
labels with their own map rows). Every label maps to Subtidal soft sediment by the dataset's scope (``habitat_from_scope`` on the facts of
rows that have a label, and rows with none get NULL habitat and no flag). Setting is ``shelf`` for the shelf labels, ``open ocean`` for rise,
abyss and hadal, and none for slope and near-shore (the SCOC mapping).

Methods: one row per (attribute, section). ``in_situ_ex_situ`` is ``in situ``, ``in situ hadal (>6,500m)`` or ``ex situ`` from the section,
with no technique.

References: ``Reference`` is filled on the first row of each study group and blank on the others, so a row belongs to the last non-blank
reference above it (asserted: the first data row of every section has one). reference_key is ``ref:<normalised string>``. Strings containing
a ``;`` or a line break are kept whole (each may cite several papers). There are no DOIs in the file.

Flags: habitat_from_scope, source_average_row, shallow_lt10m, method_excluded, low_o2_bottom_water, o2_units_ambiguous, coords_missing.

Provenance: ``source_row_ref`` = ``<file>[<sheet>]#sheet_row=<0-based row read with header=None>:<attribute>``.

Usage:
    python -I extract.py --access-date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # data/compilations/
from _g4_common import is_blank, is_num, norm_ws  # noqa: E402
from compilation_vocab import ATTRIBUTES as ATTRIBUTE_DECL  # noqa: E402
from compilation_vocab import check_skip_reasons  # noqa: E402
from coastal_crawler.compilations import ContractError, empty_table, write_extracted  # noqa: E402

SOURCE_KEY = "jorgensen-oxygen"
SOURCE_NAME = "Jorgensen et al. benthic oxygen uptake database (Earth-Science Reviews 228, 103987)"
SOURCE_DOI = "10.1016/j.earscirev.2022.103987"
SOURCE_VERSION = "Earth-Science Reviews 228:103987 supplement (2022); no version number"
SOURCE_LICENSE = "CC BY (OpenAlex: hybrid open access)"
DATA_FILE = "1-s2.0-S001282522200071X-mmc2.xlsx"
CITATION = (
    "Jorgensen, B.B., Wenzhofer, F., Egger, M., Glud, R.N. (2022): Sediment oxygen consumption: Role in the global marine "
    "carbon cycle. Earth-Science Reviews 228, 103987, https://doi.org/10.1016/j.earscirev.2022.103987, Supplementary Table S1"
)
ALLOWED_SKIP_REASONS = frozenset({"unparseable_value"})
check_skip_reasons(ALLOWED_SKIP_REASONS)

HEADER = ["Selection-ID", "Station", "Bottom Water O2 concentration", "TOU", "DOU", "OPD", "N", "Lat (N -S)", "Long (E - W)",
          "Depth", "NPP", "Depth range", "Ocean", "Region", "Comments", "Reference"]
UNITS_ROW = ["(0, 1, 2, 3, 4, 5)", "", "(µM or % saturatiion)", "(mmol m-2 d-1)", "", "(mm)", "", "(South minus)", "(West minus)",
             "(m)", "(mg C m-2 d-1)", "", "", "", "", ""]
DESCRIPTION_START = "Supplementary Table S1."
FLUX_UNIT_SENTENCE = "O2 fluxes are presented in mmol m-2 d-1"
SECTIONS = {  # section row text -> in_situ_ex_situ
    "In Situ 0 - 6,500m O2 fluxes": "in situ",
    "In situ Hadal (>6,500m) O2 fluxes": "in situ hadal (>6,500m)",
    "Ex Situ (<1,000m) O2 fluxes": "ex situ",
}
FACT_COLUMNS = {"TOU": ("jorgensen_tou", "mmol m-2 d-1"), "DOU": ("jorgensen_dou", "mmol m-2 d-1"), "OPD": ("jorgensen_opd", "mm")}
O2_UNITS = "µM or % saturatiion"  # U+00B5, the units row's own spelling
SEL_TYPO = {"0, 3,, 4": {0, 3, 4}}  # the one malformed cell
SEL_FLAGS = {2: "source_average_row", 3: "shallow_lt10m", 4: "method_excluded", 5: "low_o2_bottom_water"}
_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")


def selection_codes(v: object, what: str) -> set[int]:
    if is_num(v):
        f = float(v)  # type: ignore[arg-type]
        codes = {int(f)} if f == int(f) else set()
    elif isinstance(v, str):
        t = v.strip()
        if t in SEL_TYPO:
            codes = set(SEL_TYPO[t])
        else:
            if not re.fullmatch(r"\d(, \d)*", t):
                raise ContractError(f"{what}: unrecognised Selection-ID {v!r}")
            codes = {int(x) for x in t.split(", ")}
    else:
        codes = set()
    if not codes or not codes <= {0, 1, 2, 3, 4, 5} or len(codes & {0, 1, 2}) != 1:
        raise ContractError(f"{what}: Selection-ID {v!r} is not one of 0/1/2 plus optional 3/4/5")
    return codes


def read_sheet(path: Path) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    if len(xl.sheet_names) != 1:
        raise ContractError(f"{path.name}: sheets {xl.sheet_names}")
    df = xl.parse(xl.sheet_names[0], header=None, dtype=object, keep_default_na=False, na_values=[])  # type: ignore[arg-type]
    df.attrs["sheet_name"] = xl.sheet_names[0]
    return df


def build_tables(raw: pd.DataFrame, access_date: str) -> tuple[dict[str, pd.DataFrame], list[dict], dict]:
    sheet_name = raw.attrs["sheet_name"]
    if raw.shape[1] != 16:
        raise ContractError(f"unexpected width {raw.shape[1]}")
    desc = raw.iat[0, 0]
    if not (isinstance(desc, str) and desc.startswith(DESCRIPTION_START) and FLUX_UNIT_SENTENCE in desc):
        raise ContractError("row 0 is not the description with the flux unit sentence")
    if [norm_ws(str(v)) for v in raw.iloc[1]] != HEADER:
        raise ContractError(f"unexpected header {[norm_ws(str(v)) for v in raw.iloc[1]]}")
    if [norm_ws(str(v)) for v in raw.iloc[2]] != [norm_ws(u) for u in UNITS_ROW]:
        raise ContractError("unexpected units row")
    if any(not is_blank(v) for v in raw.iloc[3]):
        raise ContractError("row 3 is not blank")
    # ---- classify rows
    section_of: dict[int, str] = {}
    data_rows: list[int] = []
    section = None
    for r in range(4, len(raw)):
        row = list(raw.iloc[r])
        if all(is_blank(v) for v in row):
            continue
        if all(is_blank(v) for v in row[1:]):
            t = norm_ws(str(row[0]))
            if t not in SECTIONS:
                raise ContractError(f"row {r}: unknown section row {t!r}")
            section = SECTIONS[t]
            continue
        if section is None:
            raise ContractError(f"row {r}: data before the first section row")
        section_of[r] = section
        data_rows.append(r)
    if sorted(set(section_of.values())) != sorted(SECTIONS.values()):
        raise ContractError("not all three sections occur")
    n = len(data_rows)

    def col(j: int) -> list[object]:
        return [raw.iat[r, j] for r in data_rows]

    sel, station, bw, tou, dou, opd, nn = (col(j) for j in range(7))
    lat_c, lon_c, depth_c, npp, rng, ocean, region, comments, ref = (col(j) for j in range(7, 16))
    codes = [selection_codes(v, f"row {r}") for v, r in zip(sel, data_rows)]
    # ---- coordinates, depth
    lat = [None if is_blank(v) else float(v) for v in lat_c]  # type: ignore[arg-type]
    lon = [None if is_blank(v) else float(v) for v in lon_c]  # type: ignore[arg-type]
    if any((a is None) != (b is None) for a, b in zip(lat, lon)):
        raise ContractError("latitude and longitude are not blank together")
    if any(a is not None and not -90 <= a <= 90 for a in lat) or any(b is not None and not -180 <= b <= 180 for b in lon):
        raise ContractError("coordinates outside the valid range")
    if any(not is_num(v) or float(v) <= 0 for v in depth_c):  # type: ignore[arg-type]
        raise ContractError("a depth that is not a positive number")
    depth = [float(v) for v in depth_c]  # type: ignore[arg-type]
    # ---- references: forward-filled
    ref_of: list[str] = []
    cur: str | None = None
    for r, v in zip(data_rows, ref):
        if not is_blank(v):
            cur = norm_ws(str(v))
        if cur is None:
            raise ContractError(f"row {r}: no reference above")
        ref_of.append(cur)
    first_per_section: dict[str, int] = {}
    for i, r in enumerate(data_rows):
        first_per_section.setdefault(section_of[r], i)
    for sct, i in first_per_section.items():
        if is_blank(ref[i]):
            raise ContractError(f"the first data row of section {sct!r} has no reference")
    # ---- bottom water O2
    bw_num = [float(v) if is_num(v) else None for v in bw]  # type: ignore[arg-type]
    bw_text = [None if (is_blank(v) or is_num(v)) else norm_ws(str(v)) for v in bw]
    def loc_key(i: int) -> str:
        # every raw label that feeds a derived label is part of the key (INTEGRATION_PLAN 3.10): the same coordinates carry
        # different Depth range labels in 25 cases
        lab = "" if is_blank(rng[i]) else norm_ws(str(rng[i]))
        return f"{'' if lat[i] is None else repr(lat[i])}|{'' if lon[i] is None else repr(lon[i])}|{depth[i]!r}|{lab}"

    # ---- facts
    methods: dict[str, dict] = {}
    rows: list[dict] = []
    skips: list[dict] = []
    n_cells = {a: 0 for a, _ in FACT_COLUMNS.values()}
    for cname, vals in (("TOU", tou), ("DOU", dou), ("OPD", opd)):
        attr, unit = FACT_COLUMNS[cname]
        decl = ATTRIBUTE_DECL[attr]
        if unit not in decl.units:
            raise ContractError(f"{attr}: unit {unit!r}")
        for i, v in enumerate(vals):
            r = data_rows[i]
            if is_blank(v):
                continue
            n_cells[attr] += 1
            if not is_num(v):
                skips.append({"sheet_row": r, "attribute": attr, "reason": "unparseable_value", "cell": str(v)})
                continue
            x = float(v)  # type: ignore[arg-type]
            fl = sorted({SEL_FLAGS[c] for c in codes[i] if c in SEL_FLAGS}
                        | ({"habitat_from_scope"} if not is_blank(rng[i]) else set())
                        | ({"coords_missing"} if lat[i] is None else set())
                        | ({"o2_units_ambiguous"} if bw_num[i] is not None else set()))
            mk = f"{attr}|{section_of[r]}"
            if mk not in methods:
                methods[mk] = {
                    "source_key": SOURCE_KEY, "method_key": mk, "source_method_id": None, "attribute": attr,
                    "method_status": "matched", "technique": None, "coring_method": None, "carbon_type": None,
                    "carbon_measured_or_modeled": None, "fraction_carbon_method": None, "carbonates_removed": None,
                    "carbonate_removal_method": None, "conversion_applied": None, "in_situ_ex_situ": section_of[r],
                    "light_dark": None, "incubation_type": None, "experimental_status": None,
                    "params": json.dumps({"section": section_of[r]}, sort_keys=True),
                }
            nv = None if is_blank(nn[i]) else int(float(nn[i]))  # type: ignore[arg-type]
            rows.append({
                "source_key": SOURCE_KEY, "fact_key": f"row{i}:{attr}", "reference_key": f"ref:{ref_of[i]}",
                "location_key": loc_key(i),
                "sample_key": f"row{i}", "method_key": mk, "attribute": attr, "measurement_type": decl.measurement_type,
                "subfield": next(iter(decl.subfields)), "value": x, "units_raw": unit, "sd": np.nan, "se": np.nan,
                "n": nv, "flags": fl, "source_row_ref": f"{DATA_FILE}[{sheet_name}]#sheet_row={r}:{attr}",
            })
    if any(not is_blank(v) and float(v) != int(float(v)) for v in nn):  # type: ignore[arg-type]
        raise ContractError("a non-integer N")
    fact = pd.DataFrame(rows)
    fact["n"] = pd.array(fact["n"].tolist(), dtype="Int64")
    n_cells_total = sum(n_cells.values())
    if len(fact) + len(skips) != n_cells_total:
        raise ContractError(f"cells {n_cells_total} != facts {len(fact)} + skipped {len(skips)}")
    bad = {s["reason"] for s in skips} - ALLOWED_SKIP_REASONS
    if bad:
        raise ContractError(f"unlisted skip reasons {bad}")
    # ---- samples / locations (only those facts use)
    used = sorted({int(k[3:].split(":")[0]) for k in fact.fact_key})
    notes = []
    for i in used:
        parts = [f"selection_id={norm_ws(str(sel[i]))}"]
        if bw_text[i] is not None:
            parts.append(f"bottom_water_o2_as_written={bw_text[i]}")
        if not is_blank(comments[i]):
            parts.append(f"comment={norm_ws(str(comments[i]))}")
        notes.append("; ".join(parts))
    st = [None if is_blank(station[i]) else (str(int(station[i])) if is_num(station[i]) and float(station[i]) == int(float(station[i])) else norm_ws(str(station[i]))) for i in used]  # type: ignore[arg-type]
    sample = pd.DataFrame({
        "source_key": SOURCE_KEY, "sample_key": [f"row{i}" for i in used], "source_sample_id": st,
        "depth_min_cm": np.nan, "depth_max_cm": np.nan, "representative_depth_min_cm": np.nan,
        "representative_depth_max_cm": np.nan, "depth_notes": notes, "sampling_year": pd.array([None] * len(used), dtype="Int64"),
        "sampling_month": pd.array([None] * len(used), dtype="Int64"), "taxon": None, "taxon_rank": None,
        "vegetated_or_bare": None, "marsh_zone": None, "sediment_type": None, "photosynthetic_sediment": None,
        "sample_depth_m": np.nan, "water_temperature_c": np.nan, "water_salinity": np.nan, "water_salinity_units": None,
        "bottom_water_o2": [np.nan if bw_num[i] is None else bw_num[i] for i in used],
        "bottom_water_o2_units": [None if bw_num[i] is None else O2_UNITS for i in used],
    })
    first: dict[str, int] = {}
    for i in used:
        first.setdefault(loc_key(i), i)
    lkeys = sorted(first)
    nl = len(lkeys)
    labels: dict[str, set] = {k: set() for k in lkeys}
    for i in used:
        labels[loc_key(i)].add(None if is_blank(rng[i]) else norm_ws(str(rng[i])))
    if any(len(v) > 1 for v in labels.values()):
        raise ContractError("one location with two Depth range labels")
    loc = pd.DataFrame({
        "source_key": SOURCE_KEY, "location_key": lkeys, "site_name": None, "source_site_id": None, "source_core_id": lkeys,
        "latitude": [np.nan if lat[first[k]] is None else lat[first[k]] for k in lkeys],
        "longitude": [np.nan if lon[first[k]] is None else lon[first[k]] for k in lkeys],
        "position_accuracy": np.nan, "position_accuracy_units": None, "position_method": None, "country": None,
        "admin_division": None, "water_body": None, "habitat_type": [next(iter(labels[k])) for k in lkeys],
        "geomorphic_setting": None, "bioregion": None, "marine_ecoregion": None, "salinity_class": None,
        "salinity_class_level": None, "vegetation_class": None, "vegetation_class_level": None, "tidal_position": None,
        "tidal_position_level": None, "modification_status": [[] for _ in range(nl)], "dominant_species": [[] for _ in range(nl)],
        "elevation": np.nan, "elevation_units": None, "elevation_datum": None, "elevation_method": None,
        "water_depth_m": [depth[first[k]] for k in lkeys], "distance_to_shore_km": np.nan,
    })
    # ---- references
    ref_strs = sorted(set(fact.reference_key))
    reference = pd.DataFrame({"source_key": SOURCE_KEY, "reference_key": ref_strs})
    yrs = [_YEAR_RE.search(k[4:]) for k in ref_strs]
    citation = pd.DataFrame({
        "source_key": SOURCE_KEY, "citation_key": [f"{k}|0" for k in ref_strs], "reference_key": ref_strs,
        "bibliography_id": [k[4:] for k in ref_strs], "publication_type": None, "doi": None, "title": None, "authors": None,
        "year": pd.array([int(m.group(1)) if m else None for m in yrs], dtype="Int64"), "url": None, "openalex_id": None,
        "doi_resolved": None, "doi_resolution_source": None,
    })
    method = pd.DataFrame(sorted(methods.values(), key=lambda m: m["method_key"]))
    source = pd.DataFrame([{
        "source_key": SOURCE_KEY, "name": SOURCE_NAME, "version": SOURCE_VERSION, "doi": SOURCE_DOI,
        "access_date": access_date, "license": SOURCE_LICENSE,
        "notes": CITATION + " License is from OpenAlex via the integration plan (the workbook states none).",
    }])
    tables = {
        "source": source, "reference": reference, "citation": citation,
        "study_screening": empty_table("study_screening"), "location": loc, "sample": sample,
        "method": method, "fact": fact,
    }
    for what, a, b in (
        ("sample", fact.sample_key, sample.sample_key), ("reference", fact.reference_key, reference.reference_key),
        ("method", fact.method_key, method.method_key), ("location", fact.location_key, loc.location_key),
    ):
        if set(a) != set(b):
            raise ContractError(f"{what} rows and fact {what}s differ")
    flag_counts: dict[str, int] = {}
    for fl in fact["flags"]:
        for x in fl:
            flag_counts[x] = flag_counts.get(x, 0) + 1
    report = {
        "sheet_rows": len(raw),
        "data_rows": n,
        "section_rows_found": len(SECTIONS),
        "data_rows_by_section": pd.Series([section_of[r] for r in data_rows]).value_counts().sort_index().to_dict(),
        "rows_without_station": int(sum(1 for v in station if is_blank(v))),
        "rows_without_coordinates": int(sum(1 for a in lat if a is None)),
        "facts": len(fact),
        "facts_by_attribute": fact.attribute.value_counts().sort_index().to_dict(),
        "facts_by_type_subfield": {
            f"{t}|{s}": int(c) for (t, s), c in fact.groupby(["measurement_type", "subfield"]).size().items()
        },
        "numeric_cells_by_attribute": n_cells,
        "skipped": len(skips),
        "facts_with_n": int(fact.n.notna().sum()),
        "selection_id_texts": pd.Series([str(v) for v in sel]).value_counts().sort_index().to_dict(),
        "samples": len(sample),
        "samples_with_numeric_bottom_water_o2": int(sample.bottom_water_o2.notna().sum()),
        "rows_with_text_bottom_water_o2": int(sum(1 for v in bw_text if v is not None)),
        "bottom_water_o2_text_values_top": pd.Series([v for v in bw_text if v is not None]).value_counts().head(10).to_dict(),
        "locations": len(loc),
        "locations_without_habitat_label": int(loc.habitat_type.isna().sum()),
        "methods": len(method),
        "references": len(reference),
        "reference_strings_with_semicolon_or_linebreak": int(sum(1 for k in ref_strs if ";" in k or "\n" in k)),
        "citations_without_year": int(citation.year.isna().sum()),
        "flag_counts": flag_counts,
        "not_loaded_nonblank_cells": {
            "NPP": int(sum(1 for v in npp if not is_blank(v))), "Ocean": int(sum(1 for v in ocean if not is_blank(v))),
            "Region": int(sum(1 for v in region if not is_blank(v)))},
        "rows_with_comment": int(sum(1 for v in comments if not is_blank(v))),
        "data_rows_without_any_loaded_flux": int(sum(1 for i in range(n) if all(is_blank(v) for v in (tou[i], dou[i], opd[i])))),
    }
    return tables, skips, report


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=here)
    ap.add_argument("--out-dir", type=Path, default=here / "extracted")
    ap.add_argument("--access-date", required=True, help="date the file was downloaded, YYYY-MM-DD")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.access_date):
        raise SystemExit("--access-date must be YYYY-MM-DD")
    raw = read_sheet(args.raw_dir / DATA_FILE)
    tables, skips, report = build_tables(raw, args.access_date)
    write_extracted(tables, args.out_dir)
    with open(args.out_dir / "skip_report.jsonl", "w") as fh:
        for s in skips:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
