"""Unit tests (rung 1) for the rosentreter-ghg extract.py.

Hand-built fixture (a Metadata sheet and six data sheets, 12 rows); every expected number is derived
by inspection in the comments. The fixture uses two of the real extract's declared piece exceptions
(Miyajima, PANGAEA 355322); the declared set is patched to those two.
"""

from __future__ import annotations

import dataclasses
import importlib.util
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

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "rosentreter-ghg" / "extract.py"
_spec = importlib.util.spec_from_file_location("rosentreter_extract", _EXTRACT)
assert _spec and _spec.loader
rs = importlib.util.module_from_spec(_spec)
sys.modules["rosentreter_extract"] = rs  # dataclasses (with postponed annotations) look the module up here
_spec.loader.exec_module(rs)

REAL_SHEETS = rs.SHEETS
REAL_DECLARED = dict(rs.DECLARED_PIECES)
MIYA = "Miyajima et al. (2009); T. Miyajima (pers. comm.)"
PANG = "CH4 data available at PANGAEA, DOI:10.1594/PANGAEA.355322"
ACCESS = "2026-11-15"

META = pd.DataFrame(
    [
        [None, None], ["Estuaries", None], ["column name", "meaning"], ["site_name", "Name"],
        ["flux (unit)", "GHG flux"], ["lat", "Latitude"], ["long", "Longitude"], ["region", "RECCAP2 region"],
        ["type", "Estuary type (1= Tidal systems and deltas, 2= Lagoons, 3= Fjords)"],
        ["reference", "refs"], ["full_citation", "full"], [None, None],
        ["Coastal vegetation", None], ["column name", "meaning"], ["site_name", "Name"],
        ["flux (unit)", "GHG flux"], ["lat", "Latitude"], ["long", "Longitude"], ["region", "RECCAP2 region"],
        ["type", "Coastal vegetation type (1= Mangroves, 2= Saltmarshes, 3= Seagrasses)"],
        ["reference", "refs"], ["full_citation", "full"],
    ],
    dtype=object,
).fillna("")

SMITH_FC = "Smith, A. et al. Title one. J A 1, 1–2 (2000)."
DOE_FC = "Doe, D. & Roe, R. T. A. J B 2, 3–4 (2001); Poe, P. T. B. J C 3, 5–6 (2003)."
MIYA_FC = "Miyajima, T. et al. Export. J. Geophys. Res. 114 (2009)."
PANG_FC = "Schröder, Hartwig; Damm, Ellen (2006): Methane. PANGAEA,\xa0https://doi.org/10.1594/PANGAEA.355322,"
BARR_FC = "Barr, J. et al. A. J D 1 (2010); Barr, J. et al. B. J D 2 (2012)."
OIK_FC = "Oikawa, P. (2020), AmeriFlux. https://doi.org/10.17190/AMF/1543381 "
FORB_FC = "Forbrich, I. et al. T. J F 5 (2018)."
SMITH2_FC = "Smith, A. A different paper. J Z 9 (2000)."


def _r(sheet: str, site, flux, lat, lon, typ, ref, fc, region=1):
    sh = next(s for s in REAL_SHEETS if s.name == sheet)
    return {"site_name": site, sh.flux_header: flux, "lat": lat, "long": lon, "region": region, "type": typ,
            "reference": ref, "full_citation": fc}


