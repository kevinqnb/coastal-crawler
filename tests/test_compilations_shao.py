"""Unit tests (rung 1) for the shao-nfix extract.py.

Hand-built workbook (4 rows on the 24 h volumetric sheet, 1 on the integral sheet, 1 on Cell_Specific; the other sheets are
header-only); every expected number is derived by inspection in the comments.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from coastal_crawler.compilations import ContractError, add_derived_labels, validate_tables

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "shao-nfix" / "extract.py"
_spec = importlib.util.spec_from_file_location("shao_extract", _EXTRACT)
assert _spec and _spec.loader
sh = importlib.util.module_from_spec(_spec)
sys.modules["shao_extract"] = sh
_spec.loader.exec_module(sh)

ACCESS = "2026-11-15"
V24 = "N2_Fixation (Incub. 24 h)"
I24 = "N2_Fixation_Integral (24 h)"
CS = sh.CELL_SHEET
BEN = "Benavides et al. (2011), doi:10.3354/ame01534 "
PAL = "Palter et al. 2020"
BON = "Bonnet et al. (2009), doi:10.1029/2008GB003439"
MIL = "Mills et al., 2020, doi.org/10.1038/s41396-020-0610-z"
CHURCH = "Church, M.J., University of Hawaii, mjchurch@hawaii.edu"
N0 = "hourly rates multiplied to 24 h. Data read from figure."
QC = "REJECTED by Quality Control using Chauvenet’s criterion.(<10 um NFR)"


def _v(src="", meth="C2H2 Reduction", inc=24, date=dt.datetime(2009, 7, 26), lat=41.5, lon=-9.3, depth=5, trich="", het="", lt10="",
       gt10="", whole="", total="", light="", dark="", temp="", sal="", no3="", po4="", fe="", chl="", npp="", notes="") -> list:
    return [src, meth, inc, date, lat, lon, depth, trich, het, lt10, gt10, whole, total, light, dark, temp, sal, no3, po4, fe, chl, npp, notes]


V_ROWS = [
    _v(BEN, lt10=0.03, whole=0.0456, total=0.0456, temp=18.6, sal=35.7, no3="2.38 (0.64)", po4="<0.02", fe=1, chl=0.15, notes=N0),
    _v("", trich="bdl", lt10="0.49 (0.02", whole="n.d.", total="nm", temp="nd", no3="2.38 (0.64)", chl="n.a.",
       notes="rates were reported  as daily rates"),
    _v(PAL, date="2008/7/19-8/3", lat="25.128\t", lon=10, depth="", whole=5.0, no3=">5", chl="1.29  (0.22)", notes=QC),
    _v(CHURCH, date="2008/7/19-8/3", lat="25.128\t", lon=10, depth="", whole=1.0),
]
I_ROW = [BON, "15N2 bubble assimilation", 24, dt.datetime(2006, 8, 26), 0.0043, -140.0, 100, "", "", "", "", 97.5, 97.5, 35.4, 35.4,
         0.49, "", "", "", 30.3855]
C_ROW = [""] * 65
C_ROW[:7] = [MIL, "nanoSIMS", 24, dt.datetime(2017, 5, 4), 32.84, -117.531, 0]
C_ROW[13], C_ROW[14], C_ROW[15] = -0.007, 0.874, -0.008
C_ROW[60], C_ROW[63] = 0.198, "limits of detection (LOD) is 0.01"
# expected: rate facts v24 5 (r0: lt10, whole, total; r2 whole; r3 whole), integral 2, cell 3 = 10; J1 cells: v24 r0 3 + r1 2 + r2 2, integral 1,
# cell 1 = 9: facts 5 + 1 + 1 = 7, collapsed 1 (r1's nitrate equals r0's), skipped 1 (r1 chl 'n.a.'); rate skips 4 (r1: bdl, malformed, n.d.,
# nm); facts 17, skipped 5, collapsed 1, cells_in 23.


def _hdr(names: list[str]) -> list[str]:
    return names


def _raw() -> dict[str, pd.DataFrame]:
    def frame(h: list[str], rows: list[list]) -> pd.DataFrame:
        return pd.DataFrame([h] + rows, dtype=object)
    cs_hdr = []
    for h in sh._cell_headers():
        if "|" in h:
            lab, kind = h.split("|")
            cs_hdr.append({"rate": f"{lab} Nitrogen Fixation Rates (fmol N cell-1 d-1)", "cellular_n": "cellular biomass (fmol N cell-1 )",
                           "rate_per_n": f"{lab} Nitrogen Fixation Rates \n(fmol N fmol N-1 d-1)"}[kind])
        else:
            cs_hdr.append("" if h == "(unnamed)" else h)
    cs_row = C_ROW
    raw = {s: frame(["x"], []) for s in sh.SHEETS}
    raw[V24] = frame(sh.expected_headers(V24), V_ROWS)
    raw["N2_Fixation (Incub.<24 h)"] = frame(sh.expected_headers("N2_Fixation (Incub.<24 h)"), [])
    raw[I24] = frame(sh.expected_headers(I24), [I_ROW])
    raw["N2_Fixation_Integral (<24 h)"] = frame(sh.expected_headers("N2_Fixation_Integral (<24 h)"), [])
    raw[CS] = frame(cs_hdr, [cs_row])
    raw["Reference List"] = frame(["Reference"], [["Benavides, M., et al. (2011). Nitrogen fixation. Aquat. Microb. Ecol., doi:10.3354/ame01534."]])
    return raw


@pytest.fixture(autouse=True)
def _no_real_slot_conflicts(monkeypatch):
    monkeypatch.setattr(sh, "SLOT_CONFLICT_SAMPLES", frozenset())


@pytest.fixture(scope="module")
def built():
    saved = sh.SLOT_CONFLICT_SAMPLES  # module fixtures cannot use monkeypatch
    sh.SLOT_CONFLICT_SAMPLES = frozenset()
    try:
        return sh.build_tables(_raw(), ACCESS)
    finally:
        sh.SLOT_CONFLICT_SAMPLES = saved


def test_counts(built) -> None:
    tables, skips, rep = built
    assert len(tables["fact"]) == 17 == rep["facts"] and rep["skipped"] == 5 == len(skips) and rep["cells_in"] == 23
    assert rep["j1_collapse"]["collapsed_repeats"] == 1 and rep["j1_collapse"]["conflict_facts"] == 0
    assert rep["facts_by_attribute"] == {
        "shao_cellular_n_content": 1, "shao_n2fix_per_cell": 1, "shao_n2fix_per_cellular_n": 1, "shao_n2fix_total": 2,
        "shao_n2fix_whole_seawater": 4, "shao_n2fix_lt10um": 1, "water_chl_a": 3, "water_nitrate": 3, "water_phosphate": 1}
    assert rep["skipped_by_reason"] == {"below_detection_limit": 2, "not_measured": 2, "unparseable_value": 1}
    assert len(tables["sample"]) == 6 and len(tables["location"]) == 4 and len(tables["reference"]) == 5
    assert len(tables["method"]) == 13 and rep["methods_with_notes"] == 9
    validate_tables(add_derived_labels(tables))


def test_cell_rules(built) -> None:
    tables, skips, rep = built
    f = tables["fact"].set_index("fact_key")
    nit = f.loc["v24#0:water_nitrate"]
    assert nit.value == 2.38 and nit.sd == 0.64 and nit.units_raw == "µM" and "source_mean_reported" in nit["flags"]  # U+00B5
    assert "v24#1:water_nitrate" not in f.index  # equal to row 0's: collapsed
    po4 = f.loc["v24#0:water_phosphate"]
    assert po4.value == 0.02 and po4.value_comparator == "<" and "inequality" in po4["flags"]
    gt = f.loc["v24#2:water_nitrate"]
    assert gt.value == 5.0 and gt.value_comparator == ">" and "inequality" in gt["flags"]
    chl = f.loc["v24#2:water_chl_a"]
    assert chl.value == 1.29 and chl.sd == 0.22 and chl.units_raw == "mg m-3"
    assert pd.isna(f.loc["v24#0:water_nitrate", "value_comparator"]) and f.value_comparator.dropna().isin(["<", ">"]).all()
    s = {(x["data_row"], x["attribute"]): (x["reason"], x["cell"]) for x in skips}
    assert s[(1, "shao_n2fix_trichodesmium")] == ("below_detection_limit", "bdl")
    assert s[(1, "shao_n2fix_lt10um")] == ("unparseable_value", "0.49 (0.02")
    assert s[(1, "shao_n2fix_whole_seawater")][0] == "below_detection_limit" and s[(1, "shao_n2fix_total")][0] == "not_measured"
    assert s[(1, "water_chl_a")] == ("not_measured", "n.a.")
    assert rep["malformed_mean_sd_cells_skipped"] == {"0.49 (0.02": 1}


def test_rates_units_flags_methods(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    r = f.loc["v24#0:shao_n2fix_total"]
    assert (r.measurement_type, r.subfield, r.units_raw, r.value) == ("C5", "n2_fixation", "μmol N m-3 d-1", 0.0456)  # U+03BC
    assert "converted_by_source" in r["flags"] and "qc_rejected_by_source" not in r["flags"]
    q = f.loc["v24#2:shao_n2fix_whole_seawater"]
    assert "qc_rejected_by_source" in q["flags"] and "converted_by_source" not in q["flags"]
    assert f.loc["i24#0:shao_n2fix_total", "units_raw"] == "μmol N m-2 d-1"
    assert rep["conversion_note_rows_by_pattern"] == {"hourly rates multiplied (to 12/24 h or daylength)": 1}
    assert rep["qc_rejected_rows"] == 1 and rep["notes_cells_that_are_numbers"] == {I24: 1}
    m = tables["method"].set_index("method_key")
    mk = f.loc["v24#0:shao_n2fix_total", "method_key"]
    assert m.loc[mk, "notes"] == N0 and m.loc[mk, "technique"] == "C2H2 Reduction" and '"incubation_time_h": "24"' in m.loc[mk, "params"]
    assert m.loc[f.loc["i24#0:shao_n2fix_total", "method_key"], "notes"] == "30.3855"
    assert pd.isna(m.loc["water_nitrate", "notes"])


def test_cell_specific(built) -> None:
    tables, _, _ = built
    f = tables["fact"].set_index("fact_key")
    a = f.loc["cs#0:shao_n2fix_per_cell[UCYN-A1]"]
    assert (a.units_raw, a.value, a.measurement_type) == ("fmol N cell-1 d-1", -0.007, "C5")
    b = f.loc["cs#0:shao_cellular_n_content[UCYN-A1]"]
    assert (b.measurement_type, b.units_raw, b.value) == ("other", "fmol N cell-1", 0.874) and pd.isna(b.subfield)
    assert f.loc["cs#0:shao_n2fix_per_cellular_n[UCYN-A1]", "units_raw"] == "fmol N fmol N-1 d-1"
    s = tables["sample"].set_index("sample_key")
    ts = s[s.taxon.notna()]
    assert list(ts.taxon) == ["UCYN-A1"] and list(ts.taxon_rank) == ["clade"]
    m = tables["method"].set_index("method_key")
    assert m.loc[a.method_key, "notes"] == "limits of detection (LOD) is 0.01"
    assert f.loc["cs#0:water_chl_a", "value"] == 0.198 and pd.isna(s.loc[f.loc["cs#0:water_chl_a", "sample_key"], "taxon"])


def test_references_and_leftovers(built) -> None:
    tables, _, rep = built
    f = tables["fact"].set_index("fact_key")
    assert f.loc["v24#0:shao_n2fix_total", "reference_key"] == "doi:10.3354/ame01534"
    assert f.loc["v24#2:shao_n2fix_whole_seawater", "reference_key"] == f"ref:{PAL}"
    assert f.loc["v24#3:shao_n2fix_whole_seawater", "reference_key"] == f"ref:{CHURCH}"  # every SOURCE cell is a reference, e-mail ones too
    assert f.loc["v24#1:water_nitrate" if "v24#1:water_nitrate" in f.index else "v24#0:water_nitrate", "reference_key"] == "doi:10.3354/ame01534"  # blank cell: same block
    assert f.loc["cs#0:shao_n2fix_per_cell[UCYN-A1]", "reference_key"] == "doi:10.1038/s41396-020-0610-z"
    assert rep["source_strings_without_doi_and_author_year_pattern"] == 1 and rep["reference_cells"] == 5  # CHURCH has neither
    assert "pers_comm" not in f["flags"].sum()  # not inferred from an e-mail address
    c = tables["citation"].set_index("reference_key")
    assert c.loc[f"ref:{CHURCH}", "bibliography_id"] == CHURCH and pd.isna(c.loc[f"ref:{CHURCH}", "doi"])
    assert c.loc["doi:10.3354/ame01534", "year"] == 2011 and c.loc["doi:10.3354/ame01534", "title"].startswith("Benavides, M.")
    assert pd.isna(c.loc[f"ref:{PAL}", "doi"]) and c.loc[f"ref:{PAL}", "year"] == 2020 and rep["citations_with_reference_list_title"] == 1


def test_samples_dates_depth(built) -> None:
    tables, _, rep = built
    s = tables["sample"].set_index("sample_key")
    s1 = s.loc["doi:10.3354/ame01534|41.5|-9.3|2009-07-26|5|vol"]
    assert (s1.sampling_year, s1.sampling_month, s1.sample_depth_m, s1.water_temperature_c, s1.water_salinity) == (2009, 7, 5.0, 18.6, 35.7)
    assert s1.water_salinity_units == "PSU"
    s2 = s.loc[f"ref:{PAL}|25.128|10.0|2008/7/19-8/3||vol"]
    assert s2.sampling_year == 2008 and pd.isna(s2.sampling_month) and s2.depth_notes == "date as given: 2008/7/19-8/3"
    i = s.loc["doi:10.1029/2008gb003439|0.0043|-140.0|2006-08-26|100|int"]
    assert pd.isna(i.sample_depth_m) and i.depth_notes == "integration depth: 100 m" and pd.isna(i.water_temperature_c)  # near-surface: not loaded
    assert rep["coordinates_text_with_whitespace"] == 2 and rep["slot_cells_not_plain_numbers"] == {"temp_cell_not_a_plain_number": 1}
    loc = add_derived_labels(tables)["location"]
    assert (loc.habitat == "Water column").all() and loc.bottom_type.isna().all()


# ---- failure modes


@pytest.mark.parametrize(
    "sheet,r,j,v,msg",
    [(V24, 0, 11, "weird", "unrecognised cell"), (V24, 0, 17, "approx 3", "unrecognised cell"), (V24, 0, 3, "26 July", "unrecognised date"),
     (V24, 0, 4, "north", "coordinate"), (V24, 0, 4, 95.0, "out of range"), (V24, 0, 0, "", "no reference above"),
     (V24, 0, 0, 5, "not text")],
)
def test_bad_cells_raise(sheet, r, j, v, msg) -> None:
    raw = _raw()
    raw[sheet] = raw[sheet].copy()
    raw[sheet].iat[r + 1, j] = v
    with pytest.raises(ContractError, match=msg):
        sh.build_tables(raw, ACCESS)


def test_header_and_sheet_checks() -> None:
    raw = _raw()
    raw[V24] = raw[V24].copy()
    raw[V24].iat[0, 11] = "Whole Seawater N2 Fixation (µmol N m-3 d-1)"  # U+00B5 instead of U+03BC
    with pytest.raises(ContractError, match="unexpected headers"):
        sh.build_tables(raw, ACCESS)
    raw = _raw()
    raw[CS] = raw[CS].copy()
    raw[CS].iat[0, 10] = "UCYN-X Nitrogen Fixation Rates (fmol N cell-1 d-1)"
    with pytest.raises(ContractError, match="does not match"):
        sh.build_tables(raw, ACCESS)


def test_classify() -> None:
    c = sh.classify
    assert c(1.5, "x") == ("num", {"value": 1.5}) and c("", "x")[0] == "blank" and c(" ", "x")[0] == "blank"
    assert c("\xa02.23", "x")[1]["value"] == 2.23 and c("< 29", "x") == ("censored", {"value": 29.0, "comparator": "<"})
    assert c("2.38 (0.64)", "x") == ("mean_sd", {"value": 2.38, "sd": 0.64}) and c("0.01 (0.00)*", "x")[0] == "malformed"
    for code, kind in (("n.d.", "bdl"), ("ND", "bdl"), ("< DL", "bdl"), ("DL", "bdl"), ("dnq", "bdl"), ("u.d.", "bdl"), ("bld", "bdl"),
                       ("nm", "nm"), ("N.A.", "nm"), ("/", "nm"), ("-", "nm"), ("nan", "nm"), ("n", "nm")):
        assert c(code, "x")[0] == kind, code
    with pytest.raises(ContractError):
        c("x1", "x")
    with pytest.raises(ContractError):
        c(float("nan") + 1, "x") if False else c(float("inf"), "x")


def test_reference_rule() -> None:
    assert sh.is_reference("Palter et al. 2020") and sh.is_reference("10.5194/bg-18-3733-2021") and sh.is_reference("Wang et al. 2018 ISME")
    assert sh.is_reference("Bonnet et al., 2023,  10.1126/science.abq4654") and sh.doi_of("doi: 10.3389/fmicb.2013.00227.") == "10.3389/fmicb.2013.00227"
    for t in ("data shown in figure 4", "Fig.2", "ETSP cruise", "Correspondence: Berman-Frank", CHURCH, "Foster et al. Unpublish",
              "during El Nino (2010) and La Nina (2011)"):
        assert not sh.is_reference(t), t


def test_pers_comm_only_when_the_string_says_so() -> None:
    raw = _raw()
    raw[V24] = raw[V24].copy()
    raw[V24].iat[4, 0] = "Church, pers. comm."
    t, _, _ = sh.build_tables(raw, ACCESS)
    f = t["fact"].set_index("fact_key")
    assert "pers_comm" in f.loc["v24#3:shao_n2fix_whole_seawater", "flags"] and "pers_comm" not in f.loc["v24#0:shao_n2fix_total", "flags"]


def test_slot_conflicts_need_the_allow_list(monkeypatch) -> None:
    raw = _raw()
    raw[V24] = raw[V24].copy()
    raw[V24].loc[len(raw[V24])] = _v(BEN, whole=2.0, temp=19.0, sal=35.7)  # same sample as row 0, another temperature
    with pytest.raises(ContractError, match="differ from SLOT_CONFLICT_SAMPLES"):
        sh.build_tables(raw, ACCESS)
    key = "doi:10.3354/ame01534|41.5|-9.3|2009-07-26|5|vol"
    monkeypatch.setattr(sh, "SLOT_CONFLICT_SAMPLES", frozenset({key}))
    t, _, rep = sh.build_tables(raw, ACCESS)
    s = t["sample"].set_index("sample_key").loc[key]
    assert pd.isna(s.water_temperature_c) and s.water_salinity == 35.7  # only the disagreeing slot is NULL
    assert rep["samples_with_conflicting_temperature_or_salinity_set_to_null"] == [key]
    monkeypatch.setattr(sh, "SLOT_CONFLICT_SAMPLES", frozenset({key, "x|1|1|d|1|vol"}))
    with pytest.raises(ContractError, match="missing"):
        sh.build_tables(raw, ACCESS)
