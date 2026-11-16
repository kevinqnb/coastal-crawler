"""Unit tests (rung 1) for the compilations contract/builder and the CCN extract.py.

Hand-built fixture whose expected output is stated by inspection in comments.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from coastal_crawler.compilations import vocab
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
    Path(__file__).resolve().parents[1]
    / "data" / "compilations" / "smithsonian-coastal-carbon" / "extract.py"
)
_spec = importlib.util.spec_from_file_location("ccn_extract", _EXTRACT)
assert _spec and _spec.loader
ccn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ccn)

COLS = {
    "cores": "study_id site_id core_id year month latitude longitude position_accuracy position_method elevation elevation_datum elevation_method salinity_class vegetation_class inundation_class country admin_division habitat pb210_cic_accretion_rate pb210_cic_accretion_rate_se pb210_cic_max_depth pb210_cic_r2 pb210_cic_notes",
    "sites": "study_id site_id salinity_class vegetation_class inundation_class",
    "impacts": "study_id site_id core_id impact_class",
    "species": "study_id site_id core_id species_code",
    "depthseries": "study_id site_id core_id method_id depth_min depth_max representative_depth_min representative_depth_max sample_id dry_bulk_density fraction_organic_matter fraction_carbon depth_interval_notes",
    "methods": "study_id method_id coring_method roots_flag sediment_sieved_flag sediment_sieve_size compaction_flag dry_bulk_density_temperature dry_bulk_density_time dry_bulk_density_sample_volume dry_bulk_density_sample_mass dry_bulk_density_flag loss_on_ignition_temperature loss_on_ignition_time loss_on_ignition_sample_volume loss_on_ignition_sample_mass loss_on_ignition_flag carbon_measured_or_modeled carbonates_removed carbonate_removal_method fraction_carbon_method fraction_carbon_type carbon_profile_notes",
    "study_citations": "study_id bibliography_id publication_type doi title author year url",
}


def _df(table: str, rows: list[dict]) -> pd.DataFrame:
    cols = COLS[table].split()
    for r in rows:
        assert set(r) <= set(cols), set(r) - set(cols)
    return pd.DataFrame([{c: r.get(c) for c in cols} for r in rows], columns=cols, dtype=object)


def _raw() -> dict[str, pd.DataFrame]:
    cores = [
        dict(study_id="A", site_id="S1", core_id="C1", year="2020", month="5", latitude="43.0", longitude="-70.0",
             habitat="marsh", inundation_class="low", vegetation_class="emergent", country="US", admin_division="ME",
             pb210_cic_accretion_rate="0.5", pb210_cic_accretion_rate_se="2.0", pb210_cic_max_depth="30",
             pb210_cic_r2="0.9", pb210_cic_notes="CIC fit"),
        dict(study_id="A", site_id="S1", core_id="C2", year="2021", latitude="43.1", longitude="-70.1", habitat="marsh"),
        dict(study_id="B", site_id="S2", core_id="C1", latitude="9.0", longitude="-80.0", habitat="mangrove",
             vegetation_class="forested", salinity_class="saline", elevation="0.5", elevation_datum="NAVD88",
             pb210_cic_accretion_rate="0.2"),
    ]
    sites = [  # (A,S1) repeated: salinity agrees, vegetation disagrees, inundation has a null
        dict(study_id="A", site_id="S1", salinity_class="estuarine", vegetation_class="emergent", inundation_class="high"),
        dict(study_id="A", site_id="S1", salinity_class="estuarine", vegetation_class="seagrass"),
    ]
    impacts = [
        dict(study_id="A", site_id="S1", impact_class="ditched"),  # site level
        dict(study_id="A", site_id="S1", core_id="C2", impact_class="restored"),  # core level
        dict(study_id="A", site_id="S1", core_id="C1", impact_class=None),  # blank: must NOT become "nan"
    ]
    species = [dict(study_id="A", site_id="S1", core_id="C1", species_code="Spartina patens")] * 2
    r0 = dict(study_id="A", site_id="S1", core_id="C1", method_id="M1", depth_min="0", depth_max="2",
              dry_bulk_density="0.5", fraction_organic_matter="0.2")
    ds = [
        r0,  # 0: 2 facts
        dict(r0, depth_min="2", depth_max="4", dry_bulk_density="-1.0", fraction_organic_matter=None),  # 1: negative
        dict(r0),  # 2: exact duplicate of 0 -> 2 facts flagged duplicate_source_row
        dict(study_id="A", site_id="S1", core_id="C2", method_id="Mreg", depth_min="0", depth_max="5",
             dry_bulk_density="5.0", fraction_carbon="0.3"),  # 3: dbd > max; modeled carbon
        dict(study_id="B", site_id="S2", core_id="C1", dry_bulk_density="0.9", fraction_organic_matter="1.5"),  # 4: no method, no depth
        dict(study_id="A", site_id="s1", core_id="C1", method_id="M1", depth_min="0", depth_max="2",
             dry_bulk_density="0.4"),  # 5: ORPHAN (site case differs)
        dict(study_id="A", site_id="S1", core_id="C2", method_id="M3", depth_min="6", depth_max="5",
             fraction_carbon="0.1"),  # 6: inverted depth; ambiguous method
        dict(study_id="A", site_id="S1", core_id="C2", method_id="Mreg", depth_min="5", depth_max="6"),  # 7: no measurements
    ]
    methods = [
        dict(study_id="A", method_id="M1", coring_method="gouge auger", dry_bulk_density_temperature="105",
             dry_bulk_density_time="48", dry_bulk_density_flag="oven dried", loss_on_ignition_temperature="550"),
        dict(study_id="A", method_id="Mreg", coring_method="push core", fraction_carbon_method="local regression",
             carbon_measured_or_modeled="modeled", fraction_carbon_type="organic carbon"),
        dict(study_id="A", method_id="M3", coring_method="gouge auger"),
        dict(study_id="A", method_id="M3", coring_method="vibracore"),  # conflicting duplicate
        dict(study_id="A", method_id="M3", coring_method="vibracore"),  # exact duplicate (collapses)
    ]
    cites = [
        dict(study_id="A", bibliography_id="A_data", publication_type="primary dataset", doi="10.1/a", year="2020"),
        dict(study_id="A", bibliography_id="A_art", publication_type="primary source", doi="10.1/b", year="2021"),
        dict(study_id="B", bibliography_id="B_data", publication_type="primary dataset", doi="10.1/c", year="2019"),
    ]
    return {
        "cores": _df("cores", cores), "sites": _df("sites", sites), "impacts": _df("impacts", impacts),
        "species": _df("species", species), "depthseries": _df("depthseries", ds),
        "methods": _df("methods", methods), "study_citations": _df("study_citations", cites),
    }


UNITS = {
    "cores": {"position_accuracy": "meter", "elevation": "meter",
              "pb210_cic_accretion_rate": "centimeterPerYear",
              "pb210_cic_accretion_rate_se": "millimetersPerYear",
              "pb210_cic_max_depth": "centimeter", "pb210_cic_r2": "dimensionless"},
    "depthseries": {"depth_min": "centimeter", "depth_max": "centimeter",
                    "dry_bulk_density": "gramsPerCubicCentimeter",
                    "fraction_organic_matter": "dimensionless", "fraction_carbon": "dimensionless"},
    "methods": dict(ccn.EXPECTED_METHOD_UNITS),
}


@pytest.fixture()
def built():
    return ccn.build_tables(_raw(), UNITS, "2026-11-15", max_dry_bulk_density=3.0)


class TestCcnExtract:
    def test_counts(self, built) -> None:
        tables, skips, rep = built
        # depth-series facts: row0 2, row1 1, row2 2, row3 2, row4 2, row6 1 = 10 (row 5 orphan,
        # row 7 empty) + 2 core-level accretion facts (cores A|S1|C1 and B|S2|C1) = 12
        assert len(tables["fact"]) == 12 and rep["accretion_facts"] == 2
        assert rep["facts_excluded"] == 1 and len(skips) == 1
        assert len(tables["sample"]) == 8
        assert len(tables["location"]) == 3
        assert rep["depthseries_rows_without_measurements"] == 1
        assert len(tables["method"]) == 9

    def test_orphan_reported_with_unapplied_suggestion(self, built) -> None:
        _, skips, _ = built
        assert skips[0]["reason"] == "depthseries_core_not_in_cores"
        assert skips[0]["suggested_location_keys_not_applied"] == ["A|S1|C1"]
        assert skips[0]["depthseries_row"] == 5

    def test_flags(self, built) -> None:
        f = built[0]["fact"].set_index("source_row_ref")["flags"]
        g = lambda row, a: sorted(f[f"CCN_depthseries.csv#data_row={row}:{a}"])
        assert g(0, "dry_bulk_density") == []
        assert g(1, "dry_bulk_density") == ["negative_value"]
        assert g(2, "dry_bulk_density") == ["duplicate_source_row"]
        assert g(2, "fraction_organic_matter") == ["duplicate_source_row"]
        assert g(3, "dry_bulk_density") == ["dbd_gt_max"]
        assert g(3, "fraction_carbon") == []  # matched, carbon type known
        assert g(4, "dry_bulk_density") == ["depth_missing"]
        assert g(4, "fraction_organic_matter") == ["depth_missing", "fraction_gt_1"]
        assert g(6, "fraction_carbon") == ["carbon_type_unknown", "depth_inverted"]

    def test_measurement_types_and_subfields(self, built) -> None:
        f = built[0]["fact"].set_index("source_row_ref")
        g = lambda row, a: f.loc[f"CCN_depthseries.csv#data_row={row}:{a}"]
        assert (g(0, "dry_bulk_density").measurement_type, g(0, "dry_bulk_density").subfield) == ("C1", "dry_bulk_density")
        assert g(0, "fraction_organic_matter").subfield == "loi_organic_matter"
        assert g(3, "fraction_carbon").subfield == "organic_carbon"  # matched method, organic carbon
        assert g(6, "fraction_carbon").subfield == "carbon_unspecified"  # ambiguous method: unknown type

    def test_accretion_facts(self, built) -> None:
        t = built[0]
        f = t["fact"].set_index("source_row_ref")
        a = f.loc["CCN_cores.csv#data_row=0:pb210_cic_accretion_rate"]
        # value in the doc's cm/yr, se in the doc's (different) mm/yr: carried, flagged, not converted
        assert (a.value, a.se, a.units_raw) == (0.5, 2.0, "centimeterPerYear")
        assert (a.measurement_type, a.subfield, a["flags"]) == ("G2", "vertical_accretion", ["se_units_differ"])
        b = f.loc["CCN_cores.csv#data_row=2:pb210_cic_accretion_rate"]
        assert b.value == 0.2 and pd.isna(b.se) and b["flags"] == []
        s = t["sample"].set_index("sample_key").loc["A|S1|C1|core_level"]
        assert pd.isna(s.depth_min_cm) and s.sampling_month == 5 and s.marsh_zone == "low"
        m = t["method"].set_index("method_key").loc["A|S1|C1|pb210_cic_accretion_rate"]
        assert m.method_status == "method_id_null" and "210Pb" in m.technique
        assert '"max_depth_cm": 30.0' in m.params and '"se_units": "millimetersPerYear"' in m.params

    def test_labels(self, built) -> None:
        loc = add_derived_labels(built[0])["location"].set_index("location_key")
        c1, b1 = loc.loc["A|S1|C1"], loc.loc["B|S2|C1"]
        assert (c1.habitat, c1.bottom_type, c1.setting) == ("Tidal marsh", "soft", "estuary")
        assert pd.isna(c1.salinity_zone)  # 'estuarine' is a setting, not a salinity zone
        assert (b1.habitat, b1.salinity_zone) == ("Mangrove forest", None) or pd.isna(b1.salinity_zone)
        # ditched -> no standard class; restored -> restored
        assert c1.modification_class == [] and loc.loc["A|S1|C2"].modification_class == ["restored"]

    def test_values_carried_exactly(self, built) -> None:
        fact = built[0]["fact"]
        v = fact[fact.source_row_ref == "CCN_depthseries.csv#data_row=1:dry_bulk_density"].value.iloc[0]
        assert v == -1.0
        assert set(fact.units_raw) == {"gramsPerCubicCentimeter", "dimensionless", "centimeterPerYear"}

    def test_label_levels_and_derived(self, built) -> None:
        loc = built[0]["location"].set_index("location_key")
        c1, c2 = loc.loc["A|S1|C1"], loc.loc["A|S1|C2"]
        assert (c1.salinity_class, c1.salinity_class_level) == ("estuarine", "site")
        assert (c1.vegetation_class, c1.vegetation_class_level) == ("emergent", "core")
        assert (c1.tidal_position, c1.tidal_position_level) == ("low", "core")
        assert pd.isna(c2.vegetation_class)  # site rows disagree -> not used
        assert pd.isna(c2.tidal_position)  # a null among site rows -> not used
        assert c1.modification_status == ["ditched"]  # the blank impact_class row adds nothing
        assert c2.modification_status == ["ditched", "restored"]
        assert c1.dominant_species == ["Spartina patens"]
        samp = built[0]["sample"].set_index("sample_key")
        assert samp.loc["A|S1|C1|row0"].marsh_zone == "low"
        assert samp.loc["A|S1|C1|row0"].vegetated_or_bare == "vegetated"
        assert pd.isna(samp.loc["A|S1|C2|row3"].marsh_zone)
        assert samp.loc["A|S1|C1|row0"].sampling_month == 5

    def test_methods(self, built) -> None:
        m = built[0]["method"].set_index("method_key")
        assert m.loc["A|M1|dry_bulk_density"].technique == "oven dried"
        assert '"temperature_celsius": 105.0' in m.loc["A|M1|dry_bulk_density"].params
        assert m.loc["A|M1|fraction_organic_matter"].technique == "loss on ignition"
        reg = m.loc["A|Mreg|fraction_carbon"]
        assert (reg.carbon_type, reg.carbon_measured_or_modeled, reg.conversion_applied) == (
            "organic carbon", "modeled", "local regression")
        assert m.loc["A|M3|fraction_carbon"].method_status == "ambiguous_duplicate_method_id"
        assert pd.isna(m.loc["A|M3|fraction_carbon"].coring_method)
        assert m.loc["B||dry_bulk_density"].method_status == "method_id_null"

    def test_unknown_vegetation_class_raises(self) -> None:
        raw = _raw()
        raw["cores"].loc[0, "vegetation_class"] = "weird"
        with pytest.raises(ContractError, match="vegetation_class"):
            ccn.build_tables(raw, UNITS, "2026-11-15", 3.0)

    def test_wrong_doc_units_raise(self) -> None:
        bad = {**UNITS, "methods": {**UNITS["methods"], "dry_bulk_density_time": "minute"}}
        with pytest.raises(ContractError, match="doc units"):
            ccn.build_tables(_raw(), bad, "2026-11-15", 3.0)

    def test_value_is_correctly_rounded_float(self) -> None:
        # pd.to_numeric parses this 1 ULP off; the extract must equal float() exactly
        raw = _raw()
        raw["depthseries"].loc[0, "dry_bulk_density"] = "0.25163857705000003"
        fact = ccn.build_tables(raw, UNITS, "2026-11-15", 3.0)[0]["fact"]
        v = fact[fact.source_row_ref == "CCN_depthseries.csv#data_row=0:dry_bulk_density"].value.iloc[0]
        assert v == float("0.25163857705000003")

    def test_non_numeric_value_raises(self) -> None:
        raw = _raw()
        raw["depthseries"].loc[0, "dry_bulk_density"] = "abc"
        with pytest.raises(ContractError, match="non-numeric"):
            ccn.build_tables(raw, UNITS, "2026-11-15", 3.0)


class TestContract:
    def test_roundtrip_and_validate(self, built, tmp_path) -> None:
        write_extracted(built[0], tmp_path)
        back = read_extracted(tmp_path)
        assert {k: len(v) for k, v in back.items()} == {k: len(v) for k, v in built[0].items()}
        a = back["fact"].set_index("fact_key")["flags"]
        b = built[0]["fact"].set_index("fact_key")["flags"]
        assert {k: sorted(v) for k, v in a.items()} == {k: sorted(v) for k, v in b.items()}

    def test_fk_violation_raises(self, built) -> None:
        t = {k: v.copy() for k, v in add_derived_labels(built[0]).items()}
        t["fact"].loc[0, "location_key"] = "nope"
        with pytest.raises(ContractError, match="FK"):
            validate_tables(t)

    def test_duplicate_natural_key_raises(self, built) -> None:
        t = {k: v.copy() for k, v in add_derived_labels(built[0]).items()}
        t["sample"] = pd.concat([t["sample"], t["sample"].iloc[[0]]])
        with pytest.raises(ContractError, match="natural key"):
            validate_tables(t)

    def test_null_required_raises(self, built) -> None:
        t = {k: v.copy() for k, v in add_derived_labels(built[0]).items()}
        t["fact"].loc[0, "value"] = float("nan")
        with pytest.raises(ContractError, match="required"):
            validate_tables(t)


def _second_source(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out = {k: v.copy() for k, v in tables.items()}
    for v in out.values():
        v["source_key"] = "zzz-other"
    out["source"]["name"] = "Other"
    return out


class TestBuilder:
    def test_global_keys_dense_sorted_and_fk_resolved(self, built) -> None:
        # pass sources in reverse alphabetical order: ids must still follow sorted source_key
        d = add_derived_labels(built[0])
        g = assign_global_keys([_second_source(d), d])
        assert list(g["source"].source_key) == [ccn.SOURCE_KEY, "zzz-other"]
        f = g["fact"]
        assert list(f.measurement_id) == list(range(1, 25))
        assert len(f) == 24
        assert not f[["source_id", "reference_id", "location_id", "sample_id", "method_id"]].isna().any().any()
        # each fact's location id resolves to the same-source location with the same key
        loc = g["location"].set_index("location_id")
        assert (loc.loc[f.location_id, "source_id"].to_numpy() == f.source_id.to_numpy()).all()
        assert (loc.loc[f.location_id, "location_key"].to_numpy() == f.location_key.to_numpy()).all()

    def test_end_to_end_duckdb_and_determinism(self, built, tmp_path, monkeypatch) -> None:
        real = vocab.load_label_maps()
        cloned = {("zzz-other", rc, mc): m for (sk, rc, mc), m in real.items() if sk == ccn.SOURCE_KEY}
        monkeypatch.setattr(vocab, "load_label_maps", lambda: {**real, **cloned})
        d1, d2 = tmp_path / "e1", tmp_path / "e2"
        write_extracted(built[0], d1)
        write_extracted(_second_source(built[0]), d2)
        counts = build_compilations([d1, d2], tmp_path / "c.duckdb")
        assert counts["measurement_fact"] == 24 and counts["source_dim"] == 2
        build_compilations([d2, d1], tmp_path / "c2.duckdb")  # input order must not matter

        def dump(p: Path) -> dict[str, list]:
            con = duckdb.connect(str(p), read_only=True)
            out = {}
            for t in ("source_dim", "reference_dim", "citation_dim", "location_dim",
                      "sample_dim", "method_dim", "measurement_fact"):
                out[t] = con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
            con.close()
            return out

        assert dump(tmp_path / "c.duckdb") == dump(tmp_path / "c2.duckdb")
        con = duckdb.connect(str(tmp_path / "c.duckdb"), read_only=True)
        # join does not drop or duplicate facts
        n = con.execute("""SELECT count(*) FROM measurement_fact f
            JOIN location_dim l USING (location_id) JOIN sample_dim s USING (sample_id)
            JOIN method_dim m USING (method_id) JOIN reference_dim r USING (reference_id)
            JOIN source_dim o ON o.source_id = f.source_id""").fetchone()
        assert n is not None and n[0] == 24
        assert con.execute("SELECT count(*) FROM measurement_fact WHERE list_contains(flags,'negative_value')").fetchone()[0] == 2
        assert con.execute("SELECT count(*) FROM measurement_fact WHERE measurement_type = 'G2'").fetchone()[0] == 4
        con.close()
