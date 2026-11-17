"""Unit tests (rung 1) for the tang-nitrification extract.py.

Hand-built fixture (4 rows on the ammonia sheet, 2 on the nitrite sheet, two metadata sheets); every expected number
is derived by inspection in the comments.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from coastal_crawler.compilations import ContractError, add_derived_labels, validate_tables

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "tang-nitrification" / "extract.py"
_spec = importlib.util.spec_from_file_location("tang_extract", _EXTRACT)
assert _spec and _spec.loader
tg = importlib.util.module_from_spec(_spec)
sys.modules["tang_extract"] = tg
_spec.loader.exec_module(tg)

ACCESS = "2026-11-15"
REF1 = "Ref One et al., 2001. J. Ocean"
REF2A = "Ref Two, 2002. Deep Sea"
REF2N = "Ref Two, 2003. Other spelling"
D1 = dt.datetime(2001, 5, 3)

# raw headers with the trailing spaces of the real file
HEAD = list(tg.DATA_COLUMNS)
HEAD[1] = "Date "
HEAD[2] = "Latitude "


def _row(**kw) -> dict:
    r = {c: "" for c in HEAD}
    for k, v in kw.items():
        r[{"src": "Data source", "date": "Date ", "lat": "Latitude ", "lon": "Longitude", "depth": "Depth (m)",
           "rate": tg.RATE_COL, "sd": tg.SD_COL, "temp": "Temperature (°C)", "sal": "Salinity (psu)",
           "nh4": "Ammonium+ammonia (μM)", "no2": "Nitrite (μM)", "no3": "Nitrate (μM)", "ph": "pH",
           "o2": "O2 (μM)", "par": "PAR (μmol Einstein m-2 s-1)"}[k]] = v
    return r


# Sheet A
A_ROWS = [
    # 0: sample S0 (10, 20, 5 m, 2001-05-03); rate 1.5 sd .5; T, S; NH4 .3, NO3 5, O2 200
    _row(src=REF1, date=D1, lat=10.0, lon=20.0, depth=5, rate=1.5, sd=0.5, temp=12.5, sal=35, nh4=0.3, no3=5.0, o2=200),
    # 1: same sample (replicate row, comment in the source column); rate 2.5; NH4 .3 equal -> collapse; NO3 6 conflicts;
    #    O2 BDL -> skip
    _row(src="Data shown in Figure 2", date=D1, lat=10.0, lon=20.0, depth=5, rate=2.5, nh4=0.3, no3=6.0, o2="BDL"),
    # 2: sample S2: cross-year range date, no depth; rate BDL -> skip; NH4 .1 loads beside it; NO3 NM -> skip;
    #    pH 7.9 -> units_missing skip; O2 NA -> nothing; PAR is a not-loaded column
    _row(src="detection limit of ammonium: 0.01 uM", date="2008 Dec-2009 Jan", lat=11.0, lon=21.0, depth="NA",
         rate="BDL", sd="NA", nh4=0.1, no3="NM", ph=7.9, o2="NA", par=5),
    # 3: new reference, integer coordinates and depth, text date; rate 0 (a real zero); NO3 -0.1 (negative)
    _row(src=REF2A, date="2005 October", lat=30, lon=40, depth=10, rate=0, no3=-0.1),
]
# Sheet N
N_ROWS = [
    # 0: same sample as A row 3 (floats here): NO3 -0.1 collapses, NH4 .7 new; rate 3
    _row(src=REF2N, date="2005 October", lat=30.0, lon=40.0, depth=10.0, rate=3.0, nh4=0.7, no3=-0.1),
    # 1: same sample as S0 under REF1: NH4 .4 conflicts with .3, NO3 5 equals the first -> collapse; rate 4
    _row(src=REF1, date=D1, lat=10.0, lon=20.0, depth=5.0, rate=4.0, nh4=0.4, no3=5.0),
]
# expected: rate facts A0, A1, A3, N0, N1 = 5; rate skips 1 (A2 BDL);
# J1 cells 11 (A0 3, A1 2, A2 1, A3 1, N0 2, N1 2); S0 NH4 {.3,.3,.4} -> 2 facts + 1 collapsed; S0 NO3 {5,6,5} -> 2 + 1;
# S0 O2 -> 1; S2 NH4 -> 1; S3 NO3 {-.1,-.1} -> 1 + 1 collapsed; S3 NH4 -> 1: J1 facts 8, collapsed 3, conflict groups 2,
# conflict facts 4; J1 skips: A1 O2 BDL, A2 NO3 NM; pH skip 1; skips 4; facts 13; cells_in 6 + 11 + 2 + 1 = 20 = 13 + 4 + 3.


def _meta(sheet: str, rows: list[dict[str, str]]) -> pd.DataFrame:
    cols = tg.META_COLUMNS[sheet]
    return pd.DataFrame([[r.get(c, "") for c in cols] for r in rows], columns=cols, dtype=object)


def _raw(a_rows=None, n_rows=None) -> dict[str, pd.DataFrame]:
    gene = pd.DataFrame({"Data source": [REF1], "AOA (copies L-1)": [1.0]})
    return {
        "volumetric ammonia oxidation": pd.DataFrame(a_rows or A_ROWS, columns=HEAD, dtype=object),
        "volumetric nitrite oxidation": pd.DataFrame(n_rows or N_ROWS, columns=HEAD, dtype=object),
        "rate metadata": _meta("rate metadata", [
            {"Data source": REF1, "Method": "15N tracer addition", "Incubation time (hours)": 24},
            {"Data source": "Some other paper, 1999. X", "Method": "14C"}]),
        "rate metadata 2": _meta("rate metadata 2", [{"Data source": REF2N, "Method": "15N-nitrite"}]),
        "ammonia oxidizer": gene, "NOB": gene,
    }


@pytest.fixture(autouse=True)
def _no_real_slot_conflict(monkeypatch):
    monkeypatch.setattr(tg, "SLOT_CONFLICT_SAMPLES", frozenset())


@pytest.fixture(scope="module")
def built():
    # module-scoped fixtures cannot use monkeypatch: patch by hand around the call
    saved = tg.SLOT_CONFLICT_SAMPLES
    tg.SLOT_CONFLICT_SAMPLES = frozenset()
    try:
        return tg.build_tables(_raw(), ACCESS)
    finally:
        tg.SLOT_CONFLICT_SAMPLES = saved


def test_counts(built) -> None:
    tables, skips, rep = built
    f = tables["fact"]
    assert len(f) == 13 == rep["facts"]
    assert rep["facts_by_attribute"] == {
        "tang_ammonia_oxidation": 3, "tang_nitrite_oxidation": 2, "water_ammonium": 4, "water_nitrate": 3, "water_o2": 1,
    }
    assert rep["j1_collapse"] == {"cells": 11, "facts": 8, "collapsed_repeats": 3, "conflict_groups": 2, "conflict_facts": 4}
    assert rep["cells_in"] == 20 and rep["skipped"] == 4
    assert sorted((s["sheet"][-9:], s["data_row"], s["attribute"], s["reason"]) for s in skips) == [
        ("oxidation", 1, "water_o2", "below_detection_limit"), ("oxidation", 2, "tang_ammonia_oxidation", "below_detection_limit"),
        ("oxidation", 2, "water_nitrate", "not_measured"), ("oxidation", 2, "water_ph", "units_missing"),
    ]
    assert len(tables["sample"]) == 3 and len(tables["location"]) == 3 and len(tables["reference"]) == 3
    assert len(tables["method"]) == 7 and rep["methods_by_status"] == {"matched": 2, "method_id_null": 3, "method_id_unmatched": 2}
    assert rep["j1_facts_on_rows_whose_rate_was_skipped"] == 1  # S2 NH4
    assert rep["float_nan_cells_read_as_blank"] == {}
    validate_tables(add_derived_labels(tables))


def test_j1_one_fact_per_sample(built) -> None:
    f = built[0]["fact"]
    s0 = "10.0|20.0|5.0|2001-05-03"
    g = f[(f.sample_key == s0) & (f.attribute == "water_nitrate")].sort_values("value")
    assert list(g.value) == [5.0, 6.0] and all("covariate_conflict" in x for x in g["flags"])
    g = f[(f.sample_key == s0) & (f.attribute == "water_ammonium")].sort_values("value")
    assert list(g.value) == [0.3, 0.4] and all("covariate_conflict" in x for x in g["flags"])
    # the first raw cell carries the equal value: A row 0 for .3 (not row 1), and its reference is REF1
    assert g.iloc[0].source_row_ref.endswith("[volumetric ammonia oxidation]#data_row=0:water_ammonium")
    assert g.iloc[1].source_row_ref.endswith("[volumetric nitrite oxidation]#data_row=1:water_ammonium")
    o2 = f[(f.sample_key == s0) & (f.attribute == "water_o2")]
    assert list(o2.value) == [200.0] and list(o2["flags"]) == [["habitat_from_scope"]]
    s3 = "30.0|40.0|10.0|2005 October"
    g = f[(f.sample_key == s3) & (f.attribute == "water_nitrate")]
    assert len(g) == 1 and g.iloc[0].value == -0.1 and g.iloc[0].reference_key == f"ref:{REF2A}"  # first cell's reference
    assert "negative_value" in g.iloc[0]["flags"] and "covariate_conflict" not in g.iloc[0]["flags"]
    # a J1 value beside a skipped rate still loads, with the sample's missing date/depth flags
    g = f[f.sample_key == "11.0|21.0||2008 Dec-2009 Jan"]
    assert list(g.attribute) == ["water_ammonium"] and list(g["flags"].iloc[0]) == ["depth_missing", "habitat_from_scope", "year_missing"]


def test_rate_facts_and_units(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    k = "volumetric ammonia oxidation#0:tang_ammonia_oxidation"
    assert tuple(f.loc[k, ["measurement_type", "subfield", "units_raw", "value", "sd"]]) == ("C5", "ammonia_oxidation", "nmol N L-1 d-1", 1.5, 0.5)
    assert f.loc["volumetric nitrite oxidation#1:tang_nitrite_oxidation", "subfield"] == "nitrite_oxidation"
    assert pd.isna(f.loc["volumetric ammonia oxidation#1:tang_ammonia_oxidation", "sd"])
    z = f.loc["volumetric ammonia oxidation#3:tang_ammonia_oxidation"]
    assert z.value == 0 and list(z["flags"]) == ["habitat_from_scope", "zero_value"]
    assert f.loc[k, "source_row_ref"] == f"{tg.DATA_FILE}[volumetric ammonia oxidation]#data_row=0:tang_ammonia_oxidation"
    assert "volumetric ammonia oxidation#2:tang_ammonia_oxidation" not in f.index  # BDL -> skip, no fact
    assert not any(a == "water_ph" for a in f.attribute)
    n = built[0]["fact"]
    assert set(n[n.attribute.str.startswith("water_")].units_raw) == {"μM"}  # U+03BC


def test_samples_locations_references(built) -> None:
    tables, _, rep = built
    s = tables["sample"].set_index("sample_key")
    s0 = s.loc["10.0|20.0|5.0|2001-05-03"]
    assert (s0.sampling_year, s0.sampling_month, s0.sample_depth_m, s0.water_temperature_c) == (2001, 5, 5.0, 12.5)
    assert s0.water_salinity == 35 and s0.water_salinity_units == "psu" and pd.isna(s0.depth_notes)
    s2 = s.loc["11.0|21.0||2008 Dec-2009 Jan"]
    assert pd.isna(s2.sampling_year) and pd.isna(s2.sampling_month) and pd.isna(s2.sample_depth_m)
    assert s2.depth_notes == "date as given: 2008 Dec-2009 Jan" and pd.isna(s2.water_temperature_c)
    s3 = s.loc["30.0|40.0|10.0|2005 October"]
    assert (s3.sampling_year, s3.sampling_month) == (2005, 10) and pd.isna(s3.depth_notes)
    assert rep["samples_distinct_in_raw"] == 3
    loc = tables["location"].set_index("location_key")
    assert set(loc.index) == {"10.0|20.0", "11.0|21.0", "30.0|40.0"} and (loc.habitat_type == "water column").all()
    assert loc.loc["30.0|40.0", "latitude"] == 30.0
    lab = add_derived_labels(tables)["location"]
    assert (lab.habitat == "Water column").all() and lab.bottom_type.isna().all()
    assert set(tables["fact"]["flags"].map(lambda x: "habitat_from_scope" in x)) == {True}
    c = tables["citation"].set_index("bibliography_id")
    assert c.loc[REF1, "year"] == 2001 and c.loc[REF2N, "year"] == 2003 and c.loc[REF2A, "reference_key"] == f"ref:{REF2A}"


def test_methods(built) -> None:
    m = built[0]["method"].set_index("method_key")
    k = f"tang_ammonia_oxidation|ref:{REF1}"
    assert m.loc[k, "method_status"] == "matched" and m.loc[k, "technique"] == "15N tracer addition"
    assert '"Incubation time (hours)": "24"' in m.loc[k, "params"]
    k = f"tang_ammonia_oxidation|ref:{REF2A}"
    assert m.loc[k, "method_status"] == "method_id_unmatched" and pd.isna(m.loc[k, "technique"])
    assert m.loc[f"tang_nitrite_oxidation|ref:{REF2N}", "method_status"] == "matched"  # looked up in metadata 2
    assert m.loc[f"tang_nitrite_oxidation|ref:{REF1}", "method_status"] == "method_id_unmatched"  # REF1 is only in metadata 1
    assert m.loc["water_nitrate", "method_status"] == "method_id_null"


def test_skip_rows_carry_detection_limit(built) -> None:
    skips = built[1]
    bdl = [s for s in skips if s["attribute"] == "tang_ammonia_oxidation"][0]
    assert bdl["detection_limit_comments"] == ["detection limit of ammonium: 0.01 uM"]
    assert bdl["detection_limit_metadata"] is None
    # the block is that of the last REFERENCE above the row (REF1), not the comment line
    assert bdl["reference"] == REF1
    assert built[2]["bdl_skips_without_any_detection_limit_text"] == 1  # the J1 O2 BDL of row 1 has none


# ---- failure modes


def test_slot_conflict_raises_unless_allow_listed(monkeypatch) -> None:
    rows = [dict(r) for r in A_ROWS]
    rows.append(_row(src="", date=D1, lat=10.0, lon=20.0, depth=5, temp=13.0, sal=35))  # S0 again, other temperature
    with pytest.raises(ContractError, match="disagree on temperature"):
        tg.build_tables(_raw(rows), ACCESS)
    monkeypatch.setattr(tg, "SLOT_CONFLICT_SAMPLES", frozenset({"10.0|20.0|5.0|2001-05-03"}))
    t, _, rep = tg.build_tables(_raw(rows), ACCESS)
    s0 = t["sample"].set_index("sample_key").loc["10.0|20.0|5.0|2001-05-03"]
    assert pd.isna(s0.water_temperature_c) and pd.isna(s0.water_salinity) and pd.isna(s0.water_salinity_units)
    assert rep["slot_conflict_samples_nulled"] == ["10.0|20.0|5.0|2001-05-03"]


def test_allow_listed_conflict_that_does_not_occur_raises(monkeypatch) -> None:
    monkeypatch.setattr(tg, "SLOT_CONFLICT_SAMPLES", frozenset({"1.0|2.0|3.0|x"}))
    with pytest.raises(ContractError, match="allow-listed"):
        tg.build_tables(_raw(), ACCESS)


@pytest.mark.parametrize(
    "kw,msg",
    [({"date": "2001 Smarch"}, "unrecognised date text"), ({"nh4": "ND"}, "unrecognised non-numeric"),
     ({"rate": "<0.1"}, "unrecognised non-numeric"), ({"depth": "BDL"}, "depth coded"), ({"depth": -3}, "negative depth"),
     ({"lat": 91.0}, "out of range"), ({"lon": 200.0}, "out of range"), ({"lat": ""}, "coordinates"),
     ({"src": "Some mystery note"}, "neither a known comment nor a reference")],
)
def test_bad_cells_raise(kw, msg) -> None:
    rows = [dict(r) for r in A_ROWS]
    rows[3] = _row(**{**{"src": REF2A, "date": "2005 October", "lat": 30, "lon": 40, "depth": 10, "rate": 0}, **kw})
    with pytest.raises(ContractError, match=msg):
        tg.build_tables(_raw(rows), ACCESS)


def test_unknown_columns_and_sheets_raise(tmp_path) -> None:
    raw = _raw()
    raw["volumetric ammonia oxidation"] = raw["volumetric ammonia oxidation"].rename(columns={"pH": "pH value"})
    with pytest.raises(ContractError, match="unexpected columns"):
        tg.build_tables(raw, ACCESS)
    raw = _raw()
    raw["rate metadata"] = raw["rate metadata"].iloc[:, :-1]
    with pytest.raises(ContractError, match="unexpected columns"):
        tg.build_tables(raw, ACCESS)


def test_first_row_needs_a_reference() -> None:
    rows = [dict(r) for r in A_ROWS]
    rows[0]["Data source"] = ""
    with pytest.raises(ContractError, match="first row has no reference"):
        tg.build_tables(_raw(rows), ACCESS)


def test_date_forms() -> None:
    assert tg.parse_date(dt.datetime(2011, 1, 17), "x") == ("2011-01-17", 2011, 1, None)
    assert tg.parse_date("1993 March to May", "x") == ("1993 March to May", 1993, None, "1993 March to May")
    assert tg.parse_date(" 2012  March ", "x") == ("2012 March", 2012, 3, None)
    assert tg.parse_date("2008 Dec-2009 Jan", "x")[1:3] == (None, None)
    with pytest.raises(ContractError):
        tg.parse_date(dt.datetime(2011, 1, 17, 6, 0), "x")
    with pytest.raises(ContractError):
        tg.parse_date(20110117, "x")


def test_note_recognition() -> None:
    for t in ("Data shown in Figure 3", "detection limit: 0.02-1.87 nM d-1 for winter rates", "INSPIRE", "Arctic",
              "(Agulhas Current/SW subtropical Indian Ocean)", "data from Peruvian OMZ are shown in Kalvelage et al., 2013"):
        assert tg.is_note(t), t
    for t in ("Baer et al., 2017. L&O.", "Clark et al., unpublished 1", "Ward. 2005. Marine Ecology Progress Series"):
        assert not tg.is_note(t) and tg._REF_RE.search(t), t
