"""RAW to Bronze: turning landed files into machine-readable rows.

Extraction is provisional by design. Bronze may be imperfect (program.md §6),
RAW keeps the original, and a better parser can be pointed at the same bytes
later — which is the whole reason the original is preserved.
"""

from .base import PARSER_VERSION, ExtractionError, Extractor, Landed
from .documents import HtmlExtractor, PdfExtractor, TextExtractor, collapse
from .runner import (
    DEFAULT_EXTRACTORS,
    PIPELINE_VERSION,
    ExtractionResult,
    ExtractionRunner,
    walk_raw,
)
from .tabular import CsvExtractor, JsonExtractor, decode
from .worldbank import WorldBankExtractor

__all__ = [
    "DEFAULT_EXTRACTORS",
    "PARSER_VERSION",
    "PIPELINE_VERSION",
    "CsvExtractor",
    "ExtractionError",
    "ExtractionResult",
    "ExtractionRunner",
    "Extractor",
    "HtmlExtractor",
    "JsonExtractor",
    "Landed",
    "PdfExtractor",
    "TextExtractor",
    "WorldBankExtractor",
    "collapse",
    "decode",
    "walk_raw",
]
