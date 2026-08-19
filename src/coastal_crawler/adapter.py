"""Extraction adapters — thin interfaces between the workers and scholarlm's
OCR/extraction models (``scholarlm.DocumentLM`` / ``scholarlm.MeasurementLM``).

Two independent adapters, one per pipeline stage: ``OCRAdapter`` (wraps
``DocumentLM``, used by ``ocr_worker.py``) and ``MeasurementAdapter`` (wraps
``MeasurementLM`` in ``extraction_mode="direct"``, used by ``worker.py``).
Each worker depends only on its own adapter protocol; the real pipeline calls
live here. Swap in the Stub adapters for tests; use the Direct adapters for
production.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog
from pydantic import BaseModel
from scholarlm import DocumentLM, MeasurementLM
from scholarlm.attribution import AttributionMethod, ContrastiveGradientAttribution, ProbeAttribution
from scholarlm.judgementlm import JudgementLM

log = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from coastal_crawler.config import Settings


class ExtractionResult(BaseModel):
    """Single measurement extracted from a paper."""

    schema_name: str
    model_version: str
    data: dict[str, Any]
    confidence: float | None = None
    provenance: dict[str, Any] | None = None
    latitude: float | None = None
    longitude: float | None = None


DocumentOutcome = list[ExtractionResult] | str
"""Per-document result of extract_batch(): either the list of measurements
found for that document (possibly empty, if the paper legitimately has
none), or a str describing why extraction failed for that document
specifically. Keeping failure distinguishable from "found nothing" is what
lets the worker mark just that one paper 'failed' in the DB — see
EFFICIENCY.md item 3."""


# ---------------------------------------------------------------------------
# OCR stage — pdf paths in, OCR text out
# ---------------------------------------------------------------------------

@runtime_checkable
class OCRAdapter(Protocol):
    """Interface the OCR worker calls. Lives in one place so it is easy to mock."""

    def ocr_batch(self, pdf_paths: list[Path]) -> list[str]:
        """OCR a batch of PDFs.

        Processing PDFs together (rather than one at a time) lets the OCR
        LLM calls for different documents run concurrently against vLLM's
        continuous batching.

        Args:
            pdf_paths: Paths to downloaded (or Wiley-cache) PDF files.

        Returns:
            One OCR text string per input path, same order and length as
            ``pdf_paths``.
        """
        ...


class StubOCRAdapter:
    """Returns empty OCR text — usable in tests without a GPU or vLLM endpoint."""

    def ocr_batch(self, pdf_paths: list[Path]) -> list[str]:
        return ["" for _ in pdf_paths]


class DirectOCRAdapter:
    """Calls scholarlm's DocumentLM to turn PDFs into OCR text."""

    def __init__(self, doc_lm: DocumentLM) -> None:
        self.doc_lm = doc_lm

    def ocr_batch(self, pdf_paths: list[Path]) -> list[str]:
        # scholarlm ships no py.typed marker, so DocumentLM.fit() is Any to
        # mypy — cast to the return type its docstring/implementation
        # actually guarantee (one str per input path, same order/length).
        result: list[str] = self.doc_lm.fit([str(p) for p in pdf_paths])
        return result


def build_ocr_adapter(settings: "Settings") -> DirectOCRAdapter:
    """Construct the production DirectOCRAdapter from Settings.

    Raises RuntimeError if DOC_LM_MODEL is missing (mirrors
    relevance_filter.run_filter()'s guard for FILTER_MODEL).

    No sampling_params passed — accepts scholarlm's fast=True defaults
    (max_tokens=16384, no retry-on-truncation loop) as-is; see
    notes/coastal-crawler/builds/2026-08-18-scholarlm-migration-01.md.
    """
    if not settings.doc_lm_model:
        raise RuntimeError("DOC_LM_MODEL must be configured to run OCR.")

    doc_lm = DocumentLM(
        model_name=settings.doc_lm_model,
        fast=True,
        api_base=settings.doc_lm_base_url,
        api_key=settings.doc_lm_api_key,
        max_concurrent=settings.doc_lm_max_concurrent,
    )
    return DirectOCRAdapter(doc_lm=doc_lm)


