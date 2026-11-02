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
from scholarlm import DocumentLM, MeasurementLM, TableCleaner
from scholarlm.attribution import AttributionMethod, ContrastiveGradientAttribution, ProbeAttribution
from scholarlm.instruction_prompts import DIRECT_TRIPLE_EXTRACTION_INSTRUCTIONS
from scholarlm.judgementlm import JudgementLM
from scholarlm.measurementlm import ContextLengthExceededError

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
        # unknown_label_policy="coerce" recovers a document instead of failing
        # it, but a recovered document is not a clean one — log every
        # coercion so it stays visible in this run's logs (see
        # Settings.doc_lm_unknown_label_policy).
        for doc_idx, labels in self.doc_lm.coerced_labels.items():
            log.warning(
                "ocr_unknown_labels_coerced",
                pdf_path=str(pdf_paths[doc_idx]),
                labels=labels,
            )
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
        unknown_label_policy=settings.doc_lm_unknown_label_policy,
    )
    return DirectOCRAdapter(doc_lm=doc_lm)


# ---------------------------------------------------------------------------
# Table-clean stage — OCR text + page images in, normalized OCR text out
# ---------------------------------------------------------------------------

@runtime_checkable
class TableCleanAdapter(Protocol):
    """Interface the table-clean worker calls. Lives in one place so it is easy to mock."""

    @property
    def context_length_exceeded_docs(self) -> set[int]:
        """Batch-local indices (positions into the most recent
        clean_tables_batch() call's documents) of documents where a table
        page hit the server's context-length limit — see
        DirectTableCleanAdapter. Declared as a read-only property (rather
        than a plain attribute, as MeasurementAdapter.truncated_docs is) so
        DirectTableCleanAdapter can implement it as a computed
        pass-through to the underlying MeasurementLM instance."""
        ...

    def clean_tables_batch(self, documents: list[str], processed_pdf_dirs: list[str]) -> list[str]:
        """Normalize tables in a batch of already-OCR'd documents.

        Args:
            documents: OCR text strings (with <page number="N">/<table
                number="M"> tags, as written by DocumentLM.fit()), one per
                document.
            processed_pdf_dirs: Paths to pre-rendered page-image directories,
                one per document, each containing ``{page_index}.b64`` files
                (0-indexed, matching the <page number="N"> numbering).

        Returns:
            One cleaned OCR text string per input document, same order and
            length as ``documents``.
        """
        ...


class StubTableCleanAdapter:
    """Returns documents unchanged — usable in tests without a GPU or vLLM endpoint."""

    def __init__(self) -> None:
        self.context_length_exceeded_docs: set[int] = set()

    def clean_tables_batch(self, documents: list[str], processed_pdf_dirs: list[str]) -> list[str]:
        return list(documents)


class DirectTableCleanAdapter:
    """Calls scholarlm's standalone ``TableCleaner.clean`` (scholarlm >= d40846c).

    Before that commit table cleaning was the private
    ``MeasurementLM._clean_tables``; ``TableCleaner.clean`` has the same inputs,
    outputs, prompt (``CLEAN_TABLE_INSTRUCTIONS`` is byte-identical) and
    call parameters, so cleaned text is expected to match earlier runs.
    """

    def __init__(self, cleaner: TableCleaner) -> None:
        self.cleaner = cleaner

    @property
    def context_length_exceeded_docs(self) -> set[int]:
        # scholarlm ships no py.typed marker, so this attribute is Any to
        # mypy — cast to the type BatchLLMBase.__init__ actually assigns
        # (self.context_length_exceeded_docs: set[int] = set()).
        return cast("set[int]", self.cleaner.context_length_exceeded_docs)

    def clean_tables_batch(self, documents: list[str], processed_pdf_dirs: list[str]) -> list[str]:
        # TableCleaner only ever adds to this set; reset it ourselves or a
        # context-length failure from a previous batch would leak into this
        # one's worker-side fail-loud check.
        self.cleaner.context_length_exceeded_docs = set()
        result: list[str] = self.cleaner.clean(documents, processed_pdf_dirs)
        return result


