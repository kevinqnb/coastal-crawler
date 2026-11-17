"""Unit tests (rung 1) for the stratma-bio (BenBioDen) extract.py.

Hand-built fixture: 7 BenBIO rows, 5 BenDEN rows, a BenBio study list with two blank trailing rows and a
BenDen study list; expected output is stated by inspection in comments.
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
    read_extracted,
    validate_tables,
    write_extracted,
)

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "stratma-bio" / "extract.py"
_spec = importlib.util.spec_from_file_location("bio_extract", _EXTRACT)
assert _spec and _spec.loader
bx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bx)

REF_A = "Aaa, A. Paper one. J. X 1, 1-2 (2000)"
REF_B = "Bbb, B. & Ccc, C. Paper two. J. Y 2, 3-4 (2001). doi:10.1000/ABC.2"
REF_C = "Eee, E. Composite A. J. 1, 1 (2003); Fff, F. Composite B. J. 2, 2 (2004)"
REF_G = "Ggg, G. Typo paperr. J. 3, 3 (2005)"
REF_U = "Unlisted, U. Nothing (2006)"
REF_H = "Hhh, H. Marked paper. J. 4, 4 (2007)"

BIO_LIST = pd.DataFrame(
    [
        ["Aaa, A. Paper one. J. X 1, 1-2 (2000).", "included", "included", "included"],
        ["Bbb, B. & Ccc, C. Paper two. J. Y 2, 3-4 (2001). doi:10.1000/abc.2.", "included", "included", "included"],
        ["Ddd, D. Paper three. Nature 3, 5-6 (2002).", "excluded", "previously excluded", "previously excluded"],
        ["Eee, E. Composite A. J. 1, 1 (2003).", "included", "included", "included"],
        ["Fff, F. Composite B. J. 2, 2 (2004).", "included", "included", "included"],
        ["Ggg, G. Typo paper. J. 3, 3 (2005).", "included", "included", "included"],
        ["", "", "", ""],
        ["", "", "", ""],
    ],
    columns=["Study reference", "Screening phase", "Eligibility phase", "BenBio database"],
)
DEN_LIST = pd.DataFrame(
    [
        ["Aaa, A. Paper one. J. X 1, 1-2 (2000).", "included", "included", "included"],
        ["Hhh, H. Marked paper. J. 4, 4 (2007).", "included", "excluded", "previously excluded"],
        ["Iii, I. Unused. J. 5, 5 (2008).", "included", "included", "included"],
        ["Jjj, J. Another. J. 6, 6 (2009).", "excluded", "previously excluded", "previously excluded"],
    ],
    columns=["Study reference", "Screening phase", "Eligibility phase", "BenDEN database"],
)


def _brow(**kw) -> dict:
    r = dict(zip(bx.BIO_COLUMNS, [
        "R1", "Atlantic Ocean", "10.000", "20.000", "exact location", "30.0", "near-shore (0-50m)",
        "1", "g WM/ m2", "raw data", "macrobenthos", "NA", "NA", "NA", REF_A, "",
    ]))
    r.update(kw)
    return r


def _drow(**kw) -> dict:
    r = dict(zip(bx.DEN_COLUMNS, [
        "R1", "Atlantic Ocean", "10.000", "20.000", "exact location", "30.0", "near-shore (0-50m)",
        "1", "macrobenthos", "NA", "NA", "NA", REF_A,
    ]))
    r.update(kw)
    return r


BV, DV = "Biomass", "Density (ind/ m2)"
LO, HI = "Sieve mesh size (mm) lower limit", "Sieve mesh size (mm) upper limit"
B0 = _brow(**{BV: "2.58E+00", LO: "0.300 and 0.500", "sampling gear": "Grab"})
BIO_ROWS = [
    B0,  # 0
    dict(B0),  # 1: identical to row 0 in every column -> both flagged duplicate_source_row
    # 2: zero, converted with a conversion reference, latitude without longitude, no depth, no depth range,
    #    approximated, no gear: coords_missing, depth_missing, method_missing, zero_value, converted, approx
    _brow(**{BV: "0", "unit": "g C/ m2", "raw data/ converted data": "converted data",
             "Reference for biomass conversion factors": "Conv, X. (2010)", "Latitude (N)": "11",
             "Longitude (E)": "", "Depth (m)": "", "Depth range": "NA", "Location": "approximated location",
             "Benthos": "meiobenthos", "Reference": REF_B}),
    # 3: composite reference resolved part by part (two list entries)
    _brow(**{BV: "5", "sampling gear": "Core", "Depth (m)": "200.0", "Depth range": "continental shelf (>50-200m)",
             "Benthos": "megabenthos", "Reference": REF_C}),
    # 4: typo variant resolved by CROSSWALK
    _brow(**{BV: "7", "unit": "g DM/ m2", "sampling gear": "Trawl", "Reference": REF_G}),
    # 5: reference with no list entry (UNRESOLVED)
    _brow(**{BV: "9", "unit": "g AFDM/ m2", "Depth (m)": "5000", "Depth range": "abyssal plain/ continental rise (>2000m)",
             "Reference": REF_U}),
    # 6: same reference as row 0, another station, same method as row 0
    _brow(**{BV: "3", "Latitude (N)": "12", LO: "0.300 and 0.500", "sampling gear": "Grab"}),
]
DEN_ROWS = [
    # 0: same station and reference as BenBIO row 0 -> shares its location
    _drow(**{DV: "1.90E+02", LO: "1", "Sampling gear": "van Veen grab"}),
    # 1: no density -> excluded (no_density)
    _drow(**{DV: "", "Reference": REF_H}),
    # 2: the study the list marks as not in the database; trailing ". " is normalised away (same string as row 1)
    _drow(**{DV: "4", "Latitude (N)": "-5", "Longitude (E)": "100", "Depth (m)": "", "Depth range": "NA",
             "Reference": REF_H + ". "}),
    # 3: zero; the reference has a trailing space and period -> same reference as REF_A
    _drow(**{DV: "0", "Reference": REF_A + ". "}),
    # 4: UNRESOLVED reference, shared with BenBIO row 5
    _drow(**{DV: "8", "Reference": REF_U}),
]
# expected: BenBIO 7 facts; BenDEN 5 rows = 4 facts + 1 excluded; 11 facts, 11 samples; 6 references
# (A, B, composite C, Ggg typo, Unlisted, Hhh), 2 of them in both files (A, Unlisted); 8 locations;
# 9 methods; study_screening 6 (BenBio, 2 blank dropped) + 4 (BenDen) = 10.


@pytest.fixture(scope="module", autouse=True)
def constants():
    mp = pytest.MonkeyPatch()
    mp.setattr(bx, "CROSSWALK", [("Ggg, G. Typo paperr", ["Ggg, G. Typo paper"])])
    mp.setattr(bx, "UNRESOLVED", ["Unlisted, U. Nothing"])
    mp.setattr(bx, "KNOWN_USED_BUT_NOT_INCLUDED", {"bio": [], "den": ["Hhh, H. Marked paper"]})
    mp.setattr(bx, "INCLUDED_UNUSED", {"bio": [], "den": ["Iii, I. Unused"]})
    mp.setattr(bx, "KNOWN_LATITUDE_BEYOND_POLE", {"bio": {}, "den": {}})
    yield
    mp.undo()


def _raws(bio=BIO_ROWS, den=DEN_ROWS):
    return (
        {"bio": pd.DataFrame(bio, columns=bx.BIO_COLUMNS, dtype=str),
         "den": pd.DataFrame(den, columns=bx.DEN_COLUMNS, dtype=str)},
        {"bio": BIO_LIST.copy(), "den": DEN_LIST.copy()},
    )


def _build(bio=BIO_ROWS, den=DEN_ROWS):
    raws, lists = _raws(bio, den)
    return bx.build_tables(raws, lists, "2026-11-15")


@pytest.fixture(scope="module")
def built():
    return _build()


def test_counts(built) -> None:
    tables, skips, rep = built
    assert rep["source_rows"] == {"bio": 7, "den": 5}
    assert rep["facts_by_file"] == {"bio": 7, "den": 4} and len(tables["fact"]) == 11
    assert skips == [{"file": "BenDEN_database.csv", "data_row": 1, "reason": "no_density", "n_measurements": 0}]
    assert rep["rows_excluded"] == {"bio": 0, "den": 1}
    assert len(tables["sample"]) == 11 and len(tables["location"]) == 8 and len(tables["method"]) == 9
    assert len(tables["reference"]) == 6 == len(tables["citation"]) and rep["references_in_both_files"] == 2
    assert len(tables["study_screening"]) == 10
    assert rep["list_rows_dropped_as_fully_blank"] == {"bio": 2, "den": 0}
    assert rep["facts_by_attribute"] == {"benbio_biomass": 7, "benden_density": 4}
    assert rep["bio_facts_by_unit"] == {"g AFDM/ m2": 1, "g C/ m2": 1, "g DM/ m2": 1, "g WM/ m2": 4}
    validate_tables(add_derived_labels(tables))


def test_types_units_provenance(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    assert set(f.measurement_type) == {"B10"}
    assert f.loc["bio0:benbio_biomass", "subfield"] == "total_biomass"
    assert f.loc["den0:benden_density", "subfield"] == "total_density"
    assert f.loc["bio0:benbio_biomass", "value"] == 2.58 and f.loc["bio0:benbio_biomass", "units_raw"] == "g WM/ m2"
    assert f.loc["bio2:benbio_biomass", "units_raw"] == "g C/ m2" and f.loc["bio2:benbio_biomass", "value"] == 0
    assert f.loc["den0:benden_density", "units_raw"] == "ind/ m2" and f.loc["den0:benden_density", "value"] == 190.0
    assert f.loc["bio3:benbio_biomass", "source_row_ref"] == "BenBIO_database.csv#data_row=3:benbio_biomass"
    assert f.loc["den4:benden_density", "source_row_ref"] == "BenDEN_database.csv#data_row=4:benden_density"
    assert f[["sd", "se"]].isna().all().all() and f.n.isna().all()
    assert "den1:benden_density" not in f.index  # the excluded row


def test_flags(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")["flags"]
    assert f["bio0:benbio_biomass"] == ["duplicate_source_row", "habitat_from_scope"]
    assert f["bio1:benbio_biomass"] == ["duplicate_source_row", "habitat_from_scope"]
    assert f["bio2:benbio_biomass"] == [  # no habitat flag: its Depth range is NA
        "converted_by_source", "coords_approximate", "coords_missing", "depth_missing", "method_missing", "zero_value",
    ]
    assert f["bio3:benbio_biomass"] == ["habitat_from_scope"]  # composite, both entries included
    assert f["bio5:benbio_biomass"] == ["habitat_from_scope", "method_missing"]  # unresolved reference: no study flag; no gear
    assert f["den2:benden_density"] == ["depth_missing", "method_missing", "study_marked_excluded"]
    assert f["den3:benden_density"] == ["habitat_from_scope", "method_missing", "zero_value"]
    assert rep["flag_counts"]["bio"] == {
        "habitat_from_scope": 6, "duplicate_source_row": 2, "zero_value": 1, "coords_missing": 1,
        "depth_missing": 1, "method_missing": 2, "converted_by_source": 1, "coords_approximate": 1,
    }
    assert rep["flag_counts"]["den"] == {
        "habitat_from_scope": 3, "zero_value": 1, "depth_missing": 1, "method_missing": 3,
        "study_marked_excluded": 1,
    }


def test_labels_and_locations(built) -> None:
    tables = built[0]
    loc = add_derived_labels(tables)["location"].set_index("location_key")
    shared = f"ref:{REF_A}|R1|Atlantic Ocean|10.000|20.000|30.0|exact location|near-shore (0-50m)"
    assert shared in loc.index
    f = tables["fact"].set_index("fact_key")
    # BenBIO rows 0 and 1 and BenDEN rows 0 and 3 are one location (same reference and station)
    assert {f.loc[k, "location_key"] for k in ("bio0:benbio_biomass", "bio1:benbio_biomass",
                                                "den0:benden_density", "den3:benden_density")} == {shared}
    assert loc.loc[shared, "habitat"] == "Subtidal soft sediment" and pd.isna(loc.loc[shared, "setting"])
    assert loc.loc[shared, "water_depth_m"] == 30.0 and loc.loc[shared, "site_name"] == "R1"
    assert loc.loc[shared, "water_body"] == "Atlantic Ocean" and loc.loc[shared, "position_method"] == "exact location"
    shelf = [k for k in loc.index if "continental shelf" in k][0]
    assert loc.loc[shelf, "setting"] == "shelf"
    deep = [k for k in loc.index if "abyssal" in k][0]
    assert loc.loc[deep, "setting"] == "open ocean"
    noshelf = [k for k in loc.index if k.startswith(f"ref:{REF_B}")][0]  # Depth range NA: no habitat at all
    assert pd.isna(loc.loc[noshelf, "habitat"]) and pd.isna(loc.loc[noshelf, "habitat_type"])
    assert loc.loc[noshelf, "latitude"] == 11.0 and pd.isna(loc.loc[noshelf, "longitude"])  # latitude kept as reported
    assert pd.isna(loc.loc[noshelf, "water_depth_m"]) and loc.loc[noshelf, "position_method"] == "approximated location"


def test_samples_and_methods(built) -> None:
    tables = built[0]
    s = tables["sample"].set_index("sample_key")
    assert s.loc["bio2", "taxon"] == "meiobenthos" and set(s.taxon_rank) == {"size_class"}
    assert s.loc["bio3", "taxon"] == "megabenthos" and s.sampling_year.isna().all()
    m = tables["method"].set_index("method_key")
    k0 = f"ref:{REF_A}|benbio_biomass|Grab|0.300 and 0.500||raw data|"
    assert k0 in m.index and m.loc[k0, "technique"] == "Grab" and m.loc[k0, "method_status"] == "matched"
    assert tables["fact"].set_index("fact_key").loc["bio6:benbio_biomass", "method_key"] == k0  # same method as row 0
    assert m.loc[k0, "params"] == (
        '{"conversion_reference": null, "raw_or_converted": "raw data", '
        '"sieve_lower_mm_raw": "0.300 and 0.500", "sieve_upper_mm_raw": null}'
    )
    k2 = f"ref:{REF_B}|benbio_biomass||||converted data|Conv, X. (2010)"
    assert m.loc[k2, "method_status"] == "method_id_null" and pd.isna(m.loc[k2, "technique"])
    assert '"conversion_reference": "Conv, X. (2010)"' in m.loc[k2, "params"]
    kd = f"ref:{REF_A}|benden_density|van Veen grab|1|||"
    assert m.loc[kd, "params"] == '{"sieve_lower_mm_raw": "1", "sieve_upper_mm_raw": null}'  # no conversion keys for BenDEN


def test_references_and_citations(built) -> None:
    tables, _, rep = built
    assert sorted(tables["reference"].reference_key) == sorted(
        "ref:" + r for r in (REF_A, REF_B, REF_C, REF_G, REF_U, REF_H)
    )
    c = tables["citation"].set_index("bibliography_id")
    assert c.loc[REF_B, "doi"] == "10.1000/abc.2" and c.loc[REF_B, "year"] == 2001  # DOI lower-cased
    assert pd.isna(c.loc[REF_C, "doi"]) and pd.isna(c.loc[REF_C, "year"])  # composite: nothing parsed
    assert c.loc[REF_G, "year"] == 2005 and c.loc[REF_A, "title"] == REF_A and pd.isna(c.loc[REF_A, "doi"])
    assert rep["citations_with_doi"] == 1 and rep["composite_reference_strings"] == 1
    assert rep["raw_reference_strings_changed_by_whitespace_or_period_normalisation"] == 2  # REF_H+". " and REF_A+". "
    assert rep["reference_strings_equal_after_dropping_case_and_punctuation_but_not_merged"] == []


def test_study_resolution_and_screening(built) -> None:
    tables, _, rep = built
    s = tables["study_screening"].set_index("study_key")
    assert s.loc["benbio_0000", "reference_key"] == f"ref:{REF_A}"
    assert s.loc["benbio_0001", "reference_key"] == f"ref:{REF_B}"
    assert s.loc["benbio_0005", "reference_key"] == f"ref:{REF_G}"  # CROSSWALK variant, a whole string
    assert pd.isna(s.loc["benbio_0003", "reference_key"]) and pd.isna(s.loc["benbio_0004", "reference_key"])  # composite
    assert pd.isna(s.loc["benbio_0002", "reference_key"])  # excluded study, nobody cites it
    assert s.loc["benden_0000", "reference_key"] == f"ref:{REF_A}" and s.loc["benden_0001", "reference_key"] == f"ref:{REF_H}"
    assert pd.isna(s.loc["benden_0002", "reference_key"])  # included but unused (declared)
    assert s.loc["benden_0001", "database_status"] == "previously excluded"
    assert s.loc["benbio_0002", "screening_phase"] == "excluded"
    assert len(s) == 10 and rep["study_screening_with_reference"] == 5
    assert rep["study_lists"]["bio"] == {
        "list_entries": 6, "linked_to_a_reference": 3, "used_only_inside_composite_or_shared": 2,
        "not_used_by_any_fact": 1, "entries_used_by_more_than_one_string": [],
    }
    assert rep["study_lists"]["den"]["linked_to_a_reference"] == 2 and rep["study_lists"]["den"]["not_used_by_any_fact"] == 2
    assert rep["reference_strings_with_unresolved_parts"] == {"bio": {REF_U: [REF_U]}, "den": {REF_U: [REF_U]}}
    assert rep["reference_strings_resolved_part_by_part"] == {"bio": 1, "den": 0}
    assert rep["bio_raw_data_rows_with_a_conversion_reference"] == 0
    assert rep["bio_converted_rows_by_conversion_reference_state"] == {"NA": 0, "given": 1}
    assert rep["rows_with_latitude_but_no_longitude"] == {"bio": 1, "den": 0}


def test_resolution_functions() -> None:
    s, nblank = bx.build_studies(BIO_LIST.copy(), "bio")
    assert nblank == 2 and len(s) == 6 and list(s.study_key)[:2] == ["benbio_0000", "benbio_0001"]
    res, unres, how, comp = bx.resolve_references({REF_A, REF_C, REF_G, REF_U}, s)
    assert res[REF_A] == ["benbio_0000"] and res[REF_C] == ["benbio_0003", "benbio_0004"]
    assert res[REF_G] == ["benbio_0005"] and res[REF_U] == [] and unres == {REF_U: [REF_U]}
    assert comp == {REF_C} and how["whole"] == 1 and how["parts"] == 2 and how["crosswalk_parts"] == 1


def test_value_is_correctly_rounded_float() -> None:
    tables, _, _ = _build([dict(BIO_ROWS[0], **{BV: "0.1"})] + BIO_ROWS[1:], DEN_ROWS)
    assert tables["fact"].set_index("fact_key").loc["bio0:benbio_biomass", "value"] == 0.1


# ---- fail-loud checks


def _raises(match, bio=BIO_ROWS, den=DEN_ROWS, lists=None):
    raws, lsts = _raws(bio, den)
    if lists:
        lsts.update(lists)
    with pytest.raises(ContractError, match=match):
        bx.build_tables(raws, lsts, "2026-11-15")


def test_unknown_unit_raises() -> None:
    _raises("unknown or missing units", bio=[dict(BIO_ROWS[0], unit="g/m2")])


def test_unknown_raw_converted_raises() -> None:
    _raises("raw/converted", bio=[dict(BIO_ROWS[0], **{"raw data/ converted data": "derived"})])


def test_missing_biomass_raises() -> None:
    _raises("without a biomass", bio=[dict(BIO_ROWS[0], **{BV: "NA"})])


def test_non_numeric_value_raises() -> None:
    _raises("non-numeric", bio=[dict(BIO_ROWS[0], **{BV: "12x"})])


def test_negative_value_raises() -> None:
    _raises("negative", bio=[dict(BIO_ROWS[0], **{BV: "-1"})])


def test_latitude_out_of_range_raises() -> None:
    _raises("latitude", bio=[dict(BIO_ROWS[0], **{"Latitude (N)": "91"})])


def test_known_latitude_beyond_the_pole_is_kept(monkeypatch) -> None:
    monkeypatch.setattr(bx, "KNOWN_LATITUDE_BEYOND_POLE", {"bio": {}, "den": {4: "90.012"}})
    tables, _, rep = _build(den=DEN_ROWS[:4] + [dict(DEN_ROWS[4], **{"Latitude (N)": "90.012"})])
    f = tables["fact"].set_index("fact_key")
    lat = tables["location"].set_index("location_key").loc[f.loc["den4:benden_density", "location_key"], "latitude"]
    assert lat == 90.012 and rep["rows_with_latitude_beyond_the_pole"]["den"] == {4: "90.012"}
    assert "lat_beyond_pole" in f.loc["den4:benden_density", "flags"]
    assert rep["flag_counts"]["den"]["lat_beyond_pole"] == 1
    assert not any("lat_beyond_pole" in x for k, x in f["flags"].items() if k != "den4:benden_density")


def test_latitude_beyond_the_pole_in_another_row_raises(monkeypatch) -> None:
    monkeypatch.setattr(bx, "KNOWN_LATITUDE_BEYOND_POLE", {"bio": {}, "den": {4: "90.012"}})
    _raises("latitude outside", den=DEN_ROWS[:3] + [dict(DEN_ROWS[3], **{"Latitude (N)": "90.012"}), DEN_ROWS[4]])


def test_longitude_out_of_range_raises() -> None:
    _raises("longitude", bio=[dict(BIO_ROWS[0], **{"Longitude (E)": "181"})])


def test_non_positive_depth_raises() -> None:
    _raises("non-positive depth", bio=[dict(BIO_ROWS[0], **{"Depth (m)": "0"})])


def test_unknown_benthos_raises() -> None:
    _raises("unknown Benthos", bio=[dict(BIO_ROWS[0], Benthos="nanobenthos")])


def test_unknown_location_label_raises() -> None:
    _raises("unknown Location", bio=[dict(BIO_ROWS[0], Location="guessed")])


def test_replacement_character_raises(tmp_path) -> None:
    df = pd.DataFrame({"a": ["ok", "bad \ufffd"]})
    with pytest.raises(ContractError, match="U\\+FFFD in columns"):
        bx._assert_no_replacement_char(df, "x.csv")
    p = tmp_path / "x.csv"
    p.write_bytes("a,b\nCvitkovi\xe6,1\n".encode("cp1252"))  # a valid cp1252 byte reads fine
    assert bx._read(p, ",")["a"].iloc[0] == "Cvitkovi\xe6"


def test_wrong_columns_raise() -> None:
    raws, lists = _raws()
    raws["den"] = raws["den"].rename(columns={"Sampling gear": "gear"})
    with pytest.raises(ContractError, match="unexpected columns"):
        bx.build_tables(raws, lists, "2026-11-15")


def test_wrong_density_header_raises() -> None:
    raws, lists = _raws()
    raws["den"] = raws["den"].rename(columns={"Density (ind/ m2)": "Density (ind/ m3)"})
    with pytest.raises(ContractError, match="unexpected columns"):
        bx.build_tables(raws, lists, "2026-11-15")


def test_reference_part_in_no_constant_raises() -> None:
    _raises("matches no list entry", bio=[dict(BIO_ROWS[0], Reference="Nobody, N. Unknown (1999)")])


def test_blank_list_row_not_at_end_raises() -> None:
    lst = pd.concat([BIO_LIST.iloc[[6]], BIO_LIST.iloc[:6]], ignore_index=True)
    _raises("blank rows are not all at the end", lists={"bio": lst})


def test_list_entry_without_status_raises() -> None:
    lst = BIO_LIST.copy()
    lst.loc[0, "BenBio database"] = ""
    _raises("without a database status", lists={"bio": lst})


def test_included_unused_undeclared_raises(monkeypatch) -> None:
    monkeypatch.setattr(bx, "INCLUDED_UNUSED", {"bio": [], "den": []})
    _raises("included-unused")


def test_used_not_included_undeclared_raises(monkeypatch) -> None:
    monkeypatch.setattr(bx, "KNOWN_USED_BUT_NOT_INCLUDED", {"bio": [], "den": []})
    _raises("used-not-included")


def test_crosswalk_entry_matching_nothing_raises(monkeypatch) -> None:
    monkeypatch.setattr(bx, "CROSSWALK", bx.CROSSWALK + [("Never cited", ["Aaa, A. Paper one"])])
    _raises("CROSSWALK entries that match nothing")


def test_unresolved_entry_matching_nothing_raises(monkeypatch) -> None:
    monkeypatch.setattr(bx, "UNRESOLVED", bx.UNRESOLVED + ["Never cited either"])
    _raises("UNRESOLVED entries that match nothing")


def test_crosswalk_list_prefix_must_be_one_entry(monkeypatch) -> None:
    monkeypatch.setattr(bx, "CROSSWALK", [("Ggg, G. Typo paperr", ["Zzz, no such entry"])])
    _raises("matches 0 entries")


def test_roundtrip_global_build_and_determinism(built, tmp_path) -> None:
    tables, _, _ = built
    write_extracted(tables, tmp_path / "ex")
    back = read_extracted(tmp_path / "ex")
    assert len(back["fact"]) == 11
    g = assign_global_keys([back])
    assert list(g["fact"].measurement_id) == list(range(1, 12))
    out1, out2 = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    build_compilations([tmp_path / "ex"], out1)
    build_compilations([tmp_path / "ex"], out2)
    q = (
        "SELECT f.value, f.attribute, f.units_raw, s.taxon, l.site_name, l.habitat "
        "FROM measurement_fact f JOIN sample_dim s USING (sample_id) "
        "JOIN location_dim l USING (location_id) ORDER BY f.measurement_id"
    )
    r1 = duckdb.connect(str(out1), read_only=True).execute(q).fetchall()
    r2 = duckdb.connect(str(out2), read_only=True).execute(q).fetchall()
    assert r1 == r2 and len(r1) == 11
    n = duckdb.connect(str(out1), read_only=True).execute("select count(*) from study_screening_dim where reference_id is null").fetchone()
    assert n == (5,)


def test_unregistered_flag_raises_at_validation(built) -> None:
    tables, _, _ = built
    bad = {k: v.copy() for k, v in tables.items()}
    flags = list(bad["fact"]["flags"])
    flags[0] = list(flags[0]) + ["made_up_flag"]
    bad["fact"]["flags"] = flags
    with pytest.raises(ContractError, match="made_up_flag"):
        write_extracted(bad, Path("/nonexistent-never-written"))
