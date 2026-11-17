"""Unit tests (rung 1) for the francesco-phytoplankton extract.py.

Hand-built fixture (7 rows, 3 profiles, the real 49-column layout); every expected number is derived by inspection
in the comments.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from coastal_crawler.compilations import ContractError, add_derived_labels, validate_tables

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "francesco-phytoplankton" / "extract.py"
_spec = importlib.util.spec_from_file_location("francesco_extract", _EXTRACT)
assert _spec and _spec.loader
fr = importlib.util.module_from_spec(_spec)
sys.modules["francesco_extract"] = fr
_spec.loader.exec_module(fr)

ACCESS = "2026-11-15"
PPV = "Depth-resolved primary production (mg C m^-3 day^-1)"
CHL = "Depth-resolved chl a (mg m^-3)"
PPI = "Depth-integrated primary production (mg C m^-2 day^-1)"
PB = "Pbopt (mg C mg Chla-1 h-1)"
ICHL = "Depth-integrated chl a (mg m^-2)"
TCHL = "Total Chl a (mg m^-2)"
DOI_A = "https://doi.org/10.1000/ABC"


def _row(n: int, p: int, **kw) -> dict:
    r = {c: "x" for c in fr.COLUMNS}  # every other column: some text, never read
    r.update({"Count": str(n), "Profile number": str(p), "Year": "1979", "Month": "3", "Date": "16/03/1979",
              "Latitude": "10.5", "Longitude": "-20.25", "Sampling depth (m)": "0", "SST (°C)": "15.0", "SST_flag": "0",
              "PAR_flag": "1", "Event": "Cruise1", "Short reference": "Short A 2001", "Paper doi": DOI_A,
              "Max sampling depth (m)": "50", PPV: "12.0", CHL: "0.5", PPI: "100.0", PB: "2.5", ICHL: "30.0", TCHL: "40.0"})
    r.update(kw)
    return r


P2 = {"Short reference": "Short B 1999", "Paper doi": "", "Date": "01/05/1990", "Year": "1990", "Month": "5",
      PPI: "200.0", PB: "3.5", ICHL: "31.0", TCHL: "41.0", "Max sampling depth (m)": "80"}
P3 = {"Short reference": "Short C 2015", "Paper doi": "https://doi.org/10.1/A; https://doi.org/10.2/B",
      "Latitude": "30", "Longitude": "40", "SST_flag": "1", "Sampling depth (m)": "5",
      PPI: "300.0", PB: "4.5", ICHL: "32.0", TCHL: "42.0"}
ROWS = [
    _row(1, 1),
    _row(2, 1, **{"Sampling depth (m)": "10", "SST (°C)": "15.5", PPV: "0", CHL: "0.0"}),
    _row(3, 1, **{"Sampling depth (m)": "10", "SST (°C)": "15.5", PPV: "3.0", CHL: "0.0"}),  # same sample as row 2
    _row(4, 2, **{**P2, "Latitude": "20", "Longitude": "-185.0", "Sampling depth (m)": "5", "SST_flag": "1",
                  "Event": "", PPV: "5.0", CHL: "1.0"}),
    _row(5, 2, **{**P2, "Latitude": "21", "Longitude": "-184.0", "Date": "02/05/1990", "Sampling depth (m)": "15",
                  "SST (°C)": "21.0", PPV: "6.0", CHL: "2.0"}),
    _row(6, 3, **{**P3, PPV: "7.0", CHL: "3.0"}),
    _row(7, 3, **{**P3, PPV: "8.0", CHL: "3.5"}),  # same sample as row 6, a different chl
]
# expected: PP facts 7; chl cells 7 -> rows 2/3 equal (0.0) collapse into 1, rows 6/7 conflict (both kept): 6 facts;
# profile-level 3 profiles x 4 = 12; facts 25; cells_in 14 + 28 = 42 = 25 + 1 (J1) + 16 (profile repeats: 28 - 12);
# samples: row-level 5 (rows 1, 2+3, 4, 5, 6+7) + 3 profile = 8; locations 4; references 3; citations 1 + 1 + 2 = 4.


def _raw(rows=None) -> pd.DataFrame:
    return pd.DataFrame(rows or ROWS, columns=fr.COLUMNS, dtype=object)


@pytest.fixture(scope="module")
def built():
    return fr.build_tables(_raw(), ACCESS)


def test_counts(built) -> None:
    tables, skips, rep = built
    assert skips == [] and len(tables["fact"]) == 25 == rep["facts"]
    assert rep["facts_by_attribute"] == {
        "francesco_pbopt": 3, "francesco_pp_integrated": 3, "francesco_pp_volumetric": 7, "water_chl_a": 6,
        "water_chl_a_integrated": 3, "water_chl_a_total": 3}
    assert rep["cells_in"] == 42 and rep["collapsed_profile_repeats"] == 16
    assert rep["j1_collapse"]["collapsed_repeats"] == 1 and rep["j1_collapse"]["conflict_facts"] == 2
    assert rep["profiles"] == 3 and rep["profiles_spanning_several_stations"] == ["2"]
    assert len(tables["sample"]) == 8 and rep["samples_row_level"] == 5 and len(tables["location"]) == 4
    assert len(tables["reference"]) == 3 and len(tables["citation"]) == 4 and len(tables["method"]) == 6
    validate_tables(add_derived_labels(tables))


def test_values_units_types(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    t = lambda k: tuple(f.loc[k, ["measurement_type", "subfield", "units_raw", "value"]])  # noqa: E731
    assert t("row0:francesco_pp_volumetric") == ("B8", "primary_production_per_volume", "mg C m^-3 day^-1", 12.0)
    assert t("row0:water_chl_a") == ("J1", "chlorophyll_a", "mg m^-3", 0.5)
    assert t("profile1:francesco_pp_integrated") == ("B8", "depth_integrated_primary_production", "mg C m^-2 day^-1", 100.0)
    assert t("profile1:francesco_pbopt") == ("B8", "chlorophyll_specific_production", "mg C mg Chla-1 h-1", 2.5)
    assert t("profile2:water_chl_a_integrated") == ("J1", "chlorophyll_a", "mg m^-2", 31.0)
    assert t("profile2:water_chl_a_total") == ("J1", "chlorophyll_a", "mg m^-2", 41.0)  # a separate attribute, no conflict
    assert f.loc["profile1:francesco_pbopt", "source_row_ref"] == f"{fr.DATA_FILE}#data_row=0:francesco_pbopt"
    assert f.loc["profile2:francesco_pbopt", "source_row_ref"].endswith("#data_row=3:francesco_pbopt")  # first row of profile 2


def test_j1_collapse_and_conflict(built) -> None:
    f = built[0]["fact"]
    s = "1|10|16/03/1979|10.5|-20.25"
    g = f[(f.sample_key == s) & (f.attribute == "water_chl_a")]
    assert list(g.value) == [0.0] and g.iloc[0]["flags"] == ["habitat_from_scope", "secondary_compilation", "zero_value"]
    pp = f[(f.sample_key == s) & (f.attribute == "francesco_pp_volumetric")]
    assert sorted(pp.value) == [0.0, 3.0]  # PP is not J1: one fact per row, both loaded
    c = f[f.attribute == "water_chl_a"]
    conf = c[c["flags"].map(lambda x: "covariate_conflict" in x)]
    assert sorted(conf.value) == [3.0, 3.5] and set(conf.sample_key) == {"3|5|16/03/1979|30|40"}
    assert built[2]["flag_counts"]["covariate_conflict"] == 2 and built[2]["flag_counts"]["zero_value"] == 2


def test_samples(built) -> None:
    tables, _, rep = built
    s = tables["sample"].set_index("sample_key")
    a = s.loc["1|0|16/03/1979|10.5|-20.25"]
    assert (a.sampling_year, a.sampling_month, a.sample_depth_m, a.water_temperature_c, a.source_sample_id) == (1979, 3, 0.0, 15.0, "Cruise1")
    assert s.loc["1|10|16/03/1979|10.5|-20.25", "water_temperature_c"] == 15.5
    assert pd.isna(s.loc["2|5|01/05/1990|20|-185.0", "water_temperature_c"])  # SST_flag 1: reconstructed, not loaded
    assert pd.isna(s.loc["2|5|01/05/1990|20|-185.0", "source_sample_id"])  # blank Event
    assert s.loc["2|15|02/05/1990|21|-184.0", "water_temperature_c"] == 21.0
    assert rep["samples_with_temperature"] == 3 and rep["rows_with_in_situ_sst"] == 4
    p = s.loc["profile2"]
    assert pd.isna(p.sample_depth_m) and p.depth_notes == "profile-level value; max sampling depth 80 m; profile spans 2 stations, located at the first row's"
    assert s.loc["profile1", "depth_notes"] == "profile-level value; max sampling depth 50 m"


def test_locations_and_lon_wrap(built) -> None:
    tables, _, rep = built
    loc = tables["location"].set_index("location_key")
    assert loc.loc["20|-185.0", "longitude"] == 175.0 and loc.loc["21|-184.0", "longitude"] == 176.0
    assert loc.loc["10.5|-20.25", "longitude"] == -20.25 and (loc.habitat_type == "water column").all()
    f = tables["fact"]
    assert rep["flag_counts"]["lon_wrapped"] == 8  # rows 4 and 5: PP + chl each, plus 4 profile-level facts of profile 2
    assert "lon_wrapped" in f.set_index("fact_key").loc["profile2:francesco_pbopt", "flags"]
    lab = add_derived_labels(tables)["location"]
    assert (lab.habitat == "Water column").all() and lab.bottom_type.isna().all()
    assert set(tables["fact"]["flags"].map(lambda x: "secondary_compilation" in x and "habitat_from_scope" in x)) == {True}


def test_references(built) -> None:
    tables, _, rep = built
    assert set(tables["reference"].reference_key) == {"doi:10.1000/abc", "ref:Short B 1999", "doi:10.1/a+doi:10.2/b"}
    c = tables["citation"].set_index("citation_key")
    assert c.loc["doi:10.1000/abc|doi:10.1000/abc", "url"] == DOI_A.lower() and c.loc["doi:10.1000/abc|doi:10.1000/abc", "doi"] == "10.1000/abc"
    assert c.loc["ref:Short B 1999|0", "year"] == 1999 and pd.isna(c.loc["ref:Short B 1999|0", "doi"])
    assert c.loc["doi:10.1/a+doi:10.2/b|doi:10.2/b", "bibliography_id"] == "Short C 2015"
    assert pd.isna(c.loc["doi:10.1/a+doi:10.2/b|doi:10.1/a", "year"])
    assert rep["references_composite_doi"] == 1 and rep["citations_with_doi"] == 3


# ---- failure modes


def _with(i: int, **kw) -> list[dict]:
    rows = [dict(r) for r in ROWS]
    rows[i].update(kw)
    return rows


@pytest.mark.parametrize(
    "i,kw,msg",
    [(0, {CHL: "abc"}, "non-numeric"), (0, {PPV: ""}, "non-numeric"), (0, {PPV: "NA"}, "non-numeric"),
     (0, {PPI: "101.0"}, "not constant within profile"), (1, {PB: "9.9"}, "not constant within profile"),
     (1, {"Short reference": "Other"}, "not constant within profile"),
     (0, {"Count": "9"}, "Count is not 1..N"), (0, {"Date": "1979-03-16"}, "disagrees with Year"),
     (0, {"Year": "1980"}, "disagrees with Year"), (0, {"SST_flag": "2"}, "SST_flag values"),
     (0, {"Longitude": "-400"}, "wrappable range"),
     (0, {"Latitude": "95"}, "latitude out of range"), (0, {"Sampling depth (m)": "-1"}, "negative sampling depth"),
     (2, {"SST (°C)": "16.0"}, "disagree on temperature or event"), (2, {"Event": "Other"}, "disagree on temperature or event")],
)
def test_bad_input_raises(i, kw, msg) -> None:
    with pytest.raises(ContractError, match=msg):
        fr.build_tables(_raw(_with(i, **kw)), ACCESS)


def test_bad_doi_cell_raises() -> None:
    rows = [dict(r) for r in ROWS]
    for r in rows[:3]:
        r["Paper doi"] = "doi:10.1/x"
    with pytest.raises(ContractError, match="not an https://doi.org/ link"):
        fr.build_tables(_raw(rows), ACCESS)


def test_zero_profile_value_raises() -> None:
    rows = [dict(r) for r in ROWS]
    for r in rows[:3]:
        r[TCHL] = "0"
    with pytest.raises(ContractError, match="zero profile-level"):
        fr.build_tables(_raw(rows), ACCESS)


def test_unexpected_columns_raise() -> None:
    with pytest.raises(ContractError, match="unexpected columns"):
        fr.build_tables(_raw().rename(columns={PB: "Pbopt"}), ACCESS)


def test_parse_dois() -> None:
    assert fr.parse_dois("") == [] and fr.parse_dois("https://doi.org/10.1/A") == ["10.1/a"]
    assert fr.parse_dois("https://doi.org/10.1/A; https://doi.org/10.2/B") == ["10.1/a", "10.2/b"]
    with pytest.raises(ContractError):
        fr.parse_dois("10.1/a")


def test_unit_of() -> None:
    assert fr.unit_of(PB) == "mg C mg Chla-1 h-1" and fr.unit_of(CHL) == "mg m^-3"
    with pytest.raises(ContractError):
        fr.unit_of("Latitude")
