"""Extraction adapters — thin interfaces between the workers and the native
OCR/extraction pipeline (``coastal_crawler.extraction``).

Two independent adapters, one per pipeline stage: ``OCRAdapter`` (wraps
``OCRLM``, used by ``ocr_worker.py``) and ``MeasurementAdapter`` (wraps
``ExtractionLM``, used by ``worker.py``). Each worker depends only on its
own adapter protocol; the real pipeline calls live here. Swap in the Stub
adapters for tests; use the Direct adapters for production.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel

from coastal_crawler.extraction import ExtractionLM, OCRLM

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
    """Calls OCRLM to turn PDFs into OCR text."""

    def __init__(self, doc_lm: OCRLM) -> None:
        self.doc_lm = doc_lm

    def ocr_batch(self, pdf_paths: list[Path]) -> list[str]:
        return self.doc_lm.fit([str(p) for p in pdf_paths])


def build_ocr_adapter(settings: "Settings") -> DirectOCRAdapter:
    """Construct the production DirectOCRAdapter from Settings.

    Raises RuntimeError if DOC_LM_MODEL is missing (mirrors
    relevance_filter.run_filter()'s guard for FILTER_MODEL).
    """
    if not settings.doc_lm_model:
        raise RuntimeError("DOC_LM_MODEL must be configured to run OCR.")

    doc_lm = OCRLM(
        model_name=settings.doc_lm_model,
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
    Calls ExtractionLM and converts raw dicts to ExtractionResult.

    ``lat_field`` / ``lon_field`` name the entity-schema fields that hold
    geographic coordinates.  Set to None if your schema has no coordinates.
    """

    def __init__(
        self,
        meas_lm: ExtractionLM,
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
        # ExtractionLM.fit() → one entry per document, in the same order as
        # ocr_texts: either a list of measurement dicts (keys: value, units,
        # attribute, entity_id, context, document_id, and all entity/event
        # schema fields — no provenance fields such as page_number/
        # table_number, since this ablation makes a single LLM call per
        # document), or a str error message if extraction failed for that
        # document specifically.
        raw: list[list[dict[str, Any]] | str] = self.meas_lm.fit(ocr_texts)

        return [
            entry if isinstance(entry, str) else [self._to_result(record) for record in entry] for entry in raw
        ]

    def _to_result(self, record: dict[str, Any]) -> ExtractionResult:
        provenance = {
            "page_number": record.get("page_number"),
            "table_number": record.get("table_number"),
            "row_index": record.get("row_index"),
            "column_index": record.get("column_index"),
            "source": record.get("source"),
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

    from coastal_crawler.measurement_schema import DirectExtractionSchema, build_direct_extraction_prompt

    meas_lm = ExtractionLM(
        model_name=settings.meas_lm_model,
        direct_extraction_schema=DirectExtractionSchema,
        direct_extraction_prompt=build_direct_extraction_prompt(settings.meas_lm_entity_identification_prompt),
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
