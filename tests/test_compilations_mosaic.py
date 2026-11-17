"""Unit tests (rung 1) for the paradis-mosaic extract.py.

Hand-built fixture: 4 cores, 4 samples, 3 articles and a few cells of ``sample_composition`` / ``sample_sediment_properties``
(plus one isotope table with 2 cells that is not loaded). Expected numbers are derived by inspection in the comments.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from coastal_crawler.compilations import (
    ContractError,
    add_derived_labels,
    assign_global_keys,
    build_compilations,
    read_extracted,
    validate_tables,
    write_extracted,
)

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "paradis-mosaic" / "extract.py"
_spec = importlib.util.spec_from_file_location("mosaic_extract", _EXTRACT)
assert _spec and _spec.loader
mx = importlib.util.module_from_spec(_spec)
sys.modules["mosaic_extract"] = mx
_spec.loader.exec_module(mx)

_REAL_CHECK = mx.check_dump_tables
ACCESS = "2026-11-15"
THRESH = 1.0

# ------------------------------------------------------------------ COPY text parser


def test_parse_copy_escapes_and_nulls() -> None:
    sql = [
        "-- comment\n",
        'COPY mosaic.demo (id, "a,b", note) FROM stdin;\n',
        "1\tx\t1.0 MV\\nTandetron\n",
        "2\t\\N\ta\\\\Nb\\tc\\101\\x41\n",
        "3\t\\\\N\t\\N\n",
        "\\.\n",
        "COPY mosaic.empty (z) FROM stdin;\n",
        "\\.\n",
    ]
    t = mx.parse_copy_sql(sql)
    assert set(t) == {"demo", "empty"} and t["empty"] == (["z"], [])
    cols, rows = t["demo"]
    assert cols == ["id", "a,b", "note"]
    assert rows[0] == ["1", "x", "1.0 MV\nTandetron"]  # \n is a newline
    assert rows[1] == ["2", None, "a\\Nb\tc" + "AA"]  # \\ -> backslash, so "\\N" is the text \N; \t tab; octal 101 and hex 41 -> A
    assert rows[2] == ["3", "\\N", None]  # a field of backslash-backslash-N is the text "\N", not NULL


def test_parse_copy_rejects_bad_blocks() -> None:
    with pytest.raises(ContractError, match="not terminated"):
        mx.parse_copy_sql(["COPY a.b (x) FROM stdin;\n", "1\n"])
    with pytest.raises(ContractError, match="header has 2"):
        mx.parse_copy_sql(["COPY a.b (x, y) FROM stdin;\n", "1\n", "\\.\n"])
    with pytest.raises(ContractError, match="two COPY blocks"):
        mx.parse_copy_sql(["COPY a.b (x) FROM stdin;\n", "\\.\n", "COPY c.b (x) FROM stdin;\n", "\\.\n"])
    with pytest.raises(ContractError, match="not ASCII"):
        mx.parse_copy_sql(["COPY a.b (x) FROM stdin;\n", "\\351\n", "\\.\n"])


# ------------------------------------------------------------------ pg_restore wrapper (fake binaries)


def _fake(tmp: Path, name: str, body: str) -> Path:
    p = tmp / name
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


_HEADER = f"echo '; Archive created at {mx.DUMP_CREATED} 10:00:00 UTC'; echo '; dbname: {mx.DUMP_DBNAME}'"


def _script(list_body: str = _HEADER, data_body: str = ": > \"$out\"; echo hello > \"$out\"") -> str:
    # --version -> text; --list -> header; --data-only -f OUT dump -> write OUT
    return (
        'case "$1" in\n'
        '  --version) echo "pg_restore (PostgreSQL) 99.0 (fake)";;\n'
        f"  --list) {list_body};;\n"
        '  --data-only) out="$3"; ' + data_body + ";;\n"
        "esac\n"
    )


def test_wrapper_runs_a_working_binary(tmp_path) -> None:
    b = _fake(tmp_path, "pg_restore", _script())
    dump = tmp_path / "dump"
    dump.write_text("x")
    info = mx.run_pg_restore(b, dump, tmp_path / "out.sql")
    assert info["pg_restore_version"].startswith("pg_restore (PostgreSQL) 99.0")
    assert (tmp_path / "out.sql").read_text().strip() == "hello"


def test_wrapper_fails_loudly(tmp_path) -> None:
    dump = tmp_path / "dump"
    dump.write_text("x")
    with pytest.raises(ContractError, match="does not exist"):
        mx.run_pg_restore(tmp_path / "nope", dump, tmp_path / "o.sql")
    b = _fake(tmp_path, "failing", 'echo "pg_restore: unsupported version (1.14) in file header" >&2; exit 1\n')
    with pytest.raises(ContractError, match="unsupported version"):
        mx.run_pg_restore(b, dump, tmp_path / "o.sql")
    b = _fake(tmp_path, "noisy", _script(data_body='echo ok > "$out"; echo warning >&2'))
    with pytest.raises(ContractError, match="warning"):
        mx.run_pg_restore(b, dump, tmp_path / "o.sql")
    b = _fake(tmp_path, "wrongdb", _script(list_body="echo '; Archive created at 2023-04-25'; echo '; dbname: other'"))
    with pytest.raises(ContractError, match="dbname"):
        mx.run_pg_restore(b, dump, tmp_path / "o.sql")
    b = _fake(tmp_path, "wrongdate", _script(list_body=f"echo '; Archive created at 2024-01-01'; echo '; dbname: {mx.DUMP_DBNAME}'"))
    with pytest.raises(ContractError, match="created at"):
        mx.run_pg_restore(b, dump, tmp_path / "o.sql")


def test_gate_rejects_wrong_table_set_and_counts() -> None:
    t = {"articles": (["article_id"], [["1"]])}
    with pytest.raises(ContractError, match="differ from expected"):
        _REAL_CHECK(t)
    full = {n: (list(mx.EXPECTED_COLUMNS.get(n, ["sample_id", "material_analyzed_id", "replicate"])), []) for n in mx.EXPECTED_ROWS}
    with pytest.raises(ContractError, match="expected 368"):
        _REAL_CHECK(full)


# ------------------------------------------------------------------ fixture dump

COMP, SED = mx.COMPOSITION, mx.SEDIMENT
_UNIT_RAW = {"clay_%_2microm": "µm"}  # the dump's (wrong) unit for this column


def _desc():
    rows, aid = [], {}
    for tbl_id, (tbl, cmap) in ((2, ("sample_composition", COMP)), (1, ("sample_sediment_properties", SED))):
        for col, spec in cmap.items():
            for c in (col, spec.error_column):
                if c is None:
                    continue
                aid[c] = str(len(aid) + 1)
                decl = next(iter(mx.ATTRIBUTE_DECL[spec.attribute].units))
                unit = _UNIT_RAW.get(c, "" if decl == mx.UNITS_BLANK else decl)
                rows.append([aid[c], c, unit, None, str(tbl_id)])
    return rows, aid


DESC_ROWS, AID = _desc()


def _tbl(cols, rows):
    return (list(cols), [list(r) for r in rows])


def _make_dump() -> dict:
    comp_cols = ["sample_id", "material_analyzed_id"] + list(COMP) + [s.error_column for s in COMP.values() if s.error_column] + ["replicate"]
    sed_cols = (["sample_id", "material_analyzed_id"] + list(SED) + ["mean_grain_size_microm_error", "surface_area_m2_g",
                                                                      "surface_area_m2_g_error", "cumulative_dry_mass_g_cm2", "replicate"])

    def comp(sid, mat, rep, **vals):
        r = {c: None for c in comp_cols}
        r.update(sample_id=sid, material_analyzed_id=mat, replicate=rep)
        r.update(vals)
        return [r[c] for c in comp_cols]

    def sed(sid, mat, rep, **vals):
        r = {c: None for c in sed_cols}
        r.update(sample_id=sid, material_analyzed_id=mat, replicate=rep)
        r.update(vals)
        return [r[c] for c in sed_cols]

    k = lambda d: {kk.replace("_PCT", "_%"): v for kk, v in d.items()}  # noqa: E731
    composition = [
        comp("1", "1", "1", **k({"total_organic_carbon_PCT": "1.5000", "total_organic_carbon_PCT_error": "0.2", "total_nitrogen_PCT": "0.1"})),
        comp("2", "1", "1", **k({"total_organic_carbon_PCT": "0.0"})),
        comp("3", "74", "2", **k({"total_organic_carbon_PCT": "2.0"})),
        comp("4", "1", "1", **k({"total_organic_carbon_PCT": "3.0", "total_organic_carbon_PCT_error": "0.1"})),
        comp("1", "4", "1", **k({"total_organic_carbon_PCT": "0.02"})),
        comp("1", "1", "2", **k({"CaCO3_PCT": "-1.0"})),
        comp("2", "1", "2", **{"OC:TN_ratio": "9.5"}),
    ]
    sediment = [
        sed("1", "1", "1", dry_bulk_density_g_cm3="1.1", porosity="0.5", **{"clay_%_2microm": "10.0"}, mean_grain_size_microm="200",
            mean_grain_size_microm_error="5"),
        sed("2", "1", "1", mean_grain_size_microm_error="7", surface_area_m2_g="3.0", cumulative_dry_mass_g_cm2="9.0"),
    ]
    isotopes = [["1", "1", "-20.1", "-21.0", "1"]]

    def md(sid, mat, col, rep, core, art, method="_unknown", det=None, calc="0"):
        return [sid, mat, AID[col], "0", core, calc, art, "1", "XX", rep, method, det]

    oc, tn, caco3 = AID["total_organic_carbon_%"], AID["total_nitrogen_%"], AID["CaCO3_%"]
    metadata = [
        md("1", "1", "total_organic_carbon_%", "1", "1", "1", "elemental analyzer", "Carlo Erba"),
        md("1", "1", "total_nitrogen_%", "1", "1", "1", "elemental analyzer"),
        md("1", "1", "total_nitrogen_%", "1", "1", "2", "elemental analyzer"),  # second article, same method
        md("2", "1", "total_organic_carbon_%", "1", "1", "1", "_unknown", None, "1"),  # calculated
        md("3", "74", "total_organic_carbon_%", "2", "2", "2"),
        md("1", "4", "total_organic_carbon_%", "1", "1", "1"),
        md("1", "1", "CaCO3_%", "2", "1", "1", "titration"),
        md("1", "1", "CaCO3_%", "2", "1", "2", "coulometry"),  # disagreeing methods -> ambiguous
        md("2", "1", "OC:TN_ratio", "2", "1", "1"),
        md("1", "1", "dry_bulk_density_g_cm3", "1", "1", "1", "wet and dry weight"),
        md("1", "1", "porosity", "1", "1", "1"),
        md("1", "1", "clay_%_2microm", "1", "1", "1", "laser"),
        md("1", "1", "mean_grain_size_microm", "1", "1", "1", "laser"),
    ]
    # sample 2's OC:TN row above says core "1" for sample 2: fine (sample 2 is in core 1)
    # the known mismatch: sample 9 (a 14C-only sample) has 4 metadata rows pointing at another core
    for aid in ("1", "2", "3", "4"):
        metadata.append([ "9", "1", aid, "0", "3", "0", "1", "1", "XX", "1", "_unknown", None])
    cores = [
        ["1", "C-one", "10.5", "-20.25", "2001-05-17", "50.0", "1", "x", None, None, None, "Prov A", None, "North Sea", None, None, "shelf", "SP", "G", None, "0", "2001", "5", None],
        ["2", "C-two", "-5.0", "40.0", None, "0.5", "1", "x", None, None, None, None, None, None, None, None, None, "SP", "G", None, "1", None, None, None],
        ["3", "C-three", "0.0", "0.5", "2010-03-04", "100.0", "2", "x", None, None, None, None, None, None, None, None, "estuary", "SP", "G", None, "0", "2009", None, None],
        ["4", "C-four", "90.0", "-179.5", None, "1.0", "1", "x", None, None, None, None, None, None, None, None, "loch", "SP", "G", None, "0", "1999", "12", None],
    ]
    samples = [
        ["1", "s1", "0.000", "2.000", "1.000", None, "1"],
        ["2", "s2", "2.000", "4.000", "3.500", None, "1"],  # average is not the midpoint
        ["3", "s3", None, None, None, None, "2"],  # no depth at all
        ["4", "s4", "0.0", "1.0", None, None, "3"],
        ["9", "s9", "0.0", "1.0", "0.5", None, "2"],  # metadata says core 3
    ]
    d = {
        "articles": _tbl(mx.EXPECTED_COLUMNS["articles"], [
            ["1", "Title one", "10.1/one", "2000", "J1", None], ["2", None, None, None, None, None], ["0", None, None, None, None, None]]),
        "authors": _tbl(mx.EXPECTED_COLUMNS["authors"], [["1", "Smith", "John"], ["2", "Doe", "Jane"]]),
        "authorship": _tbl(mx.EXPECTED_COLUMNS["authorship"], [["1", "2"], ["1", "1"]]),
        "geopoints": _tbl(mx.EXPECTED_COLUMNS["geopoints"], cores),
        "samples": _tbl(mx.EXPECTED_COLUMNS["samples"], samples),
        "sample_metadata": _tbl(mx.EXPECTED_COLUMNS["sample_metadata"], metadata),
        "sample_analysis_description": _tbl(mx.EXPECTED_COLUMNS["sample_analysis_description"], DESC_ROWS),
        "sample_analysis_type": _tbl(mx.EXPECTED_COLUMNS["sample_analysis_type"], [["1", "sample_sediment_properties", "x"], ["2", "sample_composition", "x"]]),
        "sample_material_analyzed": _tbl(mx.EXPECTED_COLUMNS["sample_material_analyzed"], [["1", "bulk"], ["4", "surface_area"], ["74", "<2 mm"]]),
        "source_additional_data": _tbl(mx.EXPECTED_COLUMNS["source_additional_data"], [
            ["1", "Water depth provided in manuscript"], ["2", "Water depth extracted from GEBCO 2020"]]),
        "sample_composition": _tbl(comp_cols, composition),
        "sample_sediment_properties": _tbl(sed_cols, sediment),
        "sample_isotopes": _tbl(["sample_id", "material_analyzed_id", "Delta_13C", "Delta_13C_error", "replicate"], isotopes),
    }
    for name in mx.VALUE_TABLES:
        d.setdefault(name, _tbl(["sample_id", "material_analyzed_id", "replicate"], []))
    return d


S1 = pd.DataFrame({"variables": ["CaCO3_%", "total_organic_carbon_%"], "min_value": ["0.0", "0.0"], "max_value": ["100.0", "50.0"]})


@pytest.fixture(scope="module", autouse=True)
def _gate_off():
    mp = pytest.MonkeyPatch()
    mp.setattr(mx, "check_dump_tables", lambda t: None)
    mp.setattr(mx, "KNOWN_CORE_MISMATCH_SAMPLES", frozenset({"9"}))
    mp.setattr(mx, "EXPECTED_CORE_MISMATCH_ROWS", 4)
    mp.setattr(mx, "EXPECTED_ROWS", {**mx.EXPECTED_ROWS, "geopoints": 4, "samples": 5})
    yield
    mp.undo()


def _build(dump=None, thresh=THRESH, **kw):
    return mx.build_tables(dump or _make_dump(), S1, ACCESS, thresh, **kw)


@pytest.fixture(scope="module")
def built():
    return _build()


# Expected by inspection. Composition: row0 OC 1.5 (+ error attached) and TN 0.1 -> 2 facts; row1 OC 0.0 -> 1; row2 OC 2.0
# (material 74, replicate 2, article 2) -> 1; row3 OC 3.0 has no metadata -> skipped, its error -> skipped
# (error_without_fact); row4 OC 0.02 on material surface_area -> skipped (material_unit_unclear); row5 CaCO3 -1.0
# (replicate 2, two articles with different methods) -> 1; row6 OC:TN 9.5 -> 1. Sediment: row0 dbd, porosity, clay<2um, mean
# grain size (+ error attached) -> 4 facts; row1 has only an error (no value), surface area and cumulative mass (not loaded).
# Facts 2+1+1+1+1 + 4 = 10; skipped 4 (no_metadata_row 1, material_unit_unclear 1, error_without_fact 2); errors attached 2;
# not loaded 2 (sediment row1) + 2 (isotopes); cells 10 + 2 + 4 + 4 = 20.


def test_counts_and_accounting(built) -> None:
    tables, skips, rep = built
    assert rep["facts"] == 10 and len(tables["fact"]) == 10
    assert rep["skipped_by_reason"] == {"error_without_fact": 2, "material_unit_unclear": 1, "no_metadata_row": 1}
    assert rep["error_cells_attached_to_facts"] == 2
    assert rep["non_null_cells_in_value_tables"] == 20 == sum(rep["outcome_totals"].values()) and rep["accounting_closes"]
    t = rep["outcome_totals"]
    assert t["fact"] == 10 and t["error_attached"] == 2 and sum(v for k, v in t.items() if k.startswith("not_loaded")) == 4
    assert rep["accounting_by_family"]["sample_composition"]["non_null_cells"] == 10
    assert rep["accounting_by_family"]["sample_isotopes"] == {"non_null_cells": 2, "not_loaded: table not loaded": 2}
    assert rep["accounting_by_column"]["sample_sediment_properties.surface_area_m2_g"]["not_loaded: not in the plan's phase-1 list"] == 1
    assert len(skips) == 4
    validate_tables(add_derived_labels(tables))


def test_skips_carry_detail(built) -> None:
    _, skips, _ = built
    by = {(s["reason"], s["column"]): s for s in skips}
    assert by[("no_metadata_row", "total_organic_carbon_%")]["sample_id"] == "4"
    assert by[("material_unit_unclear", "total_organic_carbon_%")]["material_analyzed_id"] == "4"
    assert "skipped" in by[("error_without_fact", "total_organic_carbon_%_error")]["detail"]
    assert "blank" in by[("error_without_fact", "mean_grain_size_microm_error")]["detail"]


def test_types_units_provenance(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    t = lambda k: tuple(f.loc[k, ["measurement_type", "subfield", "units_raw", "attribute"]])  # noqa: E731
    assert t("sample_composition:1:1:1:total_organic_carbon_%") == ("C1", "organic_carbon", "%", "mosaic_organic_carbon")
    assert t("sample_composition:1:1:1:total_nitrogen_%") == ("C1", "total_nitrogen", "%", "mosaic_total_nitrogen")
    assert t("sample_composition:2:1:2:OC:TN_ratio") == ("C1", "c_n_ratio", "(blank in source)", "mosaic_oc_tn_ratio")
    assert t("sample_sediment_properties:1:1:1:clay_%_2microm") == ("G1", "clay_content", "%", "mosaic_clay_lt2um")
    assert t("sample_sediment_properties:1:1:1:dry_bulk_density_g_cm3")[:3] == ("C1", "dry_bulk_density", "g·cm-3")
    assert t("sample_sediment_properties:1:1:1:porosity")[2] == "-"
    assert t("sample_sediment_properties:1:1:1:mean_grain_size_microm")[:3] == ("G1", "mean_grain_size", "µm")
    r = f.loc["sample_composition:1:1:1:total_organic_carbon_%"]
    assert r.value == 1.5 and r.sd == 0.2 and pd.isna(r.se) and pd.isna(r.n)
    assert r.source_row_ref == "mosaic_ESSD[sample_composition]#data_row=0:total_organic_carbon_%"
    assert f.loc["sample_composition:3:74:2:total_organic_carbon_%", "source_row_ref"].endswith("#data_row=2:total_organic_carbon_%")
    assert f.loc["sample_sediment_properties:1:1:1:porosity", "source_row_ref"] == "mosaic_ESSD[sample_sediment_properties]#data_row=0:porosity"


def test_error_goes_to_sd_with_flag_note_and_params(built) -> None:
    tables, _, _ = built
    f = tables["fact"].set_index("fact_key")
    m = tables["method"].set_index("method_key")
    r = f.loc["sample_composition:1:1:1:total_organic_carbon_%"]
    p = json.loads(m.loc[r.method_key, "params"])
    assert f.loc["sample_composition:1:1:1:total_nitrogen_%", "sd"] != f.loc["sample_composition:1:1:1:total_nitrogen_%", "sd"]  # no error cell -> NULL
    assert "error_type_missing" in r["flags"] and "error_type_missing" not in f.loc["sample_composition:1:1:1:total_nitrogen_%", "flags"]
    assert m.loc[r.method_key, "notes"] == mx.SD_ASSUMPTION_NOTE and pd.isna(m.loc[f.loc["sample_composition:1:1:1:total_nitrogen_%", "method_key"], "notes"])
    assert f.loc["sample_sediment_properties:1:1:1:mean_grain_size_microm", "sd"] == 5.0
    assert p["error_value"] == 0.2 and p["error_column"] == "total_organic_carbon_%_error" and "unstated" in p["error_meaning"]
    assert p["material_analyzed"] == "bulk" and p["replicate"] == 1 and p["table"] == "sample_composition"
    p2 = json.loads(m.loc[f.loc["sample_sediment_properties:1:1:1:clay_%_2microm", "method_key"], "params"])
    assert p2["unit_in_source"] == "µm" and "unit_note" in p2 and "error_value" not in p2


def test_flags(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    fl = lambda k: f.loc[k, "flags"]  # noqa: E731
    assert fl("sample_composition:1:1:1:total_organic_carbon_%") == ["error_type_missing", "habitat_from_scope"]  # core 1: 50 m >= 1 m; error 0.2 stored as sd
    assert fl("sample_composition:2:1:1:total_organic_carbon_%") == ["calculated_by_source", "habitat_from_scope", "zero_value"]
    # sample 3: core 2, 0.5 m < 1 m: no habitat; replicate 2; material 74; no depth; core 2 has no year
    assert fl("sample_composition:3:74:2:total_organic_carbon_%") == ["depth_missing", "non_bulk_material", "replicate", "year_missing"]
    assert fl("sample_composition:1:1:2:CaCO3_%") == ["habitat_from_scope", "negative_value", "outside_source_qc_range", "replicate"]
    assert fl("sample_composition:2:1:2:OC:TN_ratio") == ["habitat_from_scope", "replicate"]
    assert rep["flag_counts"]["habitat_from_scope"] == 9 and rep["flag_counts"]["non_bulk_material"] == 1


def test_methods(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    m = tables["method"].set_index("method_key")
    g = lambda k: m.loc[f.loc[k, "method_key"]]  # noqa: E731
    r = g("sample_composition:1:1:1:total_organic_carbon_%")
    assert r.technique == "elemental analyzer" and r.method_status == "matched"
    r = g("sample_composition:2:1:1:total_organic_carbon_%")
    assert pd.isna(r.technique) and r.method_status == "method_id_null"
    r = g("sample_composition:1:1:2:CaCO3_%")
    assert pd.isna(r.technique) and r.method_status == "ambiguous_duplicate_method_id"
    assert [x["method"] for x in json.loads(r.params)["metadata_rows"]] == ["coulometry", "titration"]
    assert rep["facts_with_metadata_rows_disagreeing_on_method"] == 1 and rep["facts_with_several_articles"] == 2
    assert rep["method_status_counts"] == {"ambiguous_duplicate_method_id": 1, "matched": 5, "method_id_null": 4}


def test_references_and_citations(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    assert f.loc["sample_composition:1:1:1:total_nitrogen_%", "reference_key"] == "article:1+article:2"
    assert f.loc["sample_composition:1:1:1:total_organic_carbon_%", "reference_key"] == "article:1"
    assert f.loc["sample_composition:3:74:2:total_organic_carbon_%", "reference_key"] == "article:2"
    assert set(tables["reference"].reference_key) == {"article:1", "article:2", "article:1+article:2"}
    c = tables["citation"].set_index("citation_key")
    assert len(c) == 4  # a:1, a:2, composite -> 2
    r = c.loc["article:1|article:1"]
    assert (r.doi, r.title, r.year, r.authors, r.bibliography_id) == ("10.1/one", "Title one", 2000, "Smith, John; Doe, Jane", "1")
    assert pd.isna(c.loc["article:2|article:2", "title"]) and rep["citations_without_doi"] == 2
    assert rep["articles_cited"] == 2 and rep["articles_in_dump"] == 3


def test_locations_samples_labels(built) -> None:
    tables, _, rep = built
    loc = add_derived_labels(tables)["location"].set_index("location_key")
    assert set(loc.index) == {"core:1", "core:2"}  # only cores with a fact
    a, b = loc.loc["core:1"], loc.loc["core:2"]
    assert (a.latitude, a.longitude, a.site_name, a.water_depth_m, a.water_body, a.bioregion) == (10.5, -20.25, "C-one", 50.0, "North Sea", "Prov A")
    assert a.habitat_type == "marine sediment core" and a.habitat == "Subtidal soft sediment" and a.bottom_type == "soft"
    assert a.geomorphic_setting == "shelf" and a.setting == "shelf" and a.position_method == "coordinates from the source"
    assert b.water_depth_m == 0.5 and pd.isna(b.habitat_type) and pd.isna(b.habitat) and b.position_method.startswith("coordinates georeferenced")
    s = tables["sample"].set_index("sample_key")
    assert set(s.index) == {"sample:1", "sample:2", "sample:3"}
    assert (s.loc["sample:1", "depth_min_cm"], s.loc["sample:1", "depth_max_cm"], s.loc["sample:1", "representative_depth_min_cm"]) == (0, 2, 1)
    assert s.loc["sample:2", "representative_depth_max_cm"] == 3.5 and rep["samples_average_depth_not_midpoint"] == 1
    assert pd.isna(s.loc["sample:3", "depth_min_cm"]) and rep["samples_without_depth"] == 1
    assert (s.loc["sample:1", "sampling_year"], s.loc["sample:1", "sampling_month"]) == (2001, 5)
    assert s.loc["sample:3", "sampling_year"] is pd.NA or pd.isna(s.loc["sample:3", "sampling_year"])
    d = rep["depth"]
    assert d["cores"] == 4 and d["cores_depth_source_gebco_not_loaded"] == 1 and d["cores_depth_source_manuscript"] == 3
    assert d["manuscript_depths_at_0_le1_le5_le10_le200"] == [0, 2, 2, 2, 3]  # 0.5, 1.0, 50.0
    assert d["manuscript_depth_cores_below_threshold"] == 1 and d["cores_with_habitat_from_scope"] == 2  # 50 and 1.0 (>= 1.0)


def test_threshold_is_inclusive_and_changes_habitat() -> None:
    t, _, rep = _build(thresh=60.0)
    assert t["location"].habitat_type.isna().all() and rep["flag_counts"].get("habitat_from_scope", 0) == 0
    t, _, rep = _build(thresh=0.0)
    assert t["location"].habitat_type.notna().all()  # both cores qualify (0.5 and 50 >= 0)


def test_gebco_depth_not_loaded_and_core_year_from_date() -> None:
    d = _make_dump()
    # point sample 3 at the GEBCO core (core 3): depth NULL, no habitat; core 3 has date 2010-03-04 and year 2009 -> the
    # column wins and the disagreement is reported
    d["samples"][1][2][6] = "3"
    for r in d["sample_metadata"][1]:
        if r[0] == "3":
            r[4] = "3"
    t, _, rep = _build(d)
    loc = t["location"].set_index("location_key")
    assert pd.isna(loc.loc["core:3", "water_depth_m"]) and pd.isna(loc.loc["core:3", "habitat_type"])
    assert rep["cores_date_disagrees_with_year_or_month (columns win)"] == ["3"]
    assert rep["cores_gebco_depth_with_fact"] == 1
    s = t["sample"].set_index("sample_key")
    assert (s.loc["sample:3", "sampling_year"], s.loc["sample:3", "sampling_month"]) == (2009, 3)  # month from the date
    assert rep["cores_month_from_date"] == 1


def test_subset_mode_accounting_still_closes() -> None:
    t, skips, rep = _build(article_ids=frozenset({"2"}))
    # article 2 appears on: TN of sample 1 (shared with article 1), sample 3's OC, CaCO3 of sample 1 -> 3 facts
    assert rep["facts"] == 3 and rep["accounting_closes"]
    assert rep["outcome_totals"]["outside_subset"] > 0
    assert rep["skipped_by_reason"] == {}  # the no-metadata and surface-area cells are outside the subset
    assert skips == []


# ------------------------------------------------------------------ fail-loud checks


def test_unknown_column_in_loaded_table_raises() -> None:
    d = _make_dump()
    d["sample_composition"][0].append("brand_new_%")
    for r in d["sample_composition"][1]:
        r.append(None)
    with pytest.raises(ContractError, match="no mapping"):
        _build(d)


def test_non_numeric_value_raises() -> None:
    d = _make_dump()
    d["sample_composition"][1][0][d["sample_composition"][0].index("total_organic_carbon_%")] = "n/a"
    with pytest.raises(ContractError, match="not a number"):
        _build(d)


def test_unknown_material_raises() -> None:
    d = _make_dump()
    d["sample_composition"][1][0][1] = "999"
    with pytest.raises(ContractError, match="unknown sample"):
        _build(d)


def test_restricted_data_raises() -> None:
    d = _make_dump()
    d["sample_metadata"][1][0][3] = "1"
    with pytest.raises(ContractError, match="exclusivity"):
        _build(d)


def test_new_core_mismatch_raises() -> None:
    d = _make_dump()
    d["sample_metadata"][1][0][4] = "3"  # sample 1 now claims core 3 while samples says core 1
    with pytest.raises(ContractError, match="core mismatches changed"):
        _build(d)


def test_bad_coordinates_and_depth_raise() -> None:
    d = _make_dump()
    d["geopoints"][1][0][2] = "95.0"
    with pytest.raises(ContractError, match="coordinates"):
        _build(d)
    d = _make_dump()
    d["geopoints"][1][0][5] = "-3.0"
    with pytest.raises(ContractError, match="negative water depth"):
        _build(d)


def test_inverted_depth_raises() -> None:
    d = _make_dump()
    d["samples"][1][0][2], d["samples"][1][0][3] = "5.0", "1.0"
    with pytest.raises(ContractError, match="upper depth above bottom"):
        _build(d)


def test_unit_mismatch_raises() -> None:
    d = _make_dump()
    for r in d["sample_analysis_description"][1]:
        if r[1] == "porosity":
            r[2] = "%"
    with pytest.raises(ContractError, match="declared unit"):
        _build(d)


def test_wrong_analysis_type_raises() -> None:
    d = _make_dump()
    for r in d["sample_analysis_description"][1]:
        if r[1] == "porosity":
            r[4] = "2"
    with pytest.raises(ContractError, match="analysis type"):
        _build(d)


def test_bad_month_raises() -> None:
    d = _make_dump()
    d["geopoints"][1][0][22] = "13"
    with pytest.raises(ContractError, match="month"):
        _build(d)


def test_unhandled_sediment_column_raises() -> None:
    d = _make_dump()
    d["sample_sediment_properties"][0].remove("cumulative_dry_mass_g_cm2")
    for r in d["sample_sediment_properties"][1]:
        r.pop(-2)
    with pytest.raises(ContractError, match="unhandled columns"):
        _build(d)


# ------------------------------------------------------------------ build + determinism


def test_roundtrip_global_build_and_determinism(built, tmp_path) -> None:
    tables, _, _ = built
    write_extracted(tables, tmp_path / "ex")
    back = read_extracted(tmp_path / "ex")
    assert len(back["fact"]) == 10
    g = assign_global_keys([back])
    assert list(g["fact"].measurement_id) == list(range(1, 11))
    out1, out2 = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    build_compilations([tmp_path / "ex"], out1)
    build_compilations([tmp_path / "ex"], out2)
    q = ("SELECT f.value, f.attribute, f.units_raw, m.technique, l.habitat, f.flags FROM measurement_fact f "
         "JOIN method_dim m USING (method_id) JOIN location_dim l USING (location_id) ORDER BY f.measurement_id")
    r1 = duckdb.connect(str(out1), read_only=True).execute(q).fetchall()
    r2 = duckdb.connect(str(out2), read_only=True).execute(q).fetchall()
    assert r1 == r2 and len(r1) == 10


def test_build_is_deterministic() -> None:
    a, _, ra = _build()
    b, _, rb = _build()
    for k in a:
        pd.testing.assert_frame_equal(a[k], b[k])
    assert ra == rb
