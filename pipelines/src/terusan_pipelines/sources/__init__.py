"""Data acquisition: scrapers, API pullers, feed readers, bulk downloads.

Everything that turns a remote resource into bytes in RAW lives here, whatever
the collection method. Scraping is one method among several (see
`CollectionMethod`), and they all share the same landing, hashing, provenance
and rate limiting — so they share one package rather than splitting into
parallel structures.

A source's only job is to yield `Artifact` objects. The runner lands them.

    class Inflation(Source):
        meta = SourceMeta(
            slug="bps-inflation",
            name="BPS — Consumer Price Index",
            category=Category.STATISTICS,
            source_type=SourceType.GOVERNMENT_API,
            collection_method=CollectionMethod.API,
            base_url="https://webapi.bps.go.id",
            update_frequency=UpdateFrequency.MONTHLY,
            schedule="0 3 2 * *",
        )

        def collect(self, ctx):
            yield Artifact(content=..., filename="cpi.json", dataset="inflation")

Existing standalone scripts run unmodified through `legacy.legacy_source`.
"""

from .base import (
    Artifact,
    Category,
    CollectionMethod,
    ScrapeContext,
    Source,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from .landing import Landed, Landing
from .legacy import LegacySource, directory_artifacts, legacy_source
from .ratelimit import HostRateLimiter, TokenBucket, host_of
from .registry import DuplicateSourceSlug, Registry, UnknownSource, registry
from .runner import Runner, RunResult, RunStatus, summarize

__all__ = [
    "Artifact",
    "Category",
    "CollectionMethod",
    "DuplicateSourceSlug",
    "HostRateLimiter",
    "Landed",
    "Landing",
    "LegacySource",
    "RunResult",
    "RunStatus",
    "Runner",
    "ScrapeContext",
    "Source",
    "SourceMeta",
    "SourceType",
    "TokenBucket",
    "UnknownSource",
    "UpdateFrequency",
    "Registry",
    "directory_artifacts",
    "host_of",
    "legacy_source",
    "registry",
    "summarize",
]
