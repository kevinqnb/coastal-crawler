"""Unit tests (rung 1) for the SCOC extract.py and the contract additions it needed
(nullable FK, study_screening table, new sample/location columns).

Hand-built fixture; expected output is stated by inspection in comments.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from coastal_crawler.compilations import (
    ContractError,
    add_derived_labels,
    assign_global_keys,
    build_compilations,
    empty_table,
    validate_tables,
    write_extracted,
)

_EXTRACT = (
    Path(__file__).resolve().parents[1] / "data" / "compilations" / "stratma-scoc" / "extract.py"
)
_spec = importlib.util.spec_from_file_location("scoc_extract", _EXTRACT)
assert _spec and _spec.loader
scoc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scoc)

LIST_ROWS = [  # study_000 .. study_006
    ("A, A. et al. Alpha study. J. Alpha 1, 1-2 (2000).", "included", "included", "included"),
    ("B, B. Beta study. J. Beta 2, 3-4 (2001).", "included", "included", "included"),
    ("C, C. J. & Cc, D. Gamma study. J. Gamma 3, 5-6 (2002).", "included", "included", "included"),
    ("D, D. Delta study. J. Delta 4, 7-8 (2003).", "included", "included", "included"),
    ("E, E. Epsilon study. doi:10.1000/eps.5. J. Eps 5, 9-10 (2004).", "included", "included", "included"),
    ("F, F. Zeta study. J. Zeta 6, 1-2 (2005).", "excluded", "previously excluded", "previously excluded"),
    ("G, G. Eta study. J. Eta 7, 1-2 (2006).", "included", "excluded", "previously excluded"),
]
# 16 data columns, in source order
ROWS = [
    # 0: A, TOU, lon 255.7367 (0-360 convention), approximated
    ["Reg1, X", "Pacific Ocean", "10.5", "255.7367", "approximated location", "100", "continental shelf (>50-200m)",
     "2.5", "in situ", "benthic incubation chamber", "TOU", "NA", "silt ", "yes", "dark", "A, A. et al. Alpha study. J. Alpha 1, 1-2 (2000)."],
    # 1: exact duplicate of 0 -> both flagged duplicate_source_row, same location
    ["Reg1, X", "Pacific Ocean", "10.5", "255.7367", "approximated location", "100", "continental shelf (>50-200m)",
     "2.5", "in situ", "benthic incubation chamber", "TOU", "NA", "silt ", "yes", "dark", "A, A. et al. Alpha study. J. Alpha 1, 1-2 (2000)."],
    # 2: B (case/punctuation differs from list); everything optional is NA; value 0
    ["Reg2", "Arctic Ocean", "NA", "NA", "NA", "NA", "near-shore (0-50m)",
     "0", "NA", "NA", "NA", "Na", "NA", "NA", "NA", "B, B. BETA study. J. beta 2, 3-4 (2001)"],
    # 3: C via CROSSWALK (initial dropped in the data string), DOU, light
    ["Reg3", "Atlantic Ocean", "-5.25", "-70.5", "exact location", "10.5", "near-shore (0-50m)",
     "7", "ex situ ", "core incubation", "DOU", "120", "sand", "not investigated", "not investigated", "C, C. . & Cc, D. Gamma study. J. Gamma 3, 5-6 (2002)."],
    # 4: one cell citing two studies (D + E), AOU
    ["Reg4", "Indian Ocean", "1", "2", "exact location", "5", "near-shore (0-50m)",
     "1.25", "ex situ", "core microprofiling", "AOU", "33.5", "NA", "no", "light", "D, D. Delta study. J. Delta 4, 7-8 (2003); E, E. Epsilon study. doi:10.1000/eps.5. J. Eps 5, 9-10 (2004)"],
    # 5: fully blank -> excluded as empty_row
    [""] * 16,
]
CW = [
    ("C, C. . & Cc", ["C, C. J. & Cc"]),
    ("D, D. Delta study. J. Delta 4, 7-8 (2003); E", ["D, D. Delta", "E, E. Epsilon"]),
]


def _raw(rows=ROWS) -> pd.DataFrame:
    cols = list(scoc.DATA_COLUMNS)
    cols[3] = "Longitude €"  # as decoded from the source's mangled degree sign
    return pd.DataFrame(rows, columns=cols, dtype=str)


def _lst() -> pd.DataFrame:
    return pd.DataFrame(LIST_ROWS, columns=scoc.LIST_COLUMNS, dtype=str)


@pytest.fixture(autouse=True)
def _fixture_constants(monkeypatch):
    monkeypatch.setattr(scoc, "CROSSWALK", CW)
    monkeypatch.setattr(scoc, "KNOWN_USED_BUT_NOT_INCLUDED", frozenset())


def _build(rows=ROWS):
    return scoc.build_tables(_raw(rows), _lst(), "2026-11-15")


def test_counts_and_accounting():
    tables, skips, report = _build()
    # 6 source rows = 5 O2 facts + 1 empty row; rows 3 and 4 also give a grain-size fact
    assert report["source_rows"] == 6 and report["facts"] == 7 and report["grain_size_facts"] == 2
    assert [s["reason"] for s in skips] == ["empty_row"] and skips[0]["data_row"] == 5
    assert report["facts_by_attribute"] == {
        "scoc_tou": 2, "scoc_unspecified": 1, "scoc_dou": 1, "scoc_aou": 1,
        "scoc_median_grain_size": 2,
    }
    validate_tables(add_derived_labels(tables))  # whole contract incl. vocabulary


def test_values_units_and_provenance():
    f = _build()[0]["fact"].set_index("fact_key")
    assert f.loc["row3:scoc_dou", "value"] == 7.0
    assert set(f.units_raw) == {"mmol O2 m-2 d-1", "um"}
    # B5 for the O2 rates, G1 for grain size; subfield NULL when flux type unstated
    assert f.loc["row3:scoc_dou", "measurement_type"] == "B5" and f.loc["row3:scoc_dou", "subfield"] == "diffusive_o2_uptake"
    assert f.loc["row4:scoc_aou", "subfield"] == "advective_o2_uptake"
    assert f.loc["row0:scoc_tou", "subfield"] == "total_o2_uptake"
    assert pd.isna(f.loc["row2:scoc_unspecified", "subfield"])
    g = f.loc["row3:scoc_median_grain_size"]
    assert (g.value, g.units_raw, g.measurement_type, g.subfield) == (120.0, "um", "G1", "median_grain_size")
    assert f.loc["row4:scoc_median_grain_size", "value"] == 33.5
    assert "row0:scoc_median_grain_size" not in f.index  # grain size was NA
    assert g.source_row_ref == "SCOC_database_version_1.csv#data_row=3:scoc_median_grain_size"
    assert f.loc["row3:scoc_dou", "source_row_ref"] == "SCOC_database_version_1.csv#data_row=3:scoc_dou"
    assert f.sd.isna().all() and f.se.isna().all() and f.n.isna().all()


def test_flags():
    f = _build()[0]["fact"].set_index("fact_key")
    assert f.loc["row0:scoc_tou", "flags"] == ["coords_approximate", "duplicate_source_row", "lon_wrapped"]
    assert f.loc["row1:scoc_tou", "flags"] == ["coords_approximate", "duplicate_source_row", "lon_wrapped"]
    assert f.loc["row2:scoc_unspecified", "flags"] == [
        "coords_missing", "depth_missing", "flux_type_missing", "method_missing", "zero_value",
    ]
    assert f.loc["row3:scoc_dou", "flags"] == []
    assert f.loc["row4:scoc_aou", "flags"] == []
    assert f.loc["row3:scoc_median_grain_size", "flags"] == []


def test_location_standardisation():
    loc = _build()[0]["location"].set_index("site_name")
    # 255.7367 - 360 exactly, by decimal arithmetic (no float noise)
    assert loc.loc["Reg1, X", "longitude"] == -104.2633
    assert loc.loc["Reg1, X", "latitude"] == 10.5
    assert loc.loc["Reg1, X", "water_depth_m"] == 100.0  # positive down
    assert pd.isna(loc.loc["Reg1, X", "elevation"]) and pd.isna(loc.loc["Reg1, X", "elevation_datum"])
    assert loc.loc["Reg1, X", "water_body"] == "Pacific Ocean"
    assert loc.loc["Reg1, X", "habitat_type"] == "continental shelf (>50-200m)"
    assert loc.loc["Reg1, X", "position_method"] == "approximated location"
    assert loc.loc["Reg3", "longitude"] == -70.5 and loc.loc["Reg3", "water_depth_m"] == 10.5
    assert pd.isna(loc.loc["Reg2", "latitude"]) and pd.isna(loc.loc["Reg2", "water_depth_m"])
    assert pd.isna(loc.loc["Reg2", "elevation_units"]) and pd.isna(loc.loc["Reg2", "position_method"])
    assert len(loc) == 4  # rows 0 and 1 share one location


def test_sample_and_method_labels():
    t = _build()[0]
    s = t["sample"].set_index("sample_key")
    assert s.loc["row0", "sediment_type"] == "silt"  # stripped
    assert "median_grain_size_um" not in s.columns  # grain size is a G1 fact now
    assert s.loc["row3", "photosynthetic_sediment"] == "not investigated"  # kept as a label
    assert pd.isna(s.loc["row2", "photosynthetic_sediment"])  # NA -> NULL
    assert s.sampling_year.isna().all()
    m = t["method"].set_index("attribute")
    assert m.loc["scoc_unspecified", "method_status"] == "method_id_null"
    assert m.loc["scoc_dou", "in_situ_ex_situ"] == "ex situ"  # trailing space stripped
    assert m.loc["scoc_dou", "light_dark"] == "not investigated"
    assert m.loc["scoc_dou", "method_status"] == "matched"


def test_labels_from_label_map():
    loc = add_derived_labels(_build()[0])["location"].set_index("site_name")
    # every Depth range label -> subtidal soft sediment / soft; setting from the depth class
    assert set(loc.habitat) == {"Subtidal soft sediment"} and set(loc.bottom_type) == {"soft"}
    assert loc.loc["Reg1, X", "setting"] == "shelf"
    assert pd.isna(loc.loc["Reg2", "setting"])  # near-shore: blank on purpose
    assert loc.modification_class.map(len).eq(0).all()


def test_unmapped_raw_label_raises():
    rows = [list(r) for r in ROWS]
    rows[2][6] = "hadal trench"  # a Depth range label with no row in the label map
    tables = _build(rows)[0]
    with pytest.raises(Exception, match="hadal trench"):
        add_derived_labels(tables)


def test_references_citations_screening():
    t = _build()[0]
    assert sorted(t["reference"].reference_key) == [
        "study_000", "study_001", "study_002", "study_003+study_004",
    ]
    cit = t["citation"].set_index("citation_key")
    assert len(cit) == 5  # F and G contribute no facts
    assert cit.loc["study_003", "reference_key"] == cit.loc["study_004", "reference_key"] == "study_003+study_004"
    assert cit.loc["study_000", "year"] == 2000 and cit.loc["study_004", "doi"] == "10.1000/eps.5"
    assert pd.isna(cit.loc["study_001", "doi"])
    sc = t["study_screening"].set_index("study_key")
    assert len(sc) == 7
    assert sc.loc["study_005", "database_status"] == "previously excluded" and pd.isna(sc.loc["study_005", "reference_key"])
    assert sc.loc["study_006", "eligibility_phase"] == "excluded"
    assert sc.loc["study_004", "reference_key"] == "study_003+study_004"
    # the fact for the two-study cell points at the joined reference
    f = t["fact"].set_index("fact_key")
    assert f.loc["row4:scoc_aou", "reference_key"] == "study_003+study_004"


def test_unresolved_reference_raises(monkeypatch):
    monkeypatch.setattr(scoc, "CROSSWALK", CW[:1])
    with pytest.raises(ContractError, match="match no list entry"):
        _build()


def test_stale_crosswalk_entry_raises(monkeypatch):
    monkeypatch.setattr(scoc, "CROSSWALK", CW + [("Nonexistent prefix", ["A, A."])])
    with pytest.raises(ContractError, match="matches nothing"):
        _build()


def test_used_study_not_included_raises(monkeypatch):
    rows = [list(r) for r in ROWS]
    rows[0][15] = "F, F. Zeta study. J. Zeta 6, 1-2 (2005)."  # list says F is not in the DB
    with pytest.raises(ContractError, match="used-not-included"):
        _build(rows)
    # explicit allow-list: kept, flagged
    monkeypatch.setattr(scoc, "KNOWN_USED_BUT_NOT_INCLUDED", frozenset({"study_005"}))
    tables, _, _ = scoc.build_tables(_raw(rows), _lst(), "2026-11-15")  # row 1 still cites A
    f = tables["fact"].set_index("fact_key")
    assert "study_marked_excluded" in f.loc["row0:scoc_tou", "flags"]
    assert "study_marked_excluded" not in f.loc["row1:scoc_tou", "flags"]
    # an included study with no data row must still raise, even with the allow-list set
    rows[1][15] = rows[0][15]
    with pytest.raises(ContractError, match="included-unused"):
        _build(rows)


def test_partly_blank_row_raises():
    rows = [list(r) for r in ROWS]
    rows[5][7] = "1"  # SCOC present but everything else blank
    with pytest.raises(ContractError, match="NULL"):
        _build(rows)


def test_unknown_flux_label_raises():
    rows = [list(r) for r in ROWS]
    rows[0][10] = "XOU"
    with pytest.raises(ContractError, match="TOU/DOU/AOU"):
        _build(rows)


def test_non_numeric_value_raises():
    rows = [list(r) for r in ROWS]
    rows[0][7] = "2,5"
    with pytest.raises(ContractError, match="non-numeric"):
        _build(rows)


def test_unexpected_header_raises():
    raw = _raw()
    raw.columns = list(raw.columns[:-1]) + ["Ref"]
    with pytest.raises(ContractError, match="column"):
        scoc.build_tables(raw, _lst(), "2026-11-15")


# ---------------------------------------------------------------- contract additions


def _second_source(tables):
    t = {k: v.copy() for k, v in tables.items()}
    for v in t.values():
        v["source_key"] = "other"
    t["study_screening"] = empty_table("study_screening")  # like CCN: no screening data
    return t


def test_nullable_fk_global_keys_and_duckdb(tmp_path):
    tables = _build()[0]
    derived = add_derived_labels(tables)
    glob = assign_global_keys([derived, _second_source(derived)])
    sc = glob["study_screening"]
    assert len(sc) == 7  # the empty screening table of the second source adds nothing
    by = sc.set_index("study_key")
    assert pd.isna(by.loc["study_005", "reference_id"])
    assert not pd.isna(by.loc["study_004", "reference_id"])
    # resolved id points at the right reference
    refs = glob["reference"].set_index("reference_id")
    rid = by.loc["study_004", "reference_id"]
    assert refs.loc[rid, "reference_key"] == "study_003+study_004" and refs.loc[rid, "source_key"] == "stratma-scoc"
    out = tmp_path / "c.duckdb"
    counts = build_compilations_from(tables, tmp_path, out)
    assert counts["study_screening_dim"] == 7 and counts["measurement_fact"] == 7
    con = duckdb.connect(str(out), read_only=True)
    n_null = con.execute("select count(*) from study_screening_dim where reference_id is null").fetchone()[0]
    n_ref = con.execute(
        "select count(*) from study_screening_dim s join reference_dim r using (reference_id)"
    ).fetchone()[0]
    assert (n_null, n_ref) == (2, 5)
    cols = {r[0] for r in con.execute("describe sample_dim").fetchall()}
    assert {"sediment_type", "photosynthetic_sediment", "bottom_water_o2"} <= cols
    assert "median_grain_size_um" not in cols
    assert "water_body" in {r[0] for r in con.execute("describe location_dim").fetchall()}
    con.close()


def build_compilations_from(tables, tmp_path, out):
    d = tmp_path / "ex"
    write_extracted(tables, d)
    return build_compilations([d], out)


def test_nullable_fk_dangling_value_raises():
    tables = _build()[0]
    tables["study_screening"].loc[0, "reference_key"] = "no_such_reference"
    with pytest.raises(ContractError, match="not in reference"):
        validate_tables(add_derived_labels(tables))