def build_table_clean_adapter(settings: "Settings") -> DirectTableCleanAdapter:
    """Construct the production DirectTableCleanAdapter from Settings.

    Raises RuntimeError if TABLE_CLEAN_MODEL is missing (mirrors
    build_ocr_adapter's guard for DOC_LM_MODEL).

    ``TableCleaner.output_dir`` is left unset: the worker
    (table_clean_worker.py) writes cleaned text to disk itself, atomically.

    table_clean_max_concurrent is intentionally NOT a Settings field:
    ``TableCleaner.clean`` hardcodes max_concurrent=2 in its own _call_batch
    call, ignoring the constructor's max_concurrent for this code path — a
    TABLE_CLEAN_MAX_CONCURRENT setting would silently do nothing.
    """
    if not settings.table_clean_model:
        raise RuntimeError("TABLE_CLEAN_MODEL must be configured to run table cleaning.")

    cleaner = TableCleaner(
        model_name=settings.table_clean_model,
        sampling_params={
            "temperature": settings.table_clean_temperature,
            "top_p": settings.table_clean_top_p,
            "top_k": settings.table_clean_top_k,
            "repetition_penalty": settings.table_clean_repetition_penalty,
            "enable_thinking": settings.table_clean_enable_thinking,
        },
        api_base=settings.table_clean_base_url,
        api_key=settings.table_clean_api_key,
    )
    return DirectTableCleanAdapter(cleaner=cleaner)


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


#: Completion tokens scholarlm's direct-extraction call always requests
#: (``max_tokens=32768`` hardcoded in ``MeasurementLM._extract_triples``,
#: scholarlm/measurementlm.py). vLLM rejects a request whose prompt plus
#: requested completion exceeds ``--max-model-len``, so the truncation
#: backstop must leave this much of the context window free. Not tunable from
#: here; tests/test_adapter.py asserts it still matches the installed
#: scholarlm so a pin bump that changes it fails loudly.
SCHOLARLM_EXTRACT_MAX_TOKENS = 32768


#: ``response_format["json_schema"]["name"]`` of the per-document extraction
#: call in scholarlm's ``MeasurementLM._extract_triples``; the other steps use
#: ``standardize_response`` and ``parse_quantity_response``. Asserted against
#: the installed scholarlm in tests/test_adapter.py.
DIRECT_EXTRACTION_FORMAT_NAME = "direct_extraction_list"


