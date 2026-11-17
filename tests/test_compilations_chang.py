"""Unit tests (rung 1) for the chang-denitrification extract.py.

Hand-built fixture (6 rows of each sheet and a 3-entry reference list); every expected number is derived by
inspection in the comments.
"""

from __future__ import annotations

import importlib.util
import sys
import zipfile
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

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "chang-denitrification" / "extract.py"
_spec = importlib.util.spec_from_file_location("chang_extract", _EXTRACT)
assert _spec and _spec.loader
ch = importlib.util.module_from_spec(_spec)
sys.modules["chang_extract"] = ch
_spec.loader.exec_module(ch)

ACCESS = "2026-11-15"
R = ch.RATE_UNIT
DEN, ANA, P14 = f"Denitrification ({R})", f"Anammox ({R})", f"P14 ({R})"
REFS = {
    1: "Nielsen, L.P. and R.N. Glud, Denitrification in a sediment. Marine Ecology Progress Series, 1996. 137: 181-186.",
    2: "Sokoll, S., et al., Benthic loss. Frontiers in Microbiology, 2012. 3.",
    3: "Somebody, A., An undated report.",
}
EXP0 = ["55-76", "dark", "in situ temperature", "3 to 5.5", "50% ZnCl2", "(Nielsen, 1992)"]
EXP2 = ["~100", "", "", "24", "", "(Risgaard-Petersen et al., 2003)"]
EXP3 = ["", "", "", "", "", ""]
EXP4 = ["50", "dark", "near in situ temperature", "6", "7 M ZnCl2", "(Hsu & Kao, 2013)"]


def _raw_row(**kw) -> dict:
    r = {c: "" for c in ch.RAW_COLUMNS}
    r.update(kw)
    return r


RAW_ROWS = [
    # 0: denitrification only; slots; nitrate 0 (a real zero), ammonium coded 'na'; OC given
    _raw_row(**{"References No.": 1, "Locations": "Bay A", "Site": "S1", "Year": 1992, "Month": "June",
                "Month_1": 6, "Longitude (°)": 10.5, "Latitude (°)": 56.25, DEN: 12.5, "Process": "Denitrification",
                P14: 12.5, "Organic carbon (%)": 1.5, "Water depth (m)": 16, "Temperature (℃)": 7.5,
                "Salinity (psu)": 26, "Dissolved oxygen (μM)": 115, "Nitrate (μM)": 0, "Ammonium (μM)": "na"}),
    # 1: same site (one location), negative temperature, DO coded 'na'
    _raw_row(**{"References No.": 1, "Locations": "Bay A", "Site": "S1", "Year": 1992, "Month": "November",
                "Month_1": 11, "Longitude (°)": 10.5, "Latitude (°)": 56.25, DEN: 20, "Process": "Denitrification",
                P14: 20, "Water depth (m)": 16, "Temperature (℃)": -1, "Dissolved oxygen (μM)": "na"}),
    # 2: no coordinates and no year (month given); anammox measured; P14 = 10 + 5 + 0.02 (within tolerance);
    #    organic carbon and C/N 'nm' -> 2 skips; O2 penetration 3.5; site is a number
    _raw_row(**{"References No.": 2, "Locations": "Bay B", "Site": 5, "Month": "May", "Month_1": 5, DEN: 10,
                ANA: 5, "Process": "Denitrification and anammox", P14: 15.02, "ra (%)": 33.3,
                "Organic carbon (%)": "nm", "C/N (mol:mol)": "nm", "Oxygen penetration depth (mm)": 3.5}),
    # 3: only P14 (no denitrification, empty process); whole-degree coordinates; no month; empty experiment info
    _raw_row(**{"References No.": 2, "Locations": "Bay B", "Site": 5, "Year": 2001, "Longitude (°)": 30,
                "Latitude (°)": 20, P14: 8}),
    # 4, 5: identical rows (duplicate flag); anammox exactly 0; nitrate and ammonium; no site
    _raw_row(**{"References No.": 3, "Locations": "Bay C", "Year": 2005, "Month": "January", "Month_1": 1,
                "Longitude (°)": -70.1, "Latitude (°)": 41.2, DEN: 5, ANA: 0, "Process": "Denitrification and anammox",
                P14: 5, "Nitrate (μM)": 22.5, "Ammonium (μM)": 3.1}),
]
RAW_ROWS.append(dict(RAW_ROWS[-1]))
EXPS = [EXP0, EXP0, EXP2, EXP3, EXP4, EXP4]
# expected: 6 rows; facts D 5 + A 3 + P14 6 + OC 1 + O2PD 1 + NO3 3 + NH4 2 + DO 1 = 22; skips 2 (row 2 OC, C/N);
# na cells: NH4 row 0, DO row 1; locations 4 (rows 0+1; 2; 3; 4+5); samples 6; references 3; citations 3;
# methods: D 3 (exp0, exp2, exp4), A 2 (exp2, exp4), P14 4 (exp0, exp2, exp3, exp4), 5 covariates = 14.


