"""Unit tests (rung 1) for the strydom-seagrass extract.py.

Hand-built fixture (a PANGAEA-style header + a 10-row table); expected output is stated by
inspection in comments.
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

_EXTRACT = (
    Path(__file__).resolve().parents[1] / "data" / "compilations" / "strydom-seagrass" / "extract.py"
)
_spec = importlib.util.spec_from_file_location("strydom_extract", _EXTRACT)
assert _spec and _spec.loader
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

REL, ABS = 1e-3, 1e-3
HEADER = [
    "/* DATA DESCRIPTION:",
    "Citation:\tStrydom (2021): test",
    "Related to:\tSmith, Ann (2000): Title one. J A, 1, 1-2, https://doi.org/10.1000/Smith.1",
    "\tJones, Bo; Lee, Cy (2005): Title two. J B, 2, 3-4, https://doi.org/10.1000/jones.2",
    "\tKing, Di; Ng, Ed; Oh, Fy (2010): Title three. J C, 3, 5-6",
    "\tKing, Di; Ng, Ed (2010): Title three b. J D, 4, 7-8, https://doi.org/10.1000/king.b",
    "\tWu, Gi (2012): Title four. J E, 5, 9-10",
    "\tWu, Gi; Xu, Hu (2012): Title five. J F, 6, 11-12, https://doi.org/10.1000/wu.xu",
    "License:\tCreative Commons Attribution 4.0 International (CC-BY-4.0) (URI: x)",
    "Change history:\t2025-05-06T14:34:47 - Corrected some longitudes.",
    "*/",
]
B, A_, T = "Seagr biom above dm [g/m**2]", "Seagr biom below dm [g/m**2]", "Seagr biom tot dm [g/m**2]"
SHOOT, COVER, LEAFD = "Seagr shoot dens [#/m**2]", "Seagr cov [%]", "Seagr leaf dens [#/m**2]"
PA, PB, PT = (
    "Seagr prod above dm [g/m**2/day]",
    "Seagr prod below dm [g/m**2/day]",
    "Seagr prod total dm [g/m**2/day]",
)
PLEAF = "Seagr leaf prod dm [g/m**2/day]"


def _row(**kw) -> dict:
    r = {c: "" for c in st.EXPECTED_COLUMNS}
    r.update(kw)
    return r


def _loc1(**kw):
    return {"Latitude": "10.5", "Longitude": "20.25", "Seagr bioregion": "Mediterranean",
            "Habitat": "Coastal", **kw}


def _loc2(**kw):
    return {"Latitude": "-5", "Longitude": "100", "Seagr bioregion": "Tropical Atlantic",
            "Habitat": "Estuarine", **kw}


ROWS = [
    # 0: 4 facts (above 10, below 5, total 15 consistent; shoot 100); ref Smith 2000 -> matched, DOI
    _row(**_loc1(), Genus="Zostera", **{"Year obs [a AD]": "2000", B: "10", A_: "5", T: "15", SHOOT: "100",
                                        "Reference": "Smith 2000"}),
    # 1: identical to row 0 in every column -> rows 0 and 1 flagged duplicate_source_row; 4 facts
    _row(**_loc1(), Genus="Zostera", **{"Year obs [a AD]": "2000", B: "10", A_: "5", T: "15", SHOOT: "100",
                                        "Reference": "Smith 2000"}),
    # 2: same coordinates as row 0 but Estuarine -> a separate location; year blank; 1 fact;
    #    "Jones and Lee 2005" -> matched (two-author entry), DOI
    _row(**_loc1(Habitat="Estuarine"), Genus="Thalassia", **{T: "20", "Reference": "Jones and Lee 2005"}),
    # 3: biomass 3+4 != 9 (flag on all 3), production 1+1 == 2 ok; 6 facts; "King et al. 2010" matches
    #    two entries -> ambiguous
    _row(**_loc2(), Genus="Enhalus", **{"Year obs [a AD]": "2010", B: "3", A_: "4", T: "9", PA: "1", PB: "1",
                                        PT: "2", "Reference": "King et al. 2010"}),
    # 4: no measurement -> excluded
    _row(**_loc1(), Genus="Zostera", **{"Year obs [a AD]": "2001", "Reference": "Smith 2000"}),
    # 5: 2 facts (cover, leaf density); "Wu 2012" -> the one-author entry only (matched, no DOI)
    _row(**_loc1(), Genus="Halophila", **{"Year obs [a AD]": "2012", COVER: "50.5", LEAFD: "7",
                                          "Reference": "Wu 2012"}),
    # 6: 1 fact; "Wu et al. 2012" -> the two-author entry only (matched, DOI)
    _row(**_loc1(), Genus="Halophila", **{"Year obs [a AD]": "2012", SHOOT: "0.1", "Reference": "Wu et al. 2012"}),
    # 7: 1 fact; "Brown 2001a" -> no entry
    _row(**_loc2(), Genus="Ruppia", **{"Year obs [a AD]": "2001", COVER: "5", "Reference": "Brown 2001a"}),
    # 8: 1 fact; "Smith 2000a": a year letter -> ambiguous even though one entry has that author-year
    _row(**_loc1(), Genus="Ruppia", **{"Year obs [a AD]": "2000", PLEAF: "0.5", "Reference": "Smith 2000a"}),
    # 9: 1 fact; "Weird ref" has no year -> unparsed
    _row(**_loc1(), Genus="Ruppia", **{"Year obs [a AD]": "2003", SHOOT: "9", "Reference": "Weird ref"}),
]
# expected: 10 source rows, 1 excluded, 9 with facts, 21 facts, 3 locations, 9 samples, 8 references
# (Smith 2000, Jones and Lee 2005, King et al. 2010, Wu 2012, Wu et al. 2012, Brown 2001a,
# Smith 2000a, Weird ref), 8 citations, methods = attributes present.


def _raw(rows=ROWS) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=st.EXPECTED_COLUMNS, dtype=str)


@pytest.fixture(scope="module")
def built():
    return st.build_tables(HEADER, _raw(), "2026-11-15", REL, ABS)


def test_counts(built) -> None:
    tables, skips, rep = built
    assert rep["source_rows"] == 10 and rep["rows_with_facts"] == 9 and rep["rows_excluded"] == 1
    assert skips == [{"data_row": 4, "reason": "no_seagrass_measurement", "n_measurements": 0}]
    assert len(tables["fact"]) == 21 == rep["nonblank_measurement_cells"]
    assert len(tables["location"]) == 3 and len(tables["sample"]) == 9
    assert len(tables["reference"]) == 8 == len(tables["citation"])
    assert len(tables["study_screening"]) == 0
    assert rep["facts_by_attribute"]["seagrass_biomass_total"] == 4  # rows 0, 1, 2, 3
    assert rep["facts_by_attribute"]["seagrass_shoot_density"] == 4  # rows 0, 1, 6, 9
    assert len(tables["method"]) == len(rep["facts_by_attribute"])
    validate_tables(add_derived_labels(tables))


def test_source_row(built) -> None:
    src = built[0]["source"].iloc[0]
    assert src.version == "2025-05-06" and src.license == "CC-BY-4.0"
    assert src.doi == "10.1594/PANGAEA.929968" and src.access_date == "2026-11-15"


def test_types_units_and_provenance(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    by = f.drop_duplicates("attribute").set_index("attribute")[["measurement_type", "subfield"]]
    assert tuple(by.loc["seagrass_biomass_above"]) == ("B2", "above_ground_biomass")
    assert tuple(by.loc["seagrass_shoot_density"]) == ("B3", "shoot_or_stem_density")
    assert tuple(by.loc["seagrass_production_total"]) == ("B6", "total_npp")
    assert f.loc["row0:seagrass_biomass_above", "units_raw"] == "g/m**2"
    assert f.loc["row0:seagrass_shoot_density", "units_raw"] == "#/m**2"
    assert f.loc["row5:seagrass_percent_cover", "units_raw"] == "%"
    assert f.loc["row3:seagrass_production_total", "units_raw"] == "g/m**2/day"
    assert f.loc["row5:seagrass_percent_cover", "value"] == 50.5  # a percentage, unchanged
    assert f.loc["row6:seagrass_shoot_density", "value"] == 0.1  # correctly rounded float
    assert f.loc["row0:seagrass_biomass_above", "source_row_ref"] == (
        "Seagrass_db.tab#data_row=0:seagrass_biomass_above"
    )
    assert f.loc["row9:seagrass_shoot_density", "source_row_ref"].endswith("#data_row=9:seagrass_shoot_density")
    assert f[["sd", "se"]].isna().all().all() and f.n.isna().all()


def test_labels(built) -> None:
    loc = add_derived_labels(built[0])["location"]
    assert set(loc.habitat) == {"Seagrass meadow"} and set(loc.bottom_type) == {"soft"}
    est = loc[loc.geomorphic_setting == "Estuarine"]
    assert len(est) == 2 and set(est.setting) == {"estuary"}
    assert loc[loc.geomorphic_setting == "Coastal"].setting.isna().all()
    assert set(loc.bioregion) == {"Mediterranean", "Tropical Atlantic"}
    assert loc.marine_ecoregion.isna().all()


def test_flags(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    assert all("habitat_from_scope" in x for x in f["flags"])
    f = f.assign(flags=[[x for x in fl if x != "habitat_from_scope"] for fl in f["flags"]])
    assert f.loc["row0:seagrass_shoot_density", "flags"] == ["duplicate_source_row"]
    assert f.loc["row1:seagrass_shoot_density", "flags"] == ["duplicate_source_row"]
    assert f.loc["row2:seagrass_biomass_total", "flags"] == ["year_missing"]
    for a in ("above", "below", "total"):
        assert f.loc[f"row3:seagrass_biomass_{a}", "flags"] == ["total_ne_sum_of_parts"]
        assert f.loc[f"row3:seagrass_production_{a}", "flags"] == []
    assert "total_ne_sum_of_parts" not in f.loc["row0:seagrass_biomass_total", "flags"]
    assert rep["flag_counts"] == {"habitat_from_scope": 21, "duplicate_source_row": 8, "year_missing": 1,
                                  "total_ne_sum_of_parts": 3}
    assert rep["duplicate_source_rows_flagged"] == 2


def test_reference_match(built) -> None:
    tables, _, rep = built
    m = rep["reference_match"]
    assert m["matched"] == 4 and m["matched_with_doi"] == 3
    assert m["ambiguous"] == ["King et al. 2010", "Smith 2000a"]
    assert m["no_entry"] == ["Brown 2001a"] and m["unparsed"] == ["Weird ref"]
    assert m["header_entries_matched_by_more_than_one_string"] == []
    nm = m["near_misses_same_first_author_any_year_not_matched"]
    assert set(nm) == {"King et al. 2010", "Smith 2000a", "Brown 2001a"}  # every no_entry / ambiguous string
    assert nm["Smith 2000a"] == ["Smith, Ann (2000)"] and nm["Brown 2001a"] == []
    assert len(nm["King et al. 2010"]) == 2
    assert rep["reference_strings_with_replacement_char_u_fffd"] == []
    assert rep["reference_strings_all_in_rows_without_measurement"] == []
    assert rep["header_entries_matched_by_no_string"] == 2  # King (3 authors) and King (2 authors): both ambiguous
    assert rep["header_citations"] == 6 and rep["header_citations_with_doi"] == 4
    c = tables["citation"].set_index("bibliography_id")
    assert c.loc["Smith 2000", "doi"] == "10.1000/smith.1"  # lower-cased
    assert c.loc["Smith 2000", "authors"] == "Smith, Ann" and c.loc["Smith 2000", "year"] == 2000
    assert c.loc["Jones and Lee 2005", "doi"] == "10.1000/jones.2"
    assert c.loc["Jones and Lee 2005", "authors"] == "Jones, Bo; Lee, Cy"
    assert pd.isna(c.loc["Wu 2012", "doi"]) and c.loc["Wu 2012", "authors"] == "Wu, Gi"  # matched, entry has no DOI
    assert c.loc["Wu et al. 2012", "doi"] == "10.1000/wu.xu"
    for unmatched in ("King et al. 2010", "Smith 2000a", "Brown 2001a", "Weird ref"):
        assert pd.isna(c.loc[unmatched, "doi"]) and pd.isna(c.loc[unmatched, "authors"])
    assert c.loc["King et al. 2010", "year"] == 2010 and c.loc["Brown 2001a", "year"] == 2001
    assert pd.isna(c.loc["Weird ref", "year"])
    assert set(tables["reference"].reference_key) == {"ref:" + r for r in c.index}
    f = tables["fact"].set_index("fact_key")
    assert f.loc["row0:seagrass_shoot_density", "reference_key"] == "ref:Smith 2000"


def test_location_sample_method(built) -> None:
    tables, _, _ = built
    loc = tables["location"].set_index("location_key")
    assert set(loc.index) == {
        "10.5|20.25|Coastal|Mediterranean", "10.5|20.25|Estuarine|Mediterranean",
        "-5.0|100.0|Estuarine|Tropical Atlantic",
    }
    k = "10.5|20.25|Coastal|Mediterranean"
    assert loc.loc[k, "habitat_type"] == "seagrass" and loc.loc[k, "geomorphic_setting"] == "Coastal"
    assert loc.loc[k, "latitude"] == 10.5 and loc.loc[k, "longitude"] == 20.25
    s = tables["sample"].set_index("sample_key")
    assert s.loc["row0", "taxon"] == "Zostera" and set(s.taxon_rank) == {"genus"}
    assert s.loc["row0", "sampling_year"] == 2000 and pd.isna(s.loc["row2", "sampling_year"])
    assert s.sampling_month.isna().all()
    m = tables["method"].set_index("method_key")
    assert (m.method_status == "method_id_null").all()
    assert "sum" in m.loc["seagrass_biomass_total", "params"]
    assert "sum" not in m.loc["seagrass_biomass_above", "params"]


# ---- reference matching rules

ENTRIES = [st.parse_entry(e) for e in st.header_citations(HEADER)]


@pytest.mark.parametrize(
    "ref,status,k",
    [
        ("Smith 2000", "matched", 0),
        ("Jones and Lee 2005", "matched", 1),
        ("Jones & Lee 2005", "matched", 1),
        ("Jones et al. 2005", "matched", 1),  # et al. needs >= 2 authors and the first surname
        ("Lee and Jones 2005", "no_entry", None),  # order matters
        ("Smith and Jones 2000", "no_entry", None),
        ("Smith 2001", "no_entry", None),  # year must be equal
        ("King et al. 2010", "ambiguous", None),
        ("Wu 2012", "matched", 4),
        ("Wu et al. 2012", "matched", 5),
        ("Smith 2000a", "ambiguous", None),
        ("Smith et al. 2000", "no_entry", None),  # the only Smith entry has one author
        ("Bryars. 2009", "no_entry", None),  # trailing period is stripped; no such entry
        ("Smith. 2000", "matched", 0),
        ("Smith et al, 2000", "no_entry", None),
        ("Jones & Lee. 2005", "matched", 1),
        ("Weird ref", "unparsed", None),
        ("Jones and Lee and Kim 2005", "unparsed", None),
    ],
)
def test_match_reference(ref, status, k) -> None:
    got = st.match_reference(ref, ENTRIES)
    assert got[0] == status
    if status == "matched":
        assert got[1] == k
    else:
        assert got[1] is None


def test_diacritics_and_hyphens_are_ignored_in_surnames() -> None:
    e = [st.parse_entry("Puigcorbé, Viena (2020): T. J, https://doi.org/10.1/x")]
    assert st.match_reference("Puigcorbe 2020", e) == ("matched", 0)
    e = [st.parse_entry("Al-Bader, D (2014): T. J")]
    assert st.match_reference("Al Bader 2014", e) == ("matched", 0)


def test_parse_entry_requires_the_authors_year_form() -> None:
    with pytest.raises(ContractError, match="not in 'Authors"):
        st.parse_entry("no year here")


# ---- fail-loud checks


def test_unknown_genus_raises() -> None:
    with pytest.raises(ContractError, match="unknown Genus"):
        st.build_tables(HEADER, _raw([dict(ROWS[0], Genus="Zosterax")]), "2026-11-15", REL, ABS)


def test_unknown_habitat_raises() -> None:
    with pytest.raises(ContractError, match="unknown Habitat"):
        st.build_tables(HEADER, _raw([dict(ROWS[0], Habitat="Marine")]), "2026-11-15", REL, ABS)


def test_unknown_bioregion_raises() -> None:
    with pytest.raises(ContractError, match="unknown Seagr bioregion"):
        st.build_tables(HEADER, _raw([dict(ROWS[0], **{"Seagr bioregion": "Arctic"})]), "2026-11-15", REL, ABS)


def test_non_numeric_value_raises() -> None:
    with pytest.raises(ContractError, match="non-numeric"):
        st.build_tables(HEADER, _raw([dict(ROWS[0], **{SHOOT: "12x"})]), "2026-11-15", REL, ABS)


def test_na_literal_in_a_measurement_raises() -> None:
    with pytest.raises(ContractError, match="non-numeric"):
        st.build_tables(HEADER, _raw([dict(ROWS[0], **{SHOOT: "NA"})]), "2026-11-15", REL, ABS)


def test_longitude_out_of_range_raises() -> None:
    with pytest.raises(ContractError, match="longitude"):
        st.build_tables(HEADER, _raw([dict(ROWS[0], Longitude="200")]), "2026-11-15", REL, ABS)


def test_blank_coordinate_raises() -> None:
    with pytest.raises(ContractError, match="NULL in rows"):
        st.build_tables(HEADER, _raw([dict(ROWS[0], Latitude="")]), "2026-11-15", REL, ABS)


def test_bad_year_raises() -> None:
    with pytest.raises(ContractError, match="non-year"):
        st.build_tables(HEADER, _raw([dict(ROWS[0], **{"Year obs [a AD]": "20x0"})]), "2026-11-15", REL, ABS)


def test_wrong_columns_raise() -> None:
    raw = _raw().rename(columns={"Genus": "Genera"})
    with pytest.raises(ContractError, match="unexpected columns"):
        st.build_tables(HEADER, raw, "2026-11-15", REL, ABS)


def test_header_without_license_raises() -> None:
    bad = [l for l in HEADER if not l.startswith("License:")]
    with pytest.raises(ContractError, match="License"):
        st.build_tables(bad, _raw(), "2026-11-15", REL, ABS)


def test_header_wrong_license_raises() -> None:
    bad = [l.replace("CC-BY-4.0", "CC-BY-NC") for l in HEADER]
    with pytest.raises(ContractError, match="does not state"):
        st.build_tables(bad, _raw(), "2026-11-15", REL, ABS)


def test_header_without_change_date_raises() -> None:
    bad = [l if not l.startswith("Change history:") else "Change history:\tsome text" for l in HEADER]
    with pytest.raises(ContractError, match="change date"):
        st.build_tables(bad, _raw(), "2026-11-15", REL, ABS)


def test_tolerance_controls_total_flag() -> None:
    rows = [dict(ROWS[0], **{SHOOT: "", T: "15.01"})]
    loose, _, _ = st.build_tables(HEADER, _raw(rows), "2026-11-15", 1e-2, 1e-2)
    tight, _, _ = st.build_tables(HEADER, _raw(rows), "2026-11-15", 1e-6, 1e-6)
    assert "total_ne_sum_of_parts" not in {x for f in loose["fact"]["flags"] for x in f}
    assert "total_ne_sum_of_parts" in {x for f in tight["fact"]["flags"] for x in f}


def test_read_raw_splits_header_and_table(tmp_path) -> None:
    p = tmp_path / "Seagrass_db.tab"
    body = "\t".join(st.EXPECTED_COLUMNS) + "\n" + "\t".join(ROWS[0][c] for c in st.EXPECTED_COLUMNS) + "\n"
    p.write_text("\n".join(HEADER) + "\n" + body, encoding="utf-8")
    header, df = st.read_raw(p)
    assert header == HEADER and len(df) == 1 and list(df.columns) == st.EXPECTED_COLUMNS
    assert df[SHOOT].iloc[0] == "100" and df["Year obs [a AD]"].iloc[0] == "2000"
    p.write_text("\n".join(HEADER[1:]) + "\n" + body, encoding="utf-8")
    with pytest.raises(ContractError, match="not a PANGAEA"):
        st.read_raw(p)


def test_roundtrip_global_build_and_determinism(built, tmp_path) -> None:
    tables, _, _ = built
    write_extracted(tables, tmp_path / "ex")
    back = read_extracted(tmp_path / "ex")
    assert len(back["fact"]) == 21
    g = assign_global_keys([back])
    assert list(g["fact"].measurement_id) == list(range(1, 22))
    out1, out2 = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    build_compilations([tmp_path / "ex"], out1)
    build_compilations([tmp_path / "ex"], out2)
    q = (
        "SELECT f.value, f.attribute, f.units_raw, s.taxon, l.bioregion "
        "FROM measurement_fact f JOIN sample_dim s USING (sample_id) "
        "JOIN location_dim l USING (location_id) ORDER BY f.measurement_id"
    )
    r1 = duckdb.connect(str(out1), read_only=True).execute(q).fetchall()
    r2 = duckdb.connect(str(out2), read_only=True).execute(q).fetchall()
    assert r1 == r2 and len(r1) == 21


def test_unregistered_flag_raises_at_validation(built) -> None:
    tables, _, _ = built
    bad = {k: v.copy() for k, v in tables.items()}
    flags = list(bad["fact"]["flags"])
    flags[0] = list(flags[0]) + ["made_up_flag"]
    bad["fact"]["flags"] = flags
    with pytest.raises(ContractError, match="made_up_flag"):
        write_extracted(bad, Path("/nonexistent-never-written"))