def _format_name(response_format: dict[str, Any] | None) -> str:
    if response_format is None:
        return "<no response_format>"
    return str(response_format["json_schema"]["name"])


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
        reserved_completion_tokens: int,
        lat_field: str | None = None,
        lon_field: str | None = None,
    ) -> None:
        if max_model_len <= reserved_completion_tokens:
            raise ValueError(
                f"max_model_len={max_model_len} leaves no room for a prompt after "
                f"reserving reserved_completion_tokens={reserved_completion_tokens} "
                f"for the completion."
            )
        self.meas_lm = meas_lm
        self.schema_name = schema_name
        self.model_version = model_version
        self.tokenizer = tokenizer
        self.max_model_len = max_model_len
        self.reserved_completion_tokens = reserved_completion_tokens
        self.lat_field = lat_field
        self.lon_field = lon_field
        # scholarlm's _call_batch retries a response that is empty or fails
        # its validator, and after the last retry hands back the bad response
        # anyway; _extract_triples then substitutes an empty item list (a
        # print, no exception), so a paper whose extraction call never
        # produced a valid response is indistinguishable from one with
        # nothing to extract. This wrapper re-applies _call_batch's own
        # failure test to each final response and records which calls
        # exhausted their retries, per step.
        self.exhausted_calls: dict[str, set[int]] = {}
        self._call_batch_original = meas_lm._call_batch
        meas_lm._call_batch = self._recording_call_batch
        # Batch-local indices truncated by the backstop in the most recent
        # extract_batch() call — see MeasurementAdapter's docstring.
        self.truncated_docs: set[int] = set()

    def _recording_call_batch(
        self, message_sets: list[Any], *args: Any, **kwargs: Any
    ) -> list[Any]:
        results: list[Any] = self._call_batch_original(message_sets, *args, **kwargs)
        assert len(results) == len(message_sets), (
            f"_call_batch returned {len(results)} responses for {len(message_sets)} requests"
        )
        validator = kwargs.get("validator")
        name = _format_name(kwargs.get("response_format"))
        for i, resp in enumerate(results):
            if isinstance(resp, ContextLengthExceededError):
                continue  # tracked separately via context_length_exceeded_docs
            exhausted = not resp
            if not exhausted and validator is not None:
                try:
                    validator(resp)
                except Exception:  # noqa: BLE001 - same failure test _call_batch applies; recorded, surfaced in extract_batch
                    exhausted = True
            if exhausted:
                self.exhausted_calls.setdefault(name, set()).add(i)
        return results

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

        prompt_token_limit = self.max_model_len - self.reserved_completion_tokens
        processed_texts: list[str] = []
        for i, text in enumerate(ocr_texts):
            truncated_text, was_truncated = _truncate_context_to_budget(
                self.tokenizer, direct_extraction_prompt, text, prompt_token_limit
            )
            if was_truncated:
                self.truncated_docs.add(i)
                log.warning(
                    "document_truncated_for_extraction",
                    batch_index=i,
                    original_tokens=len(self.tokenizer.encode(text, add_special_tokens=False)),
                    max_model_len=self.max_model_len,
                    prompt_token_limit=prompt_token_limit,
                )
            processed_texts.append(truncated_text)

        self.exhausted_calls = {}
        raw: list[dict[str, Any]] = self.meas_lm.fit(processed_texts)
        extraction_exhausted = self.exhausted_calls.get(DIRECT_EXTRACTION_FORMAT_NAME, set())
        for step, indices in self.exhausted_calls.items():
            if step != DIRECT_EXTRACTION_FORMAT_NAME:
                # Indices here are per-step request positions, not document
                # ids. Standardize failures leave original units; parse
                # failures leave the seven parsed keys absent (the warehouse
                # skips and logs those as missing_parse).
                log.warning(
                    "extraction_followup_calls_exhausted_retries",
                    step=step,
                    count=len(indices),
                )

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
            if i in extraction_exhausted:
                assert not records, (
                    f"document {i} has {len(records)} records although its extraction "
                    f"call exhausted retries; batch positions no longer line up"
                )
                results.append(
                    f"MeasurementLM extraction call for document {i} exhausted its "
                    f"retries without a valid response (empty or failed validation); "
                    f"zero records extracted."
                )
                continue
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
        direct_extraction_schema=DirectExtractionSchema,
        direct_extraction_prompt=build_direct_extraction_prompt(settings.meas_lm_entity_identification_prompt),
        collect_attribute_terms=False,
        parse_quantities_context=settings.meas_lm_parse_quantities_context,
        standardize_context=settings.meas_lm_standardize_context,
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
        reserved_completion_tokens=SCHOLARLM_EXTRACT_MAX_TOKENS,
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


# ---------------------------------------------------------------------------
# Representation stage — term embeddings via scholarlm.RepresentationLM
# ---------------------------------------------------------------------------

def parse_layers(csv: str) -> list[int]:
    """Parse REPR_LM_LAYERS ("0,32") into a sorted, duplicate-free int list; raises on junk."""
    layers = [int(x) for x in csv.split(",") if x.strip()]
    if not layers or len(set(layers)) != len(layers) or any(L < 0 for L in layers):
        raise ValueError(f"invalid REPR_LM_LAYERS {csv!r}")
    return sorted(layers)


def parse_context_radius(value: str) -> int | None:
    """Parse REPR_LM_CONTEXT_RADIUS: the literal ``none`` (whole document) or an int >= 0."""
    if value.strip().lower() == "none":
        return None
    radius = int(value)
    if radius < 0:
        raise ValueError(f"invalid REPR_LM_CONTEXT_RADIUS {value!r}")
    return radius


@dataclass
class ReprResult:
    """Term-occurrence embeddings for ONE document, as parallel arrays.

    ``representations`` maps layer index -> ``[n, hidden]`` array (float32 from
    the real model), every layer with the same ``n`` rows in the same order as
    the provenance arrays. ``n`` may be 0 — a paper that never mentions any
    term is a legitimate result, not an error.

    ``char_starts``/``char_ends``/``window_starts``/``window_ends`` index the
    CLEANED text (``cleaned_sha256`` identifies it), not the file on disk.
    ``token_indices``/``token_first_indices`` are relative to the forward pass's
    window. ``n_truncated`` is always 0 for bidirectional models (overflow raises);
    the worker treats nonzero as a failure.
    """

    representations: dict[int, Any]  # layer -> np.ndarray [n, hidden]
    labels: Any  # np.ndarray[str] [n]
    char_starts: Any  # np.ndarray[int64] [n]
    char_ends: Any  # np.ndarray[int64] [n]
    token_indices: Any  # np.ndarray[int64] [n], last pooled token, window-relative
    token_first_indices: Any  # np.ndarray[int64] [n], first pooled token, window-relative
    n_pooled_tokens: Any  # np.ndarray[int64] [n]
    paragraph_indices: Any  # np.ndarray[int64] [n]
    window_starts: Any  # np.ndarray[int64] [n]
    window_ends: Any  # np.ndarray[int64] [n]
    n_truncated: int
    hidden_size: int
    cleaned_sha256: str
    reference_heading_found: bool | None  # None iff references are not removed
    kept_fraction_after_references: float | None


