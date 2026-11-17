"""Unit tests (rung 1) for the fulweiler-nfix extract.py.

Hand-built fixture of 6 rate rows, 4 ancillary rows and 2 references; every expected number is derived by inspection
in the comments.
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

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "fulweiler-nfix" / "extract.py"
_spec = importlib.util.spec_from_file_location("fulweiler_extract", _EXTRACT)
assert _spec and _spec.loader
fx = importlib.util.module_from_spec(_spec)
sys.modules["fulweiler_extract"] = fx
_spec.loader.exec_module(fx)

ACCESS = "2026-11-15"
M2 = "umol-n m-2 h-1"
L1 = "umol-n l-1 h-1"


def _rate(**kw) -> dict:
    r = {c: "NA" for c in fx.RATE_COLUMNS}
    r.update(kw)
    return r


RATES = [
    # N1: benthic salt marsh, stdev error, n 3, original = converted (not converted by the source)
    _rate(nfix_rate_id="N1", reference_id="R1", site_name="Bay", latitude="10", longitude="20", habitat="salt marshes",
          year="2000", month="5", substrate="benthos", substrate_details="sediment", sample_depth="0.1",
          nfix_rate_converted="1.5", nfix_error_converted="0.2", nfix_unit_converted=M2, nfix_error_type="stdev.",
          nfix_n="3", nfix_rate_original="1.5", nfix_error_original="0.2", nfix_unit_original=M2, nfix_method="ARA",
          ARA_conversion_factor="3 to 1"),
    # N2: same sample as N1; a zero rate, no error, no n
    _rate(nfix_rate_id="N2", reference_id="R1", site_name="Bay", latitude="10", longitude="20", habitat="salt marshes",
          year="2000", month="5", substrate="benthos", substrate_details="sediment", sample_depth="0.1",
          nfix_rate_converted="0", nfix_unit_converted=M2, nfix_rate_original="0", nfix_unit_original=M2,
          nfix_method="15N2"),
    # N3 and N4: identical except id (duplicates); water column in an estuary, year missing, converted by the source,
    # error type 'range'
    _rate(nfix_rate_id="N3", reference_id="R2", site_name="Shelf A", latitude="-28", longitude="114", habitat="estuaries",
          substrate="wc", substrate_details="water", sample_depth="2", nfix_rate_converted="3e-6",
          nfix_error_converted="1e-6", nfix_unit_converted=L1, nfix_error_type="range", nfix_n="2",
          nfix_rate_original="0.003", nfix_error_original="0.001", nfix_unit_original="nmol-n l-1 h-1",
          nfix_method="15N2"),
    _rate(nfix_rate_id="N4", reference_id="R2", site_name="Shelf A", latitude="-28", longitude="114", habitat="estuaries",
          substrate="wc", substrate_details="water", sample_depth="2", nfix_rate_converted="3e-6",
          nfix_error_converted="1e-6", nfix_unit_converted=L1, nfix_error_type="range", nfix_n="2",
          nfix_rate_original="0.003", nfix_error_original="0.001", nfix_unit_original="nmol-n l-1 h-1",
          nfix_method="15N2"),
    # N5: 'other' substrate in a lake; an error value with no type; original negative (N2/Ar), stored positive
    _rate(nfix_rate_id="N5", reference_id="R2", site_name="Lake", latitude="45", longitude="-80", habitat="lakes",
          year="2001", month="6", substrate="other", substrate_details="spartina stem", sample_depth="1.5",
          nfix_rate_converted="2", nfix_error_converted="0.5", nfix_unit_converted="umol-n g-1 h-1",
          nfix_rate_original="-2", nfix_error_original="0.5", nfix_unit_original="umol-n g-1 h-1", nfix_method="N2/Ar"),
    # N6: seagrass water column, no ancillary row
    _rate(nfix_rate_id="N6", reference_id="R1", site_name="Bay", latitude="11", longitude="21", habitat="seagrasses",
          year="2002", substrate="wc", substrate_details="water column", nfix_rate_converted="0.001",
          nfix_unit_converted=L1, nfix_rate_original="0.001", nfix_unit_original=L1, nfix_method="15N2"),
]


def _anc(**kw) -> dict:
    r = {c: "NA" for c in fx.ANC_COLUMNS}
    r.update(kw)
    return r


ANC = [
    # N1: salinity 10 psu; NH4 1.5 um; pH 7.5; chla 3 with no unit (-> skip)
    _anc(nfix_rate_id="N1", reference_id="R1", salinity="10", salinity_units="psu", NH4_wc="1.5", NH4_wc_units="um",
         pH="7.5", chla_wc="3", PAR="100", PAR_units="x"),
    # N2: same sample as N1; NH4 1.5 repeats (collapsed); NO3 2
    _anc(nfix_rate_id="N2", reference_id="R1", salinity="10", salinity_units="psu", NH4_wc="1.5", NH4_wc_units="um",
         NO3_wc="2", NO3_wc_units="um"),
    # N3 / N4: salinity 33 with no unit (flag); NH4 4 repeats; DO differs (250 vs 260: conflict)
    _anc(nfix_rate_id="N3", reference_id="R2", salinity="33", NH4_wc="4", NH4_wc_units="umol l-1", DO_wc="250",
         DO_wc_units="um"),
    _anc(nfix_rate_id="N4", reference_id="R2", salinity="33", NH4_wc="4", NH4_wc_units="umol l-1", DO_wc="260",
         DO_wc_units="um"),
]
REFS = pd.DataFrame(
    [
        {"reference_id": "R1", "reference": "Adame et al 2012", "doi": "https://doi.org/10.1000/ABC", "doi_data": "NA",
         "url": "http://x", "publication_year": "2012", "authors": "Adame, M", "title": "A title",
         "publication": "J", "volume": "1", "pages": "1-2", "extraction_location": "paper - table/s",
         "extraction_location_details": "table 2"},
        {"reference_id": "R2", "reference": "Other 2015", "doi": "NA", "doi_data": "10.5/data", "url": "NA",
         "publication_year": "2015", "authors": "Other, O", "title": "Another", "publication": "J", "volume": "2",
         "pages": "3", "extraction_location": "from author", "extraction_location_details": "NA"},
    ],
    columns=fx.REF_COLUMNS,
)
# expected: 6 rate facts; J1 cells 9 (N1 3, N2 2, N3 2, N4 2) of which 1 has no unit -> skip; sample A (N1+N2): NH4 1.5 (1
# collapsed), pH, NO3 = 3 facts; sample B (N3+N4): NH4 4 (1 collapsed), DO 250 and 260 (conflict) = 3 facts; J1 facts 6
# + collapsed 2 + skipped 1 = 9. Facts 12. Samples 4 (A, B, N5, N6), locations 4, methods 6 + 4 J1 attributes = 10.


def _frames(rates=RATES, anc=ANC, refs=REFS):
    return (pd.DataFrame(rates, columns=fx.RATE_COLUMNS, dtype=str), pd.DataFrame(anc, columns=fx.ANC_COLUMNS, dtype=str),
            refs.copy())


def _build(rates=RATES, anc=ANC, refs=REFS):
    r, a, f = _frames(rates, anc, refs)
    return fx.build_tables(r, a, f, ACCESS)


def _with(table: list[dict], i: int, **kw) -> list[dict]:
    rows = [dict(r) for r in table]
    rows[i].update(kw)
    return rows


@pytest.fixture(scope="module")
def built():
    return _build()


def test_counts(built) -> None:
    tables, skips, rep = built
    assert rep["source_rows"] == 6 and rep["rate_facts"] == 6 and rep["j1_facts"] == 6 == len(tables["fact"]) - 6
    assert len(tables["fact"]) == 12 == rep["facts"]
    assert rep["j1_numeric_cells"] == 9 and rep["j1_cells_with_unit"] == 8 and rep["j1_collapsed"] == 2
    assert [(s["data_row"], s["attribute"], s["reason"], s["cell"]) for s in skips] == [(0, "water_chl_a", "units_missing", "3")]
    assert rep["j1_conflict_facts"] == 2 and rep["j1_conflict_samples"] == 1
    assert rep["j1_facts_under_plan_key"] == 6  # same samples under the plan's key in this fixture
    assert len(tables["sample"]) == 4 and len(tables["location"]) == 4 and len(tables["method"]) == 10
    assert len(tables["reference"]) == 2 == len(tables["citation"])
    assert rep["facts_by_attribute"] == {
        "fulweiler_n2fix_benthic": 3, "fulweiler_n2fix_water": 3, "water_ammonium": 2, "water_nitrate": 1,
        "water_o2": 2, "water_ph": 1}
    assert rep["anc_columns_not_loaded_non_na_cells"]["PAR"] == 1
    validate_tables(add_derived_labels(tables))


def test_source_row(built) -> None:
    src = built[0]["source"].iloc[0]
    assert src.version == "EDI edi.1759.2 (ver 2)" and src.license == "CC BY 4.0" and src.access_date == ACCESS
    assert src.doi == "10.6073/pasta/333f651ca721da657d5fd0c393d26cf8"


def test_rate_facts(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    t = lambda k: tuple(f.loc[k, ["measurement_type", "subfield", "attribute", "units_raw"]])  # noqa: E731
    assert t("N1:fulweiler_n2fix_benthic") == ("C7", "n2_fixation", "fulweiler_n2fix_benthic", M2)
    assert t("N3:fulweiler_n2fix_water") == ("C5", "n2_fixation", "fulweiler_n2fix_water", L1)
    assert t("N5:fulweiler_n2fix_benthic")[3] == "umol-n g-1 h-1"  # 'other' substrate -> benthic attribute
    assert f.loc["N1:fulweiler_n2fix_benthic", "value"] == 1.5 and f.loc["N3:fulweiler_n2fix_water", "value"] == 3e-6
    assert f.loc["N1:fulweiler_n2fix_benthic", "sd"] == 0.2 and pd.isna(f.loc["N1:fulweiler_n2fix_benthic", "se"])
    assert f.loc["N1:fulweiler_n2fix_benthic", "n"] == 3 and pd.isna(f.loc["N2:fulweiler_n2fix_benthic", "n"])
    assert f.loc[["N3:fulweiler_n2fix_water", "N5:fulweiler_n2fix_benthic"], ["sd", "se"]].isna().all().all()  # range / no type
    assert f.loc["N1:fulweiler_n2fix_benthic", "source_row_ref"] == "aquatic_N2fix_rates.csv#data_row=0:fulweiler_n2fix_benthic"
    assert f.loc["N1:fulweiler_n2fix_benthic", "reference_key"] == "R1"


def test_se_type() -> None:
    rows = _with(RATES, 0, nfix_error_type="std. err")
    f = _build(rows)[0]["fact"].set_index("fact_key")
    assert f.loc["N1:fulweiler_n2fix_benthic", "se"] == 0.2 and pd.isna(f.loc["N1:fulweiler_n2fix_benthic", "sd"])


def test_flags(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    fl = lambda k: f.loc[k, "flags"]  # noqa: E731
    assert fl("N1:fulweiler_n2fix_benthic") == []
    assert fl("N2:fulweiler_n2fix_benthic") == ["zero_value"]
    for k in ("N3:fulweiler_n2fix_water", "N4:fulweiler_n2fix_water"):
        assert fl(k) == ["converted_by_source", "duplicate_source_row", "error_type_other", "habitat_from_scope",
                         "salinity_units_missing", "year_missing"]
    assert fl("N5:fulweiler_n2fix_benthic") == ["converted_by_source", "error_type_missing"]
    assert fl("N6:fulweiler_n2fix_water") == ["habitat_from_scope"]  # year 2002 given
    do = f[f.attribute == "water_o2"]
    assert sorted(do.value) == [250.0, 260.0] and all("covariate_conflict" in x for x in do["flags"])
    assert rep["rows_negative_original_stored_positive"] == 1 and rep["rows_converted_by_source"] == 3
    assert rep["rows_in_duplicate_groups"] == 2 and rep["duplicate_groups"] == 1


def test_j1(built) -> None:
    tables, _, _ = built
    f = tables["fact"]
    j = f[f.measurement_type == "J1"].set_index(["attribute", "value"])
    assert j.loc[("water_ammonium", 1.5), "units_raw"] == "um" and j.loc[("water_ammonium", 1.5), "subfield"] == "ammonium"
    assert j.loc[("water_ammonium", 1.5), "source_row_ref"] == "aquatic_N2fix_ancillary.csv#data_row=0:water_ammonium"
    assert j.loc[("water_ammonium", 4.0), "units_raw"] == "umol l-1" and j.loc[("water_ammonium", 4.0), "source_row_ref"].endswith("data_row=2:water_ammonium")
    assert j.loc[("water_ph", 7.5), "units_raw"] == "unitless" and j.loc[("water_nitrate", 2.0), "units_raw"] == "um"
    assert j.loc[("water_o2", 250.0), "subfield"] == "dissolved_oxygen"
    assert not (f.attribute == "water_chl_a").any()  # no unit: skipped
    # J1 facts of the water-column sample sit on the same sample / location as that sample's rate facts
    fx_ = f.set_index("fact_key")
    nh4 = f[(f.attribute == "water_ammonium") & (f.value == 4.0)].iloc[0]
    assert nh4.sample_key == fx_.loc["N3:fulweiler_n2fix_water", "sample_key"] == fx_.loc["N4:fulweiler_n2fix_water", "sample_key"]
    assert nh4.location_key == fx_.loc["N3:fulweiler_n2fix_water", "location_key"]
    assert "salinity_units_missing" in nh4["flags"] and "habitat_from_scope" in nh4["flags"]


def test_samples_locations(built) -> None:
    tables, _, _ = built
    s = tables["sample"]
    a = s[s.sample_key.str.startswith("R1|10|20|2000|5")].iloc[0]
    assert a.sampling_year == 2000 and a.sampling_month == 5 and a.water_salinity == 10 and a.water_salinity_units == "psu"
    assert a.depth_notes == "substrate: benthos; details: sediment; sample_depth (depth of site, sample or sediment, m): 0.1"
    assert pd.isna(a.sample_depth_m)  # numeric only for wc
    b = s[s.sample_key.str.startswith("R2|-28|114")].iloc[0]
    assert pd.isna(b.sampling_year) and b.sample_depth_m == 2 and b.water_salinity == 33 and pd.isna(b.water_salinity_units)
    n6 = s[s.sample_key.str.startswith("R1|11|21")].iloc[0]
    assert pd.isna(n6.sample_depth_m) and pd.isna(n6.water_salinity)
    loc = tables["location"].set_index("site_name")
    assert set(loc.habitat_type) == {"salt marshes", "estuaries | wc", "lakes", "seagrasses | wc"}
    assert loc.loc["Shelf A", "latitude"] == -28 and loc.loc["Shelf A", "longitude"] == 114


def test_labels(built) -> None:
    loc = add_derived_labels(built[0])["location"].set_index("habitat_type")
    assert loc.loc["salt marshes", "habitat"] == "Tidal marsh" and loc.loc["salt marshes", "bottom_type"] == "soft"
    assert pd.isna(loc.loc["salt marshes", "setting"])
    assert loc.loc["estuaries | wc", "habitat"] == "Water column" and loc.loc["estuaries | wc", "setting"] == "estuary"
    assert pd.isna(loc.loc["estuaries | wc", "bottom_type"])
    assert loc.loc["seagrasses | wc", "habitat"] == "Water column" and pd.isna(loc.loc["seagrasses | wc", "setting"])
    assert pd.isna(loc.loc["lakes", "habitat"])


def test_methods(built) -> None:
    tables, _, _ = built
    m = tables["method"].set_index("method_key")
    assert m.loc["N1", "technique"] == "ARA" and m.loc["N1", "method_status"] == "matched" and pd.isna(m.loc["N1", "conversion_applied"])
    assert m.loc["N3", "conversion_applied"] == "source converted: 0.003 nmol-n l-1 h-1 -> 3e-6 umol-n l-1 h-1"
    p = json.loads(m.loc["N1", "params"])
    assert p["ARA_conversion_factor"] == "3 to 1" and p["rate_original"] == "1.5" and p["extraction_location"] == "paper - table/s"
    assert p["substrate_details"] == "sediment" and p["error_type"] == "stdev." and p["incubation_time"] is None
    p5 = json.loads(m.loc["N5", "params"])
    assert p5["rate_original"] == "-2" and p5["data_archive_doi"] == "10.5/data" and p5["extraction_location"] == "from author"
    assert m.loc["water_ammonium", "method_status"] == "method_id_null" and "N6" in m.index
    f = tables["fact"].set_index("fact_key")
    assert f.loc["N2:fulweiler_n2fix_benthic", "method_key"] == "N2"


def test_references(built) -> None:
    tables, _, _ = built
    c = tables["citation"].set_index("reference_key")
    assert c.loc["R1", "doi"] == "10.1000/abc" and c.loc["R1", "year"] == 2012 and c.loc["R1", "title"] == "A title"
    assert c.loc["R1", "bibliography_id"] == "Adame et al 2012" and c.loc["R1", "authors"] == "Adame, M"
    assert pd.isna(c.loc["R2", "doi"]) and pd.isna(c.loc["R2", "url"])
    assert set(tables["reference"].reference_key) == {"R1", "R2"}


# ---- fail-loud checks


def test_unrecognised_text_raises() -> None:
    with pytest.raises(ContractError, match="unrecognised value"):
        _build(_with(RATES, 0, nfix_rate_converted="n/a"))
    with pytest.raises(ContractError, match="unrecognised value"):
        _build(anc=_with(ANC, 0, NH4_wc="<0.1"))


def test_unknown_labels_raise() -> None:
    with pytest.raises(ContractError, match="unknown habitat or substrate"):
        _build(_with(RATES, 0, habitat="deltas"))
    with pytest.raises(ContractError, match="unknown habitat or substrate"):
        _build(_with(RATES, 0, substrate="air"))
    with pytest.raises(ContractError, match="unknown error type or method"):
        _build(_with(RATES, 0, nfix_error_type="sem"))


def test_value_checks() -> None:
    with pytest.raises(ContractError, match="missing or negative converted rate"):
        _build(_with(RATES, 0, nfix_rate_converted="NA"))
    with pytest.raises(ContractError, match="missing or negative converted rate"):
        _build(_with(RATES, 0, nfix_rate_converted="-1"))
    with pytest.raises(ContractError, match="missing or out-of-range coordinates"):
        _build(_with(RATES, 0, latitude="95"))
    with pytest.raises(ContractError, match="month outside"):
        _build(_with(RATES, 0, month="13"))
    with pytest.raises(ContractError, match="nfix_n is not"):
        _build(_with(RATES, 0, nfix_n="1.5"))
    with pytest.raises(ContractError, match="negative error"):
        _build(_with(RATES, 0, nfix_error_converted="-0.1"))


def test_id_and_reference_checks() -> None:
    with pytest.raises(ContractError, match="not unique"):
        _build(_with(RATES, 1, nfix_rate_id="N1"))
    with pytest.raises(ContractError, match="unknown rate ids"):
        _build(anc=_with(ANC, 0, nfix_rate_id="N99"))
    with pytest.raises(ContractError, match="reference differs"):
        _build(anc=_with(ANC, 0, reference_id="R2"))
    with pytest.raises(ContractError, match="differ"):
        _build(refs=REFS.iloc[:1])


def test_wrong_columns_raise() -> None:
    r, a, f = _frames()
    with pytest.raises(ContractError, match="unexpected columns"):
        fx.build_tables(r.rename(columns={"habitat": "hab"}), a, f, ACCESS)
    with pytest.raises(ContractError, match="unexpected columns"):
        fx.build_tables(r, a.drop(columns=["PAR"]), f, ACCESS)


def test_unknown_unit_raises() -> None:
    with pytest.raises(ContractError, match="not declared"):
        _build(_with(RATES, 0, nfix_unit_converted="umol-n km-2 h-1"))
    with pytest.raises(ContractError, match="not declared"):
        _build(anc=_with(ANC, 0, NH4_wc_units="furlongs"))


def test_helpers() -> None:
    assert fx.num("NA", "x") is None and fx.num("1e-5", "x") == 1e-5 and fx.num("-3.5", "x") == -3.5
    for bad in ("", " ", "1,5", "abc", "NaN"):
        with pytest.raises(ContractError):
            fx.num(bad, "x")
    assert fx.txt("NA") is None and fx.txt("a  b ") == "a b"


# ---- build + determinism


def test_roundtrip_global_build_and_determinism(built, tmp_path) -> None:
    tables, _, _ = built
    write_extracted(tables, tmp_path / "ex")
    back = read_extracted(tmp_path / "ex")
    assert len(back["fact"]) == 12
    out1, out2 = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    build_compilations([tmp_path / "ex"], out1)
    build_compilations([tmp_path / "ex"], out2)
    q = ("SELECT f.attribute, f.value, f.units_raw, f.measurement_type, l.habitat FROM measurement_fact f "
         "JOIN location_dim l USING (location_id) ORDER BY f.measurement_id")
    r1 = duckdb.connect(str(out1), read_only=True).execute(q).fetchall()
    r2 = duckdb.connect(str(out2), read_only=True).execute(q).fetchall()
    assert r1 == r2 and len(r1) == 12 and {r[3] for r in r1} == {"C5", "C7", "J1"}
    assert sum(1 for r in r1 if r[4] == "Water column") == 6  # rate facts N3, N4, N6 + J1 NH4, DO, DO of sample N3/N4


def test_build_is_deterministic() -> None:
    a, sa, ra = _build()
    b, sb, rb = _build()
    for k in a:
        pd.testing.assert_frame_equal(a[k], b[k])
    assert ra == rb and sa == sb
