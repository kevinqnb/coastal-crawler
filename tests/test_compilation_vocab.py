"""Unit tests (rung 1) for the vocabulary and label maps (compilation_vocab.py)."""

from __future__ import annotations

import pandas as pd
import pytest

from coastal_crawler.compilations import vocab as v


def _fact(**kw) -> pd.DataFrame:
    row = dict(attribute="scoc_tou", measurement_type="B5", subfield="total_o2_uptake",
               units_raw="mmol O2 m-2 d-1")
    row.update(kw)
    return pd.DataFrame([row])


def test_declarations_self_consistent():
    v.check_attribute_decls()


def test_good_fact_passes_and_none_subfield_allowed():
    v.check_facts(_fact())
    v.check_facts(_fact(attribute="scoc_unspecified", subfield=None))


@pytest.mark.parametrize("kw,match", [
    (dict(attribute="not_declared"), "no declaration"),
    (dict(measurement_type="C1"), "measurement_type"),
    (dict(subfield="diffusive_o2_uptake"), "subfields"),
    (dict(subfield=None), "subfields"),
    (dict(units_raw="mmol O2 m-2 h-1"), "units_raw"),
])
def test_bad_facts_raise(kw, match):
    with pytest.raises(v.VocabError, match=match):
        v.check_facts(_fact(**kw))


def test_other_needs_note(monkeypatch):
    monkeypatch.setitem(v.ATTRIBUTES, "x", v.AttrDecl(v.OTHER, frozenset({None}), frozenset({"u"})))
    with pytest.raises(v.VocabError, match="needs a note"):
        v.check_attribute_decls()


def test_undeclared_extension_subfield_raises(monkeypatch):
    monkeypatch.setitem(v.ATTRIBUTES, "x", v.AttrDecl("B5", frozenset({"made_up"}), frozenset({"u"})))
    with pytest.raises(v.VocabError, match="made_up"):
        v.check_attribute_decls()


def test_label_maps_load_and_cover_vocab():
    maps = v.load_label_maps()
    for src in ("smithsonian-coastal-carbon", "stratma-scoc", "foster-seagrass"):
        assert (src, "habitat_type", "habitat") in maps  # habitat declared for every source
    m = maps[("smithsonian-coastal-carbon", "habitat_type", "habitat")]
    assert m["marsh"] == "Tidal marsh" and m["swamp"] is None  # blank = no equivalent


def _loc(**cols) -> pd.DataFrame:
    base = dict(habitat_type=["marsh"], salinity_class=[None], geomorphic_setting=[None],
                modification_status=[[]])
    base.update(cols)
    return pd.DataFrame(base)


def test_derive_maps_and_bottom_type():
    maps = v.load_label_maps()
    d = v.derive_location_labels(
        _loc(habitat_type=["mangrove"], salinity_class=["brine"], modification_status=[["tidally restored", "ditched"]]),
        "smithsonian-coastal-carbon", maps)
    r = d.iloc[0]
    assert (r.habitat, r.bottom_type, r.salinity_zone) == ("Mangrove forest", "soft", "hyperhaline")
    assert r.modification_class == ["restored"] and r.setting is None


def test_unlisted_label_raises():
    maps = v.load_label_maps()
    with pytest.raises(v.VocabError, match="newlabel"):
        v.derive_location_labels(_loc(habitat_type=["newlabel"]), "smithsonian-coastal-carbon", maps)
    with pytest.raises(v.VocabError, match="newmod"):
        v.derive_location_labels(_loc(modification_status=[["newmod"]]), "smithsonian-coastal-carbon", maps)


def test_source_without_habitat_map_raises():
    with pytest.raises(v.VocabError, match="no habitat label map"):
        v.derive_location_labels(_loc(), "unknown-source", v.load_label_maps())


def test_label_map_rejects_bad_rows(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("source_key,raw_column,raw_label,mapped_column,mapped_label\ns,habitat_type,a,habitat,Not a habitat\n")
    with pytest.raises(v.VocabError, match="not a habitat label"):
        v.load_label_maps(p)
    p.write_text("source_key,raw_column,raw_label,mapped_column,mapped_label\ns,salinity_class,a,habitat,Coral reef\n")
    with pytest.raises(v.VocabError, match="cannot feed"):
        v.load_label_maps(p)