@dataclass(frozen=True)
class SkipReason:
    """Why a document cannot be run: ``kind`` is ``clean_error`` or ``overflow``."""

    kind: str
    detail: str


def window_units(
    cleaned: str, occurrences: list[tuple[str, int, int]], context_radius: int | None
) -> list[tuple[tuple[int, int], list[tuple[str, int, int, int]]]]:
    """Group occurrences into forward-pass units exactly as ``RepresentationLM.collect`` does.

    Returns ``[((window_start, window_end), [(term, start, end, paragraph_index)])]``.
    """
    from scholarlm.representationlm import paragraph_of_span, split_paragraphs, window_span

    paragraphs = split_paragraphs(cleaned)
    assert paragraphs, "occurrences but no paragraphs"
    occ_para = [(t, s, e, paragraph_of_span(paragraphs, s, e)) for t, s, e in occurrences]
    if context_radius is None:
        return [((0, len(cleaned)), occ_para)]
    by_para: dict[int, list[tuple[str, int, int, int]]] = {}
    for o in occ_para:
        by_para.setdefault(o[3], []).append(o)
    return [(window_span(paragraphs, pi, context_radius), occs) for pi, occs in sorted(by_para.items())]


@runtime_checkable
class ReprAdapter(Protocol):
    """Collects embeddings of ``terms`` for one document at ``layers``."""

    hidden_size: int
    layers: list[int]

    def collect(self, doc_id: str, text: str, terms: list[str]) -> ReprResult: ...

    def skip_reason(self, doc_id: str, text: str, terms: list[str]) -> SkipReason | None:
        """Why ``collect`` would raise for this document (cleaning error or over-length window), else None."""
        ...


class StubReprAdapter:
    """Deterministic fake for tests. Cleans the text and matches terms on the
    CLEANED text with scholarlm's own helpers (as RepresentationLM does), forms
    the same paragraph windows, and emits tiny hand-checkable embeddings
    (value = char_start/1000 + layer). "Tokens" are whitespace-separated words,
    so ``max_tokens`` lets tests exercise the overflow predicate without a model."""

    def __init__(
        self,
        hidden_size: int = 4,
        layers: list[int] | None = None,
        context_radius: int | None = None,
        max_tokens: int | None = None,
        remove_references: bool = False,
    ) -> None:
        from scholarlm.representationlm import CleaningConfig

        self.hidden_size = hidden_size
        self.layers = [0, 32] if layers is None else layers
        self.context_radius = context_radius
        self.max_tokens = max_tokens
        self._cleaning = CleaningConfig(remove_references=remove_references)

    def _clean(self, text: str) -> tuple[str, dict]:
        from scholarlm.representationlm import clean_ocr_text

        return clean_ocr_text(text, self._cleaning)

    def skip_reason(self, doc_id: str, text: str, terms: list[str]) -> SkipReason | None:
        from scholarlm.representationlm import find_key_term_occurrences

        try:
            cleaned, _ = self._clean(text)
        except (AssertionError, ValueError) as e:
            return SkipReason("clean_error", str(e))
        occ = find_key_term_occurrences(cleaned, terms)
        if not occ or self.max_tokens is None:
            return None
        for (a, b), _ in window_units(cleaned, occ, self.context_radius):
            n = len(cleaned[a:b].split())
            if n > self.max_tokens:
                return SkipReason("overflow", f"window [{a}, {b}) has {n} tokens > {self.max_tokens}")
        return None

    def collect(self, doc_id: str, text: str, terms: list[str]) -> ReprResult:
        import hashlib

        import numpy as np
        from scholarlm.representationlm import find_key_term_occurrences

        cleaned, info = self._clean(text)
        occ = find_key_term_occurrences(cleaned, terms)
        n = len(occ)
        starts = np.asarray([s for _, s, _ in occ], dtype=np.float32)
        reps = {
            L: np.repeat((starts / 1000.0 + L)[:, None], self.hidden_size, axis=1).astype(np.float32)
            for L in self.layers
        }
        win: dict[tuple[int, int], tuple[int, int]] = {}
        para_of: dict[tuple[int, int], int] = {}
        if n:
            for (a, b), occs in window_units(cleaned, occ, self.context_radius):
                for _, s, e, pi in occs:
                    win[(s, e)] = (a, b)
                    para_of[(s, e)] = pi
        return ReprResult(
            representations=reps,
            labels=np.asarray([t for t, _, _ in occ], dtype="U"),
            char_starts=np.asarray([s for _, s, _ in occ], dtype=np.int64),
            char_ends=np.asarray([e for _, _, e in occ], dtype=np.int64),
            token_indices=np.arange(n, dtype=np.int64),
            token_first_indices=np.arange(n, dtype=np.int64),
            n_pooled_tokens=np.ones(n, dtype=np.int64),
            paragraph_indices=np.asarray([para_of[(s, e)] for _, s, e in occ], dtype=np.int64),
            window_starts=np.asarray([win[(s, e)][0] for _, s, e in occ], dtype=np.int64),
            window_ends=np.asarray([win[(s, e)][1] for _, s, e in occ], dtype=np.int64),
            n_truncated=0,
            hidden_size=self.hidden_size,
            cleaned_sha256=hashlib.sha256(cleaned.encode("utf-8")).hexdigest(),
            reference_heading_found=info["reference_heading_found"],
            kept_fraction_after_references=info["kept_fraction_after_references"],
        )