# ---------------------------------------------------------------------------
# Extraction stage — OCR text in, measurements out
# ---------------------------------------------------------------------------

@runtime_checkable
class MeasurementAdapter(Protocol):
    """Interface the extraction worker calls. Lives in one place so it is easy to mock."""

    def extract_batch(self, ocr_texts: list[str]) -> list[DocumentOutcome]:
        """Extract structured measurements from a batch of OCR'd documents.

        Args:
            ocr_texts: OCR text strings, one per document.

        Returns:
            One ``DocumentOutcome`` per input text, same order and length as
            ``ocr_texts``.
        """
        ...


class StubMeasurementAdapter:
    """Returns empty results — usable in tests without a GPU or vLLM endpoint."""

    def extract_batch(self, ocr_texts: list[str]) -> list[DocumentOutcome]:
        return [[] for _ in ocr_texts]


class DirectMeasurementAdapter:
    """
    Calls scholarlm's MeasurementLM (extraction_mode="direct") and converts
    raw dicts to ExtractionResult.

    ``lat_field`` / ``lon_field`` name the entity-schema fields that hold
    geographic coordinates.  Set to None if your schema has no coordinates.
    """

    def __init__(
        self,
        meas_lm: MeasurementLM,
        schema_name: str,
        model_version: str,
        lat_field: str | None = None,
        lon_field: str | None = None,
    ) -> None:
        self.meas_lm = meas_lm
        self.schema_name = schema_name
        self.model_version = model_version
        self.lat_field = lat_field
        self.lon_field = lon_field

    def extract_batch(self, ocr_texts: list[str]) -> list[DocumentOutcome]:
        # MeasurementLM.fit() in extraction_mode="direct" runs two LLM calls
        # per document (_extract_triples then _standardize), followed by
        # _deduplicate — and returns a FLAT list[dict] across all documents
        # combined (each record carries a `document_id` index back into
        # ocr_texts), not one entry per document. Regroup by document_id
        # before converting, so this adapter's own return shape (one
        # DocumentOutcome per input text, same order/length as ocr_texts)
        # stays what worker.py expects.
        #
        # There is no per-document error signal from MeasurementLM in direct
        # mode: a document whose extraction call fails validation ends up
        # with zero records, indistinguishable from a genuine
        # zero-measurement result. Accepted as a known lossy boundary of
        # depending on scholarlm as-is (see the build note's "Out of
        # scope") — the `str`-error branch of DocumentOutcome is therefore
        # never actually produced by this adapter today, even though the
        # type still allows it.
        raw: list[dict[str, Any]] = self.meas_lm.fit(ocr_texts)

        by_document: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for record in raw:
            by_document[record["document_id"]].append(record)

        out_of_range = set(by_document) - set(range(len(ocr_texts)))
        if out_of_range:
            raise ValueError(
                f"meas_lm.fit() returned document_id values outside range(len(ocr_texts))={len(ocr_texts)}: "
                f"{out_of_range}. This means document_id is not the expected 0-indexed position into "
                f"ocr_texts, and every downstream regrouping is silently wrong."
            )

        return [
            [self._to_result(record) for record in by_document[i]] for i in range(len(ocr_texts))
        ]

    def _to_result(self, record: dict[str, Any]) -> ExtractionResult:
        # _deduplicate() wraps these fields in single-element lists
        # (provenance aggregation across duplicates) — direct mode's
        # per-item-unique entity_id means every dedup group is a singleton,
        # so unwrap back to a scalar to keep this shape stable for callers.
        def _unwrap(field: str) -> Any:
            values = record.get(field)
            return values[0] if isinstance(values, list) else values

        provenance = {
            "page_number": _unwrap("page_number"),
            "table_number": _unwrap("table_number"),
            "row_index": _unwrap("row_index"),
            "column_index": _unwrap("column_index"),
            "source": _unwrap("source"),
        }
        # `context` (the full OCR'd document text) is dropped here, not
        # persisted per-record — worker.py writes it once per paper via
        # store.upsert_paper_ocr_context instead, from the same ocr_texts
        # string this record's `context` was merged in from. Storing it on
        # every measurement record (previously in both `data` and
        # `provenance`) meant a paper with N measurements stored its ~55KB
        # OCR text N*2 times — see migration c2d3e4f5a6b7.
        data = {k: v for k, v in record.items() if k != "context"}
        return ExtractionResult(
            schema_name=self.schema_name,
            model_version=self.model_version,
            data=data,
            # STUB: wire in a real confidence score if ExtractionLM exposes one.
            confidence=None,
            provenance=provenance,
            latitude=record.get(self.lat_field) if self.lat_field else None,
            longitude=record.get(self.lon_field) if self.lon_field else None,
        )


