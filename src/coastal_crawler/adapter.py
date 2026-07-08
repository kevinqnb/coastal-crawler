"""Extraction adapter — thin interface between the worker and the native
OCR/extraction pipeline (``coastal_crawler.extraction``).

The worker depends only on ``ExtractionAdapter``; the real pipeline call
lives here. Swap in ``StubAdapter`` for tests; use ``DirectExtractionAdapter``
for production.
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


@runtime_checkable
class ExtractionAdapter(Protocol):
    """Interface the worker calls. Lives in one place so it is easy to mock."""

    def extract(self, pdf_path: Path) -> list[ExtractionResult]:
        """Extract structured measurements from a PDF.

        Args:
            pdf_path: Path to a downloaded PDF file.

        Returns:
            One ``ExtractionResult`` per extracted measurement.
        """
        ...


class StubAdapter:
    """Returns empty results — usable in tests without a GPU or vLLM endpoint."""

    def extract(self, pdf_path: Path) -> list[ExtractionResult]:
        return []


# ---------------------------------------------------------------------------
# DirectExtractionAdapter — wires OCRLM (OCR) + ExtractionLM (single-call
# direct extraction). build_extraction_adapter() below constructs the
# production instance from Settings; see that function for the real wiring.
# ---------------------------------------------------------------------------
class DirectExtractionAdapter:
    """
    Calls OCRLM then ExtractionLM and converts raw dicts to ExtractionResult.

    ``lat_field`` / ``lon_field`` name the entity-schema fields that hold
    geographic coordinates.  Set to None if your schema has no coordinates.
    """

    def __init__(
        self,
        doc_lm: OCRLM,
        meas_lm: ExtractionLM,
        schema_name: str,
        model_version: str,
        lat_field: str | None = None,
        lon_field: str | None = None,
    ) -> None:
        self.doc_lm = doc_lm
        self.meas_lm = meas_lm
        self.schema_name = schema_name
        self.model_version = model_version
        self.lat_field = lat_field
        self.lon_field = lon_field

    def extract(self, pdf_path: Path) -> list[ExtractionResult]:
        # Step 1: OCR via OCRLM.fit() → list of OCR strings, one per PDF.
        ocr_texts: list[str] = self.doc_lm.fit([str(pdf_path)])

        # Step 2: Extraction via ExtractionLM.fit() → list of measurement
        # dicts. Each dict has keys: value, units, attribute, entity_id,
        # context, and all entity/event schema fields. No provenance fields
        # (page_number, table_number, etc.) are produced — this ablation
        # makes a single LLM call per document.
        raw: list[dict[str, Any]] = self.meas_lm.fit(ocr_texts)

        results: list[ExtractionResult] = []
        for record in raw:
            provenance = {
                "page_number": record.get("page_number"),
                "table_number": record.get("table_number"),
                "row_index": record.get("row_index"),
                "column_index": record.get("column_index"),
                "source": record.get("source"),
                "context": record.get("context"),
            }
            results.append(
                ExtractionResult(
                    schema_name=self.schema_name,
                    model_version=self.model_version,
                    data=record,
                    # STUB: wire in a real confidence score if ExtractionLM exposes one.
                    confidence=None,
                    provenance=provenance,
                    latitude=record.get(self.lat_field) if self.lat_field else None,
                    longitude=record.get(self.lon_field) if self.lon_field else None,
                )
            )
        return results


def build_extraction_adapter(settings: "Settings") -> DirectExtractionAdapter:
    """Construct the production DirectExtractionAdapter from Settings.

    Raises RuntimeError if required doc_lm_*/meas_lm_* settings are missing
    (mirrors relevance_filter.run_filter()'s guard for FILTER_MODEL/
    FILTER_RELEVANCE_PROMPT).
    """
    missing = [
        name
        for name, val in (
            ("DOC_LM_MODEL", settings.doc_lm_model),
            ("MEAS_LM_MODEL", settings.meas_lm_model),
            ("MEAS_LM_ENTITY_IDENTIFICATION_PROMPT", settings.meas_lm_entity_identification_prompt),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(f"{', '.join(missing)} must be configured to run extraction.")
    # Narrow str | None -> str for the type checker; the guard above already
    # verified these are non-empty at runtime.
    assert settings.doc_lm_model is not None
    assert settings.meas_lm_model is not None
    assert settings.meas_lm_entity_identification_prompt is not None

    from coastal_crawler.measurement_schema import DirectExtractionSchema, build_direct_extraction_prompt

    doc_lm = OCRLM(
        model_name=settings.doc_lm_model,
        api_base=settings.doc_lm_base_url,
        api_key=settings.doc_lm_api_key,
    )
    meas_lm = ExtractionLM(
        model_name=settings.meas_lm_model,
        direct_extraction_schema=DirectExtractionSchema,
        direct_extraction_prompt=build_direct_extraction_prompt(settings.meas_lm_entity_identification_prompt),
        api_base=settings.meas_lm_base_url,
        api_key=settings.meas_lm_api_key,
    )
    return DirectExtractionAdapter(
        doc_lm=doc_lm,
        meas_lm=meas_lm,
        schema_name=settings.extraction_schema_name,
        model_version=(
            settings.extraction_model_version
            or f"doc_lm={settings.doc_lm_model}+meas_lm={settings.meas_lm_model}"
        ),
        lat_field=settings.extraction_lat_field,
        lon_field=settings.extraction_lon_field,
    )
