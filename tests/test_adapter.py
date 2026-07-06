"""Tests for build_scholarlm_adapter() (adapter.py).

Pure construction/mocking tests — no DB fixtures needed. scholarlm.DocumentLM
and scholarlm.MeasurementLM are patched so no real vLLM server is required.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from coastal_crawler.adapter import ScholarlmAdapter, build_scholarlm_adapter
from coastal_crawler.measurement_schema import (
    ATTRIBUTE_INFO_DICT,
    MEASUREMENT_EVENT_PROMPT,
    EntitySchema,
    MeasurementEventSchema,
)

_FAKE_SETTINGS = SimpleNamespace(
    doc_lm_model="test-ocr-model",
    doc_lm_base_url="http://localhost:8083/v1",
    doc_lm_api_key="EMPTY",
    meas_lm_model="test-extraction-model",
    meas_lm_base_url="http://localhost:8084/v1",
    meas_lm_api_key="EMPTY",
    meas_lm_entity_identification_prompt="Identify coastal sites.",
    extraction_schema_name="coastal_measurement_v1",
    extraction_model_version=None,
    extraction_lat_field=None,
    extraction_lon_field=None,
)


def _fake_settings(**overrides: Any) -> SimpleNamespace:
    return SimpleNamespace(**{**_FAKE_SETTINGS.__dict__, **overrides})


class TestBuildScholarlmAdapterGuards:
    def test_raises_on_missing_doc_lm_model(self) -> None:
        settings = _fake_settings(doc_lm_model=None)
        with pytest.raises(RuntimeError, match="DOC_LM_MODEL"):
            build_scholarlm_adapter(settings)

    def test_raises_on_missing_meas_lm_model(self) -> None:
        settings = _fake_settings(meas_lm_model=None)
        with pytest.raises(RuntimeError, match="MEAS_LM_MODEL"):
            build_scholarlm_adapter(settings)

    def test_raises_on_missing_entity_identification_prompt(self) -> None:
        settings = _fake_settings(meas_lm_entity_identification_prompt=None)
        with pytest.raises(RuntimeError, match="MEAS_LM_ENTITY_IDENTIFICATION_PROMPT"):
            build_scholarlm_adapter(settings)

    def test_raises_lists_all_missing(self) -> None:
        settings = _fake_settings(doc_lm_model=None, meas_lm_model=None)
        with pytest.raises(RuntimeError, match="DOC_LM_MODEL, MEAS_LM_MODEL"):
            build_scholarlm_adapter(settings)


class TestBuildScholarlmAdapterConstruction:
    def test_constructs_doc_lm_with_settings(self, mocker: Any) -> None:
        doc_lm_cls = mocker.patch("scholarlm.DocumentLM")
        mocker.patch("scholarlm.MeasurementLM")

        build_scholarlm_adapter(_fake_settings())

        doc_lm_cls.assert_called_once_with(
            model_name="test-ocr-model",
            api_base="http://localhost:8083/v1",
            api_key="EMPTY",
        )

    def test_constructs_meas_lm_with_settings_and_clean_tables_false(self, mocker: Any) -> None:
        mocker.patch("scholarlm.DocumentLM")
        meas_lm_cls = mocker.patch("scholarlm.MeasurementLM")

        build_scholarlm_adapter(_fake_settings())

        meas_lm_cls.assert_called_once_with(
            model_name="test-extraction-model",
            entity_identification_prompt="Identify coastal sites.",
            entity_identification_schema=EntitySchema,
            attribute_info_dict=ATTRIBUTE_INFO_DICT,
            measurement_event_schema=MeasurementEventSchema,
            measurement_event_prompt=MEASUREMENT_EVENT_PROMPT,
            api_base="http://localhost:8084/v1",
            api_key="EMPTY",
            clean_tables=False,
        )

    def test_returns_scholarlm_adapter_with_schema_and_version(self, mocker: Any) -> None:
        doc_lm_sentinel = mocker.sentinel.doc_lm
        meas_lm_sentinel = mocker.sentinel.meas_lm
        mocker.patch("scholarlm.DocumentLM", return_value=doc_lm_sentinel)
        mocker.patch("scholarlm.MeasurementLM", return_value=meas_lm_sentinel)

        adapter = build_scholarlm_adapter(_fake_settings())

        assert isinstance(adapter, ScholarlmAdapter)
        assert adapter.doc_lm is doc_lm_sentinel
        assert adapter.meas_lm is meas_lm_sentinel
        assert adapter.schema_name == "coastal_measurement_v1"
        assert adapter.model_version == "doc_lm=test-ocr-model+meas_lm=test-extraction-model"
        assert adapter.lat_field is None
        assert adapter.lon_field is None

    def test_explicit_model_version_overrides_derived_default(self, mocker: Any) -> None:
        mocker.patch("scholarlm.DocumentLM")
        mocker.patch("scholarlm.MeasurementLM")

        adapter = build_scholarlm_adapter(_fake_settings(extraction_model_version="v2"))

        assert adapter.model_version == "v2"

    def test_lat_lon_fields_passed_through(self, mocker: Any) -> None:
        mocker.patch("scholarlm.DocumentLM")
        mocker.patch("scholarlm.MeasurementLM")

        adapter = build_scholarlm_adapter(
            _fake_settings(extraction_lat_field="latitude", extraction_lon_field="longitude")
        )

        assert adapter.lat_field == "latitude"
        assert adapter.lon_field == "longitude"
