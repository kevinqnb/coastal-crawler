"""Unit tests (rung 1) for the cloern-phytoplankton extract.py.

Hand-built fixture: 3 table rows (raw PDF-cell spellings, incl. the soft-hyphen / U+2010 artifacts and a wrapped cell),
4 reference-list paragraphs. Every expected number is derived by inspection in the comments.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from coastal_crawler.compilations import ContractError, read_extracted, write_extracted

_EXTRACT = (
    Path(__file__).resolve().parents[1] / "data" / "compilations" / "cloern-phytoplankton" / "extract.py"
)
_spec = importlib.util.spec_from_file_location("cloern_extract", _EXTRACT)
assert _spec and _spec.loader
cl = importlib.util.module_from_spec(_spec)
sys.modules["cloern_extract"] = cl
_spec.loader.exec_module(cl)

SHY_HY = "-\xad‐"  # how the PDF renders a hyphen-minus: '-' + soft hyphen + U+2010


@pytest.fixture(autouse=True)
def _no_real_crosswalk(monkeypatch):
    """The real CROSSWALK targets the real reference list; the fixture starts with none."""
    monkeypatch.setattr(cl, "CROSSWALK", {})


def _raw(page, k, cells):
    return {"pdf_page": page, "page_row": k, "cells": cells}


RAW = [
    _raw(2, 0, ["Alpha Bay", "DENMARK/\nSWEDEN", "Baltic", "GPP", "14C", "100", "2", "(Smith and Jones, 1990)"]),
    _raw(2, 1, ["Alpha Bay", "DENMARK/\nSWEDEN", "Baltic", "U", "O2 &\n14C", "200", "1",
                "(Lee et al., 2001b) cited in (Park, 1980); Y. Lee, pers. comm. 01/02/2010"]),
    _raw(3, 0, [f"Beta{SHY_HY}Sound", "USA", "North America", "NPP", "13C", f"{SHY_HY}5", "3", "(Smith and Jones, 1990)"]),
]
PARAS = [
    "Smith, A. B., and Jones, C.: Title one, Journal, 1, 2-3, 1990.",
    "Lee, D., Roe, E., and Poe, F.: Title two, Doi: 10.1000/ABC-1, 2001b.",
    "Park, G.: Title three, Journal, 4, 5-6, 1980.",
    "Park, G.: Title four, 1985.",
]
# rows 3; ecosystems {Alpha Bay, Beta-Sound} = 2; n sum 2+1+3 = 6; caption pretended 7; negative: Beta-Sound NPP
EXPECT = {"rows": 3, "ecosystems": 2, "n_sum": 6, "caption_n_sum": 7, "ref_paragraphs": 4,
          "negative_rows": frozenset({("Beta-Sound", "NPP")})}


def _build(raw=RAW, paras=PARAS, expect=EXPECT):
    return cl.build_tables(raw, paras, "2026-11-15", expect)


def test_clean_removes_pdf_artifacts():
    assert cl.clean(f"Core{SHY_HY}Bogue  Estuarine\nSystem") == "Core-Bogue Estuarine System"
    assert cl.clean(f"g C m{SHY_HY}2 yr{SHY_HY}1") == cl.UNITS
    assert cl.clean(cl.UNITS_HEADER_RAW) == "Median APPP " + cl.UNITS


def test_facts_by_inspection():
    tables, skips, report, _ = _build()
    f = tables["fact"].set_index("fact_key")
    assert skips == [] and len(f) == 3
    assert f.value.to_dict() == {"row000:cloern_appp_median": 100.0, "row001:cloern_appp_median": 200.0,
                                 "row002:cloern_appp_median": -5.0}  # '-<SHY><U+2010>5' is -5
    assert list(f.n) == [2, 1, 3] and (f.units_raw == "g C m-2 yr-1").all()
    assert f.loc["row000:cloern_appp_median", "flags"] == ["habitat_from_scope", "source_median"]
    assert f.loc["row001:cloern_appp_median", "flags"] == [
        "cited_secondary", "habitat_from_scope", "pers_comm", "source_median"]
    assert f.loc["row002:cloern_appp_median", "flags"] == ["habitat_from_scope", "negative_value", "source_median"]
    assert f.loc["row000:cloern_appp_median", "source_row_ref"] == (
        "bg-11-2477-2014-supplement.pdf#pdf_page=2:table_row=0:cloern_appp_median")
    assert report["n_sum_minus_caption"] == -1 and report["pers_comm_texts"] == ["Y. Lee, pers. comm. 01/02/2010"]


def test_methods_locations_and_references_by_inspection():
    tables, _, report, audit = _build()
    m = tables["method"].set_index("method_key")
    assert sorted(m.index) == ["GPP|14C", "NPP|13C", "U|O2 & 14C"]  # wrapped 'O2 &\n14C' joined
    assert m.loc["U|O2 & 14C", "technique"] == "O2 & 14C"
    assert json.loads(m.loc["GPP|14C", "params"])["measure_meaning"] == "gross primary production"
    loc = tables["location"].set_index("location_key")
    assert sorted(loc.index) == ["Alpha Bay|water column", "Beta-Sound|water column"]  # 2 ecosystems; no coordinates
    assert loc.latitude.isna().all() and loc.longitude.isna().all()
    assert loc.loc["Alpha Bay|water column", "country"] == "DENMARK/ SWEDEN"
    f = tables["fact"].set_index("fact_key")
    smith = "ref:Smith, A. B., and Jones, C. 1990"
    assert f.loc["row000:cloern_appp_median", "reference_key"] == smith
    assert f.loc["row002:cloern_appp_median", "reference_key"] == smith
    # Lee 2001b has a printed DOI (-> doi: key, lower-cased); Park 1980 (not Park 1985) is the secondary source
    assert f.loc["row001:cloern_appp_median", "reference_key"] == "doi:10.1000/abc-1+ref:Park, G. 1980"
    assert sorted(tables["reference"].reference_key) == sorted({smith, "doi:10.1000/abc-1+ref:Park, G. 1980"})
    cit = tables["citation"]
    assert len(cit) == 3  # Smith (1) + Lee & Park (2)
    assert sorted(cit.year) == [1980, 1990, 2001]
    assert cit.set_index("citation_key").loc["doi:10.1000/abc-1+ref:Park, G. 1980|0", "doi"] == "10.1000/abc-1"
    assert report["list_entries_not_cited_by_table"] == 1 and report["items_via_crosswalk"] == 0
    assert [r["in_text"] for r in [dict(zip(("in_text", "role", "how", "entry_label"), c)) for c in audit["crosswalk"]]]


def test_roundtrip_through_contract(tmp_path):
    tables, _, _, _ = _build()
    write_extracted(tables, tmp_path / "ex")
    back = read_extracted(tmp_path / "ex")
    assert len(back["fact"]) == 3
    assert set(back["location"].habitat) == {"Water column"} and back["location"].bottom_type.isna().all()


def test_count_expectations_raise():
    for key, bad in (("rows", 4), ("ecosystems", 3), ("n_sum", 7), ("ref_paragraphs", 5)):
        with pytest.raises(ContractError):
            _build(expect={**EXPECT, key: bad})
    with pytest.raises(ContractError, match="negative APPP"):
        _build(expect={**EXPECT, "negative_rows": frozenset()})


def test_bad_cells_raise():
    for col, val in ((3, "XPP"), (4, "15N"), (5, "12x"), (6, "0")):
        cells = list(RAW[0]["cells"])
        cells[col] = val
        with pytest.raises(ContractError):
            cl.parse_rows([_raw(2, 0, cells)])


def test_unmatched_item_raises_and_unused_crosswalk_raises(monkeypatch):
    raw = [dict(RAW[0], cells=RAW[0]["cells"][:7] + ["(Nobody, 1990)"]), RAW[1], RAW[2]]
    with pytest.raises(ContractError, match="add a CROSSWALK"):
        cl.build_tables(raw, PARAS, "2026-11-15", {**EXPECT})
    monkeypatch.setattr(cl, "CROSSWALK", {"Zed, 1999": "Park, G. 1980"})
    with pytest.raises(ContractError, match="matches 1 entries|unused CROSSWALK"):
        _build()


def test_crosswalk_resolves_single_author_cite_to_multi_author_entry(monkeypatch):
    # 'Smith, 1990' cites one author but the entry has two: refused automatically, accepted through the crosswalk
    raw = [dict(RAW[0], cells=RAW[0]["cells"][:7] + ["(Smith, 1990)"]), RAW[1], RAW[2]]
    with pytest.raises(ContractError, match="add a CROSSWALK"):
        cl.build_tables(raw, PARAS, "2026-11-15", EXPECT)
    monkeypatch.setattr(cl, "CROSSWALK", {"Smith, 1990": "Smith, A. B., and Jones, C. 1990"})
    tables, _, report, _ = cl.build_tables(raw, PARAS, "2026-11-15", EXPECT)
    assert report["items_via_crosswalk"] == 1


def test_split_reference_cell_forms():
    sp = cl.split_reference_cell("(Fisher et al., 1982; Sanders and Kuenzler, 1979; Thayer,\n1969, 1971)")
    assert sp["primary"] == [("Fisher et al.", "1982"), ("Sanders and Kuenzler", "1979"), ("Thayer", "1969"), ("Thayer", "1971")]
    sp = cl.split_reference_cell("(Carstensen et al., 2003),\nhttp://mads.dmu.dk")
    assert sp["url"] == "http://mads.dmu.dk" and sp["primary"] == [("Carstensen et al.", "2003")]
    sp = cl.split_reference_cell("(Ichikawa and Hirota, 2004) cited in (Ara et al., 2011),\n(Yokouchi et al., 2007)")
    assert sp["primary"] == [("Ichikawa and Hirota", "2004")]
    assert sp["secondary"] == [("Ara et al.", "2011"), ("Yokouchi et al.", "2007")]
    sp = cl.split_reference_cell("(Lindahl et al., 1998), and O. Lindahl, personal\ncommunication, 25 June 2009")
    assert sp["pers_comm"] == "O. Lindahl, personal communication, 25 June 2009" and sp["primary"] == [("Lindahl et al.", "1998")]
    with pytest.raises(ContractError):
        cl.split_reference_cell("Smith 1990")  # not parenthesised


def test_parse_entries_counts_and_rejections():
    ents = cl.parse_entries(PARAS)
    assert len(ents) == 4 and ents[1]["doi"] == "10.1000/abc-1" and ents[3]["year"] == 1985
    with pytest.raises(ContractError, match="does not end in a year"):
        cl.parse_entries(PARAS[:3] + ["Park, G.: no year here."])
    with pytest.raises(ContractError, match="share"):
        cl.parse_entries(PARAS[:3] + [PARAS[2]])
