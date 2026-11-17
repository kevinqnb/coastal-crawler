"""Unit tests (rung 1) for the ray-oyster extract.py.

Hand-built fixture (5 sediment rows and 3 oyster rows with the real column layout); every expected number is
derived by inspection in the comments.
"""

from __future__ import annotations

import importlib.util
import json
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

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "ray-oyster" / "extract.py"
_spec = importlib.util.spec_from_file_location("ray_extract", _EXTRACT)
assert _spec and _spec.loader
ry = importlib.util.module_from_spec(_spec)
sys.modules["ray_extract"] = ry
_spec.loader.exec_module(ry)

ACCESS = "2026-11-15"
S_UNIT, O_UNIT = ry.SED_UNITS, ry.OY_UNITS
SED_COLS = (
    ry.SED_META
    + [h for sol in ("N2", "NH4", "N2O", "CO2", "P", "CH4", "si", "nox", "no3", "no2")
       for arm in ("Ctrl", "Oys") for h in (f"Mean {sol} {arm}", f"SD {sol} {arm}", f"n {sol} {arm}")]
    + ["notes"]
)
_OY_SD = {"n2": "SD", "n2o": "SD", "nh4": "SD", "po4": "sd", "ch4": "SD", "nox": "sd", "no3": "sd", "no2": "sd"}
OY_COLS = ry.OY_META + [h for s in ry.OY_SOLUTES for h in (f"{s}_flux_mean", f"{s}_flux_{_OY_SD[s]}", f"{s}_flux_n")]
POT = ry.POTENTIAL_NOTE_START + " NH4 flux was measured using ammonification assays. Denitrification is a potential."


def _row(cols, **kw) -> dict:
    r = {c: "" for c in cols}
    r.update(kw)
    return r


def _trip(row: dict, cols, sol: str, arm: str, m, s, n) -> dict:
    low = {c.lower(): c for c in cols}
    row[low[f"mean {sol} {arm}".lower()]] = m
    row[low[f"sd {sol} {arm}".lower()]] = s
    row[low[f"n {sol} {arm}".lower()]] = n
    return row


def _otrip(row: dict, sol: str, m, s, n) -> dict:
    row[f"{sol}_flux_mean"], row[f"{sol}_flux_{_OY_SD[sol]}"], row[f"{sol}_flux_n"] = m, s, n
    return row


def _sed_rows() -> list[dict]:
    meta = lambda name, **kw: _row(SED_COLS, abbreviated_name=name, **kw)  # noqa: E731
    s0 = meta("Alpha et al., 2000", **{"Publication title": "T Alpha", "Authors": "Alpha, A", "Journal": "J A", "Year": 2000,
                                       "Species": "Crassostrea virginica", "Coordinates": "34.5, -76.25",
                                       "Habitat": "Reef", "n2_method": "n2_ar"})
    _trip(s0, SED_COLS, "N2", "Ctrl", 10, 2, 5), _trip(s0, SED_COLS, "N2", "Oys", 30, 4, 5)
    _trip(s0, SED_COLS, "NH4", "ctrl", 1.5, 0.5, 5), _trip(s0, SED_COLS, "NH4", "oys", 0, 0.1, 5)
    s1 = meta("Beta et al., 2001", **{"Publication title": "T Beta", "Authors": "Beta, B", "Journal": "J B", "Year": 2001,
                                      "Species": "Crassostrea gigas", "Coordinates": "", "Habitat": "Aquaculture",
                                      "notes": "A note"})
    _trip(s1, SED_COLS, "N2O", "Ctrl", 0.01, 0.001, 3), _trip(s1, SED_COLS, "N2O", "Oys", -0.02, 0.01, 3)
    _trip(s1, SED_COLS, "CH4", "ctrl", 3.3, 1, 10.5)
    s2 = meta("Gamma, 2002 - IPT", n2_method="IPT")  # an IPT companion row: no metadata at all
    _trip(s2, SED_COLS, "N2", "Ctrl", 69.8, 28.7, 15), _trip(s2, SED_COLS, "N2", "Oys", 35.5, 26.9, 15)
    s3 = meta("Delta, 2003", **{"Publication title": "T Delta", "Authors": "Delta, D", "Journal": "J D", "Year": 2003,
                                "Species": "Crassostrea virginica", "Coordinates": "40.0, -73.0", "Habitat": "Mesocosm",
                                "n2_method": "acetylene", "notes": POT})
    _trip(s3, SED_COLS, "N2", "Ctrl", 3964.48, 1954.65, 116), _trip(s3, SED_COLS, "N2", "Oys", 4199.11, 2388.87, 348)
    _trip(s3, SED_COLS, "NH4", "ctrl", -91.48, 323.89, 120), _trip(s3, SED_COLS, "NH4", "oys", -47.55, 344.77, 360)
    _trip(s3, SED_COLS, "no3", "ctrl", 5, 1, 4)
    s4 = meta("Epsilon et al., 2004", **{"Publication title": "T Eps", "Authors": "Eps, E", "Journal": "J E", "Year": 2004,
                                         "Species": "Crassostrea gigas", "Coordinates": "34.5, -76.25", "Habitat": "Reef"})
    _trip(s4, SED_COLS, "si", "ctrl", 0.5, 0.1, 3), _trip(s4, SED_COLS, "si", "oys", 3, 0.5, 3)
    _trip(s4, SED_COLS, "P", "Ctrl", 2, 1, 4), _trip(s4, SED_COLS, "P", "Oys", 8, 2, 4)
    return [s0, s1, s2, s3, s4]