def _frames(raw_rows=RAW_ROWS, exps=EXPS):
    raw = pd.DataFrame(raw_rows, columns=ch.RAW_COLUMNS, dtype=object)
    exp = pd.DataFrame(
        [[r["References No."], r["Locations"], r["Site"], *e] for r, e in zip(raw_rows, exps)],
        columns=ch.EXP_COLUMNS, dtype=object,
    )
    return raw, exp


def _build(raw_rows=None, exps=None, refs=None):
    raw, exp = _frames(raw_rows or RAW_ROWS, exps or EXPS)
    return ch.build_tables(raw, exp, refs or REFS, ACCESS)


def _with(i: int, **kw) -> list[dict]:
    rows = [dict(r) for r in RAW_ROWS]
    rows[i].update(kw)
    return rows


@pytest.fixture(scope="module")
def built():
    return _build()


def test_counts(built) -> None:
    tables, skips, rep = built
    assert rep["source_rows"] == 6 and len(tables["fact"]) == 22 == rep["facts"]
    assert rep["facts_by_attribute"] == {
        "chang_anammox": 3, "chang_denitrification": 5, "chang_o2_penetration_depth": 1, "chang_organic_carbon": 1,
        "chang_total_n2_production": 6, "water_ammonium": 2, "water_nitrate": 3, "water_o2": 1,
    }
    assert [(s["data_row"], s["attribute"], s["reason"]) for s in skips] == [
        (2, "chang_organic_carbon", "not_measured"), (2, "chang_c_n_ratio", "not_measured")]
    assert rep["coded_missing_cells_na"]["Ammonium (μM)"] == 1 and rep["coded_missing_cells_na"]["Dissolved oxygen (μM)"] == 1
    assert len(tables["sample"]) == 6 and len(tables["location"]) == 4
    assert len(tables["reference"]) == 3 == len(tables["citation"])
    assert len(tables["method"]) == 14 and rep["methods_by_attribute"]["chang_total_n2_production"] == 4
    assert rep["rows_with_p14_and_denitrification_and_anammox"] == 3 and rep["rows_without_denitrification"] == 1
    validate_tables(add_derived_labels(tables))


def test_source_row(built) -> None:
    src = built[0]["source"].iloc[0]
    assert src.version == "v3 (2024-11-15)" and src.license == "CC BY 4.0" and src.access_date == ACCESS
    assert src.doi == "10.6084/m9.figshare.27745770.v3"


