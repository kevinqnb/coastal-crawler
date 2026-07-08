"""Native OCR/extraction pipeline (no external scholarlm dependency)."""

from coastal_crawler.extraction.extraction_lm import ExtractionLM
from coastal_crawler.extraction.ocr_lm import OCRLM

__all__ = ["OCRLM", "ExtractionLM"]