class DirectReprAdapter:
    """Wraps ``scholarlm.RepresentationLM`` (needs scholarlm >= 95bde63: bidirectional
    backend, cleaning, paragraph windows, mean pooling)."""

    def __init__(self, model: Any) -> None:
        self._model = model
        self.hidden_size: int = model.hidden_size
        self.layers: list[int] = list(model.layers)

    def skip_reason(self, doc_id: str, text: str, terms: list[str]) -> SkipReason | None:
        from scholarlm.representationlm import find_key_term_occurrences

        try:
            cleaned_docs, _ = self._model.prepare_documents({doc_id: text})
        except (AssertionError, ValueError) as e:
            return SkipReason("clean_error", str(e))
        cleaned = cleaned_docs[doc_id]
        occ = find_key_term_occurrences(cleaned, terms)
        if not occ:
            return None
        limit = self._model.max_position_embeddings
        for (a, b), _ in window_units(cleaned, occ, self._model.context_radius):
            input_ids, _ = self._model._tokenize(cleaned[a:b])
            if len(input_ids) > limit:
                return SkipReason("overflow", f"window [{a}, {b}) has {len(input_ids)} tokens > {limit}")
        return None

    def collect(self, doc_id: str, text: str, terms: list[str]) -> ReprResult:
        import numpy as np
        from scholarlm.representationlm import find_key_term_occurrences

        # RepresentationLM.collect() raises when NO term occurs in the CLEANED
        # document ("nothing to collect"). For a single paper that is a legitimate
        # zero-occurrence result, so detect it up front on the same cleaned text
        # with the same matcher (a term only inside a table or the references
        # would otherwise crash the run), rather than catching the RuntimeError.
        cleaned_docs, infos = self._model.prepare_documents({doc_id: text})
        info = infos[doc_id]
        meta = {
            "cleaned_sha256": info["cleaned_sha256"],
            "reference_heading_found": info["reference_heading_found"],
            "kept_fraction_after_references": info["kept_fraction_after_references"],
        }
        if not find_key_term_occurrences(cleaned_docs[doc_id], terms):
            empty_i = np.asarray([], dtype=np.int64)
            return ReprResult(
                representations={L: np.zeros((0, self.hidden_size), dtype=np.float32) for L in self.layers},
                labels=np.asarray([], dtype="U"),
                char_starts=empty_i, char_ends=empty_i, token_indices=empty_i,
                token_first_indices=empty_i, n_pooled_tokens=empty_i,
                paragraph_indices=empty_i, window_starts=empty_i, window_ends=empty_i,
                n_truncated=0, hidden_size=self.hidden_size, **meta,
            )
        out = self._model.collect({doc_id: text}, terms)
        assert sorted(out["representations"]) == self.layers, (sorted(out["representations"]), self.layers)
        assert self._model.doc_info[doc_id]["cleaned_sha256"] == meta["cleaned_sha256"]
        return ReprResult(
            representations=out["representations"],
            labels=out["labels"],
            char_starts=out["char_starts"],
            char_ends=out["char_ends"],
            token_indices=out["token_indices"],
            token_first_indices=out["token_first_indices"],
            n_pooled_tokens=out["n_pooled_tokens"],
            paragraph_indices=out["paragraph_indices"],
            window_starts=out["window_starts"],
            window_ends=out["window_ends"],
            n_truncated=int(self._model.n_truncated),
            hidden_size=self.hidden_size,
            **meta,
        )


