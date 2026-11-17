"""Unit tests (rung 1) for the usgs-wetlands extract.py.

Hand-built fixture (five sheets, 17 rows, a Citations tab); every expected number is derived by
inspection in the comments. The real release's declared reference sets are replaced by a small
``Declared`` of the fixture's own.
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

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "usgs-wetlands" / "extract.py"
_spec = importlib.util.spec_from_file_location("usgs_extract", _EXTRACT)
assert _spec and _spec.loader
us = importlib.util.module_from_spec(_spec)
sys.modules["usgs_extract"] = us  # dataclasses (with postponed annotations) look the module up here
_spec.loader.exec_module(us)

REAL_SHEETS = us.SHEETS  # captured before the fixture patches the row counts
NA = "NA"
LIT = "litterfall, recorded monthly over at least 1 year"

META = {
    "title": "t",
    "pubdate": "20250507",
    "origins": list(us.XML_ORIGINS) + ["someone else"],
    "onlink": ["https://doi.org/10.5066/P133WKRO", "https://other"],
    "useconst": "None. Users are advised to read the metadata.",
}
DECLARED = us.Declared(
    unpublished=frozenset({"Josselyn unpublished data", "Anisfeld 1995 unpublished data"}),
    unresolved_near_miss=frozenset({"Lagomasino et al. 2020"}),
    comma_splits={"Mahall and Park 1976, Balling and Resh 1983": ("Mahall and Park 1976", "Balling and Resh 1983")},
)


def _row(sheet: str, **kw) -> dict:
    sh = next(s for s in REAL_SHEETS if s.name == sheet)
    base = {c: "" for c in sh.columns}
    base.update({"units": sh.units, "source_review": NA, "included_Feher_2017": "n"})
    if sh.range_col:
        base[sh.range_col] = NA
    base.update(kw)
    return base


def _H(**kw):
    return _row("Height", **kw)


def _B(**kw):
    return _row("Biomass", **kw)


def _P(**kw):
    return _row("Productivity", **kw)


def _D(**kw):
    return _row("soil_C_density", **kw)


def _A(**kw):
    return _row("soil_C_accumulation", **kw)


# site tuples: type, veg_type, lat, long, location
SA_M = dict(type="mangrove", veg_type="M", lat=10.5, long=-80.0, location="Site A")
SA_F = dict(type="salt flat", veg_type="F", lat=10.5, long=-80.0, location="Site A")
SB = dict(type="salt marsh", veg_type="G", lat=29, long=-90.0, location="Site B ")  # whole degrees; trailing space
SC_G = dict(type="salt marsh", veg_type="G", lat=30.5, long=-90.25, location="Site C")
SC_F = dict(type="salt flat", veg_type="F", lat=30.5, long=-90.25, location="Site C")
SD_G = dict(type="salt marsh", veg_type="G", lat=31.0, long=-91.5, location="Site D")
SD_M = dict(type="mangrove", veg_type="M", lat=31.0, long=-91.5, location="Site D")
SE = dict(type="salt marsh", veg_type="G", lat=32.1, long=-92.2, location="Site E")
SF = dict(type="salt marsh", veg_type="G", lat=10.6, long=-80.5, location="Site F")
SG = dict(type="salt marsh", veg_type="G", lat=10.7, long=-80.6, location="Site G")
SH = dict(type="salt marsh mangrove", veg_type="G/M", lat=11, long=-81, location="Site H")  # whole degrees
SI = dict(type="mangrove", veg_type="M", lat=12, long=-82.0, location="Site I")  # whole degrees
SJ = dict(type="salt marsh", veg_type="G", lat=12.5, long=-82.5, location="Site J")

HEIGHT = [
    # 0, 2: identical rows -> duplicate_source_row on both; Smith 2000 (DOI)
    _H(**SA_M, height=1.5, source_primary="Smith 2000", height_range="1.0-2.0", included_Feher_2017="y"),
    # 1: same coordinates, salt flat label -> separate location; 0 m is assigned by the source
    _H(**SA_F, height=0, source_primary="Smith 2000"),
    _H(**SA_M, height=1.5, source_primary="Smith 2000", height_range="1.0-2.0", included_Feher_2017="y"),
    # 3: whole-degree coordinates; trailing space stripped; review given; Cahoon 1994 (two full texts, one DOI)
    _H(**SB, height=0.4, source_review="Rev 1990", source_primary="Cahoon\xa01994"),
]
BIOMASS = [
    # 0: matched method
    _B(**SA_M, biomass=12.5, source_primary="Smith 2000", method="allometric equation", included_Feher_2017="y"),
    # 1: method not reported; accent differs from the tab ('Castaneda'); range with a leading space
    _B(**SC_G, biomass=3, source_primary="Castañeda-Moya et al. 2013", method="not reported", biomass_range=" 0.1- 3.2"),
    # 2: salt-flat zero
    _B(**SC_F, biomass=0, source_primary="Smith 2000", method="peak live biomass"),
]
PROD = [
    # 0: litterfall method; ';' composite with a part that is unpublished
    _P(**SD_G, productivity=8.3, source_primary="Anisfeld et al. 1999; Anisfeld 1995 unpublished data",
       method=LIT, productivity_range="3-9", included_Feher_2017="y"),
    # 1: comma composite (declared split), review given
    _P(**SD_M, productivity=7, source_review="Rev 1990",
       source_primary="Mahall and Park 1976, Balling and Resh 1983", method="Smalley 1959"),
    # 2: method not reported -> NULL subfield
    _P(**SE, productivity=5.5, source_primary="Smith 2000", method="not reported"),
]
DENS = [
    # 0, 1: same site (one location); depth 0-30 and 'not reported'
    _D(**SF, soil_c_density=0.03, source_primary="Smith 2000", core_depth_cm="0-30"),
    _D(**SF, soil_c_density=0.04, source_primary="Smith 2000", core_depth_cm="not reported"),
    # 2: fractional depth
    _D(**SG, soil_c_density=0.05, source_primary="Smith 2000", core_depth_cm="0-7.5"),
    # 3: mixed type; reference with a different-year entry (declared unresolved)
    _D(**SH, soil_c_density=0.06, source_primary="Lagomasino et al. 2020", core_depth_cm="0-10"),
]
ACCUM = [
    _A(**SI, soil_c_accum_rate=100, source_primary="Josselyn unpublished data"),  # unpublished
    _A(**SJ, soil_c_accum_rate=150, source_primary="Dai and Weigert 1996"),  # two spellings, one DOI
    _A(**SJ, soil_c_accum_rate=160, source_primary="Dai and Wiegert 1996"),
]
SHEETS_ROWS = {"Height": HEIGHT, "Biomass": BIOMASS, "Productivity": PROD, "soil_C_density": DENS,
               "soil_C_accumulation": ACCUM}

CIT_ROWS = [
    ("Smith 2000", "https://doi.org/10.1000/Smith.1", "Smith, A., 2000, Title. J A 1, 1-2."),
    ("Smith\xa02000", "https://doi.org/10.1000/Smith.1", "Smith, A.,\xa02000, Title. J A 1, 1-2."),  # NBSP duplicate
    ("Cahoon 1994", "https://doi.org/10.2307/1942126", "Cahoon, D., 1994, Accretion. Ecol Appl 41, 166."),
    ("Cahoon\xa01994", "https://doi.org/10.2307/1942126", "Cahoon, D., 1994, Accretion. Ecol Appl 4, 166."),
    ("Castaneda-Moya et al. 2013", "https://dx.doi.org/10.1000/CM.13", "Castaneda-Moya, E., 2013, X."),
    ("Anisfeld et al. 1999", "https://doi.org/10.1000/anis.99", "Anisfeld, S., 1999, Y."),
    ("Mahall and Park 1976", "https://doi.org/productivity", "Mahall, B., 1976, Z."),  # not a DOI
    ("Balling and Resh 1983", NA, "Balling, S., 1983, W."),
    ("Lagomasino et al. 2013", "https://doi.org/10.1000/lag.13", "Lagomasino, D., 2013, V."),
    ("Dai and Weigert 1996", "https://doi.org/10.1000/dai.96", "Dai, T., 1996, U."),
    ("Dai and Wiegert 1996", "https://doi.org/10.1000/dai.96", "Dai, T., 1996, U."),
    ("Rev 1990", "https://doi.org/10.1000/rev.90", "Rev, R., 1990, T."),
    ("Unused 1999", "https://example.org/u", "Unused, U., 1999, S."),
]
ACCESS = "2026-11-15"


@pytest.fixture(scope="module", autouse=True)
def _small_sheets():
    """The real SHEETS assert the release's row counts; the fixture has fewer rows."""
    mp = pytest.MonkeyPatch()
    new = tuple(dataclasses.replace(s, n_rows=len(SHEETS_ROWS[s.name])) for s in REAL_SHEETS)
    mp.setattr(us, "SHEETS", new)
    mp.setattr(us, "SHEET_BY_NAME", {s.name: s for s in new})
    yield
    mp.undo()


