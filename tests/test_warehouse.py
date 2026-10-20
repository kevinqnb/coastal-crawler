"""Tests for coastal_crawler.warehouse — pure functions, no DB required.

Same hand-built-fixture-by-inspection shape as test_resolve_locations.py
(whose entity-resolution algorithm this module ports). See
notes/coastal-crawler/builds/2026-08-12-warehouse-init-01.md for the design
this implements.
"""

from __future__ import annotations

import pytest

from coastal_crawler.measurement_schema import CANONICAL_UNITS, convert_to_canonical
from coastal_crawler.warehouse import (
    dedupe_exact_fact_rows,
    extract_parsed_quantity,
    majority_value,
    resolve_entities,
)


_ALL_PARSED = {
    "qualifiers": ["IsMean"], "point_value": 1.5, "lower": "1", "upper": 2,
    "list_values": None, "tolerance": "± 0.5", "standard_deviation": None,
}


class TestExtractParsedQuantity:
    def test_no_parsed_fields_returns_none(self) -> None:
        assert extract_parsed_quantity({"attribute": "salinity", "value": "5"}) is None

    def test_all_fields_carried_through_as_text(self) -> None:
        q = extract_parsed_quantity({**_ALL_PARSED, "list_values": ["a", 2]})
        assert q is not None
        assert q.qualifiers == ["IsMean"]
        assert (q.point_value, q.lower, q.upper) == ("1.5", "1", "2")
        assert q.list_values == ["a", "2"]
        assert q.tolerance == "± 0.5"
        assert q.standard_deviation is None

    def test_empty_qualifiers_and_zero_are_not_dropped(self) -> None:
        q = extract_parsed_quantity({**_ALL_PARSED, "qualifiers": [], "point_value": 0})
        assert q is not None
        assert q.qualifiers == []
        assert q.point_value == "0"

    @pytest.mark.parametrize("missing", sorted(_ALL_PARSED))
    def test_partial_fields_raise(self, missing: str) -> None:
        data = {k: v for k, v in _ALL_PARSED.items() if k != missing}
        with pytest.raises(ValueError, match="partial parsed-quantity fields"):
            extract_parsed_quantity(data)

    @pytest.mark.parametrize(
        "override",
        [{"qualifiers": "IsMean"}, {"qualifiers": [1]}, {"list_values": "a"},
         {"list_values": [None]}, {"point_value": [1]}, {"lower": True}],
    )
    def test_wrong_types_raise(self, override: dict) -> None:
        with pytest.raises(TypeError):
            extract_parsed_quantity({**_ALL_PARSED, **override})


class TestMajorityValue:
    def test_clear_majority_wins(self) -> None:
        assert majority_value(["estuary", "estuary", "salt marsh"]) == "estuary"

    def test_tie_broken_ascending(self) -> None:
        assert majority_value(["salt marsh", "estuary"]) == "estuary"

    def test_all_null_returns_none(self) -> None:
        assert majority_value([None, None]) is None

    def test_nulls_ignored_when_counting(self) -> None:
        assert majority_value([None, "estuary", None]) == "estuary"


class TestUnitConversionKnown:
    def test_exact_canonical_match_is_identity(self) -> None:
        assert convert_to_canonical("nitrate", "µmol/L") == ("µmol/L", 1.0)

    def test_si_prefix_scaling_millimolar_to_micromolar(self) -> None:
        canonical, factor = convert_to_canonical("dissolved_inorganic_carbon", "mmol/kg")
        assert canonical == "µmol/kg"
        assert factor == pytest.approx(1000.0)

    def test_si_prefix_scaling_nanomolar_to_micromolar(self) -> None:
        canonical, factor = convert_to_canonical("nitrate", "nmol L⁻¹")
        assert canonical == "µmol/L"
        assert factor == pytest.approx(0.001)

    def test_unicode_and_ascii_spelling_variants_agree(self) -> None:
        a = convert_to_canonical("dissolved_inorganic_carbon", "µmol kg⁻¹")
        b = convert_to_canonical("dissolved_inorganic_carbon", "μmol/kg")
        c = convert_to_canonical("dissolved_inorganic_carbon", "umol/kg")
        assert a == b == c == ("µmol/kg", 1.0)

    def test_salinity_ppt_and_gkg_equivalent_to_psu(self) -> None:
        assert convert_to_canonical("salinity", "ppt") == ("psu", 1.0)
        assert convert_to_canonical("salinity", "g/kg") == ("psu", 1.0)
        assert convert_to_canonical("salinity", "‰") == ("psu", 1.0)

    def test_exponent_form_and_slash_form_agree(self) -> None:
        a = convert_to_canonical("light_attenuation", "m⁻¹")
        b = convert_to_canonical("light_attenuation", "per meter")
        assert a == b == ("/m", 1.0)