def _oy_rows() -> list[dict]:
    meta = lambda name, **kw: _row(OY_COLS, abbreviated_name=name, **kw)  # noqa: E731
    o0 = meta("Mao et al. 2006", **{"Publication title": "T Mao", "Authors": "Mao, Y", "Journal": "AQUA", "Year": 2006,
                                    "Species": "Crassostrea gigas", "Location": "38.48, 120.62"})
    _otrip(o0, "nh4", 6.5, 4.1, 30), _otrip(o0, "po4", 0.87, 0.5, 30)
    o1 = meta("Caffrey et al. 2016", **{"Publication title": "T Caffrey ", "Authors": "Caffrey, J", "Journal": "MPB", "Year": 2016,
                                        "Species": "Crassostrea gigas", "Location": "38.1, -122.9"})
    _otrip(o1, "nh4", 3.78, 1, 4), _otrip(o1, "nox", 0.19, 0.1, 4), _otrip(o1, "no2", 0.07, 0.01, 4)
    o2 = meta("Caffrey et al. 2016", **{"Publication title": "T Caffrey", "Authors": "Caffrey, J", "Journal": "MPB", "Year": 2016,
                                        "Species": "Crassostrea virginica", "Location": "30.4, -87.1",
                                        "Denitrification Method": "IPT"})
    _otrip(o2, "n2", 0.98, 0.2, 6), _otrip(o2, "n2o", 0.000352, 0.0001, 6)
    return [o0, o1, o2]


SED_ROWS, OY_ROWS = _sed_rows(), _oy_rows()
# expected: sediment facts 4 + 3 + 2 + 5 + 4 = 18, oyster 2 + 3 + 2 = 7 -> 25; samples 8; references 7 (5 sediment names,
# Mao, Caffrey); locations 7 (sediment: s0 = s4, s1, s2, s3; oyster 3); methods 24; citations 7, one without a title
# (the IPT row).


@pytest.fixture(scope="module", autouse=True)
def _small():
    mp = pytest.MonkeyPatch()
    mp.setattr(ry, "SED_N_ROWS", len(SED_ROWS))
    mp.setattr(ry, "OY_N_ROWS", len(OY_ROWS))
    mp.setattr(ry, "DECLARED_NEGATIVE_SD", frozenset())
    yield
    mp.undo()


def _build(sed=None, oy=None, neg=None):
    sed_df = pd.DataFrame(sed if sed is not None else SED_ROWS, columns=SED_COLS, dtype=object)
    oy_df = pd.DataFrame(oy if oy is not None else OY_ROWS, columns=OY_COLS, dtype=object)
    if neg is not None:
        import unittest.mock as um

        with um.patch.object(ry, "DECLARED_NEGATIVE_SD", neg):
            return ry.build_tables(sed_df, oy_df, ACCESS)
    return ry.build_tables(sed_df, oy_df, ACCESS)


def _s(i: int, **kw) -> list[dict]:
    rows = [dict(r) for r in SED_ROWS]
    rows[i].update(kw)
    return rows


def _o(i: int, **kw) -> list[dict]:
    rows = [dict(r) for r in OY_ROWS]
    rows[i].update(kw)
    return rows


@pytest.fixture(scope="module")
def built():
    return _build()


