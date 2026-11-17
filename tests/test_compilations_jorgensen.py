"""Unit tests (rung 1) for the jorgensen-oxygen extract.py.

Hand-built sheet (description, header, units row, three section rows and six data rows with the real 16-column layout);
every expected number is derived by inspection in the comments.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from coastal_crawler.compilations import ContractError, add_derived_labels, validate_tables

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "jorgensen-oxygen" / "extract.py"
_spec = importlib.util.spec_from_file_location("jorgensen_extract", _EXTRACT)
assert _spec and _spec.loader
jo = importlib.util.module_from_spec(_spec)
sys.modules["jorgensen_extract"] = jo
_spec.loader.exec_module(jo)

ACCESS = "2026-11-15"
DESC = "Supplementary Table S1. Database. O2 fluxes are presented in mmol m-2 d-1. The selection-ID indicates data treatment."
HEAD = ["Selection-ID", "Station", "Bottom Water     O2 concentration", "TOU", "DOU", "OPD ", "N", "Lat (N -S) ",
        "Long (E - W) ", "Depth", "NPP", "Depth range", "Ocean", "Region", "Comments", "Reference"]
UNITS = list(jo.UNITS_ROW)
BLANK = [""] * 16
SEC1, SEC2, SEC3 = "  In Situ 0 - 6,500m O2 fluxes", "  In situ Hadal (>6,500m) O2 fluxes", "  Ex Situ (<1,000m) O2 fluxes"


def _data(sel, station="", bw="", tou="", dou="", opd="", n="", lat="", lon="", depth="", npp="", rng="", comment="", ref=""):
    return [sel, station, bw, tou, dou, opd, n, lat, lon, depth, npp, rng, "Pacific Ocean", "Somewhere", comment, ref]


def _section(t: str) -> list:
    return [t] + [""] * 15


R1, R2, R3, R4 = "Accornero, A. et al., 2003. Title one. J. 1, 2-3", "Ref Two, 2010. T.", "Ref Three, 2011. T.", "Ref Four, 2012. T."
ROWS = [
    [DESC] + [""] * 15, HEAD, UNITS, BLANK,                                                       # sheet rows 0-3
    _section(SEC1),                                                                              # 4
    _data(0, "INT1", 0.6, tou=0.37, lat=42.4333, lon=3.7, depth=785, rng="Slope (200-1000m)", ref=R1),     # 5 = data row 0
    _data(2, "INT1 (average)", 0.6, tou=1.235, n=4, lat=42.4333, lon=3.7, depth=785, npp=265.2, rng="Slope (200-1000m)"),  # 6
    _data("0, 3", "", "no info", tou=2.0, dou=1.0, opd=3.5, depth=5, rng="near-shore (0-50m)", comment="xyz", ref=R2),    # 7
    _data("1, 4, 5", 12, 150, dou=5.0, opd="3,8", lat=10, lon=-20, depth=1000, ref="Ref Two, 2010. T."),  # 8
    BLANK, BLANK, _section(SEC2),                                                               # 9-11
    _data(1, "H1", tou=9.9, lat=-5, lon=170, depth=7000, rng="Hadal (>6500m)", ref=R4),           # 12 = data row 4
    BLANK, BLANK, _section(SEC3),                                                               # 13-15
    _data("0, 3,, 4", "X", "170-180", tou=3.3, lat=5, lon=6, depth=20, rng="outer shelf (50-200m)", ref=R3),  # 16 = data row 5
]
# expected: data rows 6; facts TOU 5 (rows 0, 1, 2, 4, 5) + DOU 2 (rows 2, 3) + OPD 1 (row 2) = 8; skips 1 (row 3 OPD '3,8');
# samples 6; locations 6 (rows 0 and 1 share lat/lon/depth/label; rows 2, 3 (no label), 4, 5 differ... row 2 has no coordinates)
# = 5; references 4 (R1 carries to row 1, R2 on rows 2 and 3, R4, R3); methods: tou|in situ, dou|in situ, opd|in situ,
# tou|in situ hadal, tou|ex situ = 5.


def _raw(rows=None) -> pd.DataFrame:
    df = pd.DataFrame(rows or ROWS, dtype=object)
    df.attrs["sheet_name"] = "Sheet1"
    return df


@pytest.fixture(scope="module")
def built():
    return jo.build_tables(_raw(), ACCESS)


def test_counts(built) -> None:
    tables, skips, rep = built
    f = tables["fact"]
    assert len(f) == 8 == rep["facts"] and rep["data_rows"] == 6 and rep["rows_without_station"] == 1
    assert rep["facts_by_attribute"] == {"jorgensen_dou": 2, "jorgensen_opd": 1, "jorgensen_tou": 5}
    assert [(s["sheet_row"], s["attribute"], s["reason"], s["cell"]) for s in skips] == [(8, "jorgensen_opd", "unparseable_value", "3,8")]
    assert rep["numeric_cells_by_attribute"] == {"jorgensen_tou": 5, "jorgensen_dou": 2, "jorgensen_opd": 2}
    assert len(tables["sample"]) == 6 and len(tables["location"]) == 5 and len(tables["reference"]) == 4
    assert len(tables["method"]) == 5 and rep["data_rows_by_section"] == {"ex situ": 1, "in situ": 4, "in situ hadal (>6,500m)": 1}
    validate_tables(add_derived_labels(tables))


def test_values_units_provenance(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    r = f.loc["row2:jorgensen_dou"]
    assert (r.measurement_type, r.subfield, r.units_raw, r.value) == ("B5", "diffusive_o2_uptake", "mmol m-2 d-1", 1.0)
    assert tuple(f.loc["row2:jorgensen_opd", ["measurement_type", "subfield", "units_raw", "value"]]) == ("C2", "o2_penetration_depth", "mm", 3.5)
    assert f.loc["row0:jorgensen_tou", "source_row_ref"] == f"{jo.DATA_FILE}[Sheet1]#sheet_row=5:jorgensen_tou"
    assert f.loc["row5:jorgensen_tou", "source_row_ref"].endswith("#sheet_row=16:jorgensen_tou")
    assert "row3:jorgensen_opd" not in f.index and "row3:jorgensen_dou" in f.index
    assert f.loc["row1:jorgensen_tou", "n"] == 4 and pd.isna(f.loc["row0:jorgensen_tou", "n"])
    assert not any("npp" in a for a in f.attribute)


def test_selection_flags(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    fl = lambda k: f.loc[k, "flags"]  # noqa: E731
    assert fl("row0:jorgensen_tou") == ["habitat_from_scope", "o2_units_ambiguous"]
    assert fl("row1:jorgensen_tou") == ["habitat_from_scope", "o2_units_ambiguous", "source_average_row"]
    assert fl("row2:jorgensen_tou") == ["coords_missing", "habitat_from_scope", "shallow_lt10m"]  # bottom water 'no info': no slot
    assert fl("row3:jorgensen_dou") == ["low_o2_bottom_water", "method_excluded", "o2_units_ambiguous"]  # no label: no scope flag
    assert fl("row5:jorgensen_tou") == ["habitat_from_scope", "method_excluded", "shallow_lt10m"]  # '0, 3,, 4' = 0, 3, 4
    assert fl("row4:jorgensen_tou") == ["habitat_from_scope"]


def test_samples_locations(built) -> None:
    tables, _, rep = built
    s = tables["sample"].set_index("sample_key")
    assert s.loc["row0", "bottom_water_o2"] == 0.6 and s.loc["row0", "bottom_water_o2_units"] == "µM or % saturatiion"
    assert s.loc["row0", "source_sample_id"] == "INT1" and pd.isna(s.loc["row2", "source_sample_id"]) and s.loc["row3", "source_sample_id"] == "12"
    assert pd.isna(s.loc["row2", "bottom_water_o2"]) and pd.isna(s.loc["row2", "bottom_water_o2_units"])
    assert s.loc["row2", "depth_notes"] == "selection_id=0, 3; bottom_water_o2_as_written=no info; comment=xyz"
    assert s.loc["row5", "depth_notes"] == "selection_id=0, 3,, 4; bottom_water_o2_as_written=170-180"
    assert s.loc["row0", "depth_notes"] == "selection_id=0"
    loc = tables["location"].set_index("location_key")
    k = "42.4333|3.7|785.0|Slope (200-1000m)"
    assert loc.loc[k, "water_depth_m"] == 785 and loc.loc[k, "habitat_type"] == "Slope (200-1000m)"
    assert pd.isna(loc.loc["||5.0|near-shore (0-50m)", "latitude"]) and pd.isna(loc.loc["10|-20|1000.0|".replace("10|-20", "10.0|-20.0"), "habitat_type"])
    lab = add_derived_labels(tables)["location"].set_index("location_key")
    assert lab.loc[k, "habitat"] == "Subtidal soft sediment" and pd.isna(lab.loc[k, "setting"])
    assert lab.loc["5.0|6.0|20.0|outer shelf (50-200m)", "setting"] == "shelf"
    assert lab.loc["-5.0|170.0|7000.0|Hadal (>6500m)", "setting"] == "open ocean"
    assert lab.loc["10.0|-20.0|1000.0|", "habitat"] is None or pd.isna(lab.loc["10.0|-20.0|1000.0|", "habitat"])
    assert rep["locations_without_habitat_label"] == 1


def test_methods_references(built) -> None:
    tables, _, _ = built
    m = tables["method"].set_index("method_key")
    assert m.loc["jorgensen_tou|in situ", "in_situ_ex_situ"] == "in situ"
    assert m.loc["jorgensen_tou|in situ hadal (>6,500m)", "in_situ_ex_situ"] == "in situ hadal (>6,500m)"
    assert m.loc["jorgensen_tou|ex situ", "in_situ_ex_situ"] == "ex situ"
    f = tables["fact"].set_index("fact_key")
    assert f.loc["row1:jorgensen_tou", "reference_key"] == f"ref:{R1}"  # forward-filled
    assert f.loc["row3:jorgensen_dou", "reference_key"] == f"ref:{R2}"
    c = tables["citation"].set_index("bibliography_id")
    assert c.loc[R1, "year"] == 2003 and c.loc[R3, "year"] == 2011 and c.doi.isna().all()


# ---- failure modes


def _with(r: int, j: int, v) -> list:
    rows = [list(x) for x in ROWS]
    rows[r][j] = v
    return rows


@pytest.mark.parametrize(
    "rows,msg",
    [(_with(0, 0, "Something else"), "description"), (_with(1, 3, "TOU2"), "unexpected header"), (_with(2, 3, "(mmol)"), "units row"),
     (_with(5, 0, "7"), "Selection-ID"), (_with(5, 0, "a"), "unrecognised Selection-ID"), (_with(5, 0, 6), "Selection-ID"), (_with(5, 0, "0, 1"), "Selection-ID"),
     (_with(5, 0, "3"), "Selection-ID"), (_with(5, 3, "abc"), None), (_with(5, 7, ""), "not blank together"),
     (_with(5, 7, 95.0), "valid range"), (_with(5, 8, 200.0), "valid range"), (_with(5, 9, 0), "positive number"),
     (_with(5, 15, ""), "no reference above"), (_with(4, 0, "  Unknown section"), "unknown section row"),
     (_with(5, 6, 2.5), "non-integer N"), (_with(12, 15, ""), "has no reference"),
     (_with(3, 0, "x"), "row 3 is not blank")],
)
def test_bad_input_raises(rows, msg) -> None:
    if msg is None:  # a text cell in TOU is not an error here: it is an unparseable_value skip
        _, skips, _ = jo.build_tables(_raw(rows), ACCESS)
        assert {(s["attribute"], s["cell"]) for s in skips} == {("jorgensen_tou", "abc"), ("jorgensen_opd", "3,8")}
        return
    with pytest.raises(ContractError, match=msg):
        jo.build_tables(_raw(rows), ACCESS)


def test_data_before_section_and_missing_section_raise() -> None:
    rows = [list(x) for x in ROWS]
    del rows[4]
    with pytest.raises(ContractError, match="data before the first section row"):
        jo.build_tables(_raw(rows), ACCESS)
    rows = [list(x) for x in ROWS][:11]  # drops the hadal and ex situ sections
    with pytest.raises(ContractError, match="not all three sections"):
        jo.build_tables(_raw(rows), ACCESS)


def test_selection_codes() -> None:
    assert jo.selection_codes(1, "x") == {1} and jo.selection_codes("0, 3, 4", "x") == {0, 3, 4}
    assert jo.selection_codes("0, 3,, 4", "x") == {0, 3, 4} and jo.selection_codes(2.0, "x") == {2}
    for bad in ("", "0,3", "6", "0, 6", "3, 4", "0, 2", 1.5, None):
        with pytest.raises(ContractError):
            jo.selection_codes(bad, "x")