def build_repr_adapter(settings: "Settings") -> DirectReprAdapter:
    """Construct the production ReprAdapter from Settings.

    Every RepresentationLM option is read from ``REPR_LM_*`` and a missing one is a
    RuntimeError (no defaults). Refuses to run without CUDA when the device is a
    CUDA device, and always passes ``overflow_policy="error"``: over-length input
    must be excluded up front (``seed_repr``), never truncated.
    """
    import torch
    from scholarlm.representationlm import CleaningConfig, RepresentationLM

    s = settings
    required = {
        "REPR_LM_MODEL": s.repr_lm_model,
        "REPR_LM_ARCHITECTURE": s.repr_lm_architecture,
        "REPR_LM_POOLING": s.repr_lm_pooling,
        "REPR_LM_CONTEXT_RADIUS": s.repr_lm_context_radius,
        "REPR_LM_REMOVE_REFERENCES": s.repr_lm_remove_references,
        "REPR_LM_REMOVE_TABLES": s.repr_lm_remove_tables,
        "REPR_LM_REMOVE_PAGE_TAGS": s.repr_lm_remove_page_tags,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise RuntimeError(f"{', '.join(missing)} must be configured to run representation collection.")
    if s.repr_lm_remove_references and s.repr_lm_min_kept_fraction is None:
        raise RuntimeError("REPR_LM_MIN_KEPT_FRACTION must be set when REPR_LM_REMOVE_REFERENCES is true.")
    if not s.repr_lm_remove_references and s.repr_lm_min_kept_fraction is not None:
        raise RuntimeError("REPR_LM_MIN_KEPT_FRACTION is set but REPR_LM_REMOVE_REFERENCES is false.")

    cleaning = CleaningConfig(
        remove_references=bool(s.repr_lm_remove_references),
        remove_tables=bool(s.repr_lm_remove_tables),
        remove_page_tags=bool(s.repr_lm_remove_page_tags),
        min_kept_fraction_after_references=s.repr_lm_min_kept_fraction,
    )
    common: dict[str, Any] = dict(
        model_name=s.repr_lm_model,
        architecture=s.repr_lm_architecture,
        layers=parse_layers(s.repr_lm_layers),
        pooling=s.repr_lm_pooling,
        cleaning=cleaning,
        context_radius=parse_context_radius(cast(str, s.repr_lm_context_radius)),
        overflow_policy="error",
    )
    dtype = getattr(torch, s.repr_lm_dtype)
    if s.repr_lm_architecture == "bidirectional":
        if not s.repr_lm_device or not s.repr_lm_attn_implementation or not s.repr_lm_hf_cache_dir:
            raise RuntimeError(
                "bidirectional repr needs REPR_LM_DEVICE, REPR_LM_ATTN_IMPLEMENTATION and REPR_LM_HF_CACHE_DIR."
            )
        if s.repr_lm_device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("repr stage requires a CUDA GPU; torch.cuda.is_available() is False.")
        common.update(
            device=s.repr_lm_device,
            hf_cache_dir=s.repr_lm_hf_cache_dir,
            hf_kwargs={"torch_dtype": dtype, "attn_implementation": s.repr_lm_attn_implementation},
        )
    else:
        if not torch.cuda.is_available():
            raise RuntimeError("repr stage requires a CUDA GPU; torch.cuda.is_available() is False.")
        common.update(nnsight_kwargs={"torch_dtype": dtype})
    torch.manual_seed(s.repr_lm_seed)
    return DirectReprAdapter(RepresentationLM(**common))