def test_types_units_provenance(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    t = lambda k: tuple(f.loc[k, ["measurement_type", "subfield", "units_raw"]])  # noqa: E731
    assert t("row0:chang_denitrification") == ("C4", "denitrification", R)
    assert t("row2:chang_anammox") == ("C4", "anammox", R)
    assert t("row3:chang_total_n2_production") == ("C4", "total_n2_production", R)
    assert t("row0:chang_organic_carbon") == ("C1", "organic_carbon", "%")
    assert t("row2:chang_o2_penetration_depth") == ("C2", "o2_penetration_depth", "mm")
    assert t("row0:water_nitrate") == ("J1", "nitrate", "μM")
    assert t("row4:water_ammonium") == ("J1", "ammonium", "μM")
    assert t("row0:water_o2") == ("J1", "dissolved_oxygen", "μM")
    assert f.loc["row2:chang_total_n2_production", "value"] == 15.02
    assert f.loc["row0:water_nitrate", "value"] == 0.0  # a real zero is a fact
    assert "row0:water_ammonium" not in f.index and "row1:water_o2" not in f.index  # na -> no fact
    assert "row2:chang_organic_carbon" not in f.index and "row2:chang_c_n_ratio" not in f.index  # nm -> skip
    assert "row2:chang_ra" not in f.index and not any("ra" == a for a in f.attribute)  # ra is derived: not loaded
    assert f.loc["row2:chang_anammox", "source_row_ref"] == "Upload_data.xlsx[Raw_data]#data_row=2:chang_anammox"
    assert f[["sd", "se"]].isna().all().all() and f.n.isna().all()


def test_flags(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    fl = lambda k: f.loc[k, "flags"]  # noqa: E731
    assert fl("row0:chang_denitrification") == []
    assert fl("row0:water_nitrate") == ["zero_value"]
    assert fl("row2:chang_denitrification") == ["coords_missing", "year_missing"]
    assert fl("row2:chang_o2_penetration_depth") == ["coords_missing", "year_missing"]
    assert fl("row3:chang_total_n2_production") == []  # whole-degree coordinates carry no flag here
    assert fl("row4:chang_anammox") == ["duplicate_source_row", "zero_value"]
    assert fl("row5:water_nitrate") == ["duplicate_source_row"]
    assert rep["flag_counts"] == {"coords_missing": 4, "year_missing": 4, "zero_value": 3, "duplicate_source_row": 10}
    assert rep["rows_by_flag"] == {"no_coordinates": 1, "no_year": 1, "no_month": 1, "duplicate": 2}


def test_locations_samples(built) -> None:
    tables, _, _ = built
    loc = tables["location"].set_index("location_key")
    k = "1|Bay A|S1|56.25|10.5|16.0"
    assert loc.loc[k, "site_name"] == "Bay A" and loc.loc[k, "source_site_id"] == "S1"
    assert loc.loc[k, "water_depth_m"] == 16 and loc.loc[k, "latitude"] == 56.25 and loc.loc[k, "longitude"] == 10.5
    nc = loc.loc["2|Bay B|5|||"]  # site written as a number -> text; no coordinates, no depth
    assert pd.isna(nc.latitude) and pd.isna(nc.longitude) and pd.isna(nc.water_depth_m) and nc.source_site_id == "5"
    assert loc.habitat_type.isna().all()  # no label and no stated scope (D3): NULL, no flag
    assert pd.isna(loc.loc["3|Bay C||41.2|-70.1|", "source_site_id"])
    s = tables["sample"].set_index("sample_key")
    assert s.loc["row0", "sampling_year"] == 1992 and s.loc["row0", "sampling_month"] == 6
    assert pd.isna(s.loc["row2", "sampling_year"]) and s.loc["row2", "sampling_month"] == 5
    assert pd.isna(s.loc["row3", "sampling_month"])
    assert s.loc["row0", "water_temperature_c"] == 7.5 and s.loc["row1", "water_temperature_c"] == -1
    assert s.loc["row0", "water_salinity"] == 26 and s.loc["row0", "water_salinity_units"] == "psu"
    assert pd.isna(s.loc["row1", "water_salinity"]) and pd.isna(s.loc["row1", "water_salinity_units"])
    assert s.bottom_water_o2.isna().all() and s.sample_depth_m.isna().all()


def test_labels(built) -> None:
    loc = add_derived_labels(built[0])["location"]
    assert loc.habitat.isna().all() and loc.bottom_type.isna().all() and loc.setting.isna().all()


def test_methods(built) -> None:
    tables, _, _ = built
    m = tables["method"].set_index("method_key")
    k0 = "chang_denitrification|" + "|".join(EXP0)
    assert m.loc[k0, "technique"] == "(Nielsen, 1992)" and m.loc[k0, "method_status"] == "matched"
    assert m.loc[k0, "light_dark"] == "dark" and pd.isna(m.loc[k0, "incubation_type"])
    assert '"incubation_temperature": "in situ temperature"' in m.loc[k0, "params"]
    assert '"no3_addition_final_concentration_uM": "55-76"' in m.loc[k0, "params"]
    k2 = "chang_anammox|" + "|".join(EXP2)
    assert pd.isna(m.loc[k2, "light_dark"]) and m.loc[k2, "technique"] == "(Risgaard-Petersen et al., 2003)"
    k3 = "chang_total_n2_production|" + "|".join(EXP3)
    assert m.loc[k3, "method_status"] == "method_id_null" and pd.isna(m.loc[k3, "technique"])
    assert m.loc["water_nitrate", "method_status"] == "method_id_null"
    f = tables["fact"].set_index("fact_key")
    assert f.loc["row0:chang_denitrification", "method_key"] == f.loc["row1:chang_denitrification", "method_key"] == k0


def test_references(built) -> None:
    tables, _, rep = built
    assert set(tables["reference"].reference_key) == {"ref_no:1", "ref_no:2", "ref_no:3"}
    c = tables["citation"].set_index("bibliography_id")
    assert c.loc["1", "year"] == 1996 and c.loc["2", "year"] == 2012 and pd.isna(c.loc["3", "year"])
    assert c.loc["1", "title"] == REFS[1] and c.loc["1", "reference_key"] == "ref_no:1"
    assert c.doi.isna().all()
    assert rep["citations_without_year"] == 1
    f = tables["fact"].set_index("fact_key")
    assert f.loc["row3:chang_total_n2_production", "reference_key"] == "ref_no:2"


# ---- helpers


@pytest.mark.parametrize(
    "v,kind,x",
    [(1.5, "num", 1.5), (0, "num", 0.0), ("", "blank", None), (None, "blank", None), (float("nan"), "blank", None),
     ("na", "na", None), ("n/a", "na", None), ("NA", "na", None), ("nm", "nm", None), (" NM ", "nm", None)],
)
def test_classify(v, kind, x) -> None:
    assert ch.classify(v, "c") == (kind, x)


@pytest.mark.parametrize("v", ["abc", "1.5", "<0.1", True, float("inf"), "ND"])
def test_classify_rejects_other_text(v) -> None:
    with pytest.raises(ContractError):
        ch.classify(v, "c")


def test_norm_header() -> None:
    assert ch.norm_header("Denitrification (μmol N m−2\xa0h−1)") == f"Denitrification ({R})"
    assert ch.norm_header("Anammox  (μmol N m−2\xa0h−1)") == f"Anammox ({R})"


def test_read_references(tmp_path) -> None:
    def docx(paras: list[str]) -> Path:
        p = tmp_path / f"r{len(list(tmp_path.iterdir()))}.docx"
        body = "".join(f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in paras)
        xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
               + body + "</w:body></w:document>")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("word/document.xml", xml)
        return p

    ok = ["The references listed in the database."] + [f"{i}.Author {i}, Title. J, 20{i:02d}. 1: 2-3." for i in range(1, 70)]
    got = ch.read_references(docx(ok))
    assert sorted(got) == list(range(1, 70)) and got[1].startswith("Author 1, Title")
    with pytest.raises(ContractError, match="not 1..69"):
        ch.read_references(docx(ok[:-1]))
    with pytest.raises(ContractError, match="twice"):
        ch.read_references(docx(ok + ["3.Again"]))
    with pytest.raises(ContractError, match="neither the title"):
        ch.read_references(docx(ok + ["stray paragraph"]))


# ---- fail-loud checks


def test_unrecognised_text_in_a_value_column_raises() -> None:
    with pytest.raises(ContractError, match="unrecognised non-numeric"):
        _build(_with(0, **{"Organic carbon (%)": "<0.1"}))


def test_misaligned_sheets_raise() -> None:
    raw, exp = _frames()
    exp.loc[1, "Locations"] = "Bay Z"
    with pytest.raises(ContractError, match="not aligned"):
        ch.build_tables(raw, exp, REFS, ACCESS)


def test_row_count_mismatch_raises() -> None:
    raw, exp = _frames()
    with pytest.raises(ContractError, match="rows but"):
        ch.build_tables(raw, exp.iloc[:5], REFS, ACCESS)


def test_wrong_columns_raise() -> None:
    raw, exp = _frames()
    with pytest.raises(ContractError, match="unexpected columns"):
        ch.build_tables(raw.rename(columns={"Site": "Station"}), exp, REFS, ACCESS)
    with pytest.raises(ContractError, match="unexpected columns"):
        ch.build_tables(raw, exp.rename(columns={"Termination": "Stop"}), REFS, ACCESS)


def test_p14_must_equal_the_sum() -> None:
    with pytest.raises(ContractError, match="P14 differs"):
        _build(_with(2, **{P14: 16.0}))


def test_row_without_p14_raises() -> None:
    with pytest.raises(ContractError, match="without P14"):
        _build(_with(0, **{P14: ""}))


def test_process_and_anammox_must_agree() -> None:
    with pytest.raises(ContractError, match="Process"):
        _build(_with(2, **{ANA: ""}))
    with pytest.raises(ContractError, match="Process"):
        _build(_with(0, **{ANA: 1.0, P14: 13.5}))


def test_month_checks() -> None:
    with pytest.raises(ContractError, match="disagrees"):
        _build(_with(0, Month_1=7))
    with pytest.raises(ContractError, match="one is blank"):
        _build(_with(0, Month_1=""))


def test_lat_without_lon_raises() -> None:
    with pytest.raises(ContractError, match="blank together"):
        _build(_with(0, **{"Longitude (°)": ""}))


def test_coordinates_out_of_range_raise() -> None:
    with pytest.raises(ContractError, match="outside the valid range"):
        _build(_with(0, **{"Latitude (°)": 91}))


def test_negative_water_depth_raises() -> None:
    with pytest.raises(ContractError, match="negative water depth"):
        _build(_with(0, **{"Water depth (m)": -3}))


def test_blank_location_raises() -> None:
    with pytest.raises(ContractError, match="blank Locations"):
        _build(_with(0, Locations=""))
        # the experiment sheet is built from the same row, so alignment is not what fails


def test_reference_numbers_must_match_the_docx() -> None:
    with pytest.raises(ContractError, match="differ from the docx"):
        _build(refs={1: REFS[1], 2: REFS[2]})
    with pytest.raises(ContractError, match="differ from the docx"):
        _build(refs={**REFS, 4: "x"})


def test_sheet_names_are_checked(tmp_path) -> None:
    p = tmp_path / "x.xlsx"
    with pd.ExcelWriter(p) as w:  # type: ignore[abstract]
        pd.DataFrame({"a": [1]}).to_excel(w, sheet_name="Raw_data", index=False)
    with pytest.raises(ContractError, match="sheets"):
        ch.read_raw(p)


# ---- build + determinism


def test_roundtrip_global_build_and_determinism(built, tmp_path) -> None:
    tables, _, _ = built
    write_extracted(tables, tmp_path / "ex")
    back = read_extracted(tmp_path / "ex")
    assert len(back["fact"]) == 22
    g = assign_global_keys([back])
    assert list(g["fact"].measurement_id) == list(range(1, 23))
    out1, out2 = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    build_compilations([tmp_path / "ex"], out1)
    build_compilations([tmp_path / "ex"], out2)
    q = (
        "SELECT f.value, f.attribute, f.units_raw, f.measurement_type, l.habitat, l.water_depth_m "
        "FROM measurement_fact f JOIN location_dim l USING (location_id) ORDER BY f.measurement_id"
    )
    r1 = duckdb.connect(str(out1), read_only=True).execute(q).fetchall()
    r2 = duckdb.connect(str(out2), read_only=True).execute(q).fetchall()
    assert r1 == r2 and len(r1) == 22 and {r[3] for r in r1} == {"C1", "C2", "C4", "J1"}


def test_build_is_deterministic() -> None:
    a, sa, ra = _build()
    b, sb, rb = _build()
    for k in a:
        pd.testing.assert_frame_equal(a[k], b[k])
    assert ra == rb and sa == sb


def test_unregistered_flag_raises_at_validation(built) -> None:
    tables, _, _ = built
    bad = {k: v.copy() for k, v in tables.items()}
    flags = list(bad["fact"]["flags"])
    flags[0] = list(flags[0]) + ["made_up_flag"]
    bad["fact"]["flags"] = flags
    with pytest.raises(ContractError, match="made_up_flag"):
        write_extracted(bad, Path("/nonexistent-never-written"))