class TestUnitConversionUnknown:
    def test_unknown_attribute_returns_none(self) -> None:
        assert convert_to_canonical("not_a_real_attribute", "mg/L") is None

    def test_null_units_returns_none(self) -> None:
        assert convert_to_canonical("salinity", None) is None

    def test_molar_to_mass_conversion_not_attempted(self) -> None:
        """µmol/L -> mg/L needs a molar mass — Tier 3, deliberately
        unconverted rather than guessed at (see build note)."""
        assert convert_to_canonical("dissolved_organic_carbon", "µmol/L") is None

    def test_percent_saturation_not_attempted(self) -> None:
        """Needs temperature+salinity — Tier 3."""
        assert convert_to_canonical("dissolved_oxygen", "% saturation") is None

    def test_conductivity_not_attempted(self) -> None:
        """mS/cm -> psu needs the non-linear PSS-78 algorithm — Tier 3."""
        assert convert_to_canonical("salinity", "mS cm-1") is None

    def test_every_attribute_info_dict_entry_has_a_canonical_unit(self) -> None:
        from coastal_crawler.measurement_schema import ATTRIBUTE_INFO_DICT

        for attribute in ATTRIBUTE_INFO_DICT:
            assert attribute in CANONICAL_UNITS


class TestResolveEntitiesCoordinateClustering:
    def test_identical_coordinates_merge(self) -> None:
        rows = [(1, "Site A", 10.0, 20.0), (2, "Site A", 10.0, 20.0)]
        result = resolve_entities(rows, distance_threshold_km=1.0, name_similarity_threshold=0.85)
        assert result[1].entity_id == result[2].entity_id
        assert result[1].resolution_method == "coordinate"
        assert result[1].latitude == 10.0
        assert result[1].longitude == 20.0

    def test_within_threshold_merges_outside_does_not(self) -> None:
        rows = [
            (1, "A", 10.000, 20.0),   # ~0.56km from row 2
            (2, "A", 10.005, 20.0),
            (3, "A", 10.5, 20.0),     # ~55km away
        ]
        result = resolve_entities(rows, distance_threshold_km=1.0, name_similarity_threshold=0.85)
        assert result[1].entity_id == result[2].entity_id
        assert result[1].entity_id != result[3].entity_id

    def test_centroid_is_mean_of_distinct_points_not_rows(self) -> None:
        rows = [
            (1, "A", 10.000, 20.000),
            (2, "A", 10.000, 20.000),
            (3, "A", 10.002, 20.000),
        ]
        result = resolve_entities(rows, distance_threshold_km=1.0, name_similarity_threshold=0.85)
        assert result[1].latitude == pytest.approx(10.001)
        assert result[1].latitude != pytest.approx(10.000667, abs=1e-6)


class TestResolveEntitiesNameMatching:
    def test_matching_normalized_names_merge(self) -> None:
        rows = [(1, "Cedar Marsh", None, None), (2, "  CEDAR   marsh!! ", None, None)]
        result = resolve_entities(rows, distance_threshold_km=1.0, name_similarity_threshold=0.85)
        assert result[1].entity_id == result[2].entity_id
        assert result[1].resolution_method == "name"

    def test_fuzzy_match_merges_differently_normalized_names(self) -> None:
        rows = [(1, "Cedar Marsh Site", None, None), (2, "Cedar Marsh Sites", None, None)]
        result = resolve_entities(rows, distance_threshold_km=1.0, name_similarity_threshold=0.85)
        assert result[1].entity_id == result[2].entity_id

    def test_dissimilar_names_do_not_merge(self) -> None:
        rows = [(1, "Cedar Marsh", None, None), (2, "Pacific Ocean Deep Trench", None, None)]
        result = resolve_entities(rows, distance_threshold_km=1.0, name_similarity_threshold=0.85)
        assert result[1].entity_id != result[2].entity_id

    def test_coordinateless_row_never_merges_into_coordinate_entity(self) -> None:
        rows = [(1, "Cedar Marsh", 10.0, 20.0), (2, "Cedar Marsh", None, None)]
        result = resolve_entities(rows, distance_threshold_km=1.0, name_similarity_threshold=0.85)
        assert result[1].entity_id != result[2].entity_id
        assert result[1].resolution_method == "coordinate"
        assert result[2].resolution_method == "name"

    def test_partial_coordinate_pair_treated_as_no_coordinates(self) -> None:
        rows = [(1, "Cedar Marsh", 10.0, None), (2, "Cedar Marsh", None, -70.0)]
        result = resolve_entities(rows, distance_threshold_km=1.0, name_similarity_threshold=0.85)
        assert result[1].entity_id == result[2].entity_id
        assert result[1].resolution_method == "name"