ROWS = {
    "Estuaries_CO2": [
        # 0: whole-degree coordinates (int cells), site name stripped; Smith 2000 (one paper)
        _r("Estuaries_CO2", "Site A ", 0.55, 35, -77, 1, "Smith et al. (2000)", SMITH_FC),
        # 1: zero flux; Lagoons; two papers named in reference and split on "; "
        _r("Estuaries_CO2", "Site B", 0, 34.6, -77.4, 2, "Doe & Roe (2001); Poe (2003)", DOE_FC),
        # 2: Fjords; negative flux (real, not flagged); pers. comm. part with no citation (declared split of 1)
        _r("Estuaries_CO2", "Site C", -1.5, 10.5, 20.25, 3, MIYA, MIYA_FC),
    ],
    "Estuaries_CH4": [
        # 0: same site and same reference/citation text as CO2 row 0 -> one location, one reference
        _r("Estuaries_CH4", "Site A", 19.5, 35, -77, 1, "Smith et al. (2000)", SMITH_FC),
        # 1: PANGAEA citation whose authors are joined by ";" (declared whole), DOI in the text
        _r("Estuaries_CH4", "Site D", 0.16, 38.14, -122.88, 1, PANG, PANG_FC),
    ],
    "Estuaries_N2O": [
        # 0, 1: one site; "(2010,2012)" counts two papers, the split gives two pieces
        _r("Estuaries_N2O", "Site E", 0.515068, 41.576, -70.531, 1, "Barr et al. (2010,2012)", BARR_FC),
        _r("Estuaries_N2O", "Site E", 0.2, 41.576, -70.531, 1, "Barr et al. (2010,2012)", BARR_FC),
    ],
    "Coastal_vegetation_CO2": [
        # 0: mangroves; pers.comm. in the reference, a DOI in the (published) citation
        _r("Coastal_vegetation_CO2", "Veg A", -980.6, 25.3646, -81.0779, 1, "Oikawa et al. (pers.comm.)", OIK_FC),
        # 1: saltmarshes
        _r("Coastal_vegetation_CO2", "Veg B", -179.4, 42.7423, -70.8301, 2, "Forbrich et al. (2018)", FORB_FC),
    ],
    "Coastal_vegetation_CH4": [
        # 0: seagrasses; the same author-year string as Smith 2000 but a different paper
        _r("Coastal_vegetation_CH4", "Veg C", 24.5, 20.84, -90.38, 3, "Smith et al. (2000)", SMITH2_FC),
        # 1: same site/reference as vegetation CO2 row 0; zero flux
        _r("Coastal_vegetation_CH4", "Veg A", 0, 25.3646, -81.0779, 1, "Oikawa et al. (pers.comm.)", OIK_FC),
    ],
    "Coastal_vegetation_N2O": [
        _r("Coastal_vegetation_N2O", "Veg D", 1.667, 17.968, -67.043, 1, "Forbrich et al. (2018)", FORB_FC),
    ],
}
# expected: 12 rows/facts; 9 locations (estuary: A(CO2+CH4), B, C, D, E(N2O x2) = 5; vegetation: A(CO2+CH4), B, C, D = 4);
# 8 references (Smith, Doe+Poe, Miyajima, PANGAEA doi, Barr+Barr, Oikawa doi, Forbrich, Smith-other);
# citations 1+2+1+1+2+1+1+1 = 10; 6 methods; 12 samples.


@pytest.fixture(scope="module", autouse=True)
def _small_sheets():
    mp = pytest.MonkeyPatch()
    new = tuple(dataclasses.replace(s, n_rows=len(ROWS[s.name])) for s in REAL_SHEETS)
    mp.setattr(rs, "SHEETS", new)
    mp.setattr(rs, "SHEET_BY_NAME", {s.name: s for s in new})
    mp.setattr(rs, "DECLARED_PIECES", {MIYA: REAL_DECLARED[MIYA], PANG: REAL_DECLARED[PANG]})
    yield
    mp.undo()


def _frames(rows=ROWS) -> dict[str, pd.DataFrame]:
    out = {}
    for s in REAL_SHEETS:
        cols = ["site_name", s.flux_header, "lat", "long", "region", "type", "reference", "full_citation"]
        out[s.name] = pd.DataFrame(rows[s.name], columns=cols, dtype=object)
    return out


def _build(rows=None, meta=None):
    return rs.build_tables(_frames(rows or ROWS), META if meta is None else meta, ACCESS)


def _with(sheet: str, i: int, **kw) -> dict:
    rows = {k: [dict(r) for r in v] for k, v in ROWS.items()}
    flux = next(s for s in REAL_SHEETS if s.name == sheet).flux_header
    for k, v in kw.items():
        rows[sheet][i][flux if k == "flux" else k] = v
    return rows


@pytest.fixture(scope="module")
def built():
    return _build()


def test_counts(built) -> None:
    tables, skips, rep = built
    assert skips == [] and rep["rows_excluded"] == 0
    assert rep["source_rows"] == 12 == len(tables["fact"]) == len(tables["sample"])
    assert rep["facts_by_attribute"] == {
        "ghg_ch4_estuary": 2, "ghg_ch4_vegetation": 2, "ghg_co2_estuary": 3, "ghg_co2_vegetation": 2,
        "ghg_n2o_estuary": 2, "ghg_n2o_vegetation": 1,
    }
    assert len(tables["location"]) == 9
    assert len(tables["method"]) == 6
    assert len(tables["reference"]) == 8 and len(tables["citation"]) == 10
    assert len(tables["study_screening"]) == 0
    validate_tables(add_derived_labels(tables))


