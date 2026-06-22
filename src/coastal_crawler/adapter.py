"""Extraction adapter — thin interface between the worker and the scholarlm library.

The worker depends only on ``ExtractionAdapter``; the real library call lives here.
Swap in ``StubAdapter`` for tests; use ``ScholarlmAdapter`` for production.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


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
            One ``ExtractionResult`` per deduplicated measurement.
        """
        ...


class StubAdapter:
    """Returns empty results — usable in tests without a GPU or vLLM endpoint."""

    def extract(self, pdf_path: Path) -> list[ExtractionResult]:
        return []


# ---------------------------------------------------------------------------
# ScholarlmAdapter — wires DocumentLM (OCR) + MeasurementLM (extraction).
#
# SETUP EXAMPLE:
#
#   from scholarlm import DocumentLM, MeasurementLM
#
#   doc_lm = DocumentLM(model_name="your-ocr-model")
#   meas_lm = MeasurementLM(
#       model_name="your-extraction-model",
#       entity_identification_prompt=YOUR_ENTITY_PROMPT,
#       entity_identification_schema=YourEntitySchema,
#       attribute_info_dict=YOUR_ATTRIBUTE_DICT,
#   )
#   adapter = ScholarlmAdapter(
#       doc_lm=doc_lm,
#       meas_lm=meas_lm,
#       schema_name="coastal_measurement_v1",
#       model_version="llama3-70b-v1",
#       lat_field="latitude",   # entity schema field name, if applicable
#       lon_field="longitude",
#   )
# ---------------------------------------------------------------------------
class ScholarlmAdapter:
    """
    Calls DocumentLM then MeasurementLM and converts raw dicts to ExtractionResult.

    ``lat_field`` / ``lon_field`` name the entity-schema fields that hold
    geographic coordinates.  Set to None if your schema has no coordinates.
    """

    def __init__(
        self,
        doc_lm: Any,           # scholarlm.DocumentLM
        meas_lm: Any,          # scholarlm.MeasurementLM
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
        # Step 1: OCR via DocumentLM.fit() → list of OCR strings, one per PDF.
        ocr_texts: list[str] = self.doc_lm.fit([str(pdf_path)])

        # Step 2: Extraction via MeasurementLM.fit() → list of measurement dicts.
        # Each dict has keys: value, units, attribute, entity_id, page_number,
        # source, context, and all entity schema fields.
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
                    # STUB: wire in a real confidence score if MeasurementLM exposes one.
                    confidence=None,
                    provenance=provenance,
                    latitude=record.get(self.lat_field) if self.lat_field else None,
                    longitude=record.get(self.lon_field) if self.lon_field else None,
                )
            )
        return results
