"""Tests for the native OCR/extraction pipeline (coastal_crawler.extraction).

Pure unit tests — the OpenAI-compatible async client is mocked at the
`chat.completions.create` boundary, and OCRLM's PDF rendering is mocked
out entirely (no real PDF file or poppler-utils installation required).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from coastal_crawler.extraction.ocr_lm import OCRLM
from coastal_crawler.extraction.extraction_lm import ExtractionLM, response_validator

# ---------------------------------------------------------------------------
# response_validator
# ---------------------------------------------------------------------------


class _ItemsResponse(BaseModel):
    items: list[str]


class TestResponseValidator:
    def test_parses_clean_json(self) -> None:
        result = response_validator(_ItemsResponse, '{"items": ["a", "b"]}')
        assert result == {"items": ["a", "b"]}

    def test_strips_leading_prose_and_markdown_fences(self) -> None:
        response = 'Here is the JSON:\n```json\n{"items": ["a"]}\n```'
        result = response_validator(_ItemsResponse, response)
        assert result == {"items": ["a"]}

    def test_raises_on_invalid_json(self) -> None:
        with pytest.raises(Exception):
            response_validator(_ItemsResponse, "not json at all")


# ---------------------------------------------------------------------------
# ExtractionLM
# ---------------------------------------------------------------------------


class _DirectExtractionTestSchema(BaseModel):
    name: str | None
    attribute: str
    value: str | None
    units: str | None


def _fake_chat_response(content: str, prompt_tokens: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(completion_tokens=0, prompt_tokens=prompt_tokens),
    )


class TestExtractionLM:
    def _make_instance(self) -> ExtractionLM:
        return ExtractionLM(
            model_name="test-model",
            direct_extraction_schema=_DirectExtractionTestSchema,
            direct_extraction_prompt="Extract coastal site measurements.",
        )

    def test_fit_happy_path(self, mocker: Any) -> None:
        instance = self._make_instance()
        content = json.dumps(
            {"items": [{"name": "Site A", "attribute": "salinity", "value": "10", "units": "psu"}]}
        )
        mocker.patch.object(
            instance.async_client.chat.completions,
            "create",
            AsyncMock(return_value=_fake_chat_response(content)),
        )

        records = instance.fit(["some ocr text"])

        assert len(records) == 1
        record = records[0]
        assert record["name"] == "Site A"
        assert record["attribute"] == "salinity"
        assert record["value"] == "10"
        assert record["units"] == "psu"
        assert record["entity_id"] == "doc_0_entity_0"
        assert record["context"] == "some ocr text"

    def test_fit_retries_after_validation_failure(self, mocker: Any) -> None:
        instance = self._make_instance()
        good_content = json.dumps(
            {"items": [{"name": "Site A", "attribute": "salinity", "value": "10", "units": "psu"}]}
        )
        mocker.patch.object(
            instance.async_client.chat.completions,
            "create",
            AsyncMock(side_effect=[_fake_chat_response("not valid json"), _fake_chat_response(good_content)]),
        )

        records = instance.fit(["some ocr text"])

        assert len(records) == 1
        assert records[0]["value"] == "10"

    def test_fit_skips_items_with_no_value(self, mocker: Any) -> None:
        instance = self._make_instance()
        content = json.dumps(
            {
                "items": [
                    {"name": "Site A", "attribute": "salinity", "value": None, "units": None},
                    {"name": "Site A", "attribute": "turbidity", "value": "5", "units": "NTU"},
                ]
            }
        )
        mocker.patch.object(
            instance.async_client.chat.completions,
            "create",
            AsyncMock(return_value=_fake_chat_response(content)),
        )

        records = instance.fit(["some ocr text"])

        assert len(records) == 1
        assert records[0]["attribute"] == "turbidity"


# ---------------------------------------------------------------------------
# OCRLM
# ---------------------------------------------------------------------------


def _fake_ocr_response(content: str, completion_tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(completion_tokens=completion_tokens),
    )


class TestOCRLM:
    def test_fit_assembles_page_tags(self, mocker: Any) -> None:
        mocker.patch(
            "coastal_crawler.extraction.ocr_lm.render_pdf_pages",
            return_value=["fakeb64page"],
        )
        instance = OCRLM(model_name="test-vlm")
        mocker.patch.object(
            instance.async_client.chat.completions,
            "create",
            AsyncMock(return_value=_fake_ocr_response("page text", completion_tokens=10)),
        )

        [doc_text] = instance.fit(["fake.pdf"])

        assert doc_text == '<page number="0">\n\npage text\n\n</page>\n\n'

    def test_fit_retries_page_exceeding_max_tokens(self, mocker: Any) -> None:
        mocker.patch(
            "coastal_crawler.extraction.ocr_lm.render_pdf_pages",
            return_value=["fakeb64page"],
        )
        instance = OCRLM(model_name="test-vlm")
        mocker.patch.object(
            instance.async_client.chat.completions,
            "create",
            AsyncMock(
                side_effect=[
                    _fake_ocr_response("truncated...", completion_tokens=instance.max_tokens),
                    _fake_ocr_response("full page text", completion_tokens=10),
                ]
            ),
        )

        [doc_text] = instance.fit(["fake.pdf"])

        assert "full page text" in doc_text
        assert "truncated" not in doc_text
