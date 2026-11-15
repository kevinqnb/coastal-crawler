"""Tests for PipelineMeasurementAdapter / build_pipeline_measurement_adapter
(scholarlm MeasurementLM in extraction_mode="pipeline")."""

from __future__ import annotations

import inspect
import re
from typing import Any
from unittest.mock import MagicMock

import pytest

from coastal_crawler.adapter import (
    _ENTITY_QUERY,
    IDENTIFICATION_FORMAT_NAME,
    SCHOLARLM_ENTITY_MAX_TOKENS,
    PipelineMeasurementAdapter,
    _count_rendered_tokens,
    _entity_identification_prompt,
    build_pipeline_measurement_adapter,
)
from coastal_crawler.measurement_schema import (
    MEASUREMENT_EVENT_PROMPT,
    ATTRIBUTE_INFO_DICT,
    EntitySchema,
    MeasurementEventSchema,
)
from tests.test_adapter import _FakeTokenizer, _fake_settings

_ENTITY_PROMPT = "Identify coastal sites."


def _fmt(name: str) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {"name": name}}


class _PipelineLM:
    """Stand-in MeasurementLM: fit() issues one call per named step through the
    real `_call_batch` attribute the adapter wraps, with canned responses, and
    returns canned flat records."""

    def __init__(
        self,
        step_responses: dict[str, list[Any]],
        records: list[dict[str, Any]],
        step_seconds: dict[str, float] | None = None,
    ) -> None:
        self.extraction_mode = "pipeline"
        self.entity_identification_prompt = _ENTITY_PROMPT
        self.context_length_exceeded_docs: set[int] = set()
        self.step_seconds: dict[str, float] = {}
        self._step_responses = step_responses
        self._records = records
        self._canned_step_seconds = step_seconds or {}
        self.fit_inputs: list[str] = []

    def _call_batch(self, message_sets: list[Any], **kwargs: Any) -> list[Any]:
        return self._step_responses[kwargs["response_format"]["json_schema"]["name"]]

    def fit(self, texts: list[str]) -> list[dict[str, Any]]:
        self.fit_inputs = list(texts)
        validator = lambda r: None if r.startswith("{") else (_ for _ in ()).throw(ValueError(r))  # noqa: E731
        for name, resp in self._step_responses.items():
            self._call_batch([None] * len(resp), response_format=_fmt(name), validator=validator)
        self.step_seconds = dict(self._canned_step_seconds)
        return list(self._records)


def _adapter(lm: Any, **kwargs: Any) -> PipelineMeasurementAdapter:
    kwargs.setdefault("tokenizer", _FakeTokenizer())
    kwargs.setdefault("max_model_len", 100_000)
    kwargs.setdefault("reserved_completion_tokens", 0)
    return PipelineMeasurementAdapter(
        meas_lm=lm, schema_name="coastal_measurement_v1", model_version="v1", **kwargs
    )


def _rec(doc: int, value: float, attribute: str = "salinity") -> dict[str, Any]:
    return {
        "document_id": doc,
        "value": value,
        "units": "psu",
        "attribute": attribute,
        "page_number": [3],
        "table_number": [None],
        "row_index": [None],
        "column_index": [None],
        "source": ["text"],
        "context": "full document text",
    }


