"""Unit tests (rung 1) for the boynton-flux extract.py.

Hand-built fixture of 6 data rows and a 4-entry reference list; every expected number is derived by inspection in
the comments.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from coastal_crawler.compilations import (
    ContractError,
    add_derived_labels,
    build_compilations,
    read_extracted,
    validate_tables,
    write_extracted,
)

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "boynton-flux" / "extract.py"
_spec = importlib.util.spec_from_file_location("boynton_extract", _EXTRACT)
assert _spec and _spec.loader
bx = importlib.util.module_from_spec(_spec)
sys.modules["boynton_extract"] = bx
_spec.loader.exec_module(bx)

ACCESS = "2026-11-15"
ENTRIES = [
    "Smith, K. L. (1990). A title. Journal, 1, 1-2. http://doi.org/10.1000/Abc.1",
    "Jones, A., & Lee, B. (1991). Another title. Journal, 2, 3-4.",
    "Hopkinson Jr., C. S. (1985). Shallow-water benthic and pelagic metabolism. Marine Biology, 87, 19-32.",
]
REFSTR = {"smith": "Smith 1990", "jones": "Jones and Lee 1991", "nobody": "Nobody 1999",
          "hop": "Hopkinson 1985 and 1987"}


def _row(**kw) -> dict:
    r = {c: "ND" for c in bx.EXPECTED_COLUMNS}
    r.update({
        "REGION": "N. AMERICA", "Estuary Name": "Bay A", "general characteristics": "tidal", "Reference": REFSTR["smith"],
        "state/area": "MD", "country": "USA", "Method/measurement technique": "lab/intact cores",
        "Photic/Aphotic": "Aphotic", "stirring": "Y", "Flowthrough or Batch": "Batch", "Station Name": "S1",
        "Latitude": 38.5, "Longitude": -76.5, "SEASON": "Summer", "Date": dt.datetime(1990, 7, 1), "Month": "July",
        "Year": 1990, "Water Depth (m)": 5, "Salinity": 10, "Temp (°C)": 22, "DO(uM)": 150,
        "sites averaged Y/N": "N", "time averaged Y/N": "N", "pub or us": "", "pub or us.1": "", "Edit Notes": "",
    })
    r.update(kw)
    return r


ROWS = [
    # 0: sample A. SOC, NH4 flux, bottom-water NH4 1.5 and PO4 0.3
    _row(**{"ID#": 1, "O2 or SOC (umol/m2/hr)": -500, "NH4 Flux (umol/m2/hr)": 20, "NH4(uM)": 1.5, "PO4(uM)": 0.3}),
    # 1: same sample A (identical key and slots): NH4 1.5 repeats (collapsed), PO4 0.4 differs (conflict), flux 0
    _row(**{"ID#": 2, "O2 or SOC (umol/m2/hr)": -600, "NH4 Flux (umol/m2/hr)": 0, "NH4(uM)": 1.5, "PO4(uM)": 0.4}),
    # 2: in situ, photic, year range, month range, averaged both ways; DO and NH4(uM) coded OSN; depth ND
    _row(**{"ID#": 3, "Reference": REFSTR["nobody"], "Station Name": "S2", "Latitude": 40.0,
            "Method/measurement technique": "in situ chambers", "Photic/Aphotic": "Photic", "Year": "1990-1991",
            "Month": "Jan-Feb", "DO(uM)": "OSN", "Water Depth (m)": "ND", "sites averaged Y/N": "Y",
            "time averaged Y/N": "Y", "POC(%DW)": 2.0, "PON(%DW)": 0.2, "SedCHLa (mg/m2)": 30, "RES (gO2/m2/day)": 1.2,
            "pH": 7.5, "NH4(uM)": "OSN", "NO3 Flux (umol/m2/hr)": -3}),
    # 3: nothing measured -> no fact, no sample, no location
    _row(**{"ID#": 4, "Reference": REFSTR["jones"], "Station Name": "S9"}),
    # 4: SOC exactly 0, NH4 -0.3 (negative_value), DSi flux, PP; Date ND; different stirring -> a second SOC method
    _row(**{"ID#": 5, "Reference": REFSTR["hop"], "Station Name": "S3", "Date": "ND", "stirring": "N",
            "O2 or SOC (umol/m2/hr)": 0, "NH4(uM)": -0.3, "DSi Flux (umol/m2/hr)": 100, "PP(%DW)": 0.05}),
    # 5: another study, own station
    _row(**{"ID#": 6, "Reference": REFSTR["jones"], "Station Name": "S4", "O2 or SOC (umol/m2/hr)": -10}),
]
# expected: non-J1 cells = r0 2 + r1 2 + r2 5 (POC PON CHL RES NO3F) + r4 3 (SOC DSiF PP) + r5 1 = 13;
# J1 cells = r0 2 + r1 2 + r2 1 (pH) + r4 1 = 6 -> kept 5 (NH4 1.5, PO4 0.3, PO4 0.4, pH, NH4 -0.3), collapsed 1;
# facts 18; samples 4 (A, r2, r4, r5); locations 4 (S1, S2, S3, S4); row 3 has no fact.
# methods: SOC 3 (default params; stirring N; ...) is wrong: r0/r1/r5 share one SOC method, r4 another = 2; NH4F 1; NO3F 1;
# DSiF 1; method_id_null attrs: poc pon pp chl res + ph ammonium phosphate = 8 -> 13.


def _frame(rows=ROWS, entries=ENTRIES):
    return pd.DataFrame(rows, columns=bx.EXPECTED_COLUMNS, dtype=object), entries


def _build(rows=ROWS, entries=ENTRIES):
    raw, e = _frame(rows, entries)
    return bx.build_tables(raw, e, ACCESS)


def _with(i: int, **kw) -> list[dict]:
    rows = [dict(r) for r in ROWS]
    rows[i].update(kw)
    return rows


@pytest.fixture(scope="module")
def built():
    return _build()


def test_counts(built) -> None:
    tables, skips, rep = built
    assert skips == [] and rep["skipped"] == 0
    assert rep["source_rows"] == 6 and len(tables["fact"]) == 18 == rep["facts"]
    assert rep["non_j1_numeric_cells"] == 13 and rep["j1_numeric_cells"] == 6
    assert rep["j1_facts"] == 5 and rep["j1_collapsed"] == 1
    assert 13 + 5 == 18
    assert rep["facts_by_attribute"] == {
        "boynton_dsi_flux": 1, "boynton_nh4_flux": 2, "boynton_no3_flux": 1, "boynton_poc": 1, "boynton_pon": 1,
        "boynton_pp": 1, "boynton_sediment_chl_a": 1, "boynton_soc": 4, "boynton_water_respiration": 1,
        "water_ammonium": 2, "water_ph": 1, "water_phosphate": 2,
    }
    assert len(tables["sample"]) == 4 and len(tables["location"]) == 4 and len(tables["method"]) == 13
    assert rep["source_rows_without_any_fact"] == 1
    assert rep["coded_missing_cells_osn"] == {"NH4(uM)": 1} and rep["osn_in_do_slot"] == 1
    assert len(tables["reference"]) == 4 and len(tables["citation"]) == 5
    validate_tables(add_derived_labels(tables))


def test_source_row(built) -> None:
    src = built[0]["source"].iloc[0]
    assert src.version == "Mendeley Data v3 (2017-07-21)" and src.license == "CC BY 4.0" and src.access_date == ACCESS
    assert src.doi == "10.17632/8gc3ys3jx6.3"


def test_types_units_provenance(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    t = lambda k: tuple(f.loc[k, ["measurement_type", "subfield", "units_raw"]])  # noqa: E731
    assert t("row0:boynton_soc") == ("B5", "total_o2_uptake", "umol/m2/hr")
    assert t("row0:boynton_nh4_flux") == ("C3", "ammonium_flux", "umol/m2/hr")
    assert t("row2:boynton_no3_flux") == ("C3", "nitrate_flux", "umol/m2/hr")
    assert t("row4:boynton_dsi_flux") == ("C3", "silicate_flux", "umol/m2/hr")
    assert t("row2:boynton_water_respiration") == ("B9", "community_respiration", "gO2/m2/day")
    assert t("row2:boynton_sediment_chl_a") == ("B1", "sediment_chlorophyll_a", "mg/m2")
    assert t("row2:boynton_poc") == ("C1", "organic_carbon", "%DW")
    assert t("row2:boynton_pon")[0] == "other" and pd.isna(t("row2:boynton_pon")[1])
    assert t("row4:boynton_pp") == ("other", None, "%DW") or pd.isna(t("row4:boynton_pp")[1])
    assert f.loc["row0:boynton_soc", "value"] == -500 and f.loc["row4:boynton_soc", "value"] == 0
    assert f.loc["row1:boynton_nh4_flux", "source_row_ref"] == (
        "Published2016v3WorldFlux DataSet.xlsx[WorldFluxDataSet2016v3]#data_row=1:boynton_nh4_flux")
    assert f[["sd", "se"]].isna().all().all() and f.n.isna().all()
    j = f[f.measurement_type == "J1"]
    assert len(j) == 5
    ph = j[j.attribute == "water_ph"].iloc[0]
    assert ph.units_raw == "unitless" and ph.subfield == "ph" and ph.source_row_ref.endswith("#data_row=2:water_ph")
    assert set(j[j.attribute != "water_ph"].units_raw) == {"uM"}


def test_j1_collapse_and_conflict(built) -> None:
    tables, _, rep = built
    f = tables["fact"]
    nh4_a = f[(f.attribute == "water_ammonium") & (f.sample_key == f.loc[f.fact_key == "row0:boynton_soc", "sample_key"].iloc[0])]
    assert len(nh4_a) == 1 and nh4_a.iloc[0].value == 1.5 and nh4_a.iloc[0].source_row_ref.endswith("data_row=0:water_ammonium")
    assert list(nh4_a.iloc[0]["flags"]) == []  # one distinct value: collapsed, no conflict
    po4 = f[f.attribute == "water_phosphate"]
    assert sorted(po4.value) == [0.3, 0.4] and all("covariate_conflict" in x for x in po4["flags"])
    assert rep["j1_conflict_facts"] == 2 and rep["j1_conflict_samples"] == 1
    assert rep["j1_facts_under_plan_key"] == 5 and rep["samples_under_plan_key"] == 5  # 5 distinct plan keys (rows 0/1 share)


def test_flags(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    assert f.loc["row0:boynton_soc", "flags"] == []  # negative SOC carries sign, no flag
    assert f.loc["row1:boynton_nh4_flux", "flags"] == ["zero_value"]
    assert f.loc["row4:boynton_soc", "flags"] == ["zero_value"]
    assert f.loc["row2:boynton_poc", "flags"] == ["source_site_averaged", "source_time_averaged", "year_missing"]
    assert f.loc["row2:boynton_no3_flux", "flags"] == ["source_site_averaged", "source_time_averaged", "year_missing"]  # negative flux: no flag
    neg = f[f.attribute == "water_ammonium"].set_index("value")
    assert neg.loc[-0.3, "flags"] == ["negative_value"]
    assert rep["flag_counts"]["covariate_conflict"] == 2 and rep["flag_counts"]["negative_value"] == 1


def test_samples_locations(built) -> None:
    tables, _, _ = built
    s = tables["sample"].set_index("sample_key")
    a = s.loc[[k for k in s.index if k.startswith("Smith 1990|S1")][0]]
    assert a.sampling_year == 1990 and a.sampling_month == 7 and a.water_temperature_c == 22
    assert a.water_salinity == 10 and a.water_salinity_units == "unitless"
    assert a.bottom_water_o2 == 150 and a.bottom_water_o2_units == "uM"
    b = s.loc[[k for k in s.index if k.startswith("Nobody 1999")][0]]
    assert pd.isna(b.sampling_year) and pd.isna(b.sampling_month) and pd.isna(b.bottom_water_o2)  # range, range, OSN
    assert pd.isna(b.bottom_water_o2_units)
    loc = tables["location"].set_index("source_site_id")
    assert set(loc.index) == {"S1", "S2", "S3", "S4"}
    assert loc.loc["S1", "water_depth_m"] == 5 and pd.isna(loc.loc["S2", "water_depth_m"])
    assert loc.loc["S1", "site_name"] == "Bay A" and loc.loc["S1", "country"] == "USA"
    assert loc.loc["S1", "admin_division"] == "MD" and loc.loc["S1", "bioregion"] == "N. AMERICA"
    assert loc.loc["S1", "geomorphic_setting"] == "tidal" and loc.habitat_type.isna().all()
    assert loc.loc["S1", "latitude"] == 38.5 and loc.loc["S1", "longitude"] == -76.5


def test_labels(built) -> None:
    loc = add_derived_labels(built[0])["location"]
    assert loc.habitat.isna().all() and loc.setting.isna().all() and loc.bottom_type.isna().all()


def test_methods(built) -> None:
    tables, _, rep = built
    m = tables["method"].set_index("method_key")
    f = tables["fact"].set_index("fact_key")
    k0 = f.loc["row0:boynton_soc", "method_key"]
    assert k0 == f.loc["row1:boynton_soc", "method_key"] == f.loc["row5:boynton_soc", "method_key"]
    assert f.loc["row4:boynton_soc", "method_key"] != k0
    assert m.loc[k0, "technique"] == "lab/intact cores" and m.loc[k0, "in_situ_ex_situ"] == "ex situ"
    assert m.loc[k0, "light_dark"] == "dark" and m.loc[k0, "method_status"] == "matched"
    k2 = f.loc["row2:boynton_no3_flux", "method_key"]
    assert m.loc[k2, "in_situ_ex_situ"] == "in situ" and m.loc[k2, "light_dark"] == "light"
    assert '"stirring": "Y"' in m.loc[k0, "params"] and '"stirring": "N"' in m.loc[f.loc["row4:boynton_soc", "method_key"], "params"]
    assert '"water_depth' not in m.loc[k0, "params"]
    assert m.loc["boynton_poc", "method_status"] == "method_id_null" and m.loc["water_ph", "method_status"] == "method_id_null"
    assert rep["methods_by_attribute"]["boynton_soc"] == 2


def test_references(built) -> None:
    tables, _, rep = built
    c = tables["citation"].set_index("bibliography_id")
    assert c.loc["Smith 1990", "doi"] == "10.1000/abc.1" and c.loc["Smith 1990", "year"] == 1990
    assert c.loc["Smith 1990", "title"].startswith("Smith, K. L. (1990)")
    assert pd.isna(c.loc["Nobody 1999", "title"]) and c.loc["Nobody 1999", "year"] == 1999
    assert c.loc["Jones and Lee 1991", "title"].startswith("Jones, A., & Lee, B.")
    assert c.loc["Hopkinson 1985", "title"].startswith("Hopkinson Jr., C. S. (1985)") and pd.isna(c.loc["Hopkinson 1987", "title"])
    assert c.loc["Hopkinson 1985", "reference_key"] == "ref:Hopkinson 1985+ref:Hopkinson 1987" == c.loc["Hopkinson 1987", "reference_key"]
    assert set(tables["reference"].reference_key) == {
        "ref:Smith 1990", "ref:Jones and Lee 1991", "ref:Nobody 1999", "ref:Hopkinson 1985+ref:Hopkinson 1987"}
    assert sorted(rep["citations_unresolved"]) == [
        "Hopkinson 1987: no DataRefs entry with this first author and year",
        "Nobody 1999: no DataRefs entry with this first author and year"]


# ---- helpers


@pytest.mark.parametrize(
    "piece,start",
    [("Smith 1990", "Smith, K. L. (1990)"), ("Jones and Lee 1991", "Jones, A., & Lee"), ("Hopkinson 1985", "Hopkinson Jr.")],
)
def test_resolve_piece(piece, start) -> None:
    e, _ = bx.resolve_piece(piece, ENTRIES)
    assert e and e.startswith(start)


def test_resolve_piece_second_author_and_ambiguity() -> None:
    assert bx.resolve_piece("Jones and Park 1991", ENTRIES)[0] is None  # second author not in the entry
    assert bx.resolve_piece("Smith 1991", ENTRIES)[0] is None  # wrong year
    two = ENTRIES + ["Smith, K. L. (1990). Another. J."]
    with pytest.raises(ContractError, match="matches 2 DataRefs"):
        bx.resolve_piece("Smith 1990", two)


def test_override_prefix_must_match_one_entry() -> None:
    with pytest.raises(ContractError, match="matches 0"):
        bx.resolve_piece("Balzar et al 1983", ENTRIES)


def test_unmatched_ambiguity_without_override_raises_in_build() -> None:
    with pytest.raises(ContractError, match="no year"):
        _build(_with(0, Reference="Unknown"))


# ---- fail-loud checks


def test_unrecognised_text_in_a_value_column_raises() -> None:
    with pytest.raises(ContractError, match="unrecognised non-numeric"):
        _build(_with(0, **{"POC(%DW)": "<0.1"}))


def test_positive_soc_raises() -> None:
    with pytest.raises(ContractError, match="positive SOC"):
        _build(_with(0, **{"O2 or SOC (umol/m2/hr)": 5}))


def test_unknown_technique_raises() -> None:
    with pytest.raises(ContractError, match="unrecognised measurement technique"):
        _build(_with(0, **{"Method/measurement technique": "dredge"}))


def test_unknown_photic_raises() -> None:
    with pytest.raises(ContractError, match="Photic/Aphotic"):
        _build(_with(0, **{"Photic/Aphotic": "dim"}))


def test_wrong_columns_raise() -> None:
    raw, e = _frame()
    with pytest.raises(ContractError, match="unexpected columns"):
        bx.build_tables(raw.rename(columns={"Station Name": "Station"}), e, ACCESS)


def test_duplicate_id_and_duplicate_rows_raise() -> None:
    with pytest.raises(ContractError, match="not unique"):
        _build(_with(1, **{"ID#": 1}))
    rows = [dict(r) for r in ROWS]
    rows[1] = {**rows[0], "ID#": 99}
    with pytest.raises(ContractError, match="identical except"):
        _build(rows)


def test_coordinates_checked() -> None:
    with pytest.raises(ContractError, match="outside the valid range"):
        _build(_with(0, Latitude=95))
    with pytest.raises(ContractError, match="non-numeric"):
        _build(_with(0, Longitude="ND"))


def test_blank_estuary_raises() -> None:
    with pytest.raises(ContractError, match="blank Estuary"):
        _build(_with(0, **{"Estuary Name": ""}))


def test_negative_sediment_variable_raises() -> None:
    with pytest.raises(ContractError, match="negative value"):
        _build(_with(2, **{"POC(%DW)": -1}))


def test_sites_averaged_value_checked() -> None:
    with pytest.raises(ContractError, match="averaged"):
        _build(_with(0, **{"sites averaged Y/N": "maybe"}))


def test_negative_depth_raises() -> None:
    with pytest.raises(ContractError, match="negative water depth"):
        _build(_with(0, **{"Water Depth (m)": -1}))


# ---- build + determinism


def test_roundtrip_global_build_and_determinism(built, tmp_path) -> None:
    tables, _, _ = built
    write_extracted(tables, tmp_path / "ex")
    back = read_extracted(tmp_path / "ex")
    assert len(back["fact"]) == 18
    out1, out2 = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    build_compilations([tmp_path / "ex"], out1)
    build_compilations([tmp_path / "ex"], out2)
    q = "SELECT attribute, value, units_raw, measurement_type FROM measurement_fact ORDER BY measurement_id"
    r1 = duckdb.connect(str(out1), read_only=True).execute(q).fetchall()
    r2 = duckdb.connect(str(out2), read_only=True).execute(q).fetchall()
    assert r1 == r2 and len(r1) == 18 and {r[3] for r in r1} == {"B1", "B5", "B9", "C1", "C3", "J1", "other"}


def test_build_is_deterministic() -> None:
    a, sa, ra = _build()
    b, sb, rb = _build()
    for k in a:
        pd.testing.assert_frame_equal(a[k], b[k])
    assert ra == rb and sa == sb
