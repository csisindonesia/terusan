"""Bronze to Silver: deciding what the text means.

Silver is where types are decided, periods are bounded, dimensions are
resolved and units are made explicit — the judgement Bronze defers so that a
value cannot change type between partitions (program.md §6, §7).

Two rules run through the module. Ambiguity is recorded rather than resolved by
guessing: a number that could be read two ways is marked, and a name that
resolves to nothing stays unresolved and visible. And normalization is a pure
function of Bronze plus a mapping, so Silver can always be rebuilt — which is
what happens every time a mapping turns out to be wrong.
"""

from .dimensions import (
    Commodity,
    CommodityRegistry,
    EntityType,
    Geography,
    GeographyRegistry,
    GeoType,
    Registry,
    normalize_name,
)
from .observations import (
    ColumnMapping,
    MappingError,
    NormalizationResult,
    ObservationNormalizer,
    observation_id,
)
from .periods import Period, Resolution, UnparseablePeriod, parse_period, try_parse_period
from .reference import (
    REFERENCE_ROOT,
    ReferenceDataError,
    commodity_registry,
    geography_registry,
    load_aggregates,
    load_commodities,
    load_countries,
    load_indonesia,
)
from .runner import (
    PIPELINE_VERSION,
    CollapsedDimension,
    SilverResult,
    SilverRunner,
)
from .schema import (
    SILVER_COMMODITIES,
    SILVER_DOCUMENTS,
    SILVER_GEOGRAPHY,
    SILVER_INDICATORS,
    SILVER_OBSERVATIONS,
    SILVER_SCHEMAS,
    SILVER_SOURCES,
)
from .values import NumberFormat, ParsedValue, ValueStatus, parse_value

__all__ = [
    "PIPELINE_VERSION",
    "REFERENCE_ROOT",
    "SILVER_COMMODITIES",
    "SILVER_DOCUMENTS",
    "SILVER_GEOGRAPHY",
    "SILVER_INDICATORS",
    "SILVER_OBSERVATIONS",
    "SILVER_SOURCES",
    "SILVER_SCHEMAS",
    "ColumnMapping",
    "Commodity",
    "CommodityRegistry",
    "EntityType",
    "GeoType",
    "Geography",
    "GeographyRegistry",
    "MappingError",
    "NormalizationResult",
    "NumberFormat",
    "ObservationNormalizer",
    "ParsedValue",
    "Period",
    "ReferenceDataError",
    "Registry",
    "Resolution",
    "SilverResult",
    "CollapsedDimension",
    "SilverRunner",
    "UnparseablePeriod",
    "ValueStatus",
    "commodity_registry",
    "geography_registry",
    "load_aggregates",
    "load_commodities",
    "load_countries",
    "load_indonesia",
    "normalize_name",
    "observation_id",
    "parse_period",
    "parse_value",
    "try_parse_period",
]