def test_source_row(built) -> None:
    src = built[0]["source"].iloc[0]
    assert src.version == "v1 (2023-05-05)" and src.license == "CC BY 4.0"
    assert src.doi == "10.6084/m9.figshare.22351267.v1" and src.access_date == ACCESS


def test_types_units_provenance(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    by = f.drop_duplicates("attribute").set_index("attribute")[["measurement_type", "subfield"]]
    assert tuple(by.loc["ghg_co2_estuary"]) == ("C8", "co2_flux")
    assert tuple(by.loc["ghg_ch4_vegetation"]) == ("C8", "ch4_flux")
    assert tuple(by.loc["ghg_n2o_estuary"]) == ("C8", "n2o_flux")
    assert f.loc["Estuaries_CO2:row0:ghg_co2_estuary", "units_raw"] == "g CO2 m2 d-1"
    assert f.loc["Coastal_vegetation_CO2:row0:ghg_co2_vegetation", "units_raw"] == "g C m2 yr-1"
    assert f.loc["Coastal_vegetation_CH4:row0:ghg_ch4_vegetation", "units_raw"] == "mg CH4 m2 d-1"
    assert f.loc["Estuaries_CO2:row2:ghg_co2_estuary", "value"] == -1.5  # negative carried
    assert f.loc["Estuaries_N2O:row0:ghg_n2o_estuary", "value"] == 0.515068
    assert f.loc["Estuaries_CH4:row1:ghg_ch4_estuary", "source_row_ref"] == (
        "database_coastalGHG_Rosentreter et al._figshare.xlsx[Estuaries_CH4]#data_row=1:ghg_ch4_estuary"
    )
    assert f[["sd", "se"]].isna().all().all() and f.n.isna().all()


def test_flags(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    fl = lambda k: f.loc[k, "flags"]  # noqa: E731
    assert fl("Estuaries_CO2:row0:ghg_co2_estuary") == ["coords_integer_degree", "habitat_from_scope"]
    assert fl("Estuaries_CH4:row0:ghg_ch4_estuary") == ["coords_integer_degree", "habitat_from_scope"]
    assert fl("Estuaries_CO2:row1:ghg_co2_estuary") == ["habitat_from_scope", "zero_value"]
    assert fl("Estuaries_CO2:row2:ghg_co2_estuary") == ["habitat_from_scope", "pers_comm"]
    assert fl("Coastal_vegetation_CO2:row0:ghg_co2_vegetation") == ["pers_comm"]
    assert fl("Coastal_vegetation_CH4:row1:ghg_ch4_vegetation") == ["pers_comm", "zero_value"]
    assert fl("Coastal_vegetation_CO2:row1:ghg_co2_vegetation") == []
    assert rep["flag_counts"] == {"coords_integer_degree": 2, "habitat_from_scope": 7, "pers_comm": 3, "zero_value": 2}
    assert rep["value_summary_by_attribute"]["ghg_co2_estuary"] == {"min": -1.5, "max": 0.55, "negative": 1, "zero": 1}


def test_locations_and_labels(built) -> None:
    tables, _, rep = built
    loc = tables["location"].set_index("location_key")
    k = "35.0|-77.0|Site A|water column|Tidal systems and deltas"  # stripped name; ints -> floats; CO2 + CH4 share it
    assert loc.loc[k, "site_name"] == "Site A" and loc.loc[k, "latitude"] == 35.0
    assert "25.3646|-81.0779|Veg A|Mangroves|" in loc.index  # vegetation: no geomorphic_setting
    assert rep["locations_by_habitat_type"] == {"Mangroves": 2, "Saltmarshes": 1, "Seagrasses": 1, "water column": 5}
    lab = add_derived_labels(tables)["location"]
    est = lab[lab.habitat_type == "water column"]
    assert set(est.habitat) == {"Water column"} and est.bottom_type.isna().all()
    got = est.groupby("geomorphic_setting").setting.agg(lambda x: set(x.fillna("-")))
    assert got["Lagoons"] == {"coastal lagoon"} and got["Fjords"] == {"fjord"}
    assert got["Tidal systems and deltas"] == {"-"}  # two settings in one label: blank, not guessed
    veg = lab[lab.habitat_type != "water column"].groupby("habitat_type").habitat.first()
    assert veg.to_dict() == {"Mangroves": "Mangrove forest", "Saltmarshes": "Tidal marsh", "Seagrasses": "Seagrass meadow"}
    assert lab[lab.habitat_type != "water column"].setting.isna().all()


def test_methods_and_samples(built) -> None:
    tables, _, _ = built
    m = tables["method"].set_index("method_key")
    assert (m.method_status == "method_id_null").all() and m.technique.isna().all()
    assert '"interface": "water-air exchange over estuary water"' in m.loc["ghg_co2_estuary", "params"]
    assert '"interface": "ecosystem-atmosphere exchange over vegetated coast"' in m.loc["ghg_n2o_vegetation", "params"]
    assert '"flux_column": "flux (g C m2 yr-1)"' in m.loc["ghg_co2_vegetation", "params"]
    s = tables["sample"]
    assert s.sampling_year.isna().all() and s.depth_min_cm.isna().all() and s.taxon.isna().all()
    assert set(s.sample_key) == {f"{sh}:row{i}" for sh, rows in ROWS.items() for i in range(len(rows))}


def test_references_and_citations(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    rk = lambda k: f.loc[k, "reference_key"]  # noqa: E731
    r_smith = "ref:" + SMITH_FC
    assert rk("Estuaries_CO2:row0:ghg_co2_estuary") == rk("Estuaries_CH4:row0:ghg_ch4_estuary") == r_smith
    # the same author-year string, a different paper -> a different reference
    assert rk("Coastal_vegetation_CH4:row0:ghg_ch4_vegetation") == "ref:" + SMITH2_FC
    doe_poe = "ref:Doe, D. & Roe, R. T. A. J B 2, 3–4 (2001)+ref:Poe, P. T. B. J C 3, 5–6 (2003)."
    assert rk("Estuaries_CO2:row1:ghg_co2_estuary") == doe_poe
    assert rk("Estuaries_CO2:row2:ghg_co2_estuary") == "ref:" + MIYA_FC  # pers. comm. part has no citation
    assert rk("Estuaries_CH4:row1:ghg_ch4_estuary") == "doi:10.1594/pangaea.355322"  # whole text, DOI lower-cased
    assert rk("Coastal_vegetation_CO2:row0:ghg_co2_vegetation") == rk(
        "Coastal_vegetation_CH4:row1:ghg_ch4_vegetation") == "doi:10.17190/amf/1543381"
    assert rk("Coastal_vegetation_CO2:row1:ghg_co2_vegetation") == rk(
        "Coastal_vegetation_N2O:row0:ghg_n2o_vegetation") == "ref:" + FORB_FC
    assert rk("Estuaries_N2O:row0:ghg_n2o_estuary") == rk("Estuaries_N2O:row1:ghg_n2o_estuary")
    assert "+" in rk("Estuaries_N2O:row0:ghg_n2o_estuary")
    c = tables["citation"]
    assert c.groupby("reference_key").size().loc[doe_poe] == 2 and c.groupby("reference_key").size().loc[r_smith] == 1
    cd = c.set_index("citation_key")
    assert cd.loc[doe_poe + "|0", "title"].startswith("Doe, D.") and cd.loc[doe_poe + "|1", "title"].startswith("Poe, P.")
    assert cd.loc[doe_poe + "|0", "year"] == 2001 and cd.loc[doe_poe + "|1", "year"] == 2003
    assert cd.loc[doe_poe + "|0", "bibliography_id"] == "Doe & Roe (2001); Poe (2003)"
    assert cd.loc["doi:10.1594/pangaea.355322|0", "doi"] == "10.1594/pangaea.355322"
    assert cd.loc["doi:10.1594/pangaea.355322|0", "year"] == 2006
    assert cd.loc["doi:10.17190/amf/1543381|0", "title"].endswith("1543381")  # whitespace-normalised, trailing space gone
    assert pd.isna(cd.loc[r_smith + "|0", "doi"]) and cd.loc[r_smith + "|0", "year"] == 2000
    assert rep["distinct_reference_strings"] == 7  # "Smith et al. (2000)" stands for two different papers
    assert rep["reference_strings_with_several_full_citation_texts"] == 1  # "Smith et al. (2000)" -> two papers
    assert rep["full_citation_texts_under_several_reference_strings"] == 0
    assert rep["rows_with_several_pieces"] == 3 and rep["pieces_per_row"] == {1: 9, 2: 3}
    assert rep["distinct_dois"] == 2 and rep["pieces_with_doi"] == 2
    assert rep["reference_strings_with_pers_comm"] == [MIYA, "Oikawa et al. (pers.comm.)"]


# ---- helpers


@pytest.mark.parametrize(
    "ref,n",
    [
        ("Smith et al. (2000)", 1),
        ("Smith et al. (2000); Jones (2001)", 2),
        ("Barr et al. (2010,2012)", 2),
        ("Allen et al. (2007, 2010a)", 2),
        ("Smith (pers. comm.)", 1),
        ("A (2000); B (2001,2002); C (2003)", 4),
        ("Burgos et al. (2018), Ferron et al. (2007)", 1),  # comma-joined papers are not counted: a declared case
        ("MEMENTO database, contact person: A.Kock", 1),
    ],
)
def test_count_papers(ref, n) -> None:
    assert rs.count_papers(ref) == n


def test_piece_doi() -> None:
    assert rs.piece_doi("X (2020). https://doi.org/10.1000/ABC.1,") == "10.1000/abc.1"
    assert rs.piece_doi("X (2020). doi:10.1029/2020gb006848") == "10.1029/2020gb006848"
    assert rs.piece_doi("X. DOI: 10.5/y") is None  # not a 4-9 digit registrant: no DOI taken
    assert rs.piece_doi("Fluxnet database: https://fluxnet.org/ no doi here") is None
    with pytest.raises(ContractError, match="several DOIs"):
        rs.piece_doi("doi:10.1000/a and doi:10.1000/b")


def test_decode_type_codes() -> None:
    assert rs.decode_type_codes(META) == (rs.ESTUARY_TYPES, rs.VEGETATION_TYPES)


def test_decode_type_codes_rejects_a_changed_legend() -> None:
    bad = META.copy()
    bad.iat[8, 1] = "Estuary type (1= Tidal systems and deltas, 2= Fjords, 3= Lagoons)"
    with pytest.raises(ContractError, match="differ from the expected"):
        rs.decode_type_codes(bad)
    bad = META.copy()
    bad.iat[19, 1] = "Vegetation type (1= Mangroves, 2= Saltmarshes, 3= Seagrasses)"
    with pytest.raises(ContractError, match="does not start with"):
        rs.decode_type_codes(bad)
    with pytest.raises(ContractError, match="two 'type' rows"):
        rs.decode_type_codes(META.iloc[:12])


# ---- fail-loud checks


def test_unknown_type_code_raises() -> None:
    with pytest.raises(ContractError, match="unknown type codes"):
        _build(_with("Estuaries_CO2", 0, type=4))


def test_type_code_must_be_an_integer() -> None:
    with pytest.raises(ContractError, match="integer code"):
        _build(_with("Estuaries_CO2", 0, type="1"))


def test_unknown_region_raises() -> None:
    with pytest.raises(ContractError, match="unknown region"):
        _build(_with("Estuaries_CO2", 0, region=11))


def test_non_numeric_or_blank_flux_raises() -> None:
    for bad in ("1.5", "", True, float("nan"), float("inf")):
        with pytest.raises(ContractError, match="non-"):
            _build(_with("Estuaries_CO2", 0, flux=bad))


def test_coordinates_out_of_range_raise() -> None:
    with pytest.raises(ContractError, match="coordinates outside"):
        _build(_with("Estuaries_CO2", 0, long=190.0))
    with pytest.raises(ContractError, match="non-numeric"):
        _build(_with("Estuaries_CO2", 0, lat=""))


def test_blank_or_na_site_raises() -> None:
    with pytest.raises(ContractError, match="blank or non-text"):
        _build(_with("Estuaries_CO2", 0, site_name=""))
    with pytest.raises(ContractError, match="literal missing-value"):
        _build(_with("Estuaries_CO2", 0, site_name="NA"))


def test_blank_reference_raises() -> None:
    with pytest.raises(ContractError, match="blank or non-text"):
        _build(_with("Estuaries_CO2", 0, reference=""))


def test_header_unit_mismatch_raises() -> None:
    fr = _frames()
    fr["Estuaries_CO2"] = fr["Estuaries_CO2"].rename(columns={"flux (g CO2 m2 d-1)": "flux (mg CO2 m2 d-1)"})
    with pytest.raises(ContractError, match="unexpected columns"):
        rs.build_tables(fr, META, ACCESS)


def test_row_count_must_match_the_release(monkeypatch) -> None:
    monkeypatch.setattr(rs, "SHEETS", tuple(dataclasses.replace(s, n_rows=s.n_rows + 1) for s in rs.SHEETS))
    with pytest.raises(ContractError, match="data rows, expected"):
        _build()


def test_citation_count_mismatch_raises() -> None:
    # two papers named in the reference, one citation text
    with pytest.raises(ContractError, match="undeclared"):
        _build(_with("Estuaries_CO2", 0, reference="Smith et al. (2000); Jones (2001)"))


def test_declared_exception_that_agrees_raises() -> None:
    # Miyajima declared, but now the free text and the split agree on one paper
    with pytest.raises(ContractError, match="declared but agreeing"):
        _build(_with("Estuaries_CO2", 2, reference="Miyajima et al. (2009)"))


def test_declared_split_with_wrong_length_raises() -> None:
    two_pieces = MIYA_FC + "; Extra, E. Another. J Q 1 (2010)."
    with pytest.raises(ContractError, match="declared .* but the split has 2"):
        rs.split_pieces(MIYA, two_pieces)
    assert rs.split_pieces(MIYA, MIYA_FC) == [MIYA_FC]
    assert rs.split_pieces(PANG, PANG_FC.replace("\xa0", " ")) == [PANG_FC.replace("\xa0", " ")]  # "whole"


def test_empty_piece_raises() -> None:
    with pytest.raises(ContractError, match="empty citation piece"):
        _build(_with("Estuaries_CO2", 0, reference="A (2000); B (2001); C (2002)", full_citation="X (2000); ; Y (2001)"))


def test_two_dois_in_one_piece_raise() -> None:
    fc = "X (2020). doi:10.1000/aaa and doi:10.1000/bbb"
    with pytest.raises(ContractError, match="several DOIs"):
        _build(_with("Coastal_vegetation_N2O", 0, full_citation=fc))


def test_same_paper_twice_in_one_row_raises() -> None:
    fc = "Dup, D. Same. J A 1 (2000); Dup, D. Same. J A 1 (2000)"
    with pytest.raises(ContractError, match="one key"):
        _build(_with("Coastal_vegetation_N2O", 0, reference="Dup (2000); Dup (2000)", full_citation=fc))


def test_sheet_list_is_checked(tmp_path) -> None:
    p = tmp_path / "x.xlsx"
    with pd.ExcelWriter(p) as w:  # type: ignore[abstract]
        pd.DataFrame({"a": [1]}).to_excel(w, sheet_name="Metadata", index=False)
    with pytest.raises(ContractError, match="sheets"):
        rs.read_raw(p)


def test_duplicate_rows_are_flagged() -> None:
    rows = {k: [dict(r) for r in v] for k, v in ROWS.items()}
    rows["Estuaries_N2O"][1] = dict(rows["Estuaries_N2O"][0])
    t, _, rep = _build(rows)
    f = t["fact"].set_index("fact_key")
    assert "duplicate_source_row" in f.loc["Estuaries_N2O:row0:ghg_n2o_estuary", "flags"]
    assert "duplicate_source_row" in f.loc["Estuaries_N2O:row1:ghg_n2o_estuary", "flags"]
    assert rep["duplicate_source_rows_flagged"] == 2


# ---- build + determinism


def test_roundtrip_global_build_and_determinism(built, tmp_path) -> None:
    tables, _, _ = built
    write_extracted(tables, tmp_path / "ex")
    back = read_extracted(tmp_path / "ex")
    assert len(back["fact"]) == 12
    g = assign_global_keys([back])
    assert list(g["fact"].measurement_id) == list(range(1, 13))
    out1, out2 = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    build_compilations([tmp_path / "ex"], out1)
    build_compilations([tmp_path / "ex"], out2)
    q = (
        "SELECT f.value, f.attribute, f.units_raw, l.habitat, l.bottom_type, l.setting "
        "FROM measurement_fact f JOIN location_dim l USING (location_id) ORDER BY f.measurement_id"
    )
    r1 = duckdb.connect(str(out1), read_only=True).execute(q).fetchall()
    r2 = duckdb.connect(str(out2), read_only=True).execute(q).fetchall()
    assert r1 == r2 and len(r1) == 12
    assert ("Water column" in {r[3] for r in r1}) and any(r[3] == "Water column" and r[4] is None for r in r1)


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
