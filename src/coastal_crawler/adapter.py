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
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

import structlog
from pydantic import BaseModel
from scholarlm import DocumentLM, MeasurementLM
from scholarlm.attribution import AttributionMethod, ContrastiveGradientAttribution, ProbeAttribution
from scholarlm.instruction_prompts import DIRECT_TRIPLE_EXTRACTION_INSTRUCTIONS
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
        drop_references=True,
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

    #: Batch-local indices (positions into the most recent extract_batch()
    #: call's ocr_texts) of documents whose context was truncated by the
    #: tokenizer-based backstop — see DirectMeasurementAdapter.extract_batch.
    #: Read by worker.py after each call to log a paper_id-scoped warning
    #: (adapter.py has no paper identity, only batch position).
    truncated_docs: set[int]

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

    def __init__(self) -> None:
        self.truncated_docs: set[int] = set()

    def extract_batch(self, ocr_texts: list[str]) -> list[DocumentOutcome]:
        return [[] for _ in ocr_texts]


@runtime_checkable
class _Tokenizer(Protocol):
    """The subset of a HuggingFace tokenizer's interface the truncation
    backstop needs. A structural Protocol (rather than importing
    transformers' own type, which ships incomplete stubs) so mypy strict
    checks the exact surface used here, and so tests can supply a fast fake
    instead of loading a real (slow) HF tokenizer where truncation
    correctness itself isn't what's under test."""

    def encode(self, text: str, add_special_tokens: bool = ...) -> list[int]: ...

    def decode(self, token_ids: list[int], skip_special_tokens: bool = ...) -> str: ...

    def apply_chat_template(
        self,
        conversation: list[dict[str, str]],
        tokenize: bool = ...,
        add_generation_prompt: bool = ...,
        return_dict: bool = ...,
    ) -> list[int]: ...


_DIRECT_EXTRACTION_QUERY = "Extract all measurement records from this document as described in the instructions."


def _direct_extraction_prompt(direct_extraction_prompt: str, context: str) -> str:
    """Reconstruct the exact prompt string scholarlm's MeasurementLM._extract_triples
    sends per document, so token-counting here matches what the server actually
    receives. Duplicated from scholarlm's own f-string assembly rather than
    imported — scholarlm doesn't expose this as a function — so this is an
    accepted, silently-driftable coupling: if scholarlm changes its template,
    this budget calculation goes stale with no test spanning both repos to
    catch it. See notes/coastal-crawler/builds/2026-08-20-extraction-hardening-01.md.
    """
    return (
        f"## INSTRUCTIONS:\n{DIRECT_TRIPLE_EXTRACTION_INSTRUCTIONS}\n\n"
        f"## DATASET SPECIFIC INSTRUCTIONS:\n{direct_extraction_prompt}\n\n"
        f"## CONTEXT:\n{context}\n\n## QUERY:\n{_DIRECT_EXTRACTION_QUERY}"
    )


def _count_prompt_tokens(tokenizer: _Tokenizer, direct_extraction_prompt: str, context: str) -> int:
    """Token count of the full chat-templated prompt scholarlm sends to the
    server for this (direct_extraction_prompt, context) pair. Uses
    apply_chat_template (not a plain encode()) so the count includes
    whatever role-wrapping tokens the server's own chat template adds —
    scholarlm sends this same content as a single user-role message via
    ``chat.completions.create`` — avoiding the need for a guessed safety
    margin to cover that overhead.

    ``return_dict=False`` is required: transformers >=5 defaults
    ``apply_chat_template`` to ``return_dict=True``, which (with
    ``tokenize=True``) returns a dict-like ``BatchEncoding`` whose ``len()``
    is the number of keys (2), not the token count — silently collapsing
    every prompt to "2 tokens" and disabling the truncation backstop
    entirely. ``return_dict=False`` returns the plain ``list[int]`` this
    count needs."""
    prompt = _direct_extraction_prompt(direct_extraction_prompt, context)
    return len(
        tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=True,
            add_generation_prompt=True,
            return_dict=False,
        )
    )


