"""Tests for build_ocr_adapter()/build_measurement_adapter() (adapter.py).

Pure construction/mocking tests — no DB fixtures needed. scholarlm's
DocumentLM and MeasurementLM are patched so no real vLLM server is required.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from coastal_crawler.adapter import (
    DirectMeasurementAdapter,
    DirectOCRAdapter,
    _count_prompt_tokens,
    _truncate_context_to_budget,
    build_measurement_adapter,
    build_ocr_adapter,
)
from coastal_crawler.measurement_schema import (
    ATTRIBUTE_INFO_DICT,
    DirectExtractionSchema,
    EntitySchema,
    MeasurementEventSchema,
    build_direct_extraction_prompt,
)

_FAKE_SETTINGS = SimpleNamespace(
    doc_lm_model="test-ocr-model",
    doc_lm_base_url="http://localhost:8083/v1",
    doc_lm_api_key="EMPTY",
    doc_lm_max_concurrent=32,
    doc_lm_unknown_label_policy="raise",
    meas_lm_model="test-extraction-model",
    meas_lm_base_url="http://localhost:8084/v1",
    meas_lm_api_key="EMPTY",
    meas_lm_max_concurrent=4,
    meas_lm_max_model_len=32768,
    meas_lm_entity_identification_prompt="Identify coastal sites.",
    meas_lm_temperature=0.90,
    meas_lm_top_p=0.95,
    meas_lm_top_k=64,
    meas_lm_repetition_penalty=1.0,
    meas_lm_enable_thinking=False,
    extraction_schema_name="coastal_measurement_v1",
    extraction_model_version=None,
    extraction_lat_field=None,
    extraction_lon_field=None,
)


def _fake_settings(**overrides: Any) -> SimpleNamespace:
    return SimpleNamespace(**{**_FAKE_SETTINGS.__dict__, **overrides})


class _FakeTokenizer:
    """Structural stand-in for a HuggingFace tokenizer (adapter._Tokenizer) —
    one token per whitespace-separated word, so token budgets are easy to
    reason about by inspection.

    ``apply_chat_template`` mimics transformers >=5: it returns a dict-like
    ``BatchEncoding`` (``len()`` == 2, the key count) *unless* the caller
    passes ``return_dict=False``, in which case it returns the plain
    ``list[int]``. That is what makes ``test_count_prompt_tokens_*`` a
    regression guard — if the adapter stops passing ``return_dict=False`` the
    token count silently collapses to 2 and the truncation backstop goes
    dead.
    """

    #: role/template tokens apply_chat_template adds around the message body
    WRAPPER_TOKENS = 4

    def encode(self, text: str, add_special_tokens: bool = True) -> list[Any]:
        return text.split()  # type: ignore[return-value]

    def decode(self, token_ids: list[Any], skip_special_tokens: bool = True) -> str:
        return " ".join(token_ids)

    def apply_chat_template(
        self,
        conversation: list[dict[str, str]],
        tokenize: bool = True,
        add_generation_prompt: bool = True,
        return_dict: bool = True,
    ) -> Any:
        toks: list[str] = []
        for msg in conversation:
            toks += ["<tmpl>"] * self.WRAPPER_TOKENS
            toks += msg["content"].split()
        if return_dict:
            return {"input_ids": toks, "attention_mask": [1] * len(toks)}
        return toks


# ---------------------------------------------------------------------------
# build_ocr_adapter
# ---------------------------------------------------------------------------

class TestBuildOCRAdapterGuards:
    def test_raises_on_missing_doc_lm_model(self) -> None:
        settings = _fake_settings(doc_lm_model=None)
        with pytest.raises(RuntimeError, match="DOC_LM_MODEL"):
            build_ocr_adapter(settings)


class TestBuildOCRAdapterConstruction:
    def test_constructs_doc_lm_with_settings(self, mocker: Any) -> None:
        doc_lm_cls = mocker.patch("coastal_crawler.adapter.DocumentLM")

        build_ocr_adapter(_fake_settings())

        doc_lm_cls.assert_called_once_with(
            model_name="test-ocr-model",
            fast=True,
            drop_references=True,
            api_base="http://localhost:8083/v1",
            api_key="EMPTY",
            max_concurrent=32,
            unknown_label_policy="raise",
        )

    def test_passes_unknown_label_policy_from_settings(self, mocker: Any) -> None:
        doc_lm_cls = mocker.patch("coastal_crawler.adapter.DocumentLM")

        build_ocr_adapter(_fake_settings(doc_lm_unknown_label_policy="coerce"))

        assert doc_lm_cls.call_args.kwargs["unknown_label_policy"] == "coerce"

    def test_returns_direct_ocr_adapter(self, mocker: Any) -> None:
        doc_lm_sentinel = mocker.sentinel.doc_lm
        mocker.patch("coastal_crawler.adapter.DocumentLM", return_value=doc_lm_sentinel)

        adapter = build_ocr_adapter(_fake_settings())

        assert isinstance(adapter, DirectOCRAdapter)
        assert adapter.doc_lm is doc_lm_sentinel


class TestDirectOCRAdapterOcrBatch:
    def test_calls_doc_lm_fit_with_str_paths(self) -> None:
        adapter = DirectOCRAdapter(doc_lm=MagicMock())
        adapter.doc_lm.fit.return_value = ["ocr text 0", "ocr text 1"]
        adapter.doc_lm.coerced_labels = {}

        result = adapter.ocr_batch([Path("a.pdf"), Path("b.pdf")])

        adapter.doc_lm.fit.assert_called_once_with(["a.pdf", "b.pdf"])
        assert result == ["ocr text 0", "ocr text 1"]

    def test_logs_coerced_labels_per_document(self, mocker: Any) -> None:
        warn = mocker.patch("coastal_crawler.adapter.log.warning")
        adapter = DirectOCRAdapter(doc_lm=MagicMock())
        adapter.doc_lm.fit.return_value = ["ocr text 0", "ocr text 1"]
        adapter.doc_lm.coerced_labels = {1: {"Chemical-Block": 2}}

        adapter.ocr_batch([Path("a.pdf"), Path("b.pdf")])

        warn.assert_called_once_with(
            "ocr_unknown_labels_coerced",
            pdf_path="b.pdf",
            labels={"Chemical-Block": 2},
        )


# ---------------------------------------------------------------------------
# build_measurement_adapter
# ---------------------------------------------------------------------------

class TestBuildMeasurementAdapterGuards:
    def test_raises_on_missing_meas_lm_model(self) -> None:
        settings = _fake_settings(meas_lm_model=None)
        with pytest.raises(RuntimeError, match="MEAS_LM_MODEL"):
            build_measurement_adapter(settings)

    def test_raises_on_missing_entity_identification_prompt(self) -> None:
        settings = _fake_settings(meas_lm_entity_identification_prompt=None)
        with pytest.raises(RuntimeError, match="MEAS_LM_ENTITY_IDENTIFICATION_PROMPT"):
            build_measurement_adapter(settings)

    def test_raises_on_missing_max_model_len(self) -> None:
        settings = _fake_settings(meas_lm_max_model_len=None)
        with pytest.raises(RuntimeError, match="MEAS_LM_MAX_MODEL_LEN"):
            build_measurement_adapter(settings)

    def test_raises_lists_all_missing(self) -> None:
        settings = _fake_settings(
            meas_lm_model=None,
            meas_lm_entity_identification_prompt=None,
            meas_lm_max_model_len=None,
        )
        with pytest.raises(
            RuntimeError,
            match="MEAS_LM_MODEL, MEAS_LM_ENTITY_IDENTIFICATION_PROMPT, MEAS_LM_MAX_MODEL_LEN",
        ):
            build_measurement_adapter(settings)


class TestBuildMeasurementAdapterConstruction:
    @pytest.fixture(autouse=True)
    def _patch_tokenizer(self, mocker: Any) -> Any:
        """build_measurement_adapter() loads the real HF tokenizer for
        MEAS_LM_MODEL. The fake model name here has no tokenizer to load, so
        patch it — mirrors the DocumentLM/MeasurementLM patches. The import
        is function-local (``from transformers import AutoTokenizer``), so
        the patch target is transformers' own module."""
        auto = mocker.patch("transformers.AutoTokenizer")
        auto.from_pretrained.return_value = _FakeTokenizer()
        return auto

    def test_constructs_meas_lm_with_settings(self, mocker: Any) -> None:
        meas_lm_cls = mocker.patch("coastal_crawler.adapter.MeasurementLM")

        build_measurement_adapter(_fake_settings())

        meas_lm_cls.assert_called_once_with(
            model_name="test-extraction-model",
            entity_identification_prompt="Identify coastal sites.",
            entity_identification_schema=EntitySchema,
            attribute_info_dict=ATTRIBUTE_INFO_DICT,
            measurement_event_schema=MeasurementEventSchema,
            extraction_mode="direct",
            clean_tables=False,
            direct_extraction_schema=DirectExtractionSchema,
            direct_extraction_prompt=build_direct_extraction_prompt("Identify coastal sites."),
            collect_attribute_terms=False,
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

    def test_returns_direct_measurement_adapter_with_schema_and_version(self, mocker: Any) -> None:
        meas_lm_sentinel = mocker.sentinel.meas_lm
        mocker.patch("coastal_crawler.adapter.MeasurementLM", return_value=meas_lm_sentinel)

        adapter = build_measurement_adapter(_fake_settings())

        assert isinstance(adapter, DirectMeasurementAdapter)
        assert adapter.meas_lm is meas_lm_sentinel
        assert adapter.schema_name == "coastal_measurement_v1"
        assert adapter.model_version == "doc_lm=test-ocr-model+meas_lm=test-extraction-model"
        assert adapter.lat_field is None
        assert adapter.lon_field is None
        assert adapter.max_model_len == 32768
        assert isinstance(adapter.tokenizer, _FakeTokenizer)

    def test_loads_tokenizer_for_configured_model(self, _patch_tokenizer: Any) -> None:
        build_measurement_adapter(_fake_settings())

        _patch_tokenizer.from_pretrained.assert_called_once_with("test-extraction-model")

    def test_explicit_model_version_overrides_derived_default(self, mocker: Any) -> None:
        mocker.patch("coastal_crawler.adapter.MeasurementLM")

        adapter = build_measurement_adapter(_fake_settings(extraction_model_version="v2"))

        assert adapter.model_version == "v2"

    def test_lat_lon_fields_passed_through(self, mocker: Any) -> None:
        mocker.patch("coastal_crawler.adapter.MeasurementLM")

        adapter = build_measurement_adapter(
            _fake_settings(extraction_lat_field="latitude", extraction_lon_field="longitude")
        )

        assert adapter.lat_field == "latitude"
        assert adapter.lon_field == "longitude"


class TestDirectMeasurementAdapterExtractBatch:
    """MeasurementLM.fit() (extraction_mode="direct") returns a FLAT
    list[dict] across all documents combined, each record carrying a
    `document_id` index back into ocr_texts — not one entry per document
    the way the deleted native ExtractionLM.fit() did. extract_batch() must
    regroup by document_id before converting."""

    def _adapter(self, **kwargs: Any) -> DirectMeasurementAdapter:
        # tokenizer/max_model_len have no production default (CLAUDE.md:
        # no silent fallbacks) — build_measurement_adapter() always passes
        # them. Only this test helper supplies convenience values: a
        # word-level fake and a budget large enough that nothing truncates
        # unless a test overrides it.
        kwargs.setdefault("tokenizer", _FakeTokenizer())
        kwargs.setdefault("max_model_len", 100_000)
        meas_lm = kwargs.pop("meas_lm", None) or MagicMock()
        return DirectMeasurementAdapter(
            meas_lm=meas_lm,
            schema_name="coastal_measurement_v1",
            model_version="v1",
            **kwargs,
        )

    def test_calls_meas_lm_once_for_whole_batch(self) -> None:
        adapter = self._adapter()
        adapter.meas_lm.fit.return_value = []

        ocr_texts = ["ocr text 0", "ocr text 1", "ocr text 2"]
        adapter.extract_batch(ocr_texts)

        adapter.meas_lm.fit.assert_called_once_with(ocr_texts)

    def test_maps_records_by_document_id(self) -> None:
        adapter = self._adapter()
        adapter.meas_lm.fit.return_value = [
            {"document_id": 0, "value": 2.0, "units": "m", "attribute": "depth"},
            {"document_id": 1, "value": 1.0, "units": "m", "attribute": "depth"},
            {"document_id": 0, "value": 3.0, "units": "m", "attribute": "width"},
        ]

        results = adapter.extract_batch(["doc0 text", "doc1 text"])

        assert len(results) == 2
        assert [r.data["value"] for r in results[0]] == [2.0, 3.0]
        assert [r.data["value"] for r in results[1]] == [1.0]

    def test_returns_empty_list_for_document_with_no_records(self) -> None:
        adapter = self._adapter()
        adapter.meas_lm.fit.return_value = [
            {"document_id": 0, "value": 1.0, "units": "m", "attribute": "depth"},
        ]

        results = adapter.extract_batch(["doc0 text", "doc1 text"])

        assert len(results) == 2
        assert len(results[0]) == 1
        assert results[1] == []

    def test_context_dropped_from_data_and_provenance(self) -> None:
        """`context` (the full OCR'd document text MeasurementLM merges into
        every record) must not be persisted per-record — worker.py stores it
        once per paper instead (see PaperOcrContext). Previously this landed
        in both `data` and `provenance`, doubling an already-duplicated cost."""
        adapter = self._adapter()
        adapter.meas_lm.fit.return_value = [
            {
                "document_id": 0,
                "context": "the full document text",
                "value": 1.0,
                "units": "m",
                "attribute": "depth",
            },
        ]

        results = adapter.extract_batch(["doc0 text"])

        assert "context" not in results[0][0].data
        assert results[0][0].provenance is not None
        assert "context" not in results[0][0].provenance

    def test_provenance_fields_unwrapped_from_dedup_singleton_lists(self) -> None:
        """MeasurementLM's _deduplicate() wraps page_number/table_number/
        row_index/column_index/source in single-element lists (provenance
        aggregation across duplicates) — direct mode's per-item-unique
        entity_id means every dedup group is a singleton, so _to_result()
        unwraps back to a scalar."""
        adapter = self._adapter()
        adapter.meas_lm.fit.return_value = [
            {
                "document_id": 0,
                "value": 1.0,
                "units": "m",
                "attribute": "depth",
                "page_number": [3],
                "table_number": [None],
                "row_index": [None],
                "column_index": [None],
                "source": ["text"],
            },
        ]

        results = adapter.extract_batch(["doc0 text"])

        assert results[0][0].provenance == {
            "page_number": 3,
            "table_number": None,
            "row_index": None,
            "column_index": None,
            "source": "text",
        }

    def test_raises_on_out_of_range_document_id(self) -> None:
        """A document_id scholarlm returns that isn't a valid 0-indexed position
        into ocr_texts means the whole regrouping assumption is wrong — fail
        loud rather than silently dropping the record (CLAUDE.md's
        assert-at-every-boundary rule)."""
        adapter = self._adapter()
        adapter.meas_lm.fit.return_value = [
            {"document_id": 5, "value": 1.0, "units": "m", "attribute": "depth"},
        ]

        with pytest.raises(ValueError, match="document_id"):
            adapter.extract_batch(["doc0 text"])

    def test_lat_lon_extracted_from_records(self) -> None:
        adapter = self._adapter(lat_field="latitude", lon_field="longitude")
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

        results = adapter.extract_batch(["doc0 text"])

        assert results[0][0].latitude == pytest.approx(51.5)
        assert results[0][0].longitude == pytest.approx(-0.1)


# ---------------------------------------------------------------------------
# Truncation backstop (2026-08-20-extraction-hardening-01)
# ---------------------------------------------------------------------------

_PROMPT = "Extract coastal measurements from this document."


class TestCountPromptTokens:
    def test_counts_full_templated_prompt_not_batchencoding_keys(self) -> None:
        """Regression guard for the transformers>=5 return_dict default:
        apply_chat_template(tokenize=True) returns a dict-like BatchEncoding
        unless return_dict=False, and len(BatchEncoding) is 2 (its key
        count). If the adapter stops passing return_dict=False, every prompt
        silently counts as 2 tokens and the backstop never truncates."""
        tok = _FakeTokenizer()
        context = "one two three four five six seven eight nine ten"

        n = _count_prompt_tokens(tok, _PROMPT, context)

        # Far more than the BatchEncoding key count (2), and the context slot
        # in the template contributes exactly its own word count on top of
        # the fixed overhead — i.e. the whole prompt was counted, not a dict.
        assert n > 2
        assert n - _count_prompt_tokens(tok, _PROMPT, "") == len(context.split())


class TestTruncateContextToBudget:
    def test_under_budget_passes_through_unchanged(self) -> None:
        context = "salinity was 35 psu at 2 m depth"
        out, truncated = _truncate_context_to_budget(_FakeTokenizer(), _PROMPT, context, 100_000)

        assert out == context
        assert truncated is False

    def test_over_budget_is_truncated_to_fit(self) -> None:
        tok = _FakeTokenizer()
        overhead = _count_prompt_tokens(tok, _PROMPT, "")
        budget = overhead + 25
        context = " ".join(f"w{i}" for i in range(500))

        out, truncated = _truncate_context_to_budget(tok, _PROMPT, context, budget)

        assert truncated is True
        assert len(out.split()) < 500
        assert _count_prompt_tokens(tok, _PROMPT, out) <= budget

    def test_budget_smaller_than_template_overhead_raises(self) -> None:
        with pytest.raises(ValueError, match="smaller than the fixed"):
            _truncate_context_to_budget(_FakeTokenizer(), _PROMPT, "a b c d e f", max_model_len=3)


def _meas_lm(fit_return: Any, *, context_length_exceeded: Any = (), prompt: Any = _PROMPT) -> Any:
    m = MagicMock()
    m.direct_extraction_prompt = prompt
    m.context_length_exceeded_docs = set(context_length_exceeded)
    m.fit.return_value = fit_return
    return m


class TestDirectMeasurementAdapterTruncation:
    def _adapter(self, meas_lm: Any, max_model_len: int) -> DirectMeasurementAdapter:
        return DirectMeasurementAdapter(
            meas_lm=meas_lm,
            schema_name="coastal_measurement_v1",
            model_version="v1",
            tokenizer=_FakeTokenizer(),
            max_model_len=max_model_len,
        )

    def test_under_budget_doc_not_marked_and_passed_verbatim(self) -> None:
        meas_lm = _meas_lm([])
        adapter = self._adapter(meas_lm, max_model_len=100_000)

        adapter.extract_batch(["short document text"])

        assert adapter.truncated_docs == set()
        meas_lm.fit.assert_called_once_with(["short document text"])

    def test_over_budget_doc_marked_and_truncated_text_sent(self) -> None:
        overhead = _count_prompt_tokens(_FakeTokenizer(), _PROMPT, "")
        meas_lm = _meas_lm([])
        adapter = self._adapter(meas_lm, max_model_len=overhead + 20)
        long_doc = " ".join(f"w{i}" for i in range(300))

        adapter.extract_batch([long_doc])

        assert adapter.truncated_docs == {0}
        sent = meas_lm.fit.call_args[0][0][0]
        assert len(sent.split()) < 300

    def test_truncated_docs_reset_between_calls(self) -> None:
        overhead = _count_prompt_tokens(_FakeTokenizer(), _PROMPT, "")
        meas_lm = _meas_lm([])
        adapter = self._adapter(meas_lm, max_model_len=overhead + 20)

        adapter.extract_batch([" ".join(f"w{i}" for i in range(300))])
        assert adapter.truncated_docs == {0}

        adapter.extract_batch(["short"])
        assert adapter.truncated_docs == set()

    def test_context_length_exceeded_with_zero_records_becomes_failure_str(self) -> None:
        meas_lm = _meas_lm([], context_length_exceeded={0})
        adapter = self._adapter(meas_lm, max_model_len=100_000)

        results = adapter.extract_batch(["doc text"])

        assert isinstance(results[0], str)
        assert "context-length-exceeded" in results[0]

    def test_context_length_exceeded_with_records_keeps_partial_results(self) -> None:
        meas_lm = _meas_lm(
            [{"document_id": 0, "value": 1.0, "units": "m", "attribute": "depth"}],
            context_length_exceeded={0},
        )
        adapter = self._adapter(meas_lm, max_model_len=100_000)

        results = adapter.extract_batch(["doc text"])

        assert not isinstance(results[0], str)
        assert len(results[0]) == 1

    def test_asserts_direct_extraction_prompt_is_wired(self) -> None:
        meas_lm = _meas_lm([], prompt=None)
        adapter = self._adapter(meas_lm, max_model_len=100_000)

        with pytest.raises(AssertionError, match="direct_extraction_prompt is None"):
            adapter.extract_batch(["doc text"])
