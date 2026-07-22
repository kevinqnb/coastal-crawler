"""Tests for build_extraction_adapter() (adapter.py).

Pure construction/mocking tests — no DB fixtures needed. OCRLM and
ExtractionLM are patched so no real vLLM server is required.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from coastal_crawler.adapter import DirectExtractionAdapter, build_extraction_adapter
from coastal_crawler.measurement_schema import DirectExtractionSchema, build_direct_extraction_prompt

_FAKE_SETTINGS = SimpleNamespace(
    doc_lm_model="test-ocr-model",
    doc_lm_base_url="http://localhost:8083/v1",
    doc_lm_api_key="EMPTY",
    doc_lm_max_concurrent=32,
    meas_lm_model="test-extraction-model",
    meas_lm_base_url="http://localhost:8084/v1",
    meas_lm_api_key="EMPTY",
    meas_lm_max_concurrent=4,
    meas_lm_entity_identification_prompt="Identify coastal sites.",
    extraction_schema_name="coastal_measurement_v1",
    extraction_model_version=None,
    extraction_lat_field=None,
    extraction_lon_field=None,
)


def _fake_settings(**overrides: Any) -> SimpleNamespace:
    return SimpleNamespace(**{**_FAKE_SETTINGS.__dict__, **overrides})


class TestBuildExtractionAdapterGuards:
    def test_raises_on_missing_doc_lm_model(self) -> None:
        settings = _fake_settings(doc_lm_model=None)
        with pytest.raises(RuntimeError, match="DOC_LM_MODEL"):
            build_extraction_adapter(settings)

    def test_raises_on_missing_meas_lm_model(self) -> None:
        settings = _fake_settings(meas_lm_model=None)
        with pytest.raises(RuntimeError, match="MEAS_LM_MODEL"):
            build_extraction_adapter(settings)

    def test_raises_on_missing_entity_identification_prompt(self) -> None:
        settings = _fake_settings(meas_lm_entity_identification_prompt=None)
        with pytest.raises(RuntimeError, match="MEAS_LM_ENTITY_IDENTIFICATION_PROMPT"):
            build_extraction_adapter(settings)

    def test_raises_lists_all_missing(self) -> None:
        settings = _fake_settings(doc_lm_model=None, meas_lm_model=None)
        with pytest.raises(RuntimeError, match="DOC_LM_MODEL, MEAS_LM_MODEL"):
            build_extraction_adapter(settings)


class TestBuildExtractionAdapterConstruction:
    def test_constructs_doc_lm_with_settings(self, mocker: Any) -> None:
        doc_lm_cls = mocker.patch("coastal_crawler.adapter.OCRLM")
        mocker.patch("coastal_crawler.adapter.ExtractionLM")

        build_extraction_adapter(_fake_settings())

        doc_lm_cls.assert_called_once_with(
            model_name="test-ocr-model",
            api_base="http://localhost:8083/v1",
            api_key="EMPTY",
            max_concurrent=32,
        )

    def test_constructs_meas_lm_with_settings(self, mocker: Any) -> None:
        mocker.patch("coastal_crawler.adapter.OCRLM")
        meas_lm_cls = mocker.patch("coastal_crawler.adapter.ExtractionLM")

        build_extraction_adapter(_fake_settings())

        meas_lm_cls.assert_called_once_with(
            model_name="test-extraction-model",
            direct_extraction_schema=DirectExtractionSchema,
            direct_extraction_prompt=build_direct_extraction_prompt("Identify coastal sites."),
            api_base="http://localhost:8084/v1",
            api_key="EMPTY",
            max_concurrent=4,
        )

    def test_returns_direct_extraction_adapter_with_schema_and_version(self, mocker: Any) -> None:
        doc_lm_sentinel = mocker.sentinel.doc_lm
        meas_lm_sentinel = mocker.sentinel.meas_lm
        mocker.patch("coastal_crawler.adapter.OCRLM", return_value=doc_lm_sentinel)
        mocker.patch("coastal_crawler.adapter.ExtractionLM", return_value=meas_lm_sentinel)

        adapter = build_extraction_adapter(_fake_settings())

        assert isinstance(adapter, DirectExtractionAdapter)
        assert adapter.doc_lm is doc_lm_sentinel
        assert adapter.meas_lm is meas_lm_sentinel
        assert adapter.schema_name == "coastal_measurement_v1"
        assert adapter.model_version == "doc_lm=test-ocr-model+meas_lm=test-extraction-model"
        assert adapter.lat_field is None
        assert adapter.lon_field is None

    def test_explicit_model_version_overrides_derived_default(self, mocker: Any) -> None:
        mocker.patch("coastal_crawler.adapter.OCRLM")
        mocker.patch("coastal_crawler.adapter.ExtractionLM")

        adapter = build_extraction_adapter(_fake_settings(extraction_model_version="v2"))

        assert adapter.model_version == "v2"

    def test_lat_lon_fields_passed_through(self, mocker: Any) -> None:
        mocker.patch("coastal_crawler.adapter.OCRLM")
        mocker.patch("coastal_crawler.adapter.ExtractionLM")

        adapter = build_extraction_adapter(
            _fake_settings(extraction_lat_field="latitude", extraction_lon_field="longitude")
        )

        assert adapter.lat_field == "latitude"
        assert adapter.lon_field == "longitude"


class TestDirectExtractionAdapterExtractBatch:
    def _adapter(self, **kwargs: Any) -> DirectExtractionAdapter:
        return DirectExtractionAdapter(
            doc_lm=MagicMock(),
            meas_lm=MagicMock(),
            schema_name="coastal_measurement_v1",
            model_version="v1",
            **kwargs,
        )

    def test_calls_doc_lm_and_meas_lm_once_for_whole_batch(self) -> None:
        adapter = self._adapter()
        adapter.doc_lm.fit.return_value = ["ocr text 0", "ocr text 1", "ocr text 2"]
        adapter.meas_lm.fit.return_value = []

        pdf_paths = [Path("a.pdf"), Path("b.pdf"), Path("c.pdf")]
        adapter.extract_batch(pdf_paths)

        adapter.doc_lm.fit.assert_called_once_with(["a.pdf", "b.pdf", "c.pdf"])
        adapter.meas_lm.fit.assert_called_once_with(["ocr text 0", "ocr text 1", "ocr text 2"])

    def test_groups_records_by_document_id(self) -> None:
        adapter = self._adapter()
        adapter.doc_lm.fit.return_value = ["doc0 text", "doc1 text"]
        adapter.meas_lm.fit.return_value = [
            {"document_id": 1, "value": 1.0, "units": "m", "attribute": "depth"},
            {"document_id": 0, "value": 2.0, "units": "m", "attribute": "depth"},
            {"document_id": 0, "value": 3.0, "units": "m", "attribute": "width"},
        ]

        results = adapter.extract_batch([Path("a.pdf"), Path("b.pdf")])

        assert len(results) == 2
        assert [r.data["value"] for r in results[0]] == [2.0, 3.0]
        assert [r.data["value"] for r in results[1]] == [1.0]

    def test_returns_empty_list_for_document_with_no_records(self) -> None:
        adapter = self._adapter()
        adapter.doc_lm.fit.return_value = ["doc0 text", "doc1 text"]
        adapter.meas_lm.fit.return_value = [
            {"document_id": 0, "value": 1.0, "units": "m", "attribute": "depth"},
        ]

        results = adapter.extract_batch([Path("a.pdf"), Path("b.pdf")])

        assert len(results) == 2
        assert len(results[0]) == 1
        assert results[1] == []

    def test_lat_lon_extracted_from_records(self) -> None:
        adapter = self._adapter(lat_field="latitude", lon_field="longitude")
        adapter.doc_lm.fit.return_value = ["doc0 text"]
        adapter.meas_lm.fit.return_value = [
            {
                "document_id": 0,
                "value": 1.0,
                "units": "m",
                "attribute": "depth",
                "latitude": 51.5,
                "longitude": -0.1,
            },
        ]

        results = adapter.extract_batch([Path("a.pdf")])

        assert results[0][0].latitude == pytest.approx(51.5)
        assert results[0][0].longitude == pytest.approx(-0.1)
