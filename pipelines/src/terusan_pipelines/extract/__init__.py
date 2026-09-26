"""RAW to Bronze: turning landed files into machine-readable rows.

Extraction is provisional by design. Bronze may be imperfect (program.md §6),
RAW keeps the original, and a better parser can be pointed at the same bytes
later — which is the whole reason the original is preserved.
"""

from .bank_indonesia import ConsumerSurveyExtractor, RetailSalesExtractor
from .base import PARSER_VERSION, ExtractionError, Extractor, Landed
from .bnpb import BnpbDatastoreExtractor
from .djpk import DjpkApbdExtractor
from .documents import HtmlExtractor, PdfExtractor, TextExtractor, collapse
from .fred import FredExtractor, FredSeriesPageExtractor
from .gdelt import GdeltExtractor
from .hdx_mobility import MovementDistributionExtractor
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
from .sp2kp import Sp2kpPricesExtractor
from .sp2kp_national import Sp2kpNationalExtractor
from .tabular import CsvExtractor, JsonExtractor, decode
from .trading_economics import TradingEconomicsChartExtractor, TradingEconomicsExtractor
from .ucdp import UcdpOrganizedViolenceExtractor
from .vews import VewsCollectiveViolenceExtractor
from .wits import RcaSeedExtractor, WitsTradeStatsExtractor
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
    "DjpkApbdExtractor",
    "ExtractionError",
    "ExtractionResult",
    "ExtractionRunner",
    "Extractor",
    "FredExtractor",
    "FredSeriesPageExtractor",
    "GdeltExtractor",
    "HtmlExtractor",
    "JsonExtractor",
    "Landed",
    "MovementDistributionExtractor",
    "PdfExtractor",
    "PihpsPricesExtractor",
    "RcaSeedExtractor",
    "SekiExtractor",
    "SipriMilexExtractor",
    "Sp2kpNationalExtractor",
    "Sp2kpPricesExtractor",
    "RetailSalesExtractor",
    "TextExtractor",
    "TradingEconomicsChartExtractor",
    "TradingEconomicsExtractor",
    "UcdpOrganizedViolenceExtractor",
    "VewsCollectiveViolenceExtractor",
    "WitsTradeStatsExtractor",
    "SpreadsheetMLExtractor",
    "WorkbookExtractor",
    "WorldBankExtractor",
    "YahooChartExtractor",
    "ZippedWorkbookExtractor",
    "collapse",
    "decode",
    "walk_raw",
]