def _truncate_context_to_budget(
    tokenizer: _Tokenizer,
    direct_extraction_prompt: str,
    context: str,
    max_model_len: int,
) -> tuple[str, bool]:
    """Truncate `context` so the full prompt (template + context) fits within
    max_model_len tokens, if it doesn't already.

    Returns (possibly-truncated context, was_truncated).

    decode(encode(context)[:n]) is not guaranteed to re-encode to exactly n
    tokens (BPE merges can shift at the truncation boundary), so the initial
    slice is verified by re-counting the actual assembled prompt and shrunk
    further if it still overshoots, rather than trusting the arithmetic
    blindly (CLAUDE.md: fail loud, don't quietly ship a still-too-long
    prompt).
    """
    if _count_prompt_tokens(tokenizer, direct_extraction_prompt, context) <= max_model_len:
        return context, False

    overhead_tokens = _count_prompt_tokens(tokenizer, direct_extraction_prompt, "")
    budget = max_model_len - overhead_tokens
    if budget <= 0:
        raise ValueError(
            f"meas_lm_max_model_len={max_model_len} is smaller than the fixed "
            f"prompt-template overhead ({overhead_tokens} tokens) with an empty "
            f"document context — no truncation budget is possible."
        )

    context_ids = tokenizer.encode(context, add_special_tokens=False)
    truncated_ids = context_ids[:budget]

    for _ in range(5):
        truncated_context = tokenizer.decode(truncated_ids, skip_special_tokens=True)
        total = _count_prompt_tokens(tokenizer, direct_extraction_prompt, truncated_context)
        if total <= max_model_len:
            return truncated_context, True
        overshoot = total - max_model_len
        truncated_ids = truncated_ids[: max(0, len(truncated_ids) - overshoot - 16)]

    raise RuntimeError(
        f"Could not truncate document context to fit meas_lm_max_model_len="
        f"{max_model_len} after 5 attempts (last total={total} tokens)."
    )


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
        tokenizer: _Tokenizer,
        max_model_len: int,
        lat_field: str | None = None,
        lon_field: str | None = None,
    ) -> None:
        self.meas_lm = meas_lm
        self.schema_name = schema_name
        self.model_version = model_version
        self.tokenizer = tokenizer
        self.max_model_len = max_model_len
        self.lat_field = lat_field
        self.lon_field = lon_field
        # Batch-local indices truncated by the backstop in the most recent
        # extract_batch() call — see MeasurementAdapter's docstring.
        self.truncated_docs: set[int] = set()

    def extract_batch(self, ocr_texts: list[str]) -> list[DocumentOutcome]:
        # MeasurementLM.fit() in extraction_mode="direct" runs two LLM calls
        # per document (_extract_triples then _standardize), followed by
        # _deduplicate — and returns a FLAT list[dict] across all documents
        # combined (each record carries a `document_id` index back into
        # ocr_texts), not one entry per document. Regroup by document_id
        # before converting, so this adapter's own return shape (one
        # DocumentOutcome per input text, same order/length as ocr_texts)
        # stays what worker.py expects.
        self.truncated_docs = set()
        direct_extraction_prompt = self.meas_lm.direct_extraction_prompt
        assert direct_extraction_prompt is not None, (
            "meas_lm.direct_extraction_prompt is None — MeasurementLM was constructed "
            "without extraction_mode='direct' wiring; the truncation budget calculation "
            "needs the same prompt text the server will actually receive."
        )

        processed_texts: list[str] = []
        for i, text in enumerate(ocr_texts):
            truncated_text, was_truncated = _truncate_context_to_budget(
                self.tokenizer, direct_extraction_prompt, text, self.max_model_len
            )
            if was_truncated:
                self.truncated_docs.add(i)
                log.warning(
                    "document_truncated_for_extraction",
                    batch_index=i,
                    original_tokens=len(self.tokenizer.encode(text, add_special_tokens=False)),
                    max_model_len=self.max_model_len,
                )
            processed_texts.append(truncated_text)

        raw: list[dict[str, Any]] = self.meas_lm.fit(processed_texts)

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

        # context_length_exceeded_docs is populated from two different
        # places in scholarlm's fit(): a document that produced zero
        # records (a true total failure, from _extract_triples) and a
        # document whose records survive with un-standardized original
        # value/units (from _standardize, which starts from a copy of the
        # raw data and just skips the update on failure). Only the first
        # case is converted to a str failure here — the second keeps its
        # partial results (discarding them would throw away real data) but
        # gets a loud warning, since an un-standardized value/units pair
        # reaching the warehouse is its own quietly-wrong-number risk. See
        # notes/coastal-crawler/builds/2026-08-20-extraction-hardening-01.md.
        context_length_exceeded = self.meas_lm.context_length_exceeded_docs
        results: list[DocumentOutcome] = []
        for i in range(len(ocr_texts)):
            records = by_document[i]
            if i in context_length_exceeded:
                if not records:
                    results.append(
                        f"MeasurementLM context-length-exceeded for document {i} "
                        f"(truncated_by_backstop={i in self.truncated_docs}); zero "
                        f"records extracted."
                    )
                    continue
                log.warning(
                    "document_partially_standardized",
                    batch_index=i,
                    records=len(records),
                )
            results.append([self._to_result(record) for record in records])
        return results

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
    FILTER_RELEVANCE_PROMPT). MEAS_LM_MAX_MODEL_LEN is required here too
    (unlike its other use as an optional vLLM --max-model-len override) —
    the tokenizer-based truncation backstop needs a concrete token budget to
    enforce; a silently-disabled backstop would be exactly the kind of quiet
    wrong-result CLAUDE.md warns against. See
    notes/coastal-crawler/builds/2026-08-20-extraction-hardening-01.md.
    """
    missing = [
        name
        for name, val in (
            ("MEAS_LM_MODEL", settings.meas_lm_model),
            ("MEAS_LM_ENTITY_IDENTIFICATION_PROMPT", settings.meas_lm_entity_identification_prompt),
            ("MEAS_LM_MAX_MODEL_LEN", settings.meas_lm_max_model_len),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(f"{', '.join(missing)} must be configured to run extraction.")
    # Narrow str | None -> str for the type checker; the guard above already
    # verified these are non-empty at runtime.
    assert settings.meas_lm_model is not None
    assert settings.meas_lm_entity_identification_prompt is not None
    assert settings.meas_lm_max_model_len is not None

    from transformers import AutoTokenizer

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
    # Loaded once per process (~90s for a 120B-parameter model's tokenizer
    # from a warm HF cache) — the truncation backstop needs the real
    # tokenizer for MEAS_LM_MODEL, not a generic/approximate one, since
    # token counts must match what the served model will actually see.
    # cast: transformers' own return type is a backend union with
    # incomplete stubs — _Tokenizer names the exact structural surface used.
    tokenizer = cast("_Tokenizer", AutoTokenizer.from_pretrained(settings.meas_lm_model))
    return DirectMeasurementAdapter(
        meas_lm=meas_lm,
        schema_name=settings.extraction_schema_name,
        model_version=(
            settings.extraction_model_version
            or f"doc_lm={settings.doc_lm_model}+meas_lm={settings.meas_lm_model}"
        ),
        tokenizer=tokenizer,
        max_model_len=settings.meas_lm_max_model_len,
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
