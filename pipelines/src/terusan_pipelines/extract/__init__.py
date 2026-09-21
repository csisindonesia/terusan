"""RAW to Bronze: turning landed files into machine-readable rows.

Extraction is provisional by design. Bronze may be imperfect (program.md §6),
RAW keeps the original, and a better parser can be pointed at the same bytes
later — which is the whole reason the original is preserved.
"""

from .bank_indonesia import ConsumerSurveyExtractor, RetailSalesExtractor
from .base import PARSER_VERSION, ExtractionError, Extractor, Landed
from .bnpb import BnpbDatastoreExtractor
from .documents import HtmlExtractor, PdfExtractor, TextExtractor, collapse
from .fred import FredExtractor, FredSeriesPageExtractor
from .hdx_mobility import MovementDistributionExtractor
from .heesi import HeesiExtractor
from .pihps import PihpsPricesExtractor
from .runner import (
    DEFAULT_EXTRACTORS,
    PIPELINE_VERSION,
    ExtractionResult,
    ExtractionRunner,
    walk_raw,
)
from .seki import SekiExtractor
from .sipri import SipriMilexExtractor
from .tabular import CsvExtractor, JsonExtractor, decode
from .trading_economics import TradingEconomicsExtractor
from .workbooks import (
    SpreadsheetMLExtractor,
    WorkbookExtractor,
    ZippedWorkbookExtractor,
)
from .worldbank import WorldBankExtractor
from .yahoo_finance import YahooChartExtractor

__all__ = [
    "DEFAULT_EXTRACTORS",
    "PARSER_VERSION",
    "PIPELINE_VERSION",
    "BnpbDatastoreExtractor",
    "ConsumerSurveyExtractor",
    "CsvExtractor",
    "ExtractionError",
    "ExtractionResult",
    "ExtractionRunner",
    "Extractor",
    "FredExtractor",
    "FredSeriesPageExtractor",
    "HeesiExtractor",
    "HtmlExtractor",
    "JsonExtractor",
    "Landed",
    "MovementDistributionExtractor",
    "PdfExtractor",
    "PihpsPricesExtractor",
    "SekiExtractor",
    "SipriMilexExtractor",
    "RetailSalesExtractor",
    "TextExtractor",
    "TradingEconomicsExtractor",
    "SpreadsheetMLExtractor",
    "WorkbookExtractor",
    "WorldBankExtractor",
    "YahooChartExtractor",
    "ZippedWorkbookExtractor",
    "collapse",
    "decode",
    "walk_raw",
]
