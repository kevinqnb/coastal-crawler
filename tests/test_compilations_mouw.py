"""Unit tests (rung 1) for the mouw-carbon extract.py.

Hand-built fixture (8 rows of the GO_flux.tab layout and a 5-line header); every expected number is derived by
inspection in the comments.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from coastal_crawler.compilations import ContractError, add_derived_labels, validate_tables

_EXTRACT = Path(__file__).resolve().parents[1] / "data" / "compilations" / "mouw-carbon" / "extract.py"
_spec = importlib.util.spec_from_file_location("mouw_extract", _EXTRACT)
assert _spec and _spec.loader
mw = importlib.util.module_from_spec(_spec)
sys.modules["mouw_extract"] = mw
_spec.loader.exec_module(mw)

ACCESS = "2026-11-15"
HEADER = [
    "/* DATA DESCRIPTION:",
    "Citation:\tMouw et al.",
    "Related to:\tBCO-DMO: Some dataset (URI: https://www.bco-dmo.org/dataset/1)",
    "\tLutz, Michael J; Caldeira, Kenneth (2007): Seasonal rhythms of net primary production. Journal of Geophysical "
    "Research, 112, C10011, https://doi.org/10.1029/2006JC003706",
    "\tHonjo, Susumu: Sediment properties (NCEI Accession 9800155) [dataset]. NOAA, https://www.ncei.noaa.gov/x",
    "Further details:\tx",
]
BATS = ("31.6130", "-64.3140")
TS = "2008-01-01T00:00:00"


def _row(**kw) -> dict:
    r = {c: "" for c in mw.HEADER_COLUMNS}
    r.update({"Type (Data type)": "sediment_trap", "Type (Sediment trap type)": "unknown",
              "Date/time end (Retrieved)": "2008-02-01T00:00:00", "Duration [days]": "30.000"})
    for k, v in kw.items():
        r[k] = v
    return r


F_TOT, F_POC, F_PON = "Flux tot [mg/m**2/day]", "POC flux [mg/m**2/day]", "PON flux [mg/m**2/day]"
F_PSI, F_CACO3, F_PAL = "PSi flux [mg/m**2/day]", "CaCO3 flux [mg/m**2/day]", "PAl [mg/m**2/day]"
ROWS = [
    # 0: ID 1 (Lutz), location A (58, 179, -3783); only POC
    _row(**{"ID (Reference identifier)": "1", "Latitude": "58.0000", "Longitude": "179.0000",
            "Elevation [m a.s.l.] (Total water depth)": "-3783.000", "Depth water [m] (Sediment trap deployment depth)": "3137.0",
            "Date/Time (Deployed)": "1991-07-18T00:00:00", "Duration [days]": "330.000", F_POC: "5.5300", "Reference": "Lutz et al. 2007"}),
    # 1: same location; total 160.15 (sd 2), POC 7.32 (sd .5), PON 1.12, PSi 51.86 (sd 3); PAl is not loaded
    _row(**{"ID (Reference identifier)": "1", "Latitude": "58.0000", "Longitude": "179.0000",
            "Elevation [m a.s.l.] (Total water depth)": "-3783.000", "Depth water [m] (Sediment trap deployment depth)": "3137.0",
            "Date/Time (Deployed)": "1991-07-18T00:00:00", F_TOT: "160.1500", "Flux std dev [±]": "2.0", F_POC: "7.3200",
            "POC flux std dev [±]": "0.5", F_PON: "1.1200", F_PSI: "51.8600", "PSi flux std dev [±]": "3.0", F_PAL: "0.57",
            "Reference": "Lutz et al. 2007"}),
    # 2: ID 14 (BATS, no reference text), location B (BATS, no elevation); POC 0 (zero), CaCO3 2, PSi -0.5 (negative); duration 0
    _row(**{"ID (Reference identifier)": "14", "Latitude": BATS[0], "Longitude": BATS[1],
            "Depth water [m] (Sediment trap deployment depth)": "150.0", "Date/Time (Deployed)": "1988-12-16T00:00:00",
            "Duration [days]": "0.000", F_POC: "0.0000", F_CACO3: "2.0", F_PSI: "-0.5"}),
    # 3: BATS again, later date
    _row(**{"ID (Reference identifier)": "14", "Latitude": BATS[0], "Longitude": BATS[1],
            "Depth water [m] (Sediment trap deployment depth)": "150.0", "Date/Time (Deployed)": "2011-12-07T00:00:00",
            F_POC: "3.0"}),
    # 4: ID 2 (no reference text, unresolved), th234, location C, no deployment date, no elevation
    _row(**{"ID (Reference identifier)": "2", "Type (Data type)": "th234", "Latitude": "10.0000", "Longitude": "-20.0000",
            "Depth water [m] (Sediment trap deployment depth)": "100.0", F_POC: "1.0"}),
    # 5: ID 7: two cited papers in one string; location D
    _row(**{"ID (Reference identifier)": "7", "Latitude": "20.0000", "Longitude": "30.0000",
            "Elevation [m a.s.l.] (Total water depth)": "-500.000", "Depth water [m] (Sediment trap deployment depth)": "200.0",
            "Date/Time (Deployed)": TS, F_TOT: "9.0",
            "Reference": "Collier and Dymond, 1994, BCO-DMO, Honjo and Dymond, 1994, BCO-DMO,"}),
    # 6: ID 36 personal communication; location E
    _row(**{"ID (Reference identifier)": "36", "Latitude": "40.0000", "Longitude": "50.0000",
            "Depth water [m] (Sediment trap deployment depth)": "300.0", "Date/Time (Deployed)": TS, F_POC: "4.0",
            "Reference": "Conte, 2015, pers. comm."}),
    # 7: ID 1, location A; only PIC (not loaded): no fact, so no sample row
    _row(**{"ID (Reference identifier)": "1", "Latitude": "58.0000", "Longitude": "179.0000",
            "Elevation [m a.s.l.] (Total water depth)": "-3783.000", "Depth water [m] (Sediment trap deployment depth)": "3137.0",
            "Date/Time (Deployed)": "1991-07-18T00:00:00", "PIC flux [mg/m**2/day]": "3.2", "Reference": "Lutz et al. 2007"}),
]
# expected facts: row0 1 + row1 4 + row2 3 + row3 1 + row4 1 + row5 1 + row6 1 = 12; sd on 3; samples 7; locations 5
# (A rows 0,1; B rows 2,3; C; D; E); references 5 (IDs 1, 14, 2, 7, 36); citations 5 (Lutz, BATS, Collier, Honjo, Conte); methods:
# (poc|sediment_trap|unknown) (total|..) (pon|..) (psi|..) (caco3|..) + (poc|th234|unknown) = 6.


def _raw(rows=None) -> pd.DataFrame:
    return pd.DataFrame(rows or ROWS, columns=mw.HEADER_COLUMNS, dtype=object)


@pytest.fixture(scope="module")
def built():
    return mw.build_tables(HEADER, _raw(), ACCESS)


def test_counts(built) -> None:
    tables, skips, rep = built
    assert skips == [] and rep["skipped"] == 0
    assert len(tables["fact"]) == 12 == rep["facts"]
    assert rep["facts_by_attribute"] == {
        "mouw_caco3_flux": 1, "mouw_pon_flux": 1, "mouw_poc_flux": 6, "mouw_psi_flux": 2, "mouw_total_mass_flux": 2}
    assert rep["facts_with_sd"] == 3 and rep["source_rows"] == 8 and rep["source_rows_with_a_fact"] == 7
    assert len(tables["sample"]) == 7 and len(tables["location"]) == 5
    assert len(tables["reference"]) == 5 and len(tables["citation"]) == 5 and len(tables["method"]) == 6
    assert rep["not_loaded_columns_nonblank_cells"]["PAl [mg/m**2/day]"] == 1
    assert rep["not_loaded_columns_nonblank_cells"]["PIC flux [mg/m**2/day]"] == 1
    validate_tables(add_derived_labels(tables))


def test_values_units_sd_provenance(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    r = f.loc["row1:mouw_total_mass_flux"]
    assert (r.measurement_type, r.subfield, r.units_raw, r.value, r.sd) == ("C6", "total_mass_flux", "mg/m**2/day", 160.15, 2.0)
    assert f.loc["row1:mouw_poc_flux", "sd"] == 0.5 and pd.isna(f.loc["row1:mouw_pon_flux", "sd"])
    assert f.loc["row2:mouw_caco3_flux", "subfield"] == "biogenic_silica_carbonate_flux"
    assert f.loc["row1:mouw_psi_flux", "source_row_ref"] == "datasets/GO_flux.tab#data_row=1:mouw_psi_flux"
    assert "row7:mouw_poc_flux" not in f.index and not any(a.startswith("mouw_pal") for a in f.attribute)


def test_flags(built) -> None:
    f = built[0]["fact"].set_index("fact_key")
    fl = lambda k: f.loc[k, "flags"]  # noqa: E731
    assert fl("row0:mouw_poc_flux") == ["habitat_from_scope"]
    assert fl("row2:mouw_poc_flux") == ["habitat_from_scope", "reference_inferred", "zero_value"]
    assert fl("row2:mouw_psi_flux") == ["habitat_from_scope", "negative_value", "reference_inferred"]
    assert fl("row4:mouw_poc_flux") == ["habitat_from_scope", "reference_unresolved", "year_missing"]
    assert fl("row6:mouw_poc_flux") == ["habitat_from_scope", "pers_comm"]
    assert built[2]["flag_counts"]["habitat_from_scope"] == 12


def test_samples_locations(built) -> None:
    tables, _, _ = built
    s = tables["sample"].set_index("sample_key")
    assert s.loc["row0", "sample_depth_m"] == 3137.0 and (s.loc["row0", "sampling_year"], s.loc["row0", "sampling_month"]) == (1991, 7)
    assert s.loc["row0", "depth_notes"] == "duration_days=330.000; retrieved=2008-02-01T00:00:00"
    assert s.loc["row2", "depth_notes"].startswith("duration_days=0.000;")
    assert pd.isna(s.loc["row4", "sampling_year"]) and "row7" not in s.index
    loc = tables["location"].set_index("location_key")
    a = loc.loc["58.0000|179.0000|-3783.000"]
    assert a.water_depth_m == 3783.0 and a.latitude == 58.0 and a.longitude == 179.0  # sign flipped
    b = loc.loc["31.6130|-64.3140|"]
    assert pd.isna(b.water_depth_m) and (loc.habitat_type == "water column").all()
    lab = add_derived_labels(tables)["location"]
    assert (lab.habitat == "Water column").all() and lab.bottom_type.isna().all()


def test_references_citations(built) -> None:
    tables, _, rep = built
    assert set(tables["reference"].reference_key) == {"mouw_id:1", "mouw_id:2", "mouw_id:7", "mouw_id:14", "mouw_id:36"}
    c = tables["citation"].set_index("citation_key")
    assert c.loc["mouw_id:1|0", "doi"] == "10.1029/2006JC003706" and c.loc["mouw_id:1|0", "year"] == 2007
    assert c.loc["mouw_id:1|0", "url"] == "https://doi.org/10.1029/2006JC003706"
    assert c.loc["mouw_id:7|0", "bibliography_id"] == "Collier and Dymond, 1994, BCO-DMO"
    assert c.loc["mouw_id:7|1", "bibliography_id"] == "Honjo and Dymond, 1994, BCO-DMO" and c.loc["mouw_id:7|1", "year"] == 1994
    assert pd.isna(c.loc["mouw_id:7|0", "doi"])  # archive strings are never matched
    assert c.loc["mouw_id:14|0", "url"] == "http://bats.bios.edu" and "BATS" in c.loc["mouw_id:14|0", "bibliography_id"]
    assert not any(k.startswith("mouw_id:2|") for k in c.index)  # ID 2: reference_dim row, no citation
    assert pd.isna(c.loc["mouw_id:36|0", "doi"])
    assert rep["citations_with_doi"] == 1
    assert "Conte, 2015, pers. comm." in rep["unmatched_reference_strings"]


def test_methods(built) -> None:
    m = built[0]["method"].set_index("method_key")
    assert m.loc["mouw_poc_flux|sediment_trap|unknown", "technique"] == "sediment_trap; unknown"
    assert m.loc["mouw_poc_flux|th234|unknown", "technique"] == "th234; unknown"
    assert '"data_type": "th234"' in m.loc["mouw_poc_flux|th234|unknown", "params"]


# ---- failure modes


def _with(i: int, **kw) -> list[dict]:
    rows = [dict(r) for r in ROWS]
    rows[i].update(kw)
    return rows


@pytest.mark.parametrize(
    "i,kw,msg",
    [(0, {F_POC: "abc"}, "non-numeric"), (0, {F_POC: "<0.1"}, "non-numeric"), (0, {"Longitude": "190.0"}, "outside the valid range"),
     (0, {"Elevation [m a.s.l.] (Total water depth)": "10.0"}, "elevation"), (0, {"Type (Data type)": "net"}, "unknown data type"),
     (0, {"Date/Time (Deployed)": "1991-07-18"}, "deployment date"), (0, {"Type (Sediment trap type)": ""}, "blank trap type"),
     (1, {"Reference": "Another 2001"}, "two different reference texts"),
     (7, {"Reference": ""}, "two different reference texts"),
     (5, {"Reference": "Collier and Dymond, 1994, BCO-DMO"}, "two-paper string"),
     (2, {"Latitude": "31.7000"}, "single location"),
     (3, {"Date/Time (Deployed)": ""}, "malformed"),
     (1, {"Flux std dev [±]": "2.0", F_TOT: ""}, "std-dev cells without")],
)
def test_bad_input_raises(i, kw, msg) -> None:
    with pytest.raises(ContractError, match=msg):
        mw.build_tables(HEADER, _raw(_with(i, **kw)), ACCESS)


def test_unexpected_columns_raise() -> None:
    with pytest.raises(ContractError, match="unexpected columns"):
        mw.build_tables(HEADER, _raw().rename(columns={"Reference": "Ref"}), ACCESS)


def test_extra_blank_reference_id_raises() -> None:
    rows = _with(0, **{"ID (Reference identifier)": "9", "Reference": ""})
    rows[1]["ID (Reference identifier)"] = "9"
    rows[1]["Reference"] = ""
    rows[7]["ID (Reference identifier)"] = "9"
    rows[7]["Reference"] = ""
    with pytest.raises(ContractError, match="IDs without a Reference text"):
        mw.build_tables(HEADER, _raw(rows), ACCESS)


def test_doi_matching() -> None:
    hdr = mw.header_citations(HEADER)
    assert list(hdr) == [("lutz", "2007")]
    assert mw.match_doi("Lutz et al., 2007", hdr)["doi"] == "10.1029/2006JC003706"
    assert mw.match_doi("Lutz and Caldeira, 2007", hdr) is not None
    assert mw.match_doi("Lutz and Other, 2007", hdr) is None  # second author must agree
    assert mw.match_doi("Lutz et al. 2008", hdr) is None
    assert mw.match_doi("Lutz, 2007, BCO-DMO", hdr) is None and mw.match_doi("Lutz 2007, pers. comm.", hdr) is None


def test_read_tab(tmp_path) -> None:
    p = tmp_path / "t.tab"
    body = "\t".join(mw.HEADER_COLUMNS) + "\n" + "\t".join(ROWS[0][c] for c in mw.HEADER_COLUMNS) + "\n"
    p.write_text("\n".join(HEADER) + "\n*/\n" + body, encoding="utf-8")
    h, df = mw.read_tab(p)
    assert len(df) == 1 and h == HEADER and df.iloc[0][F_POC] == "5.5300"
    p.write_text("\n".join(HEADER) + "\n" + body, encoding="utf-8")
    with pytest.raises(ContractError, match="header end"):
        mw.read_tab(p)