class TestPipelineExtractBatch:
    def test_regroups_flat_records_by_document_id(self) -> None:
        lm = _PipelineLM({IDENTIFICATION_FORMAT_NAME: ['{"items": []}'] * 3}, [_rec(0, 1.0), _rec(2, 3.0), _rec(0, 2.0)])
        results = _adapter(lm).extract_batch(["a", "b", "c"])

        assert [len(r) for r in results] == [2, 0, 1]  # type: ignore[arg-type]
        assert [r.data["value"] for r in results[0]] == [1.0, 2.0]  # type: ignore[union-attr]
        assert "context" not in results[0][0].data  # type: ignore[index,union-attr]
        assert results[0][0].provenance["page_number"] == 3  # type: ignore[index,union-attr]

    def test_entity_step_failure_is_a_per_paper_error(self) -> None:
        lm = _PipelineLM({IDENTIFICATION_FORMAT_NAME: ['{"items": []}', "", "not json"]}, [_rec(0, 1.0)])
        adapter = _adapter(lm)

        results = adapter.extract_batch(["a", "b", "c"])

        assert not isinstance(results[0], str)
        assert isinstance(results[1], str) and "exhausted its retries" in results[1]
        assert isinstance(results[2], str) and "exhausted its retries" in results[2]
        assert adapter.exhausted_calls == {IDENTIFICATION_FORMAT_NAME: {1, 2}}

    def test_later_step_failure_is_recorded_not_attributed_to_a_paper(self) -> None:
        lm = _PipelineLM(
            {IDENTIFICATION_FORMAT_NAME: ['{"items": []}'], "text_value_extraction": ["", '{"items": []}']},
            [_rec(0, 1.0)],
        )
        adapter = _adapter(lm)

        results = adapter.extract_batch(["a"])

        assert not isinstance(results[0], str)
        assert adapter.exhausted_calls == {"text_value_extraction": {0}}

    def test_step_seconds_copied_and_exhausted_state_resets_between_batches(self) -> None:
        lm = _PipelineLM({IDENTIFICATION_FORMAT_NAME: [""]}, [], step_seconds={"entities": 1.5, "final": 0.5})
        adapter = _adapter(lm)
        adapter.extract_batch(["a"])
        assert adapter.step_seconds == {"entities": 1.5, "final": 0.5}
        assert adapter.exhausted_calls == {IDENTIFICATION_FORMAT_NAME: {0}}

        lm._step_responses = {IDENTIFICATION_FORMAT_NAME: ['{"items": []}']}
        adapter.extract_batch(["a"])
        assert adapter.exhausted_calls == {}

    def test_context_length_exceeded_with_no_records_is_an_error(self) -> None:
        lm = _PipelineLM({IDENTIFICATION_FORMAT_NAME: ['{"items": []}']}, [])
        lm.context_length_exceeded_docs = {0}
        results = _adapter(lm).extract_batch(["a"])
        assert isinstance(results[0], str) and "context-length-exceeded" in results[0]

    def test_refuses_a_direct_mode_meas_lm(self) -> None:
        lm = _PipelineLM({}, [])
        lm.extraction_mode = "direct"
        with pytest.raises(AssertionError):
            _adapter(lm).extract_batch(["a"])


class TestPipelineTruncation:
    def test_truncates_to_entity_prompt_budget_and_flags_document(self) -> None:
        tok = _FakeTokenizer()
        overhead = _count_rendered_tokens(tok, _entity_identification_prompt(_ENTITY_PROMPT, ""))
        limit = overhead + 10
        lm = _PipelineLM({IDENTIFICATION_FORMAT_NAME: ['{"items": []}'] * 2}, [])
        adapter = _adapter(lm, tokenizer=tok, max_model_len=limit, reserved_completion_tokens=0)

        adapter.extract_batch(["w " * 50, "w " * 3])

        assert adapter.truncated_docs == {0}
        sent = lm.fit_inputs
        assert _count_rendered_tokens(tok, _entity_identification_prompt(_ENTITY_PROMPT, sent[0])) <= limit
        assert sent[1] == "w " * 3  # under budget: untouched

    def test_reserved_completion_tokens_shrink_the_budget(self) -> None:
        with pytest.raises(ValueError, match="leaves no room"):
            _adapter(_PipelineLM({}, []), max_model_len=8192, reserved_completion_tokens=8192)


