"""Unit tests (rung 1) for the pessarrodona-seaweed extract.py.

Hand-built fixture of 6 data rows and a 3-entry reference list; every expected number is derived by inspection in
the comments.
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
    build_compilations,
    read_extracted,
    validate_tables,
    write_extracted,
)

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "pessarrodona-seaweed" / "extract.py"
_spec = importlib.util.spec_from_file_location("pessarrodona_extract", _EXTRACT)
assert _spec and _spec.loader
px = importlib.util.module_from_spec(_spec)
sys.modules["pessarrodona_extract"] = px
_spec.loader.exec_module(px)

ACCESS = "2026-11-15"
UNNAMED = [f"Unnamed: {i}" for i in range(36, 80)]
COLUMNS = px.NAMED_COLUMNS + UNNAMED


def _row(**kw) -> dict:
    r = {c: "" for c in COLUMNS}
    r.update({
        "Vegetation_category": "Marine forest", "Substrate_category": "Rocky Reef", "Level": "Subtidal",
        "Species": "Laminaria hyperborea", "Phyla": "Brown", "Order": "Laminariales", "Family": "Laminariaceae",
        "Genus": "Laminaria", "Multispecies": "NO", "Aggregation_required": "NO", "Site": "Bay, UK",
        "Site_ID_within_study": "1", "Latitude_decimal_degrees": "50", "Longitude_decimal_degrees": "-4",
        "Originalcoordinates": "YES", "Depth_min_m": "5", "Depth_max_m": "5", "Start_year": "2000", "End_year": "2000",
        "Ann_sampling_freq": "4", "Seasons": "all", "Data_mining_method": "Text", "Description": "a description",
        "Reference": "Smith et al. 2000", "Prod_method_group": "BA-periodic harvest", "Prod_method_general": "BA",
        "Standing biomass_kg_DW_m2": "", "Avg_NPP": "1.2", "sted_NPP": "0.1", "NPP_units": "kg C m-2 yr-1",
        "Matter_Unit": "C", "FW_DW_Conversion_factor": "NA", "DW_Carbon_Conversion_factor": "NA",
        "Avg_NPP_kg_C_m2_y": "1.2", "stdev_NPP_kg_C_m2_y": "0.1", "Person_entering_data": "AP",
    })
    r.update(kw)
    return r


ROWS = [
    # 0: kelp on rocky reef, depth 5 = 5 (numeric slot), single year, not converted, sd 0.1
    _row(),
    # 1: turf on coral reef, no coordinates (coords_missing), depth -0.4..0 (notes), years 1990-1992 (NULL year),
    #    digitised, converted from g DW (0.0258), secondary reference, original coordinates NO
    _row(**{"Vegetation_category": "Algal turfs", "Substrate_category": "Coral Reef", "Level": "Intertidal",
            "Species": "Turf assemblage", "Latitude_decimal_degrees": "NA", "Longitude_decimal_degrees": "NA",
            "Originalcoordinates": "NO", "Site": "Turf site", "Depth_min_m": "-0.4", "Depth_max_m": "0", "Start_year": "1990",
            "End_year": "1992", "Data_mining_method": "Graph digitizer", "Reference": "Ref X 1999 in Y 2005",
            "Avg_NPP": "100", "NPP_units": "g DW m-2 yr-1", "Matter_Unit": "DW", "DW_Carbon_Conversion_factor": "0.258",
            "Avg_NPP_kg_C_m2_y": "0.0258", "stdev_NPP_kg_C_m2_y": "NA", "sted_NPP": "NA"}),
    # 2: habitat with no standard label, Level NA, no years (year_missing), negative value, unknown method, several species
    _row(**{"Vegetation_category": "Marine forest", "Substrate_category": "Salt-marsh", "Level": "NA",
            "Multispecies": "YES", "Species": "Mixed", "Depth_min_m": "NA", "Depth_max_m": "NA",
            "Start_year": "NA", "End_year": "NA", "Reference": "Nobody 2010", "Prod_method_group": "Unknown",
            "Avg_NPP": "-0.1", "Avg_NPP_kg_C_m2_y": "-0.1", "stdev_NPP_kg_C_m2_y": "", "sted_NPP": "",
            "Site": "Marsh"}),
    # 3: green algae on sand; negative equal depth (notes only); SD exactly 0; a standing biomass value (not loaded);
    #    Jones 2001 matches two PDF entries, settled by the single-author shape
    _row(**{"Vegetation_category": "Green algal beds", "Substrate_category": "Sand", "Depth_min_m": "-3",
            "Depth_max_m": "-3", "Reference": "Jones 2001", "stdev_NPP_kg_C_m2_y": "0", "Avg_NPP": "0.5",
            "Avg_NPP_kg_C_m2_y": "0.5", "Standing biomass_kg_DW_m2": "2.0", "Site": "Sandy"}),
    # 4: same location as row 0 (different species and reference spelling of the same study)
    _row(**{"Species": "Saccharina latissima", "Avg_NPP": "2", "Avg_NPP_kg_C_m2_y": "2", "stdev_NPP_kg_C_m2_y": ""}),
    # 5: tidal-flat-free coral habitat: red algae on coral, 0 harmonised value (zero_value)
    _row(**{"Vegetation_category": "Red algal beds", "Substrate_category": "Coral Reef", "Site": "Reef",
            "Avg_NPP": "0", "Avg_NPP_kg_C_m2_y": "0", "Reference": "Jones 2001"}),
]
ENTRIES = [
    {"text": "Smith, A., B. Lee, and C. Wu. 2000. “T.” J 1(1): 1–2. https://doi.org/10.1234/X.",
     "author": "Smith, A., B. Lee, and C. Wu", "year": "2000", "surname": "smith"},
    {"text": "Jones, D. 2001. “One author.” J 2: 3.", "author": "Jones, D", "year": "2001", "surname": "jones"},
    {"text": "Jones, D., E. Fox, and G. Hill. 2001. “Three authors.” J 3: 4. http://example.org/p.",
     "author": "Jones, D., E. Fox, and G. Hill", "year": "2001", "surname": "jones"},
]
# expected: 6 facts, 6 samples, 6 methods; locations: row 4 shares row 0's -> 5; references 4 (Smith et al. 2000,
# Ref X 1999 in Y 2005, Nobody 2010, Jones 2001).


def _frame(rows=ROWS):
    return pd.DataFrame(rows, columns=COLUMNS, dtype=str)


def _build(rows=ROWS, entries=ENTRIES):
    return px.build_tables(_frame(rows), entries, ACCESS, expected_stray={})


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
    assert len(tables["fact"]) == 6 == rep["facts"] == rep["source_rows"]
    assert len(tables["sample"]) == 6 and len(tables["method"]) == 6 and len(tables["location"]) == 5
    assert len(tables["reference"]) == 4 == len(tables["citation"]) and rep["reference_strings"] == 4
    assert rep["rows_with_sd"] == 3 and rep["rows_with_sd_zero"] == 1 and rep["rows_with_original_sd"] == 4
    assert rep["standing_biomass_cells_not_loaded"] == 1 and rep["methods_unknown_technique"] == 1
    assert rep["rows_negative_value"] == 1 and rep["rows_multi_year"] == 1
    validate_tables(add_derived_labels(tables))


def test_source_row(built) -> None:
    src = built[0]["source"].iloc[0]
    assert src.version == "figshare v5 (2022-08-04)" and src.license == "CC BY 4.0" and src.access_date == ACCESS
    assert src.doi == "10.6084/m9.figshare.14882322.v5"


def test_facts(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    assert set(f.measurement_type) == {"B6"} and set(f.subfield) == {"seagrass_seaweed_npp"}
    assert set(f.units_raw) == {"kg C m-2 y-1"} and set(f.attribute) == {"pessarrodona_npp"}
    assert f.loc["row0:pessarrodona_npp", "value"] == 1.2 and f.loc["row0:pessarrodona_npp", "sd"] == 0.1
    assert f.loc["row1:pessarrodona_npp", "value"] == 0.0258 and pd.isna(f.loc["row1:pessarrodona_npp", "sd"])
    assert f.loc["row3:pessarrodona_npp", "sd"] == 0 and f.loc["row2:pessarrodona_npp", "value"] == -0.1
    assert f.loc["row1:pessarrodona_npp", "source_row_ref"] == "NPP Dataset.csv#data_row=1:pessarrodona_npp"
    assert f[["se"]].isna().all().all() and f.n.isna().all()


def test_flags(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    fl = lambda i: f.loc[f"row{i}:pessarrodona_npp", "flags"]  # noqa: E731
    assert fl(0) == []
    assert fl(1) == ["cited_secondary", "converted_by_source", "coords_missing", "digitized_from_figure"]
    assert fl(2) == ["negative_value", "year_missing"]  # original already in kg C m-2 yr-1 with the same value
    assert fl(3) == []
    assert fl(5) == ["zero_value"]
    assert "coords_approximate" not in rep["flag_counts"]  # the only NO-coordinates row has no coordinates at all
    assert rep["flag_counts"] == {"cited_secondary": 1, "converted_by_source": 1, "coords_missing": 1,
                                  "digitized_from_figure": 1, "negative_value": 1, "year_missing": 1, "zero_value": 1}


def test_habitat_labels(built) -> None:
    loc = add_derived_labels(built[0])["location"].set_index("habitat_type")
    assert loc.loc["Marine forest | Rocky Reef", "habitat"] == "Kelp and macroalgal bed"
    assert loc.loc["Marine forest | Rocky Reef", "bottom_type"] == "hard"
    assert loc.loc["Algal turfs | Coral Reef", "habitat"] == "Coral reef"
    assert pd.isna(loc.loc["Marine forest | Salt-marsh", "habitat"])
    assert loc.loc["Green algal beds | Sand", "habitat"] == "Kelp and macroalgal bed"
    assert loc.loc["Red algal beds | Coral Reef", "habitat"] == "Coral reef"


def test_locations_samples(built) -> None:
    tables, _, _ = built
    loc = tables["location"].set_index("site_name")
    assert loc.loc["Bay, UK", "water_depth_m"] == 5 and loc.loc["Bay, UK", "position_method"] == "reported"
    assert loc.loc["Bay, UK", "tidal_position"] == "Subtidal" and loc.loc["Bay, UK", "source_site_id"] == "1"
    assert loc.loc["Bay, UK", "latitude"] == 50 and loc.loc["Bay, UK", "longitude"] == -4
    assert pd.isna(loc.loc["Marsh", "tidal_position"]) and pd.isna(loc.loc["Marsh", "water_depth_m"])
    r1 = loc[loc.habitat_type == "Algal turfs | Coral Reef"].iloc[0]
    assert pd.isna(r1.latitude) and pd.isna(r1.water_depth_m) and r1.position_method == "approximated by compiler"
    assert pd.isna(loc.loc["Sandy", "water_depth_m"])  # negative equal depth: notes only
    s = tables["sample"].set_index("sample_key")
    assert s.loc["row0", "sampling_year"] == 2000 and pd.isna(s.loc["row0", "depth_notes"])
    assert pd.isna(s.loc["row1", "sampling_year"]) and s.loc["row1", "depth_notes"] == "depth_min_m=-0.4; depth_max_m=0; years=1990-1992"
    assert pd.isna(s.loc["row2", "sampling_year"]) and pd.isna(s.loc["row2", "depth_notes"])
    assert s.loc["row3", "depth_notes"] == "depth_min_m=-3; depth_max_m=-3"
    assert s.loc["row0", "taxon"] == "Laminaria hyperborea" and s.loc["row0", "taxon_rank"] == "species"
    assert s.loc["row2", "taxon_rank"] == "multiple"
    f = tables["fact"].set_index("fact_key")
    assert f.loc["row0:pessarrodona_npp", "location_key"] == f.loc["row4:pessarrodona_npp", "location_key"]


def test_methods(built) -> None:
    tables, _, _ = built
    m = tables["method"].set_index("method_key")
    assert m.loc["row0", "technique"] == "BA-periodic harvest" and m.loc["row0", "method_status"] == "matched"
    assert pd.isna(m.loc["row0", "conversion_applied"])
    assert m.loc["row1", "conversion_applied"] == "source harmonised: 100 g DW m-2 yr-1 (matter DW) -> 0.0258 kg C m-2 y-1"
    assert pd.isna(m.loc["row2", "technique"]) and m.loc["row2", "method_status"] == "method_id_null"
    p = json.loads(m.loc["row1", "params"])
    assert p["avg_npp_original"] == "100" and p["npp_units_original"] == "g DW m-2 yr-1" and p["matter_unit"] == "DW"
    assert p["dw_carbon_conversion_factor"] == "0.258" and p["fw_dw_conversion_factor"] is None
    assert p["data_mining_method"] == "Graph digitizer" and p["start_year"] == 1990 and p["end_year"] == 1992
    assert p["person_entering_data"] == "AP" and p["originalcoordinates"] == "NO"


def test_references(built) -> None:
    tables, _, rep = built
    c = tables["citation"].set_index("bibliography_id")
    assert c.loc["Smith et al. 2000", "doi"] == "10.1234/x" and c.loc["Smith et al. 2000", "year"] == 2000
    assert c.loc["Smith et al. 2000", "title"].startswith("Smith, A., B. Lee")
    assert c.loc["Jones 2001", "title"].startswith("Jones, D. 2001. “One author.”")  # single-author shape settles it
    assert pd.isna(c.loc["Nobody 2010", "title"]) and c.loc["Nobody 2010", "year"] == 2010
    assert pd.isna(c.loc["Ref X 1999 in Y 2005", "title"]) and c.loc["Ref X 1999 in Y 2005", "year"] == 1999
    assert sorted(rep["citations_unresolved"]) == [
        "Nobody 2010: no PDF entry with this first author and year",
        "Ref X 1999 in Y 2005: no PDF entry with this first author and year"]
    assert rep["strings_marked_secondary"] == ["Ref X 1999 in Y 2005"]
    assert set(tables["reference"].reference_key) == {
        "ref:Smith et al. 2000", "ref:Ref X 1999 in Y 2005", "ref:Nobody 2010", "ref:Jones 2001"}


# ---- helpers


def test_resolve_reference_cases() -> None:
    e, how = px.resolve_reference("Smith et al. 2000", ENTRIES)
    assert e and e["surname"] == "smith" and how == "unique surname and year"
    assert px.resolve_reference("Smith & Park 2000", ENTRIES)[0] is None  # second author not in the entry
    assert px.resolve_reference("Smith 2001", ENTRIES)[0] is None  # wrong year
    e2, how2 = px.resolve_reference("Jones et al 2001", ENTRIES)
    assert e2 and e2["author"].startswith("Jones, D., E. Fox") and "author count" in how2
    amb, why = px.resolve_reference("Jones, Fox 2001", ENTRIES)  # comma form: shape unknown, two entries remain
    assert amb is None and "2 PDF entries" in why
    assert px.resolve_reference("Unpublished", ENTRIES)[0] is None


def test_helpers() -> None:
    assert px.num("NA", "x") is None and px.num("", "x") is None and px.num("-0.4", "x") == -0.4
    with pytest.raises(ContractError):
        px.num("n/a", "x")
    assert px.fold("Muñoz Sørensen") == "munoz sorensen"
    assert px._string_kind("Smith 2000") == "single" and px._string_kind("Smith & Lee 2000") == "pair"
    assert px._string_kind("Smith et al. 2000") == "many" and px._string_kind("Chapman, Lindley 1981") is None


# ---- fail-loud checks


def test_stray_cells_must_be_the_known_ones() -> None:
    rows = _with(0, **{"Unnamed: 50": "x"})
    with pytest.raises(ContractError, match="unnamed columns"):
        _build(rows)


def test_real_stray_cells_are_the_default_expectation() -> None:
    with pytest.raises(ContractError, match="unnamed columns"):
        px.build_tables(_frame(), ENTRIES, ACCESS)  # the fixture lacks the two known stray rows


def test_columns_checked() -> None:
    f = _frame().rename(columns={"Level": "Lvl"})
    with pytest.raises(ContractError, match="unexpected columns"):
        px.build_tables(f, ENTRIES, ACCESS, expected_stray={})
    with pytest.raises(ContractError, match="columns, expected"):
        px.build_tables(_frame().iloc[:, :70], ENTRIES, ACCESS, expected_stray={})


def test_value_checks() -> None:
    with pytest.raises(ContractError, match="without Avg_NPP_kg_C_m2_y"):
        _build(_with(0, Avg_NPP_kg_C_m2_y="NA"))
    with pytest.raises(ContractError, match="unrecognised value"):
        _build(_with(0, stdev_NPP_kg_C_m2_y="abc"))
    with pytest.raises(ContractError, match="negative SD"):
        _build(_with(0, stdev_NPP_kg_C_m2_y="-1"))
    with pytest.raises(ContractError, match="not missing together"):
        _build(_with(0, Longitude_decimal_degrees="NA"))
    with pytest.raises(ContractError, match="outside the valid range"):
        _build(_with(0, Latitude_decimal_degrees="95"))
    with pytest.raises(ContractError, match="Start_year / End_year"):
        _build(_with(0, Start_year="2005"))
    with pytest.raises(ContractError, match="Depth_min_m and Depth_max_m"):
        _build(_with(0, Depth_max_m="NA"))


def test_label_checks() -> None:
    with pytest.raises(ContractError, match="unknown Level"):
        _build(_with(0, Level="Deep"))
    with pytest.raises(ContractError, match="YES/NO"):
        _build(_with(0, Multispecies="maybe"))
    with pytest.raises(ContractError, match="Matter_Unit"):
        _build(_with(0, Matter_Unit="X"))
    with pytest.raises(ContractError, match="Data_mining_method"):
        _build(_with(0, Data_mining_method="guess"))
    with pytest.raises(ContractError, match="blank Reference"):
        _build(_with(0, Reference=""))


def test_duplicate_rows_raise() -> None:
    rows = [dict(r) for r in ROWS]
    rows.append(dict(rows[0]))
    with pytest.raises(ContractError, match="duplicate source rows"):
        _build(rows)


def test_unlisted_habitat_combination_raises_at_write(built, tmp_path) -> None:
    bad = _with(0, Vegetation_category="Giant kelp", Substrate_category="Rocky Reef")
    tables, _, _ = _build(bad)
    with pytest.raises(Exception, match="no row in the label map"):
        write_extracted(tables, tmp_path / "ex")


# ---- build + determinism


def test_roundtrip_global_build_and_determinism(built, tmp_path) -> None:
    tables, _, _ = built
    write_extracted(tables, tmp_path / "ex")
    back = read_extracted(tmp_path / "ex")
    assert len(back["fact"]) == 6
    out1, out2 = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    build_compilations([tmp_path / "ex"], out1)
    build_compilations([tmp_path / "ex"], out2)
    q = ("SELECT f.value, f.sd, l.habitat, l.bottom_type FROM measurement_fact f JOIN location_dim l USING (location_id) "
         "ORDER BY f.measurement_id")
    r1 = duckdb.connect(str(out1), read_only=True).execute(q).fetchall()
    r2 = duckdb.connect(str(out2), read_only=True).execute(q).fetchall()
    assert r1 == r2 and len(r1) == 6 and {r[2] for r in r1} == {"Kelp and macroalgal bed", "Coral reef", None}


def test_build_is_deterministic() -> None:
    a, sa, ra = _build()
    b, sb, rb = _build()
    for k in a:
        pd.testing.assert_frame_equal(a[k], b[k])
    assert ra == rb and sa == sb


def test_coords_approximate_flag_needs_coordinates() -> None:
    tables, _, rep = _build(_with(5, Originalcoordinates="NO"))
    f = tables["fact"].set_index("fact_key")
    assert f.loc["row5:pessarrodona_npp", "flags"] == ["coords_approximate", "zero_value"]
    assert rep["flag_counts"]["coords_approximate"] == 1