def test_counts(built) -> None:
    tables, skips, rep = built
    assert skips == []
    assert rep["facts"] == 25 == rep["mean_cells"] == len(tables["fact"])
    assert rep["facts_by_sheet_arm"] == {"oyster fluxes / oyster": 7, "sediment fluxes / ctrl": 10, "sediment fluxes / oys": 8}
    assert rep["facts_by_attribute"]["ray_sediment_n2"] == 6 and rep["facts_by_attribute"]["ray_oyster_nh4"] == 2
    assert len(tables["sample"]) == 8 and len(tables["location"]) == 7 and len(tables["method"]) == 24
    assert len(tables["reference"]) == 7 == len(tables["citation"]) and rep["citations_without_title"] == 1
    assert len(tables["study_screening"]) == 0
    validate_tables(add_derived_labels(tables))


def test_source_row(built) -> None:
    src = built[0]["source"].iloc[0]
    assert src.version == "v1 (2020-10-08)" and src.license == "CC BY 4.0" and src.access_date == ACCESS
    assert src.doi == "10.6084/m9.figshare.12488753.v1"


def test_types_units_provenance(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    t = lambda k: tuple(f.loc[k, ["measurement_type", "subfield", "units_raw"]])  # noqa: E731
    assert t("sed:row0:ray_sediment_n2:ctrl") == ("C4", "denitrification", S_UNIT)
    assert t("sed:row0:ray_sediment_nh4:ctrl") == ("C3", "ammonium_flux", S_UNIT)
    assert t("sed:row1:ray_sediment_n2o:oys") == ("C8", "n2o_flux", S_UNIT)
    assert t("sed:row4:ray_sediment_si:oys") == ("C3", "silicate_flux", S_UNIT)
    assert t("sed:row4:ray_sediment_po4:ctrl") == ("C3", "phosphate_flux", S_UNIT)
    assert t("sed:row3:ray_sediment_no3:ctrl") == ("C3", "nitrate_flux", S_UNIT)
    assert t("oy:row0:ray_oyster_po4:oyster") == ("C3", "phosphate_flux", O_UNIT)
    assert t("oy:row1:ray_oyster_nox:oyster") == ("C3", "nitrate_nitrite_flux", O_UNIT)
    assert t("oy:row1:ray_oyster_no2:oyster") == ("C3", "nitrite_flux", O_UNIT)
    assert t("oy:row2:ray_oyster_n2o:oyster") == ("C8", "n2o_flux", O_UNIT)  # the file's umol (not nmol)
    assert f.loc["oy:row2:ray_oyster_n2o:oyster", "value"] == 0.000352
    v = f.loc["sed:row0:ray_sediment_n2:oys"]
    assert (v.value, v.sd, v.n) == (30, 4, 5) and pd.isna(v.se)
    assert f.loc["sed:row0:ray_sediment_n2:oys", "source_row_ref"] == (
        "oyster_meta - excel for figshare.xlsx[sediment fluxes]#data_row=0:ray_sediment_n2:oys")
    assert f.loc["oy:row2:ray_oyster_n2:oyster", "source_row_ref"].endswith("[oyster fluxes]#data_row=2:ray_oyster_n2:oyster")
    assert f.loc["sed:row3:ray_sediment_nh4:ctrl", "value"] == -91.48  # negative flux carried


def test_flags(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    fl = lambda k: f.loc[k, "flags"]  # noqa: E731
    assert fl("sed:row0:ray_sediment_n2:ctrl") == []
    assert fl("sed:row0:ray_sediment_nh4:oys") == ["zero_value"]
    assert fl("sed:row1:ray_sediment_n2o:ctrl") == ["coords_missing"]
    assert fl("sed:row2:ray_sediment_n2:ctrl") == ["coords_missing"]
    assert fl("sed:row3:ray_sediment_n2:ctrl") == ["potential_rate"]
    assert fl("sed:row3:ray_sediment_nh4:oys") == ["potential_rate"]
    assert fl("sed:row3:ray_sediment_no3:ctrl") == []  # the note does not name nitrate
    assert rep["flag_counts"] == {"coords_missing": 5, "zero_value": 1, "potential_rate": 4}
    assert rep["facts_with_non_integer_n"] == 1 and rep["rows_without_coordinates"] == 2 and rep["rows_with_note"] == 2


def test_non_integer_n(built) -> None:
    tables, _, _ = built
    f = tables["fact"].set_index("fact_key")
    assert pd.isna(f.loc["sed:row1:ray_sediment_ch4:ctrl", "n"])
    m = tables["method"].set_index("method_key")
    p = json.loads(m.loc[f.loc["sed:row1:ray_sediment_ch4:ctrl", "method_key"], "params"])
    assert p["n_reported"] == 10.5 and p["note"] == "A note"
    assert f.n.dropna().map(float.is_integer if False else (lambda x: x == int(x))).all()


def test_methods(built) -> None:
    tables, _, _ = built
    m = tables["method"].set_index("method_key")
    k = "sed|ray_sediment_n2|control|n2_ar|"
    assert m.loc[k, "technique"] == "n2_ar" and m.loc[k, "method_status"] == "matched"
    assert m.loc[k, "experimental_status"] == "control" and pd.isna(m.loc[k, "incubation_type"])
    assert m.loc["sed|ray_sediment_n2|oyster|n2_ar|", "experimental_status"] == "oyster"
    assert m.loc["sed|ray_sediment_n2|control|IPT|", "technique"] == "IPT"
    k = "sed|ray_sediment_nh4|control||"  # solutes other than N2 carry no technique
    assert pd.isna(m.loc[k, "technique"]) and m.loc[k, "method_status"] == "method_id_null"
    kp = "sed|ray_sediment_n2|control|acetylene|row3"
    p = json.loads(m.loc[kp, "params"])
    assert p["note"].startswith(ry.POTENTIAL_NOTE_START) and "potential_rate_note" in p
    assert p["mean_column"] == "Mean N2 Ctrl" and p["sd_note"] and p["sheet"] == "sediment fluxes"
    ko = "oy|ray_oyster_n2|oyster (the oysters' own flux; no control)|IPT|"
    assert m.loc[ko, "technique"] == "IPT"
    assert m.loc["oy|ray_oyster_nh4|oyster (the oysters' own flux; no control)||", "experimental_status"].startswith("oyster")
    f = tables["fact"].set_index("fact_key")
    assert f.loc["oy:row0:ray_oyster_nh4:oyster", "method_key"] == f.loc["oy:row1:ray_oyster_nh4:oyster", "method_key"]


def test_locations_samples_references(built) -> None:
    tables, _, rep = built
    loc = tables["location"].set_index("location_key")
    assert set(loc.index) == {
        "34.5|-76.25|Reef", "nocoords|Beta et al., 2001|Aquaculture", "nocoords|Gamma, 2002 - IPT|",
        "40.0|-73.0|Mesocosm", "38.48|120.62|", "38.1|-122.9|", "30.4|-87.1|",
    }
    assert loc.loc["34.5|-76.25|Reef", "latitude"] == 34.5 and loc.loc["38.48|120.62|", "longitude"] == 120.62
    assert pd.isna(loc.loc["nocoords|Beta et al., 2001|Aquaculture", "latitude"])
    f = tables["fact"].set_index("fact_key")
    assert f.loc["sed:row0:ray_sediment_n2:ctrl", "location_key"] == f.loc["sed:row4:ray_sediment_po4:ctrl", "location_key"]
    s = tables["sample"].set_index("sample_key")
    assert s.loc["sed:row0", "taxon"] == "Crassostrea virginica" and s.loc["sed:row0", "taxon_rank"] == "species"
    assert pd.isna(s.loc["sed:row2", "taxon"]) and pd.isna(s.loc["sed:row2", "taxon_rank"])
    assert s.sampling_year.isna().all()  # the Year is the publication year
    c = tables["citation"].set_index("bibliography_id")
    assert c.loc["Alpha et al., 2000", "year"] == 2000 and c.loc["Alpha et al., 2000", "authors"] == "Alpha, A"
    assert c.loc["Alpha et al., 2000", "title"] == "T Alpha"
    assert c.loc["Caffrey et al. 2016", "title"] == "T Caffrey"  # two rows, one reference; whitespace-normalised
    assert pd.isna(c.loc["Gamma, 2002 - IPT", "title"]) and pd.isna(c.loc["Gamma, 2002 - IPT", "year"])
    assert f.loc["oy:row1:ray_oyster_nh4:oyster", "reference_key"] == f.loc["oy:row2:ray_oyster_n2:oyster", "reference_key"]
    assert rep["references_sharing_a_title"] == {}


def test_labels(built) -> None:
    lab = add_derived_labels(built[0])["location"].set_index("habitat_type")
    assert lab.loc["Reef", "habitat"] == "Shellfish reef / bed" and lab.loc["Reef", "bottom_type"] == "biogenic"
    assert pd.isna(lab.loc["Aquaculture", "habitat"]) and pd.isna(lab.loc["Mesocosm", "habitat"])
    no_habitat = add_derived_labels(built[0])["location"]
    assert no_habitat[no_habitat.habitat_type.isna()].habitat.isna().all()  # oyster sheet and the IPT row


# ---- helpers


def test_triples_header_check() -> None:
    df = pd.DataFrame([SED_ROWS[0]], columns=SED_COLS, dtype=object)
    assert len(ry.triples(df, 9, len(SED_COLS) - 1, ry.SED_SHEET)) == 20
    bad = df.rename(columns={"SD N2 Ctrl": "SE N2 Ctrl"})
    with pytest.raises(ContractError, match="mean / SD / n triple"):
        ry.triples(bad, 9, len(SED_COLS) - 1, ry.SED_SHEET)
    with pytest.raises(ContractError, match="multiple of 3"):
        ry.triples(df, 9, len(SED_COLS) - 2, ry.SED_SHEET)
    bad = df.rename(columns={"Mean N2 Ctrl": "Mean N3 Ctrl", "SD N2 Ctrl": "SD N3 Ctrl", "n N2 Ctrl": "n N3 Ctrl"})
    with pytest.raises(ContractError, match="unknown solute"):
        ry.triples(bad, 9, len(SED_COLS) - 1, ry.SED_SHEET)


# ---- fail-loud checks


def test_mean_without_sd_or_n_raises() -> None:
    with pytest.raises(ContractError, match="mean without SD and n"):
        _build(sed=_s(0, **{"SD N2 Ctrl": ""}))
    with pytest.raises(ContractError, match="mean without SD and n"):
        _build(oy=_o(0, nh4_flux_n=""))


def test_sd_or_n_without_mean_raises() -> None:
    with pytest.raises(ContractError, match="SD or n without a mean"):
        _build(sed=_s(0, **{"Mean N2 Ctrl": ""}))


def test_non_numeric_value_raises() -> None:
    for bad in ("12", True, float("nan") if False else "x"):
        with pytest.raises(ContractError, match="non-numeric"):
            _build(sed=_s(0, **{"Mean N2 Ctrl": bad}))


def test_row_without_any_mean_raises() -> None:
    rows = _s(4, **{})
    for k in list(rows[4]):
        if k.lower().startswith(("mean ", "sd ", "n ")):
            rows[4][k] = ""
    with pytest.raises(ContractError, match="has no mean at all"):
        _build(sed=rows)


def test_undeclared_negative_sd_raises() -> None:
    with pytest.raises(ContractError, match="undeclared negative SD"):
        _build(sed=_s(0, **{"SD N2 Ctrl": -1}))


def test_declared_negative_sd_is_kept_and_flagged() -> None:
    t, _, rep = _build(oy=_o(2, n2o_flux_SD=-0.0001), neg=frozenset({(ry.OY_SHEET, 2, "n2o")}))
    f = t["fact"].set_index("fact_key")
    assert f.loc["oy:row2:ray_oyster_n2o:oyster", "sd"] == -0.0001
    assert f.loc["oy:row2:ray_oyster_n2o:oyster", "flags"] == ["negative_sd"]
    assert rep["flag_counts"]["negative_sd"] == 1


def test_declared_negative_sd_that_is_absent_raises() -> None:
    with pytest.raises(ContractError, match="not found"):
        _build(neg=frozenset({(ry.OY_SHEET, 2, "n2o")}))


def test_non_positive_n_raises() -> None:
    with pytest.raises(ContractError, match="non-positive n"):
        _build(sed=_s(0, **{"n N2 Ctrl": 0}))


def test_unknown_habitat_raises() -> None:
    with pytest.raises(ContractError, match="unknown Habitat"):
        _build(sed=_s(0, Habitat="Swamp"))


@pytest.mark.parametrize("bad", ["34.5 -76.25", "34.5,-76.25", "north, west", "95.0, 10.0", "10.0, 190.0"])
def test_bad_coordinates_raise(bad) -> None:
    with pytest.raises(ContractError, match="coordinates"):
        _build(sed=_s(0, Coordinates=bad))


def test_blank_name_raises() -> None:
    with pytest.raises(ContractError, match="blank abbreviated_name"):
        _build(sed=_s(0, abbreviated_name=""))


def test_literal_na_in_text_raises() -> None:
    with pytest.raises(ContractError, match="literal missing-value"):
        _build(sed=_s(0, Species="NA"))


def test_same_label_with_different_title_raises() -> None:
    with pytest.raises(ContractError, match="different title"):
        _build(oy=_o(2, **{"Publication title": "Another"}))


def test_year_must_be_numeric() -> None:
    with pytest.raises(ContractError, match="non-numeric"):
        _build(sed=_s(0, Year="2000a"))


def test_column_and_row_checks() -> None:
    with pytest.raises(ContractError, match="unexpected columns"):
        ry.build_tables(pd.DataFrame(SED_ROWS, columns=SED_COLS, dtype=object).rename(columns={"Species": "Taxon"}),
                        pd.DataFrame(OY_ROWS, columns=OY_COLS, dtype=object), ACCESS)
    with pytest.raises(ContractError, match="unexpected columns"):
        ry.build_tables(pd.DataFrame(SED_ROWS, columns=SED_COLS, dtype=object),
                        pd.DataFrame(OY_ROWS, columns=OY_COLS, dtype=object).rename(columns={"Location": "Coordinates"}), ACCESS)
    with pytest.raises(ContractError, match="row counts"):
        _build(sed=SED_ROWS[:4])


def test_sheet_checks(tmp_path) -> None:
    p = tmp_path / "x.xlsx"
    with pd.ExcelWriter(p) as w:  # type: ignore[abstract]
        pd.DataFrame({"a": [1]}).to_excel(w, sheet_name="Overview", index=False)
    with pytest.raises(ContractError, match="sheets"):
        ry.read_raw(p)
    with pd.ExcelWriter(p) as w:  # type: ignore[abstract]
        pd.DataFrame({"a": [1]}).to_excel(w, sheet_name="Overview", index=False)
        pd.DataFrame({"a": [1]}).to_excel(w, sheet_name=ry.SED_SHEET, index=False)
        pd.DataFrame({"a": [1]}).to_excel(w, sheet_name=ry.OY_SHEET, index=False)
    with pytest.raises(ContractError, match="no longer empty"):
        ry.read_raw(p)


def test_duplicate_rows_are_flagged() -> None:
    sed = [dict(r) for r in SED_ROWS]
    sed.append(dict(SED_ROWS[0]))
    sed_df = pd.DataFrame(sed, columns=SED_COLS, dtype=object)
    oy_df = pd.DataFrame(OY_ROWS, columns=OY_COLS, dtype=object)
    import unittest.mock as um

    with um.patch.object(ry, "SED_N_ROWS", 6):
        t, _, rep = ry.build_tables(sed_df, oy_df, ACCESS)
    f = t["fact"].set_index("fact_key")
    assert "duplicate_source_row" in f.loc["sed:row0:ray_sediment_n2:ctrl", "flags"]
    assert "duplicate_source_row" in f.loc["sed:row5:ray_sediment_n2:ctrl", "flags"]
    assert rep["flag_counts"]["duplicate_source_row"] == 8


# ---- build + determinism


def test_roundtrip_global_build_and_determinism(built, tmp_path) -> None:
    tables, _, _ = built
    write_extracted(tables, tmp_path / "ex")
    back = read_extracted(tmp_path / "ex")
    assert len(back["fact"]) == 25
    g = assign_global_keys([back])
    assert list(g["fact"].measurement_id) == list(range(1, 26))
    out1, out2 = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    build_compilations([tmp_path / "ex"], out1)
    build_compilations([tmp_path / "ex"], out2)
    q = (
        "SELECT f.value, f.sd, f.n, f.attribute, f.units_raw, m.experimental_status, l.habitat "
        "FROM measurement_fact f JOIN method_dim m USING (method_id) JOIN location_dim l USING (location_id) "
        "ORDER BY f.measurement_id"
    )
    r1 = duckdb.connect(str(out1), read_only=True).execute(q).fetchall()
    r2 = duckdb.connect(str(out2), read_only=True).execute(q).fetchall()
    assert r1 == r2 and len(r1) == 25


def test_build_is_deterministic() -> None:
    a, _, ra = _build()
    b, _, rb = _build()
    for k in a:
        pd.testing.assert_frame_equal(a[k], b[k])
    assert ra == rb


def test_unregistered_flag_raises_at_validation(built) -> None:
    tables, _, _ = built
    bad = {k: v.copy() for k, v in tables.items()}
    flags = list(bad["fact"]["flags"])
    flags[0] = list(flags[0]) + ["made_up_flag"]
    bad["fact"]["flags"] = flags
    with pytest.raises(ContractError, match="made_up_flag"):
        write_extracted(bad, Path("/nonexistent-never-written"))
