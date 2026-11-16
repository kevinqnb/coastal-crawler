"""Unit tests (rung 1) for the foster-seagrass extract.py and the contract additions it
needed (location.bioregion / marine_ecoregion, sample.taxon / taxon_rank).

Hand-built fixture; expected output is stated by inspection in comments.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from coastal_crawler.compilations import (
    add_derived_labels,
    ContractError,
    assign_global_keys,
    build_compilations,
    validate_tables,
    write_extracted,
    read_extracted,
)

_EXTRACT = (
    Path(__file__).resolve().parents[1] / "data" / "compilations" / "foster-seagrass" / "extract.py"
)
_spec = importlib.util.spec_from_file_location("foster_extract", _EXTRACT)
assert _spec and _spec.loader
fs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fs)

REL, ABS = 1e-3, 1e-3
BO = {c: "1.5" for c in fs.BO22_COLUMNS}


def _row(**kw) -> dict:
    """A source row: every column blank unless given (keys are source column names)."""
    r = {c: "" for c in fs.EXPECTED_COLUMNS}
    r.update(BO)
    r.update(kw)
    return r


B, A_, T = "Above.ground.biomass..g.DW.m.2.", "Below.ground.biomass..g.DW.m.2.", "Total.biomass..g.DW.m.2."
PA, PB, PT = (
    "Above.ground.production..g.DW.m.2.day..1.",
    "Below.ground.production..g.DW.m.2.day..1.",
    "Total.production..g.DW.m.2.day..1.",
)
SHOOT, COVER, LEAFD = "Shoot.density..no..shoots.m.2.", "Percent.cover....", "Leaf.density..no..leaves.m.2."


def _loc1(**kw):
    return dict(Latitude="10.5", Longitude="20.25", Bioregion="B1", Geotype="Coastal", ECOREGION="E1", **kw)


ROWS = [
    # 0: 4 facts (biomass above/below/total consistent 10+5=15, shoot density); DOI upper case
    _row(**{"Unique.ID": "1", "Genera": "Zostera", "Year.of.sampling": "2000", B: "10", A_: "5", T: "15",
            SHOOT: "100", "Reference": "A et al. 2000", "DOI": "10.1000/AAA"}, **_loc1()),
    # 1: same paper, DOI as a doi.org URL in lower case -> same reference; year NA; 1 fact
    _row(**{"Unique.ID": "2", "Genera": "Thalassia", "Year.of.sampling": "NA", T: "20",
            "Reference": "A et al. 2000", "DOI": "https://doi.org/10.1000/aaa"}, **_loc1()),
    # 2: Mixed genus, Estuarine; biomass 3+4 != 9 (flag on all 3), production 1+1=2 ok; 6 facts;
    #    DOI with an inner space
    _row(**{"Unique.ID": "3", "Genera": "Mixed", "Year.of.sampling": "2010", B: "3", A_: "4", T: "9",
            PA: "1", PB: "1", PT: "2", "Reference": "B 2010", "DOI": "10.1000/ bbb"},
         Latitude="-5", Longitude="100", Bioregion="B2", Geotype="Estuarine", ECOREGION="E2"),
    # 3: no seagrass measurement (only BO22 values) -> excluded
    _row(**{"Unique.ID": "4", "Genera": "Zostera", "Year.of.sampling": "2001", "Reference": "A et al. 2000",
            "DOI": "10.1000/aaa"}, **_loc1()),
    # 4: identical to row 0 except Unique.ID -> rows 0 and 4 both flagged duplicate_source_row; 4 facts
    _row(**{"Unique.ID": "5", "Genera": "Zostera", "Year.of.sampling": "2000", B: "10", A_: "5", T: "15",
            SHOOT: "100", "Reference": "A et al. 2000", "DOI": "10.1000/AAA"}, **_loc1()),
    # 5: same coordinates as row 0 but Estuarine -> separate location; DOI cell is free text ->
    #    reference falls back to the Reference string; year from "2011a"; 1 fact
    _row(**{"Unique.ID": "6", "Genera": "Halophila", "Year.of.sampling": "2011", COVER: "50.5",
            "Reference": "C 2011a", "DOI": "C, C. (2011). A long title. J. X 1, 1-2."},
         Latitude="10.5", Longitude="20.25", Bioregion="B1", Geotype="Estuarine", ECOREGION="E1"),
    # 6: same reference, blank DOI; same location as row 5; 1 fact
    _row(**{"Unique.ID": "7", "Genera": "Halophila", "Year.of.sampling": "2012", COVER: "0.5",
            "Reference": "C 2011a", "DOI": ""},
         Latitude="10.5", Longitude="20.25", Bioregion="B1", Geotype="Estuarine", ECOREGION="E1"),
    # 7: "B 2010" with a blank DOI (row 2 has a DOI) -> ref:B 2010; mixes DOI/non-DOI rows; 1 fact
    _row(**{"Unique.ID": "8", "Genera": "Enhalus", "Year.of.sampling": "2010", LEAFD: "7",
            "Reference": "B 2010", "DOI": ""},
         Latitude="-5", Longitude="100", Bioregion="B2", Geotype="Estuarine", ECOREGION="E2"),
]
# expected: 8 source rows, 1 excluded, 7 rows with facts, 18 facts, 3 locations,
# 4 references (doi:10.1000/aaa, doi:10.1000/bbb, ref:C 2011a, ref:B 2010), 4 citations,
# 7 samples, 9 methods.


def _raw(rows=ROWS) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=fs.EXPECTED_COLUMNS, dtype=str)


@pytest.fixture(scope="module")
def built():
    return fs.build_tables(_raw(), "2026-11-15", REL, ABS)


def test_counts(built) -> None:
    tables, skips, rep = built
    assert rep["source_rows"] == 8 and rep["rows_with_facts"] == 7 and rep["rows_excluded"] == 1
    assert skips == [{"data_row": 3, "reason": "no_seagrass_measurement", "n_measurements": 0}]
    assert len(tables["fact"]) == 18 == rep["nonblank_measurement_cells"]
    assert len(tables["location"]) == 3
    assert len(tables["reference"]) == 4 and len(tables["citation"]) == 4
    assert len(tables["sample"]) == 7 and len(tables["method"]) == 9
    assert len(tables["study_screening"]) == 0
    assert rep["facts_by_attribute"]["seagrass_biomass_total"] == 4  # rows 0,1,2,4
    validate_tables(add_derived_labels(tables))


def test_types_and_labels(built) -> None:
    tables = built[0]
    f = tables["fact"]
    by = f.drop_duplicates("attribute").set_index("attribute")[["measurement_type", "subfield"]]
    assert tuple(by.loc["seagrass_biomass_above"]) == ("B2", "above_ground_biomass")
    assert tuple(by.loc["seagrass_shoot_density"]) == ("B3", "shoot_or_stem_density")
    assert tuple(by.loc["seagrass_production_total"]) == ("B6", "total_npp")
    loc = add_derived_labels(tables)["location"]
    assert set(loc.habitat) == {"Seagrass meadow"} and set(loc.bottom_type) == {"soft"}
    # Geotype Estuarine -> setting estuary; Coastal -> blank; raw label kept
    est = loc[loc.geomorphic_setting == "Estuarine"]
    assert len(est) and set(est.setting) == {"estuary"}
    assert loc[loc.geomorphic_setting == "Coastal"].setting.isna().all()


def test_no_bo22_loaded(built) -> None:
    tables, _, rep = built
    assert not tables["fact"].attribute.str.contains("bo22").any()
    assert rep["bo22_columns_not_loaded"] == fs.BO22_COLUMNS and rep["bo22_rows_populated"] == 8


def test_references(built) -> None:
    tables, _, rep = built
    assert sorted(tables["reference"].reference_key) == [
        "doi:10.1000/aaa", "doi:10.1000/bbb", "ref:B 2010", "ref:C 2011a",
    ]
    f = tables["fact"].set_index("fact_key")
    assert f.loc["id1:seagrass_biomass_above", "reference_key"] == "doi:10.1000/aaa"
    assert f.loc["id2:seagrass_biomass_total", "reference_key"] == "doi:10.1000/aaa"  # URL form, same DOI
    assert f.loc["id3:seagrass_biomass_above", "reference_key"] == "doi:10.1000/bbb"  # inner space removed
    assert f.loc["id6:seagrass_percent_cover", "reference_key"] == "ref:C 2011a"  # free-text DOI cell
    assert f.loc["id8:seagrass_leaf_density", "reference_key"] == "ref:B 2010"
    assert rep["reference_strings_mixing_doi_and_non_doi_rows"] == ["B 2010"]
    assert rep["doi_cells_not_doi_nonblank"] == 1  # the free-text cell
    c = tables["citation"].set_index("citation_key")
    assert c.loc["ref:C 2011a|C 2011a", "year"] == 2011 and pd.isna(c.loc["ref:C 2011a|C 2011a", "doi"])
    assert c.loc["doi:10.1000/aaa|A et al. 2000", "doi"] == "10.1000/aaa"
    assert c.loc["doi:10.1000/aaa|A et al. 2000", "bibliography_id"] == "A et al. 2000"


def test_flags(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    # rows 0 and 4 identical except id: every fact flagged duplicate
    assert f.loc["id1:seagrass_shoot_density", "flags"] == ["duplicate_source_row"]
    assert f.loc["id5:seagrass_shoot_density", "flags"] == ["duplicate_source_row"]
    assert f.loc["id2:seagrass_biomass_total", "flags"] == ["year_missing"]
    # row 2: biomass 3+4 != 9 flags all three; production 1+1 == 2 not flagged
    for a in ("above", "below", "total"):
        assert f.loc[f"id3:seagrass_biomass_{a}", "flags"] == ["total_ne_sum_of_parts"]
        assert f.loc[f"id3:seagrass_production_{a}", "flags"] == []
    # consistent triple 10+5=15 is not flagged beyond the duplicate flag
    assert "total_ne_sum_of_parts" not in f.loc["id1:seagrass_biomass_total", "flags"]
    assert rep["flag_counts"] == {"duplicate_source_row": 8, "year_missing": 1, "total_ne_sum_of_parts": 3}


def test_values_units_and_provenance(built) -> None:
    tables, _, _ = built
    f = tables["fact"].set_index("fact_key")
    assert f.loc["id6:seagrass_percent_cover", "value"] == 50.5  # kept as a percentage
    assert f.loc["id6:seagrass_percent_cover", "units_raw"] == "%"
    assert f.loc["id3:seagrass_production_total", "units_raw"] == "g DW m-2 d-1"
    assert f.loc["id1:seagrass_shoot_density", "units_raw"] == "shoots m-2"
    assert f.loc["id1:seagrass_biomass_above", "source_row_ref"] == (
        "Dataset_ecological_biomass_complete.csv#data_row=0:seagrass_biomass_above"
    )
    assert f.loc["id8:seagrass_leaf_density", "source_row_ref"].endswith("#data_row=7:seagrass_leaf_density")
    assert f[["sd", "se"]].isna().all().all() and f.n.isna().all()


def test_location_sample_method(built) -> None:
    tables, _, _ = built
    loc = tables["location"].set_index("location_key")
    k0 = "10.5|20.25|Coastal|B1|E1"
    k5 = "10.5|20.25|Estuarine|B1|E1"
    assert set(loc.index) == {k0, k5, "-5.0|100.0|Estuarine|B2|E2"}
    assert loc.loc[k0, "geomorphic_setting"] == "Coastal" and loc.loc[k5, "geomorphic_setting"] == "Estuarine"
    assert loc.loc[k0, "habitat_type"] == "seagrass"
    assert loc.loc[k0, "bioregion"] == "B1" and loc.loc[k0, "marine_ecoregion"] == "E1"
    assert loc.loc[k0, "latitude"] == 10.5 and loc.loc["-5.0|100.0|Estuarine|B2|E2", "latitude"] == -5.0
    assert loc[["site_name", "country", "water_body", "salinity_class"]].isna().all().all()
    s = tables["sample"].set_index("sample_key")
    assert s.loc["id1", "taxon"] == "Zostera" and s.loc["id1", "taxon_rank"] == "genus"
    assert s.loc["id3", "taxon"] == "Mixed" and s.loc["id3", "taxon_rank"] == "mixed"
    assert s.loc["id1", "sampling_year"] == 2000 and pd.isna(s.loc["id2", "sampling_year"])
    assert s.sampling_month.isna().all()
    m = tables["method"].set_index("method_key")
    assert (m.method_status == "method_id_null").all()
    assert "sum" in m.loc["seagrass_biomass_total", "params"]
    assert "sum" not in m.loc["seagrass_biomass_above", "params"]


def test_value_is_correctly_rounded_float() -> None:
    raw = _raw([_row(**{"Unique.ID": "1", "Genera": "Zostera", "Year.of.sampling": "2000",
                        SHOOT: "0.1", "Reference": "R 2000", "DOI": ""}, **_loc1())])
    tables, _, _ = fs.build_tables(raw, "2026-11-15", REL, ABS)
    assert tables["fact"].value.iloc[0] == 0.1


@pytest.mark.parametrize(
    "cell,want",
    [
        ("10.1000/ABC.1", "10.1000/abc.1"),
        ("https://doi.org/10.1000/abc", "10.1000/abc"),
        ("http://dx.doi.org/10.1000/abc", "10.1000/abc"),
        ("10.1371/ journal.pone.0198348", "10.1371/journal.pone.0198348"),
        ("Smith, J. (2011). A title. J. X, 1, 1-2.", None),
        ("", None),
        (None, None),
    ],
)
def test_normalise_doi(cell, want) -> None:
    assert fs.normalise_doi(cell) == want


def test_unknown_genus_raises() -> None:
    rows = [dict(ROWS[0], Genera="Zosterax")]
    with pytest.raises(ContractError, match="unknown Genera"):
        fs.build_tables(_raw(rows), "2026-11-15", REL, ABS)


def test_unknown_geotype_raises() -> None:
    rows = [dict(ROWS[0], Geotype="Marine")]
    with pytest.raises(ContractError, match="unknown Geotype"):
        fs.build_tables(_raw(rows), "2026-11-15", REL, ABS)


def test_non_numeric_value_raises() -> None:
    rows = [dict(ROWS[0], **{SHOOT: "12x"})]
    with pytest.raises(ContractError, match="non-numeric"):
        fs.build_tables(_raw(rows), "2026-11-15", REL, ABS)


def test_longitude_out_of_range_raises() -> None:
    rows = [dict(ROWS[0], Longitude="200")]
    with pytest.raises(ContractError, match="longitude"):
        fs.build_tables(_raw(rows), "2026-11-15", REL, ABS)


def test_bad_year_raises() -> None:
    rows = [dict(ROWS[0], **{"Year.of.sampling": "20x0"})]
    with pytest.raises(ContractError, match="non-year"):
        fs.build_tables(_raw(rows), "2026-11-15", REL, ABS)


def test_duplicate_unique_id_raises() -> None:
    rows = [ROWS[0], dict(ROWS[1], **{"Unique.ID": "1"})]
    with pytest.raises(ContractError, match="not unique"):
        fs.build_tables(_raw(rows), "2026-11-15", REL, ABS)


def test_wrong_columns_raise() -> None:
    raw = _raw().rename(columns={"Genera": "Genus"})
    with pytest.raises(ContractError, match="unexpected columns"):
        fs.build_tables(raw, "2026-11-15", REL, ABS)


def test_tolerance_controls_total_flag() -> None:
    rows = [_row(**{"Unique.ID": "1", "Genera": "Zostera", "Year.of.sampling": "2000",
                    B: "10", A_: "5", T: "15.01", "Reference": "R 2000", "DOI": ""}, **_loc1())]
    loose, _, _ = fs.build_tables(_raw(rows), "2026-11-15", 1e-2, 1e-2)
    tight, _, _ = fs.build_tables(_raw(rows), "2026-11-15", 1e-6, 1e-6)
    assert "total_ne_sum_of_parts" not in {x for f in loose["fact"]["flags"] for x in f}
    assert "total_ne_sum_of_parts" in {x for f in tight["fact"]["flags"] for x in f}


def test_roundtrip_global_build_and_determinism(built, tmp_path) -> None:
    tables, _, _ = built
    write_extracted(tables, tmp_path / "ex")
    back = read_extracted(tmp_path / "ex")
    assert len(back["fact"]) == 18
    g = assign_global_keys([back])
    assert list(g["fact"].measurement_id) == list(range(1, 19))
    out1, out2 = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    build_compilations([tmp_path / "ex"], out1)
    build_compilations([tmp_path / "ex"], out2)
    q = (
        "SELECT f.value, f.attribute, f.units_raw, s.taxon, l.marine_ecoregion, l.bioregion "
        "FROM measurement_fact f JOIN sample_dim s USING (sample_id) "
        "JOIN location_dim l USING (location_id) ORDER BY f.measurement_id"
    )
    r1 = duckdb.connect(str(out1), read_only=True).execute(q).fetchall()
    r2 = duckdb.connect(str(out2), read_only=True).execute(q).fetchall()
    assert r1 == r2 and len(r1) == 18