class TestResolveEntitiesUnresolvedSingleton:
    def test_no_name_no_coords_becomes_own_unresolved_entity(self) -> None:
        rows = [(1, None, None, None), (2, None, None, None)]
        result = resolve_entities(rows, distance_threshold_km=1.0, name_similarity_threshold=0.85)
        assert result[1].entity_id != result[2].entity_id
        assert result[1].resolution_method == "unresolved"
        assert result[2].resolution_method == "unresolved"

    def test_every_row_gets_an_entity(self) -> None:
        rows = [(1, "A", 1.0, 1.0), (2, None, None, None), (3, "B", None, None)]
        result = resolve_entities(rows, distance_threshold_km=1.0, name_similarity_threshold=0.85)
        assert set(result.keys()) == {1, 2, 3}


def _fact_row(extraction_id: int, **overrides: object) -> dict:
    row = {
        "extraction_id": extraction_id,
        "paper_id": 1,
        "model_id": 1,
        "entity_id": 1,
        "event_id": 1,
        "attribute": "salinity",
        "quantity_raw": "28.4",
        "units_raw": "psu",
        "qualifiers": ["IsMean"],
        "point_value": "28.4",
        "lower": None,
        "upper": None,
        "list_values": ["1", "2"],
        "tolerance": None,
        "standard_deviation": None,
        "page_number": 0,
        "confidence": None,
    }
    row.update(overrides)
    return row


class TestDedupeExactFactRows:
    def test_identical_rows_collapse_keeping_highest_extraction_id(self) -> None:
        rows = [_fact_row(10), _fact_row(11)]
        kept, dropped = dedupe_exact_fact_rows(rows)
        assert [r["extraction_id"] for r in kept] == [11]
        assert [r["extraction_id"] for r in dropped] == [10]

    def test_rows_differing_only_by_a_parsed_field_are_not_duplicates(self) -> None:
        rows = [_fact_row(10, qualifiers=["IsMean"]), _fact_row(11, qualifiers=["IsMedian"]),
                _fact_row(12, list_values=None)]
        kept, dropped = dedupe_exact_fact_rows(rows)
        assert {r["extraction_id"] for r in kept} == {10, 11, 12}
        assert dropped == []

    def test_rows_differing_only_by_entity_id_are_not_duplicates(self) -> None:
        rows = [_fact_row(10, entity_id=1), _fact_row(11, entity_id=2)]
        kept, dropped = dedupe_exact_fact_rows(rows)
        assert {r["extraction_id"] for r in kept} == {10, 11}
        assert dropped == []

    def test_rows_differing_only_by_event_id_are_not_duplicates(self) -> None:
        rows = [_fact_row(10, event_id=1), _fact_row(11, event_id=2)]
        kept, dropped = dedupe_exact_fact_rows(rows)
        assert {r["extraction_id"] for r in kept} == {10, 11}
        assert dropped == []

    def test_no_duplicates_returns_all_rows_kept(self) -> None:
        rows = [_fact_row(10, attribute="salinity"), _fact_row(11, attribute="nitrate")]
        kept, dropped = dedupe_exact_fact_rows(rows)
        assert len(kept) == 2
        assert dropped == []

    def test_three_way_duplicate_keeps_only_the_highest_id(self) -> None:
        rows = [_fact_row(5), _fact_row(20), _fact_row(12)]
        kept, dropped = dedupe_exact_fact_rows(rows)
        assert [r["extraction_id"] for r in kept] == [20]
        assert {r["extraction_id"] for r in dropped} == {5, 12}
