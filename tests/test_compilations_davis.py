"""Unit tests (rung 1) for the davis-calcification extract.py.

Hand-built fixture of 5 data rows (and tiny workbooks for the reader); every expected number is derived by inspection in
the comments.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import duckdb
import openpyxl
import pandas as pd
import pytest
from openpyxl.styles import Font

from coastal_crawler.compilations import (
    ContractError,
    add_derived_labels,
    build_compilations,
    read_extracted,
    validate_tables,
    write_extracted,
)

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "davis-calcification" / "extract.py"
_spec = importlib.util.spec_from_file_location("davis_extract", _EXTRACT)
assert _spec and _spec.loader
dx = importlib.util.module_from_spec(_spec)
sys.modules["davis_extract"] = dx
_spec.loader.exec_module(dx)

ACCESS = "2026-11-15"
PURPLE = ("rgb", dx.PURPLE)
BLUE = ("theme", dx.BLUE_THEME)
OTHER = ("rgb", "FF00FF00")


def _row(colours: dict | None = None, **kw) -> dict:
    base = {
        "A": "Smith et al., 2000. J Reef", "B": 2000, "C": 1999, "D": 1990, "E": "Reef A", "F": "Australia",
        "G": -18.5, "H": 147.2, "I": "reef flat", "J": "M", "K": "days", "L": 5, "M": "y", "N": "Summer 1999",
        "O": "Summer", "P": "H", "Q": "L", "R": "FL", "S": "Yes", "W": 28.5, "Y": 1.8,
    }
    base.update(kw)
    colours = colours or {}
    return {c: (base.get(c), colours.get(c), False) for c in dx.HEADERS}


ROWS = [
    # 0: all four attributes; Gnet purple (calculated), error SE 58; Pnet 100 with EP-SE error 20; cover 10 purple with
    #    a coral error 2; coralline 8; temperature 28.5 and depth 1.8
    _row({"AJ": PURPLE, "Z": PURPLE}, AJ=165, AK=58, AL="SE", AM=100, AN=20, AO="EP-SE", Z=10, AA=2, AB=8),
    # 1: no Year Studied; text coordinates ('149.9 W', '-28.5'); Gnet 0 with SD error 5 ('SD ' spelling); cover 0 in blue
    #    (secondary); Reef Type with a trailing space; reef state and health
    _row({"Z": BLUE}, C=None, G="-28.5", H="149.9 W", E="Reef B", I="Reef flat ", AH="bleached", AI="D", AJ=0, AK=5,
         AL="SD ", Z=0, W=None, Y=None),
    # 2: same location as row 0; Gnet -3 with a negative error and type 'S' (not interpreted); a font colour not in the
    #    legend (no flag); Pnet 4 with an error but no type
    _row({"AJ": OTHER}, AJ=-3, AK=-2, AL="S", AM=4, AN=1),
    # 3 and 4: another reference whose Year Pub disagrees between rows (2001 vs 2002; the string says 2001)
    _row(A="Jones, 2001. J Sea", B=2001, E="Reef C", AJ=1),
    _row(A="Jones, 2001. J Sea", B=2002, E="Reef C", AJ=2),
]
LAYOUT = {"formula_cells": 0}
# expected facts: r0 4 (Gnet, Pnet, cover, coralline), r1 2 (Gnet, cover), r2 2 (Gnet, Pnet), r3 1, r4 1 = 10; samples 5;
# locations: r0 = r2, r1, r3 = r4 -> 3; references 2; methods: Gnet 4 (rows 0, 1, 2 differ in their error params; rows 3 and 4
# have identical params and share one), Pnet 2, cover 2 (coral error 2 vs none), coralline 1 = 9.


def _build(rows=ROWS):
    return dx.build_tables(rows, LAYOUT, ACCESS)


def _with(i: int, colours: dict | None = None, **kw) -> list[dict]:
    rows = [dict(r) for r in ROWS]
    old = {c: v for c, (v, _col, _f) in rows[i].items()}
    old.update(kw)
    keep = {c: col for c, (_v, col, _f) in rows[i].items() if col is not None}
    keep.update(colours or {})
    rows[i] = _row(keep, **old)
    return rows


@pytest.fixture(scope="module")
def built():
    return _build()


def test_counts(built) -> None:
    tables, skips, rep = built
    assert skips == [] and rep["skipped"] == 0
    assert len(tables["fact"]) == 10 == rep["facts"]
    assert rep["numeric_cells_by_attribute"] == {"davis_gnet": 5, "davis_pnet": 2, "davis_coral_cover": 2, "davis_coralline_cover": 1}
    assert len(tables["sample"]) == 5 and len(tables["location"]) == 3 and len(tables["method"]) == 9
    assert len(tables["reference"]) == 2 == len(tables["citation"])
    assert rep["methods_by_attribute"] == {"davis_coral_cover": 2, "davis_coralline_cover": 1, "davis_gnet": 4, "davis_pnet": 2}
    assert rep["coral_error_cells"] == 1 and rep["value_cells_with_unlisted_font_colour"] == 1
    validate_tables(add_derived_labels(tables))


def test_source_row(built) -> None:
    src = built[0]["source"].iloc[0]
    assert src.version == "SEANOE 80022 (2021-11-26), last version" and src.license == "CC BY 4.0" and src.access_date == ACCESS
    assert src.doi == "10.17882/80022"


def test_facts(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    t = lambda k: tuple(f.loc[k, ["measurement_type", "subfield", "units_raw"]])  # noqa: E731
    assert t("row0:davis_gnet") == ("B7", "net_ecosystem_calcification", "mmol m-2 d-1")
    assert t("row0:davis_pnet") == ("B5", "net_ecosystem_production", "mmol m-2 d-1")
    assert t("row0:davis_coral_cover") == ("B4", "live_hard_coral_cover", "%")
    assert t("row0:davis_coralline_cover") == ("B4", "turf_cca_invertebrate_cover", "%")
    assert f.loc["row0:davis_gnet", "value"] == 165 and f.loc["row2:davis_gnet", "value"] == -3
    assert f.loc["row1:davis_coral_cover", "value"] == 0
    assert "row1:davis_pnet" not in f.index and "row2:davis_coral_cover" not in f.index  # blank cells: no fact
    assert f.loc["row2:davis_pnet", "source_row_ref"] == "89830.xlsx[Metadata1]#data_row=2:davis_pnet"
    assert f.n.isna().all()


def test_errors(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    assert f.loc["row0:davis_gnet", "se"] == 58 and pd.isna(f.loc["row0:davis_gnet", "sd"])
    assert f.loc["row0:davis_pnet", "se"] == 20  # EP-SE -> se
    assert f.loc["row1:davis_gnet", "sd"] == 5 and pd.isna(f.loc["row1:davis_gnet", "se"])  # 'SD ' -> sd
    # 'S' (Gnet, error -2.0) and no type (Pnet, error 1.0): not stated -> assumed SD, flagged, noted (user rule 2026-11-17)
    assert f.loc["row2:davis_gnet", "sd"] == -2.0 and f.loc["row2:davis_pnet", "sd"] == 1.0
    assert f.loc[["row2:davis_gnet", "row2:davis_pnet"], "se"].isna().all()
    mn = built[0]["method"]["notes"]
    assert mn.notna().sum() == 2 and (mn.dropna() == dx.SD_ASSUMED_NOTE).all()
    assert f.loc[["row0:davis_coral_cover", "row0:davis_coralline_cover"], ["sd", "se"]].isna().all().all()  # no type column


def test_flags(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    fl = lambda k: f.loc[k, "flags"]  # noqa: E731
    assert fl("row0:davis_gnet") == ["calculated_by_source"] and fl("row0:davis_coral_cover") == ["calculated_by_source"]
    assert fl("row0:davis_pnet") == ["error_propagated"] and fl("row0:davis_coralline_cover") == []
    assert fl("row1:davis_gnet") == ["year_missing", "zero_value"]
    assert fl("row1:davis_coral_cover") == ["secondary_source", "year_missing", "zero_value"]
    assert fl("row2:davis_gnet") == ["error_type_missing", "negative_sd"]  # an unlisted font colour gives no flag
    assert fl("row2:davis_pnet") == ["error_type_missing"]
    assert rep["flag_counts"] == {"calculated_by_source": 2, "error_propagated": 1, "error_type_missing": 2, "negative_sd": 1,
                                  "secondary_source": 1, "year_missing": 2, "zero_value": 2}
    assert rep["error_values_without_type"] == 1 and rep["negative_error_values"] == 1


def test_locations_samples(built) -> None:
    tables, _, _ = built
    loc = tables["location"].set_index("site_name")
    a = loc.loc["Reef A"]
    assert a.latitude == -18.5 and a.longitude == 147.2 and a.country == "Australia" and a.water_depth_m == 1.8
    assert a.habitat_type == "reef flat" and a.geomorphic_setting == "reef flat" and list(a.modification_status) == []
    b = loc.loc["Reef B"]
    assert b.latitude == -28.5 and b.longitude == -149.9 and pd.isna(b.water_depth_m)  # '149.9 W' -> -149.9
    assert b.habitat_type == "Reef flat" and list(b.modification_status) == ["reef state: bleached", "health: D"]
    s = tables["sample"].set_index("sample_key")
    assert s.loc["row0", "sampling_year"] == 1999 and s.loc["row0", "water_temperature_c"] == 28.5
    assert pd.isna(s.loc["row1", "sampling_year"]) and pd.isna(s.loc["row1", "water_temperature_c"])
    f = tables["fact"].set_index("fact_key")
    assert f.loc["row0:davis_gnet", "location_key"] == f.loc["row2:davis_gnet", "location_key"]


def test_labels(built) -> None:
    loc = add_derived_labels(built[0])["location"].set_index("site_name")
    assert loc.loc["Reef A", "habitat"] == "Coral reef" and loc.loc["Reef A", "bottom_type"] == "hard_biogenic"
    assert loc.loc["Reef B", "habitat"] == "Coral reef"


def test_soft_sediments_has_no_habitat() -> None:
    tables, _, _ = _build(_with(0, I="Soft Sediments"))
    loc = add_derived_labels(tables)["location"]
    assert pd.isna(loc.loc[loc.habitat_type == "Soft Sediments", "habitat"]).all()


def test_methods(built) -> None:
    tables, _, _ = built
    m = tables["method"].set_index("method_key")
    f = tables["fact"].set_index("fact_key")
    k = f.loc["row0:davis_gnet", "method_key"]
    assert m.loc[k, "technique"] == "L" and m.loc[k, "method_status"] == "matched"
    p = json.loads(m.loc[k, "params"])
    assert p["method_grouped"] == "FL" and p["wave_action"] == "M" and p["night_measured"] == "Yes"
    assert p["error_value"] == 58 and p["error_type_raw"] == "SE" and p["duration_days"] == 5
    kc = f.loc["row0:davis_coral_cover", "method_key"]
    assert m.loc[kc, "method_status"] == "method_id_null" and json.loads(m.loc[kc, "params"])["coral_error_value"] == 2
    assert f.loc["row0:davis_coralline_cover", "method_key"] == "davis_coralline_cover"


def test_references(built) -> None:
    tables, _, rep = built
    c = tables["citation"].set_index("bibliography_id")
    assert c.loc["Smith et al., 2000. J Reef", "year"] == 2000 and pd.isna(c.loc["Smith et al., 2000. J Reef", "doi"])
    assert pd.isna(c.loc["Smith et al., 2000. J Reef", "title"])
    assert c.loc["Jones, 2001. J Sea", "year"] == 2001  # Year Pub 2001 and 2002: the year in the string settles it
    assert rep["year_pub_conflicts_within_a_string"] == {"Jones, 2001. J Sea": [2001, 2002]}
    assert set(tables["reference"].reference_key) == {"ref:Smith et al., 2000. J Reef", "ref:Jones, 2001. J Sea"}


# ---- helpers and fail-loud checks


@pytest.mark.parametrize("v,x", [(5, 5.0), ("-28.5", -28.5), ("149.9 W", -149.9), (" 12.5 S ", -12.5), ("10 E", 10.0), (None, None), ("", None)])
def test_lonlat(v, x) -> None:
    assert dx.lonlat(v, "x", 180) == x


@pytest.mark.parametrize("v", ["-149.9 W", "abc", "181", "12 Q", True])
def test_lonlat_raises(v) -> None:
    with pytest.raises(ContractError):
        dx.lonlat(v, "x", 180)


def test_provenance_classes() -> None:
    assert dx.provenance(PURPLE) == "calculated" and dx.provenance(BLUE) == "compilation"
    assert dx.provenance(None) == "plain" and dx.provenance(("theme", 1)) == "plain" and dx.provenance(OTHER) == "other"


def test_unlisted_error_type_raises() -> None:
    with pytest.raises(ContractError, match="unlisted error type"):
        _build(_with(0, AL="XX"))


def test_non_numeric_cells_raise() -> None:
    with pytest.raises(ContractError, match="non-numeric"):
        _build(_with(0, AJ="n/a"))
    with pytest.raises(ContractError, match="non-numeric"):
        _build(_with(0, AA="<1"))  # unloaded numeric columns must stay numeric too
    with pytest.raises(ContractError, match="non-numeric"):
        _build(_with(0, Y="deep"))


def test_row_checks_raise() -> None:
    with pytest.raises(ContractError, match="without Short Reference"):
        _build(_with(0, A=None))
    with pytest.raises(ContractError, match="without Location"):
        _build(_with(0, E=None))
    with pytest.raises(ContractError, match="not missing together"):
        _build(_with(0, H=None))
    with pytest.raises(ContractError, match="negative Depth"):
        _build(_with(0, Y=-1))
    for string in ("Jones, 2005. J Sea", "Jones. J Sea"):  # Year Pub 2001 vs 2002 and the string says 2005, or no year
        rows = _with(3, A=string)
        rows[4] = _with(4, A=string)[4]
        with pytest.raises(ContractError, match="no usable year"):
            _build(rows)


def test_error_without_type_and_type_without_error() -> None:
    tables, _, rep = _build(_with(0, AK=None))  # type EP-SE but no error value
    assert rep["error_types_without_value"] == 1
    assert tables["fact"].set_index("fact_key").loc["row0:davis_gnet", "flags"] == ["calculated_by_source"]


# ---- the workbook reader


def _workbook(path: Path, *, header_fix: dict | None = None, extra: dict | None = None, formula: bool = False,
              legend_colour: str = dx.PURPLE) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = dx.SHEET
    ws["C1"] = dx.LEGEND["C"]
    ws["C1"].font = Font(color=legend_colour)
    ws["H1"] = dx.LEGEND["H"]
    ws["H1"].font = Font(color=openpyxl.styles.colors.Color(theme=dx.BLUE_THEME))
    for letter, head in {**dx.HEADERS, **(header_fix or {})}.items():
        ws[f"{letter}2"] = head
    for r in (4, 5):
        ws[f"A{r}"] = f"Ref {r}, 2000. J"
        ws[f"B{r}"] = 2000
        ws[f"AJ{r}"] = 1.5 * r
    if formula:
        ws["AM4"] = "=2*3"  # a formula written by openpyxl has no cached value
    for cell, v in (extra or {}).items():
        ws[cell] = v
    wb.save(path)
    return path


@pytest.fixture()
def small(monkeypatch):
    monkeypatch.setattr(dx, "LAST_ROW", 5)


def test_read_raw_happy_path(small, tmp_path) -> None:
    rows, layout = dx.read_raw(_workbook(tmp_path / "a.xlsx"))
    assert len(rows) == 2 and rows[0]["AJ"][0] == 6.0 and rows[1]["A"][0] == "Ref 5, 2000. J" and layout["formula_cells"] == 0


def test_read_raw_checks(small, tmp_path) -> None:
    with pytest.raises(ContractError, match="header"):
        dx.read_raw(_workbook(tmp_path / "b.xlsx", header_fix={"AJ": "Gnet (mmol m-3 d-1)"}))
    with pytest.raises(ContractError, match="beyond AQ"):
        dx.read_raw(_workbook(tmp_path / "c.xlsx", extra={"AS4": "x"}))
    with pytest.raises(ContractError, match="exactly sheet rows"):
        dx.read_raw(_workbook(tmp_path / "d.xlsx", extra={"A9": "stray row"}))
    with pytest.raises(ContractError, match="no cached value"):
        dx.read_raw(_workbook(tmp_path / "e.xlsx", formula=True))
    with pytest.raises(ContractError, match="legend font colours"):
        dx.read_raw(_workbook(tmp_path / "f.xlsx", legend_colour="FF123456"))


# ---- build + determinism


def test_roundtrip_global_build_and_determinism(built, tmp_path) -> None:
    tables, _, _ = built
    write_extracted(tables, tmp_path / "ex")
    back = read_extracted(tmp_path / "ex")
    assert len(back["fact"]) == 10
    out1, out2 = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    build_compilations([tmp_path / "ex"], out1)
    build_compilations([tmp_path / "ex"], out2)
    q = ("SELECT f.attribute, f.value, f.units_raw, l.habitat FROM measurement_fact f JOIN location_dim l USING (location_id) "
         "ORDER BY f.measurement_id")
    r1 = duckdb.connect(str(out1), read_only=True).execute(q).fetchall()
    r2 = duckdb.connect(str(out2), read_only=True).execute(q).fetchall()
    assert r1 == r2 and len(r1) == 10 and {r[3] for r in r1} == {"Coral reef"}


def test_build_is_deterministic() -> None:
    a, sa, ra = _build()
    b, sb, rb = _build()
    for k in a:
        pd.testing.assert_frame_equal(a[k], b[k])
    assert ra == rb and sa == sb