def _frames(rows: dict[str, list[dict]] = SHEETS_ROWS) -> dict[str, pd.DataFrame]:
    return {n: pd.DataFrame(r, columns=next(s for s in REAL_SHEETS if s.name == n).columns, dtype=object)
            for n, r in rows.items()}


def _cit() -> pd.DataFrame:
    return pd.DataFrame(CIT_ROWS, columns=us.CITATION_COLS, dtype=object)


def _build(rows=None, cit=None, meta=None, declared=DECLARED):
    return us.build_tables(_frames(rows or SHEETS_ROWS), _cit() if cit is None else cit,
                           META if meta is None else meta, ACCESS, declared)


def _with(sheet: str, i: int, **kw) -> dict[str, list[dict]]:
    rows = {k: list(v) for k, v in SHEETS_ROWS.items()}
    rows[sheet][i] = {**rows[sheet][i], **kw}
    return rows


@pytest.fixture(scope="module")
def built():
    return _build()


def test_counts(built) -> None:
    tables, skips, rep = built
    assert skips == [] and rep["rows_excluded"] == 0
    assert rep["source_rows"] == 4 + 3 + 3 + 4 + 3 == len(tables["fact"]) == 17
    assert rep["facts_by_attribute"] == {
        "usgs_aboveground_biomass": 3, "usgs_canopy_height": 4, "usgs_productivity": 3,
        "usgs_soil_c_accum": 3, "usgs_soil_c_density": 4,
    }
    assert len(tables["sample"]) == 17
    # locations: A_M, A_F, B | C_G, C_F (A_M shared) | D_G, D_M, E | F, G, H | I, J = 13
    assert len(tables["location"]) == 13
    # methods: Height 3 (h0=h2, h1, h3), Biomass 3, Productivity 3, density 1, accumulation 1
    assert len(tables["method"]) == 11
    assert len(tables["reference"]) == 8
    assert len(tables["citation"]) == 12
    assert len(tables["study_screening"]) == 0
    validate_tables(add_derived_labels(tables))