def build_measurement_adapter(settings: "Settings") -> DirectMeasurementAdapter:
    """Construct the production DirectMeasurementAdapter from Settings.

    Raises RuntimeError if required meas_lm_* settings are missing (mirrors
    relevance_filter.run_filter()'s guard for FILTER_MODEL/
    FILTER_RELEVANCE_PROMPT).
    """
    missing = [
        name
        for name, val in (
            ("MEAS_LM_MODEL", settings.meas_lm_model),
            ("MEAS_LM_ENTITY_IDENTIFICATION_PROMPT", settings.meas_lm_entity_identification_prompt),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(f"{', '.join(missing)} must be configured to run extraction.")
    # Narrow str | None -> str for the type checker; the guard above already
    # verified these are non-empty at runtime.
    assert settings.meas_lm_model is not None
    assert settings.meas_lm_entity_identification_prompt is not None

    from coastal_crawler.measurement_schema import (
        ATTRIBUTE_INFO_DICT,
        DirectExtractionSchema,
        EntitySchema,
        MeasurementEventSchema,
        build_direct_extraction_prompt,
    )

    # sampling_params is never omitted: MeasurementLM's constructor default
    # (`sampling_params: dict = {}`, checked with `is None`) silently drops
    # every inference param if the kwarg isn't passed explicitly — see the
    # build note's "What exists now" resolution.
    meas_lm = MeasurementLM(
        model_name=settings.meas_lm_model,
        entity_identification_prompt=settings.meas_lm_entity_identification_prompt,
        entity_identification_schema=EntitySchema,
        attribute_info_dict=ATTRIBUTE_INFO_DICT,
        measurement_event_schema=MeasurementEventSchema,
        extraction_mode="direct",
        clean_tables=False,
        direct_extraction_schema=DirectExtractionSchema,
        direct_extraction_prompt=build_direct_extraction_prompt(settings.meas_lm_entity_identification_prompt),
        collect_attribute_terms=False,
        sampling_params={
            "temperature": settings.meas_lm_temperature,
            "top_p": settings.meas_lm_top_p,
            "top_k": settings.meas_lm_top_k,
            "repetition_penalty": settings.meas_lm_repetition_penalty,
            "enable_thinking": settings.meas_lm_enable_thinking,
        },
        api_base=settings.meas_lm_base_url,
        api_key=settings.meas_lm_api_key,
        max_concurrent=settings.meas_lm_max_concurrent,
    )
    return DirectMeasurementAdapter(
        meas_lm=meas_lm,
        schema_name=settings.extraction_schema_name,
        model_version=(
            settings.extraction_model_version
            or f"doc_lm={settings.doc_lm_model}+meas_lm={settings.meas_lm_model}"
        ),
        lat_field=settings.extraction_lat_field,
        lon_field=settings.extraction_lon_field,
    )


# ---------------------------------------------------------------------------
# Judge stage — extraction (context, value) in, p_true/probe_score/
# attribution scores out
# ---------------------------------------------------------------------------

@dataclass
class JudgeComponents:
    """Everything judge_worker.py needs, loaded once per process.

    No adapter Protocol/Stub pair here (unlike OCR/extraction above) —
    JudgementLM/AttributionMethod are scholarlm classes called directly,
    not fronted by an OpenAI-compatible endpoint, so there is no
    stub-vs-direct distinction to make. Tests inject plain fakes with
    matching ``.generate()``/``.attribute()`` methods instead.
    """

    judge: JudgementLM
    attribution_methods: dict[str, AttributionMethod]
    instructions_prompt: str


def build_judge(settings: "Settings") -> JudgeComponents:
    """Construct JudgementLM + both attribution methods from Settings.

    Raises RuntimeError if required judge_* settings are missing (mirrors
    build_measurement_adapter's guard for MEAS_LM_MODEL/
    MEAS_LM_ENTITY_IDENTIFICATION_PROMPT) or if the loaded probe's
    judge_model doesn't match settings.judge_probe_model_key (fail loud if
    JUDGE_PROBE_PATH is pointing at an unexpected pickle file, per
    CLAUDE.md). This does NOT verify the probe is actually trained for
    whatever model JUDGE_MODEL currently names — see
    JUDGE_PROBE_MODEL_KEY's description for why that pairing can't be
    checked automatically and remains the operator's responsibility.
    """
    missing = [
        name
        for name, val in (
            ("JUDGE_MODEL", settings.judge_model),
            ("JUDGE_PROBE_PATH", settings.judge_probe_path),
            ("JUDGE_PROBE_MODEL_KEY", settings.judge_probe_model_key),
            ("JUDGE_INSTRUCTIONS_PROMPT", settings.judge_instructions_prompt),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(f"{', '.join(missing)} must be configured to run judgement.")
    assert settings.judge_model is not None
    assert settings.judge_probe_path is not None
    assert settings.judge_probe_model_key is not None
    assert settings.judge_instructions_prompt is not None

    import joblib
    import torch

    judge = JudgementLM(
        model_name=settings.judge_model,
        sampling_params={
            "do_sample": False,
            "max_new_tokens": settings.judge_max_new_tokens,
        },
        nnsight_kwargs={"torch_dtype": getattr(torch, settings.judge_dtype)},
        use_chat_template=settings.judge_use_chat_template,
    )

    probe_data: dict[str, Any] = joblib.load(settings.judge_probe_path)
    # probe_data["judge_model"] is the probe's self-identified scholarlm
    # registry key (e.g. "qwen-2.5-7b"), not a HuggingFace model id — it is
    # NOT directly comparable to settings.judge_model. This check only
    # confirms JUDGE_PROBE_PATH points at the pickle JUDGE_PROBE_MODEL_KEY
    # says it should — it cannot verify that probe is actually trained for
    # whatever model JUDGE_MODEL currently names (see JUDGE_PROBE_MODEL_KEY's
    # description). Logging both below so the actual pairing used for a run
    # is always visible in log.txt, not just asserted.
    if probe_data["judge_model"] != settings.judge_probe_model_key:
        raise RuntimeError(
            f"JUDGE_PROBE_PATH's probe self-identifies as judge_model="
            f"{probe_data['judge_model']!r}, but JUDGE_PROBE_MODEL_KEY="
            f"{settings.judge_probe_model_key!r}. JUDGE_PROBE_PATH is "
            f"probably pointing at the wrong probe file."
        )
    log.info(
        "judge_probe_loaded",
        judge_model=settings.judge_model,
        probe_path=settings.judge_probe_path,
        probe_judge_model_key=probe_data["judge_model"],
        probe_dataset=probe_data.get("dataset"),
    )

    attribution_methods: dict[str, AttributionMethod] = {
        "contrastive_gradient": ContrastiveGradientAttribution(judge),
        "probe": ProbeAttribution(judge, probe_data),
    }
    return JudgeComponents(
        judge=judge,
        attribution_methods=attribution_methods,
        instructions_prompt=settings.judge_instructions_prompt,
    )
