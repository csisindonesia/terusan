"""What a source is, and what it hands back.

A source acquires bytes and nothing else. It does not parse, clean, or derive
— extraction lives in `terusan_pipelines.extract`, normalization in
`terusan_pipelines.normalize`. The split is what makes reprocessing possible:
when a parser improves, RAW is replayed, and the source portal does not have
to still exist (program.md §2.1).

Scrapers therefore yield `Artifact` objects and let the runner land them.
Hashing, deduplication, RAW path construction and provenance all happen in one
place, so fifty source modules cannot get provenance fifty different ways.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class SourceType(StrEnum):
    """Mirrors the `source_type` enum in db/migrations/001_catalog.up.sql."""

    OFFICIAL_PORTAL = "official_portal"
    GOVERNMENT_API = "government_api"
    SCRAPING = "scraping"
    MANUAL_UPLOAD = "manual_upload"
    NEWS = "news"
    RESEARCH_REPOSITORY = "research_repository"
    INTERNAL = "internal"
    #: A commercial aggregator: Yahoo Finance carries the exchange's figures
    #: without publishing on its behalf, which is not a government API.
    MARKET_DATA = "market_data"


class CollectionMethod(StrEnum):
    """Mirrors `collection_method`. How bytes arrive, not what they contain."""

    API = "api"
    SCRAPE = "scrape"
    BULK_DOWNLOAD = "bulk_download"
    MANUAL_UPLOAD = "manual_upload"
    FEED = "feed"


class UpdateFrequency(StrEnum):
    REALTIME = "realtime"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    IRREGULAR = "irregular"


#: RAW's first path level groups by domain, matching the directories in
#: program.md §5. A source declares which one it belongs to.
class Category(StrEnum):
    GOVERNMENT = "government"
    STATISTICS = "statistics"
    REGULATIONS = "regulations"
    NEWS = "news"
    RESEARCH = "research"
    SCRAPING = "scraping"
    DOCUMENTS = "documents"


@dataclass(frozen=True, slots=True)
class SourceMeta:
    """Registry record for a data provider (program.md §16).

    This is the authority: `terusan sources sync` upserts these into the
    `sources` table, so the registry cannot drift from the code that reads it.
    """

    slug: str
    name: str
    category: Category
    source_type: SourceType
    collection_method: CollectionMethod

    organization: str | None = None
    base_url: str | None = None
    country: str | None = "ID"
    license: str | None = None
    update_frequency: UpdateFrequency = UpdateFrequency.IRREGULAR
    active: bool = True
    notes: str | None = None

    #: Politeness is a property of the server, not of our taxonomy. Several
    #: sources often share one host, so the runner limits per host and this is
    #: only the source's own ceiling.
    max_requests_per_second: float = 1.0

    #: Cron expression for scheduled runs. None means manual only.
    schedule: str | None = None


@dataclass(frozen=True, slots=True)
class Artifact:
    """One retrieved file, on its way to RAW.

    `content` is the bytes exactly as received. Anything the scraper learned
    while fetching — an HTTP status, a listing-page title, a publication date
    parsed off the page — belongs in `metadata`, which is written alongside
    the file rather than folded into it.
    """

    content: bytes
    filename: str

    #: Logical grouping under the source: a series, a collection, a document
    #: type. Becomes a RAW path segment.
    dataset: str

    source_url: str | None = None
    media_type: str | None = None
    published_at: date | None = None
    retrieved_at: datetime | None = None

    #: Extra RAW path segments, typically Hive-style: ("year=2026",).
    partition: tuple[str, ...] = ()

    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        """SHA-256 over the bytes. Identity for deduplication (§18)."""
        return hashlib.sha256(self.content).hexdigest()

    @property
    def document_id(self) -> str:
        """Stable RAW directory name, derived from content.

        Content-addressed so that re-running a scraper over unchanged material
        resolves to the path already on disk: re-runs become idempotent
        instead of accumulating near-duplicate directories.
        """
        return f"doc_{self.content_hash[:16]}"


@dataclass(frozen=True, slots=True)
class ScrapeContext:
    """What the runner hands a scraper for one run."""

    #: Only fetch material published or updated at or after this date.
    #: None means a full pull.
    since: date | None = None

    #: Fetch but do not land. Scrapers should still make requests so that a
    #: dry run exercises the real code path.
    dry_run: bool = False

    #: Stop after this many artifacts. For smoke tests against a live source.
    limit: int | None = None

    params: dict[str, Any] = field(default_factory=dict)


class Source(ABC):
    """Base class for everything that acquires data, scraped or not.

    Intermediate base classes that supply behaviour but no registry record
    declare themselves abstract:

        class LegacySource(Source, abstract=True): ...
    """

    #: Set by each concrete subclass. The runner reads it without
    #: instantiating, so it is a class attribute rather than a property.
    meta: SourceMeta

    #: True for base classes that should never be registered or run.
    abstract: bool = True

    @abstractmethod
    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        """Yield artifacts to be landed in RAW.

        Yield rather than return a list: a source covering years of documents
        should stream, so the runner can land and free each artifact instead
        of holding the whole corpus in memory.

        Raising aborts the run and is recorded. Skipping one bad document is
        the scraper's own call — log it and continue.
        """

    def __init_subclass__(cls, abstract: bool = False, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.abstract = abstract
        if abstract or getattr(cls, "__abstractmethods__", None):
            return
        # Catch a missing or malformed `meta` at import time rather than when
        # the scheduler fires at 3am.
        if not isinstance(getattr(cls, "meta", None), SourceMeta):
            raise TypeError(
                f"{cls.__name__} must define `meta` as a SourceMeta instance, "
                f"or declare `class {cls.__name__}(Source, abstract=True)`"
            )