def test_source_row(built) -> None:
    src = built[0]["source"].iloc[0]
    assert src.version == "2025-05-07" and src.doi == "10.5066/P133WKRO" and src.access_date == ACCESS
    assert src.license.startswith("No use constraints stated") and "read the metadata" in src.license


def test_types_subfields_units_and_provenance(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    t = lambda k: tuple(f.loc[k, ["measurement_type", "subfield", "units_raw"]])  # noqa: E731
    assert t("Height:row0:usgs_canopy_height") == ("B3", "canopy_height", "m")
    assert t("Biomass:row0:usgs_aboveground_biomass") == ("B2", "above_ground_biomass", "t/ha")
    assert t("soil_C_density:row0:usgs_soil_c_density") == ("C1", "soil_carbon_density", "g/cm3")
    assert t("soil_C_accumulation:row0:usgs_soil_c_accum") == ("G2", "carbon_burial", "g/m2/yr")
    assert t("Productivity:row0:usgs_productivity") == ("B6", "litterfall", "t/ha/yr")
    assert t("Productivity:row1:usgs_productivity")[1] == "above_ground_npp"
    assert pd.isna(f.loc["Productivity:row2:usgs_productivity", "subfield"])  # method not reported
    assert f.loc["Height:row0:usgs_canopy_height", "value"] == 1.5
    assert f.loc["soil_C_density:row2:usgs_soil_c_density", "value"] == 0.05
    assert f.loc["Productivity:row0:usgs_productivity", "source_row_ref"] == (
        "ecosystem_attribute_data_2023.xlsx[Productivity]#data_row=0:usgs_productivity"
    )
    assert f.loc["soil_C_accumulation:row2:usgs_soil_c_accum", "source_row_ref"].endswith(
        "[soil_C_accumulation]#data_row=2:usgs_soil_c_accum")
    assert f[["sd", "se"]].isna().all().all() and f.n.isna().all()


def test_flags(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    fl = lambda k: f.loc[k, "flags"]  # noqa: E731
    assert fl("Height:row0:usgs_canopy_height") == ["duplicate_source_row"]
    assert fl("Height:row2:usgs_canopy_height") == ["duplicate_source_row"]
    assert fl("Height:row1:usgs_canopy_height") == ["assigned_by_source", "zero_value"]
    assert fl("Height:row3:usgs_canopy_height") == ["coords_integer_degree", "secondary_source"]
    assert fl("Biomass:row0:usgs_aboveground_biomass") == []
    assert fl("Biomass:row1:usgs_aboveground_biomass") == ["method_missing"]
    assert fl("Biomass:row2:usgs_aboveground_biomass") == ["assigned_by_source", "zero_value"]
    assert fl("Productivity:row1:usgs_productivity") == ["secondary_source"]
    assert fl("Productivity:row2:usgs_productivity") == ["method_missing"]
    assert fl("soil_C_density:row0:usgs_soil_c_density") == []
    assert fl("soil_C_density:row1:usgs_soil_c_density") == ["depth_missing"]
    assert fl("soil_C_density:row3:usgs_soil_c_density") == ["coords_integer_degree"]
    assert fl("soil_C_accumulation:row0:usgs_soil_c_accum") == ["coords_integer_degree"]
    assert rep["flag_counts"] == {
        "assigned_by_source": 2, "coords_integer_degree": 3, "depth_missing": 1,
        "duplicate_source_row": 2, "method_missing": 2, "secondary_source": 2, "zero_value": 2,
    }
    assert rep["duplicate_source_rows_flagged"] == 2 and rep["rows_with_source_review"] == 2


def test_locations_samples_depths(built) -> None:
    tables, _, _ = built
    loc = tables["location"].set_index("location_key")
    k = "10.5|-80.0|mangrove|M|Site A"
    assert loc.loc[k, "site_name"] == "Site A" and loc.loc[k, "habitat_type"] == "mangrove"
    assert loc.loc[k, "vegetation_class"] == "M" and loc.loc[k, "latitude"] == 10.5
    assert "29.0|-90.0|salt marsh|G|Site B" in loc.index  # trailing space stripped, int latitude -> float
    assert loc.loc["10.5|-80.0|salt flat|F|Site A", "habitat_type"] == "salt flat"
    s = tables["sample"].set_index("sample_key")
    assert (s.loc["soil_C_density:row0", ["depth_min_cm", "depth_max_cm"]] == [0, 30]).all()
    assert s.loc["soil_C_density:row2", "depth_max_cm"] == 7.5
    assert s.loc["soil_C_density:row1", ["depth_min_cm", "depth_max_cm"]].isna().all()
    assert s.loc["soil_C_density:row1", "depth_notes"] == "core_depth_cm: not reported"
    assert s.loc[[i for i in s.index if i.startswith("Height")], "depth_min_cm"].isna().all()
    assert s.sampling_year.isna().all() and s.taxon.isna().all()


def test_labels(built) -> None:
    loc = add_derived_labels(built[0])["location"]
    got = loc.groupby("habitat_type").habitat.agg(lambda x: set(x.fillna("-")))
    assert got["salt marsh"] == {"Tidal marsh"} and got["mangrove"] == {"Mangrove forest"}
    assert got["salt flat"] == {"-"} and got["salt marsh mangrove"] == {"-"}  # no equivalent -> blank
    assert loc.setting.isna().all()


def test_methods(built) -> None:
    tables, _, _ = built
    m = tables["method"].set_index("method_key")
    k = "usgs_aboveground_biomass|allometric equation|||y"
    assert m.loc[k, "method_status"] == "matched" and m.loc[k, "technique"] == "allometric equation"
    k = "usgs_aboveground_biomass|not reported|0.1- 3.2||n"  # range stripped, interior kept
    assert m.loc[k, "method_status"] == "method_id_null" and pd.isna(m.loc[k, "technique"])
    assert '"range": "0.1- 3.2"' in m.loc[k, "params"] and '"range_column": "biomass_range"' in m.loc[k, "params"]
    k = "usgs_canopy_height||1.0-2.0||y"
    assert m.loc[k, "method_status"] == "method_id_null" and '"range": "1.0-2.0"' in m.loc[k, "params"]
    k = "usgs_canopy_height||||n"
    assert '"range": null' in m.loc[k, "params"] and '"source_review": null' in m.loc[k, "params"]
    k = "usgs_canopy_height|||Rev 1990|n"
    assert '"source_review": "Rev 1990"' in m.loc[k, "params"]
    assert '"range"' not in m.loc["usgs_soil_c_density||||n", "params"]  # no range column on that sheet
    assert (m.loc[m.attribute.isin(["usgs_soil_c_density", "usgs_soil_c_accum"]), "method_status"] == "method_id_null").all()


def test_references_and_citations(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    rk = lambda k: f.loc[k, "reference_key"]  # noqa: E731
    assert rk("Height:row0:usgs_canopy_height") == "doi:10.1000/smith.1"  # lower-cased
    assert rk("Height:row3:usgs_canopy_height") == "doi:10.2307/1942126"  # NBSP in data and tab normalised
    assert rk("Biomass:row1:usgs_aboveground_biomass") == "doi:10.1000/cm.13"  # accent folding, unique
    assert rk("Productivity:row0:usgs_productivity") == "doi:10.1000/anis.99+ref:Anisfeld 1995 unpublished data"
    assert rk("Productivity:row1:usgs_productivity") == "ref:Mahall and Park 1976+ref:Balling and Resh 1983"
    assert rk("soil_C_density:row3:usgs_soil_c_density") == "ref:Lagomasino et al. 2020"
    assert rk("soil_C_accumulation:row0:usgs_soil_c_accum") == "ref:Josselyn unpublished data"
    assert rk("soil_C_accumulation:row1:usgs_soil_c_accum") == rk("soil_C_accumulation:row2:usgs_soil_c_accum") == (
        "doi:10.1000/dai.96")  # spelling variants sharing one DOI -> one reference
    c = tables["citation"]
    assert c.groupby("reference_key").size().to_dict() == {
        "doi:10.1000/smith.1": 1, "doi:10.2307/1942126": 2, "doi:10.1000/cm.13": 1,
        "doi:10.1000/anis.99+ref:Anisfeld 1995 unpublished data": 2,
        "ref:Mahall and Park 1976+ref:Balling and Resh 1983": 2,
        "ref:Lagomasino et al. 2020": 1, "ref:Josselyn unpublished data": 1, "doi:10.1000/dai.96": 2,
    }
    cc = c.set_index("citation_key")
    k = "doi:10.1000/smith.1|Smith 2000"
    assert cc.loc[k, "doi"] == "10.1000/smith.1" and cc.loc[k, "year"] == 2000
    assert cc.loc[k, "title"] == "Smith, A., 2000, Title. J A 1, 1-2."  # NBSP normalised; duplicate collapsed
    assert cc.loc[k, "url"] == "https://doi.org/10.1000/Smith.1"
    cah = c[c.reference_key == "doi:10.2307/1942126"]
    assert sorted(cah.title) == ["Cahoon, D., 1994, Accretion. Ecol Appl 4, 166.",
                                 "Cahoon, D., 1994, Accretion. Ecol Appl 41, 166."]  # both texts kept
    assert set(cah.citation_key) == {"doi:10.2307/1942126|Cahoon 1994#0", "doi:10.2307/1942126|Cahoon 1994#1"}
    mp = cc.loc["ref:Mahall and Park 1976+ref:Balling and Resh 1983|Mahall and Park 1976"]
    assert pd.isna(mp.doi) and mp.url == "https://doi.org/productivity"  # a doi.org url that is not a DOI
    assert pd.isna(cc.loc["ref:Mahall and Park 1976+ref:Balling and Resh 1983|Balling and Resh 1983", "url"])  # NA
    un = cc.loc["ref:Josselyn unpublished data|Josselyn unpublished data"]
    assert pd.isna(un.title) and pd.isna(un.doi) and pd.isna(un.year)  # no year in the string
    ag = cc.loc["doi:10.1000/anis.99+ref:Anisfeld 1995 unpublished data|Anisfeld 1995 unpublished data"]
    assert ag.year == 1995 and pd.isna(ag.title)
    assert rep["cited_papers_unpublished"] == ["Anisfeld 1995 unpublished data", "Josselyn unpublished data"]
    assert rep["cited_papers_unresolved_near_miss"] == ["Lagomasino et al. 2020"]
    assert rep["near_misses_same_first_word_in_citations_tab"]["Lagomasino et al. 2020"] == ["Lagomasino et al. 2013"]
    assert rep["cited_papers_matched_via_accent_folding"] == {"Castañeda-Moya et al. 2013": "Castaneda-Moya et al. 2013"}
    assert rep["citations_tab_rows"] == 13 and rep["citations_tab_distinct_strings"] == 11
    assert rep["citations_tab_strings_with_valid_doi"] == 8
    assert rep["citations_tab_strings_with_non_doi_url_on_doi_org"] == ["Mahall and Park 1976"]
    assert rep["citations_tab_strings_with_several_full_citation_texts"] == ["Cahoon 1994"]
    assert rep["citations_tab_strings_sharing_a_doi"] == ["Dai and Weigert 1996", "Dai and Wiegert 1996"]
    assert rep["citations_tab_strings_unused_by_any_row"] == ["Lagomasino et al. 2013", "Unused 1999"]
    assert rep["composite_source_primary_strings"] == [
        "Anisfeld et al. 1999; Anisfeld 1995 unpublished data", "Mahall and Park 1976, Balling and Resh 1983"]
    assert set(tables["reference"].reference_key) == set(f.reference_key)


# ---- helper functions


@pytest.mark.parametrize(
    "p,parts",
    [
        ("A 2000", ["A 2000"]),
        ("A 2000; B 2001", ["A 2000", "B 2001"]),
        ("A 2000;B\xa02001", ["A 2000", "B 2001"]),
        ("Mahall and Park 1976, Balling and Resh 1983", ["Mahall and Park 1976", "Balling and Resh 1983"]),
        ("A 2000, B 2001", ["A 2000, B 2001"]),  # no general comma rule
    ],
)
def test_split_primary(p, parts) -> None:
    assert us.split_primary(p, DECLARED) == parts


def test_split_primary_empty_part_raises() -> None:
    with pytest.raises(ContractError, match="empty part"):
        us.split_primary("A 2000; ", DECLARED)


def test_doi_of_requires_a_real_doi() -> None:
    assert us._doi_of("https://doi.org/10.1002/ecs2.2828") == "10.1002/ecs2.2828"
    assert us._doi_of("http://dx.doi.org/10.1000/ABC") == "10.1000/abc"
    assert us._doi_of("https://doi.org/productivity") is None
    assert us._doi_of("https://doi.org/c_accumulation") is None
    assert us._doi_of("https://escholarship.org/uc/item/1x80s86s") is None
    assert us._doi_of(None) is None


def test_match_key_and_norm_ws() -> None:
    assert us.match_key(us.norm_ws("Castañeda\xa0-Moya  2013")) == us.match_key("Castaneda -Moya 2013")
    assert us.norm_ws(" a\xa0\xa0b\t c ") == "a b c"


# ---- fail-loud checks


def test_unknown_productivity_method_raises() -> None:
    with pytest.raises(ContractError, match="unknown method strings"):
        _build(_with("Productivity", 1, method="Smalley 1960"))


def test_unknown_biomass_method_raises() -> None:
    with pytest.raises(ContractError, match="unknown method strings"):
        _build(_with("Biomass", 0, method="eyeballed"))


def test_unknown_type_raises() -> None:
    with pytest.raises(ContractError, match="unknown type"):
        _build(_with("Height", 0, type="swamp"))


def test_unknown_veg_type_raises() -> None:
    with pytest.raises(ContractError, match="unknown veg_type"):
        _build(_with("Height", 0, veg_type="X"))


def test_units_mismatch_raises() -> None:
    with pytest.raises(ContractError, match="units column"):
        _build(_with("Height", 0, units="ft"))


def test_non_numeric_value_raises() -> None:
    with pytest.raises(ContractError, match="non-numeric"):
        _build(_with("Height", 0, height="1.5"))
    with pytest.raises(ContractError, match="non-numeric"):
        _build(_with("Height", 0, height=True))


def test_blank_value_raises() -> None:
    with pytest.raises(ContractError, match="non-numeric"):
        _build(_with("Height", 0, height=""))


def test_coordinates_out_of_range_raise() -> None:
    with pytest.raises(ContractError, match="coordinates outside"):
        _build(_with("Height", 0, long=200.0))


def test_bad_depth_string_raises() -> None:
    with pytest.raises(ContractError, match="core_depth_cm"):
        _build(_with("soil_C_density", 0, core_depth_cm="10-30"))
    with pytest.raises(ContractError, match="blank core_depth_cm"):
        _build(_with("soil_C_density", 0, core_depth_cm=""))


def test_zero_value_on_a_non_salt_flat_raises() -> None:
    with pytest.raises(ContractError, match="salt-flat rows and zero values"):
        _build(_with("Height", 3, height=0))


def test_salt_flat_with_a_value_raises() -> None:
    with pytest.raises(ContractError, match="salt-flat rows and zero values"):
        _build(_with("Biomass", 2, biomass=1.2))


def test_zero_in_another_sheet_raises() -> None:
    with pytest.raises(ContractError, match="unexpected zero"):
        _build(_with("soil_C_density", 0, soil_c_density=0))


def test_unknown_included_flag_raises() -> None:
    with pytest.raises(ContractError, match="included_Feher_2017"):
        _build(_with("Height", 0, included_Feher_2017="maybe"))


def test_literal_na_in_a_text_column_raises() -> None:
    with pytest.raises(ContractError, match="literal missing-value"):
        _build(_with("Biomass", 0, method="NA"))


def test_blank_location_raises() -> None:
    with pytest.raises(ContractError, match="location"):
        _build(_with("Height", 0, location=""))


def test_undeclared_unmatched_reference_raises() -> None:
    with pytest.raises(ContractError, match="undeclared"):
        _build(_with("Height", 0, source_primary="Brand New 2024"))


def test_declared_reference_that_matches_raises() -> None:
    decl = dataclasses.replace(DECLARED, unpublished=DECLARED.unpublished | {"Smith 2000"})
    with pytest.raises(ContractError, match="declared but matched"):
        _build(declared=decl)


def test_comma_split_with_a_part_not_in_the_tab_raises() -> None:
    cit = _cit()
    cit = cit[cit.citation != "Balling and Resh 1983"].reset_index(drop=True)
    decl = dataclasses.replace(
        DECLARED, unresolved_near_miss=DECLARED.unresolved_near_miss | {"Balling and Resh 1983"})
    with pytest.raises(ContractError, match="a part is not in the Citations tab"):
        _build(cit=cit, declared=decl)


def test_comma_split_not_in_data_raises() -> None:
    decl = dataclasses.replace(DECLARED, comma_splits={**DECLARED.comma_splits, "X 1, Y 2": ("X 1", "Y 2")})
    with pytest.raises(ContractError, match="does not occur"):
        _build(declared=decl)


def test_two_parts_with_one_key_raise() -> None:
    with pytest.raises(ContractError, match="one reference key"):
        _build(_with("Height", 0, source_primary="Dai and Weigert 1996; Dai and Wiegert 1996"))


def test_citation_variants_with_different_dois_raise() -> None:
    cit = pd.concat([_cit(), pd.DataFrame([("Smith 2000", "https://doi.org/10.1000/other", "x")],
                                          columns=us.CITATION_COLS, dtype=object)], ignore_index=True)
    with pytest.raises(ContractError, match="different DOIs"):
        _build(cit=cit)


def test_blank_citation_text_raises() -> None:
    cit = _cit()
    cit.loc[0, "full_citation"] = ""
    with pytest.raises(ContractError, match="blank citation"):
        _build(cit=cit)


def test_review_not_in_tab_raises() -> None:
    with pytest.raises(ContractError, match="not in the Citations tab"):
        _build(_with("Height", 3, source_review="Nobody 1850"))


def test_wrong_columns_raise() -> None:
    rows = {k: list(v) for k, v in SHEETS_ROWS.items()}
    fr = _frames(rows)
    fr["Height"] = fr["Height"].rename(columns={"height": "ht"})
    with pytest.raises(ContractError, match="unexpected columns"):
        us.build_tables(fr, _cit(), META, ACCESS, DECLARED)


def test_row_count_must_match_the_release(monkeypatch) -> None:
    monkeypatch.setattr(us, "SHEETS", tuple(dataclasses.replace(s, n_rows=s.n_rows + 1) for s in us.SHEETS))
    with pytest.raises(ContractError, match="data rows, expected"):
        _build()


def test_meta_checks() -> None:
    with pytest.raises(ContractError, match="YYYYMMDD"):
        _build(meta={**META, "pubdate": "2025"})
    with pytest.raises(ContractError, match="onlink"):
        _build(meta={**META, "onlink": ["https://other"]})
    with pytest.raises(ContractError, match="authors differ"):
        _build(meta={**META, "origins": ["x"]})
    with pytest.raises(ContractError, match="useconst"):
        _build(meta={**META, "useconst": "CC0 public domain"})


def test_read_xml_meta(tmp_path) -> None:
    p = tmp_path / "m.xml"
    p.write_text(
        "<metadata><idinfo><citation><citeinfo><origin>A B</origin><origin>C D</origin>"
        "<pubdate>20250507</pubdate><title>T</title><onlink>https://doi.org/10.5066/P133WKRO</onlink>"
        "<lworkcit><citeinfo><onlink>https://nested</onlink></citeinfo></lworkcit></citeinfo></citation>"
        "<useconst>None.  Read   the\n metadata.</useconst></idinfo></metadata>",
        encoding="utf-8",
    )
    m = us.read_xml_meta(p)
    assert m == {"title": "T", "pubdate": "20250507", "origins": ["A B", "C D"],
                 "onlink": ["https://doi.org/10.5066/P133WKRO"], "useconst": "None. Read the metadata."}
    p.write_text("<metadata><idinfo/></metadata>", encoding="utf-8")
    with pytest.raises(ContractError, match="citeinfo"):
        us.read_xml_meta(p)


def test_read_raw_sheet_list_is_checked(tmp_path) -> None:
    p = tmp_path / "x.xlsx"
    with pd.ExcelWriter(p) as w:  # type: ignore[abstract]
        pd.DataFrame({"a": [1]}).to_excel(w, sheet_name="Height", index=False)
    with pytest.raises(ContractError, match="sheets"):
        us.read_raw(p)


def test_read_raw_keeps_na_text_and_blank_cells(tmp_path) -> None:
    p = tmp_path / "x.xlsx"
    with pd.ExcelWriter(p) as w:  # type: ignore[abstract]
        for s in REAL_SHEETS:
            pd.DataFrame([{c: ("NA" if c == "source_review" else 1) for c in s.columns}]).to_excel(
                w, sheet_name=s.name, index=False)
        pd.DataFrame([("a", None, "c")], columns=us.CITATION_COLS).to_excel(w, sheet_name="Citations", index=False)
    sheets, cit = us.read_raw(p)
    assert sheets["Height"]["source_review"].iloc[0] == "NA"  # not auto-converted
    assert us._null(cit["url"].iloc[0])


# ---- build + determinism


def test_roundtrip_global_build_and_determinism(built, tmp_path) -> None:
    tables, _, _ = built
    write_extracted(tables, tmp_path / "ex")
    back = read_extracted(tmp_path / "ex")
    assert len(back["fact"]) == 17
    g = assign_global_keys([back])
    assert list(g["fact"].measurement_id) == list(range(1, 18))
    out1, out2 = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    build_compilations([tmp_path / "ex"], out1)
    build_compilations([tmp_path / "ex"], out2)
    q = (
        "SELECT f.value, f.attribute, f.units_raw, f.subfield, l.habitat_type, l.habitat, s.depth_max_cm "
        "FROM measurement_fact f JOIN sample_dim s USING (sample_id) "
        "JOIN location_dim l USING (location_id) ORDER BY f.measurement_id"
    )
    r1 = duckdb.connect(str(out1), read_only=True).execute(q).fetchall()
    r2 = duckdb.connect(str(out2), read_only=True).execute(q).fetchall()
    assert r1 == r2 and len(r1) == 17


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