class TestInstalledScholarlmCoupling:
    """The adapter duplicates two facts from scholarlm's source; a pin bump
    that changes either must fail here, not silently skew the truncation."""

    def test_entity_prompt_template_and_query_match_installed_scholarlm(self) -> None:
        import scholarlm.measurementlm as m

        src = inspect.getsource(m.MeasurementLM._extract_entities)
        assert 'f"## INSTRUCTIONS:\\n{instructions}\\n\\n## CONTEXT:\\n{context}\\n\\n## QUERY:\\n{query}"' in src
        # scholarlm builds the query from adjacent string literals; join them.
        joined = re.sub(r'"\s*\n\s*"', "", src)
        assert _ENTITY_QUERY in joined

    def test_entity_max_tokens_and_format_name_match_installed_scholarlm(self) -> None:
        import scholarlm.measurementlm as m

        default = inspect.signature(m.MeasurementLM._extract_entities).parameters["max_tokens"].default
        assert default == SCHOLARLM_ENTITY_MAX_TOKENS
        assert f'"name": "{IDENTIFICATION_FORMAT_NAME}"' in inspect.getsource(m.MeasurementLM._extract_entities)
        # fit() must still call it with no override.
        assert "self._extract_entities()" in inspect.getsource(m.MeasurementLM.fit)


class TestBuildPipelineMeasurementAdapter:
    @pytest.fixture(autouse=True)
    def _patch_tokenizer(self, mocker: Any) -> Any:
        auto = mocker.patch("transformers.AutoTokenizer")
        auto.from_pretrained.return_value = _FakeTokenizer()
        return auto

    def test_constructs_pipeline_mode_meas_lm(self, mocker: Any) -> None:
        cls = mocker.patch("coastal_crawler.adapter.MeasurementLM")

        build_pipeline_measurement_adapter(_fake_settings(), collect_attribute_terms=False)

        cls.assert_called_once_with(
            model_name="test-extraction-model",
            entity_identification_prompt="Identify coastal sites.",
            entity_identification_schema=EntitySchema,
            attribute_info_dict=ATTRIBUTE_INFO_DICT,
            measurement_event_schema=MeasurementEventSchema,
            measurement_event_prompt=MEASUREMENT_EVENT_PROMPT,
            extraction_mode="pipeline",
            collect_attribute_terms=False,
            parse_quantities_context="value_only",
            standardize_context="value_only",
            sampling_params={
                "temperature": 0.90,
                "top_p": 0.95,
                "top_k": 64,
                "repetition_penalty": 1.0,
                "enable_thinking": False,
            },
            api_base="http://localhost:8084/v1",
            api_key="EMPTY",
            max_concurrent=4,
        )
        assert MEASUREMENT_EVENT_PROMPT  # the whole point: never None/empty

    def test_collect_attribute_terms_is_passed_through(self, mocker: Any) -> None:
        cls = mocker.patch("coastal_crawler.adapter.MeasurementLM")
        build_pipeline_measurement_adapter(_fake_settings(), collect_attribute_terms=True)
        assert cls.call_args.kwargs["collect_attribute_terms"] is True

    def test_returns_pipeline_adapter_with_entity_token_reservation(self, mocker: Any) -> None:
        mocker.patch("coastal_crawler.adapter.MeasurementLM", return_value=MagicMock())
        adapter = build_pipeline_measurement_adapter(_fake_settings(), collect_attribute_terms=False)
        assert isinstance(adapter, PipelineMeasurementAdapter)
        assert adapter.reserved_completion_tokens == SCHOLARLM_ENTITY_MAX_TOKENS
        assert adapter.model_version.endswith("+mode=pipeline")

    @pytest.mark.parametrize("missing", ["meas_lm_model", "meas_lm_entity_identification_prompt", "meas_lm_max_model_len"])
    def test_raises_on_missing_setting(self, missing: str) -> None:
        with pytest.raises(RuntimeError, match=missing.upper()):
            build_pipeline_measurement_adapter(_fake_settings(**{missing: None}), collect_attribute_terms=False)
