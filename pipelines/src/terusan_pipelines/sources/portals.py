"""The shapes an Indonesian data portal comes in, written once.

Forty-odd portals were surveyed for this lake, and they fall into three kinds.
A handful answer JSON. Most publish files — workbooks, PDFs, archives — behind
an index page. The rest render everything server-side, so the page itself is
the only artifact there is. A scraper per portal would write those three
patterns forty times and get the provenance slightly different each time, so
they are written here and each portal declares the parts that are actually its
own: its URLs, its dataset name, and what a link has to look like to be worth
landing.

What stays out of here is anything that reads the bytes. These land what the
portal served (§2.1); the extractors decide later what it meant, and can be
improved without asking forty ministries for their files again.

The fourth kind is a portal that cannot be collected at all today — a paid AIS
feed, an account-gated satellite archive, a host whose DNS no longer resolves.
`GatedSource` is for those: it carries the registry record, so the catalogue
can say the source exists and what it would take to open it, and refuses to
pretend it collected anything.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from .base import Artifact, ScrapeContext, Source
from .http import Fetcher, fetcher

log = structlog.get_logger(__name__)

#: Long enough for a ministry's file server on a slow afternoon, short enough
#: that a hung request does not hold a scheduled run open until morning.
TIMEOUT_SECONDS = 90.0

#: Sent by every source in this module. Several Indonesian portals answer a
#: bare client with a challenge page and a real browser string with the data;
#: none of them are being deceived about who is asking, because the product
#: name and the contact remain in `http.USER_AGENT` for the ones that accept
#: it. This is the fallback for the ones that do not.
BROWSER_HEADERS: Mapping[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

#: Every `href`, as written. Sources match their own pattern against these
#: rather than parsing the document: a portal's index page is markup we do not
#: control and will not be able to keep a parser in step with, and all that is
#: wanted from it is the links.
_HREF = re.compile(r'href=["\']([^"\'>\s]+)["\']', re.IGNORECASE)

#: A four-digit year anywhere in a URL or a title. It becomes a RAW partition
#: where a source can find one, which is what lets a query for 2024 skip the
#: twenty other years a ministry has published.
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


class AccessNotProvisioned(RuntimeError):
    """A source whose data is real but whose access has not been arranged.

    Raised rather than returning nothing, so a run against a gated source fails
    loudly and is recorded, instead of succeeding with zero artifacts and
    looking like a portal that published nothing this month.
    """


def links(page: str, base: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
    """Absolute URLs on a page whose href matches `pattern`, in page order.

    Deduplicated, because an index page links the same workbook from the row
    and from the download icon, and landing it twice costs the ministry two
    requests to arrive at one content hash.
    """
    found: list[str] = []
    seen: set[str] = set()
    for match in _HREF.finditer(page):
        href = match.group(1).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        if not pattern.search(href):
            continue
        url = urljoin(base, href)
        if url in seen:
            continue
        seen.add(url)
        found.append(url)
    return tuple(found)


#: What counts as "no" from the command line. `--param full=false` arrives as
#: the string "false", which is perfectly truthy in Python — a run that meant
#: to skip a gigabyte download would start one.
_DENIALS = frozenset({"", "0", "false", "no", "off", "none"})


def wants(ctx: ScrapeContext, key: str) -> bool:
    """Whether a run asked for an optional, expensive part of a source.

    Takes a real bool where a caller passed one, and reads the string forms the
    CLI hands through otherwise.
    """
    value = ctx.params.get(key)
    if isinstance(value, bool) or value is None:
        return bool(value)
    return str(value).strip().lower() not in _DENIALS


def year_in(*values: str | None) -> str | None:
    """The first four-digit year in any of `values`, where there is one."""
    for value in values:
        if not value:
            continue
        match = _YEAR.search(value)
        if match:
            return match.group(0)
    return None


def filename_of(url: str, fallback: str = "index.html") -> str:
    """The last path segment of a URL, or something honest when it has none."""
    name = urlparse(url).path.rsplit("/", 1)[-1]
    return name or fallback


def published_at(response: httpx.Response) -> date | None:
    """The server's `Last-Modified`, where it sent a parseable one."""
    header = response.headers.get("last-modified")
    if not header:
        return None
    try:
        return parsedate_to_datetime(header).date()
    except (TypeError, ValueError):
        return None


def provenance(response: httpx.Response) -> dict[str, Any]:
    """What the response said about itself, kept beside the bytes.

    A landed file states neither when it was published nor what the server
    called it; these headers are the only record of either, and they are gone
    the moment the connection closes.
    """
    return {
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "etag": response.headers.get("etag"),
        "last_modified": response.headers.get("last-modified"),
        "retrieved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One URL a source fetches, and what the result should land as."""

    dataset: str
    url: str
    filename: str
    media_type: str = "application/json"
    partition: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def artifact(self, response: httpx.Response) -> Artifact:
        return Artifact(
            content=response.content,
            filename=self.filename,
            dataset=self.dataset,
            source_url=str(response.url),
            media_type=self.media_type,
            published_at=published_at(response),
            partition=self.partition,
            metadata={**self.metadata, **provenance(response)},
        )


class PortalSource(Source, abstract=True):
    """Shared plumbing: one client, the headers, and the limit honoured."""

    #: Extra request headers this portal needs. Merged over `BROWSER_HEADERS`.
    headers: Mapping[str, str] = {}

    timeout: float = TIMEOUT_SECONDS

    def request_headers(self) -> dict[str, str]:
        """The headers for this run. Overridden where a credential is read."""
        return {**BROWSER_HEADERS, **self.headers}


class ApiSource(PortalSource, abstract=True):
    """A portal that answers JSON, landed response by response.

    The responses land as served rather than merged into one file: each carries
    its own URL and headers in provenance, and a portal that changes its
    envelope halfway through a run should leave both shapes in RAW rather than
    one file that is half of each.
    """

    #: The fixed endpoints. A source whose URLs depend on the run — a year
    #: range, a page count, a list fetched first — overrides `endpoints_for`.
    endpoints: tuple[Endpoint, ...] = ()

    def endpoints_for(self, ctx: ScrapeContext, http: Fetcher) -> Iterator[Endpoint]:
        """What to fetch this run.

        Handed the open client, because several portals only say what they hold
        in a listing that has to be fetched first — and a source that opened a
        second client for that would leave the shared per-host limiter counting
        half its requests.
        """
        yield from self.endpoints

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        landed = 0
        with fetcher(headers=self.request_headers(), timeout=self.timeout) as http:
            for endpoint in self.endpoints_for(ctx, http):
                if ctx.limit is not None and landed >= ctx.limit:
                    return
                response = http.try_get(endpoint.url)
                if response is None:
                    # One dead endpoint out of a dozen is a portal having a bad
                    # day, not a reason to discard the eleven that answered.
                    continue
                yield endpoint.artifact(response)
                landed += 1


@dataclass
class _Walk:
    """One run's position in a file index.

    Held in one object rather than in four locals, because the walk now spans
    two levels and the counts have to mean the same thing on both: a file seen
    on a detail page is the same file the index linked, and the ceiling counts
    it once.
    """

    ceiling: int
    landed: int = 0
    followed: int = 0
    full: bool = False
    seen: set[str] = field(default_factory=set)


class FileIndexSource(PortalSource, abstract=True):
    """A portal that publishes files behind an index page.

    The index is landed too. It is what says which files existed on the day and
    what the publisher called them — a workbook named `Lampiran-3.xlsx` states
    neither — and it is the record that a file which used to be listed is not
    listed any more.
    """

    #: Pages listing the files. Several where a portal paginates or splits by
    #: year, and the first is the one a reader would call the source page.
    index_urls: tuple[str, ...] = ()

    #: Which links to take, matched against the href as written.
    file_pattern: re.Pattern[str] = re.compile(r"\.(xlsx?|csv|pdf|zip|json)(\?|$)", re.I)

    #: Which of those to drop again, before they are fetched. For the case one
    #: index page serves two sources: a file another slug already lands should
    #: not be downloaded twice to be discarded once.
    exclude_pattern: re.Pattern[str] | None = None

    #: What the files land under.
    dataset: str = "files"

    #: What the index pages land under, where they are kept. None drops them,
    #: for a portal whose index is a search form carrying no information.
    index_dataset: str | None = None

    #: A ceiling on one run. Ministries publish archives running to thousands
    #: of documents, and a full pull is a deliberate act — `ctx.limit` raises
    #: or lowers it per run.
    max_files: int = 200

    #: Which links on an index page are themselves index pages. Several portals
    #: list datasets and put the file one page deeper — Kementan's listing is
    #: 277 `detail_data/{id}` pages, each holding one workbook — so following
    #: one level is the difference between landing a catalogue and landing the
    #: figures. None means the files are linked directly and nothing is
    #: followed, which is the safer default: a pattern that matches too much
    #: turns a source into a crawler.
    follow_pattern: re.Pattern[str] | None = None

    #: How many followed pages one run opens. Independent of `max_files`
    #: because a page may hold no file at all, and a portal with a thousand
    #: detail pages should not be walked end to end by a nightly run.
    max_followed: int = 100

    def index_pages(self, ctx: ScrapeContext) -> tuple[str, ...]:
        return self.index_urls

    def file_metadata(self, url: str) -> dict[str, Any]:
        """Anything the source knows about a file from its URL alone."""
        return {}

    def partition_for(self, url: str) -> tuple[str, ...]:
        year = year_in(url)
        return (f"year={year}",) if year else ()

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        ceiling = ctx.limit if ctx.limit is not None else self.max_files
        state = _Walk(ceiling=ceiling)

        with fetcher(headers=self.request_headers(), timeout=self.timeout) as http:
            for index_url in self.index_pages(ctx):
                page = http.try_get(index_url)
                if page is None:
                    continue

                if self.index_dataset is not None:
                    yield Endpoint(
                        dataset=self.index_dataset,
                        url=index_url,
                        filename=filename_of(index_url, "index.html"),
                        media_type="text/html",
                        metadata={"role": "index"},
                    ).artifact(page)

                yield from self._files_on(page.text, index_url, http, state)
                if state.full:
                    return

                if self.follow_pattern is None:
                    continue

                for detail_url in links(page.text, index_url, self.follow_pattern):
                    if state.full or state.followed >= self.max_followed:
                        return
                    if detail_url in state.seen:
                        continue
                    state.seen.add(detail_url)
                    state.followed += 1

                    detail = http.try_get(detail_url)
                    if detail is None:
                        continue

                    if self.index_dataset is not None:
                        yield Endpoint(
                            dataset=self.index_dataset,
                            url=detail_url,
                            filename=filename_of(detail_url, "detail.html"),
                            media_type="text/html",
                            # The detail page carries what the file does not:
                            # the title, the period, the producing unit.
                            metadata={"role": "detail", "index_url": index_url},
                        ).artifact(detail)

                    yield from self._files_on(detail.text, detail_url, http, state)

    def _files_on(
        self, page: str, page_url: str, http: Fetcher, state: _Walk
    ) -> Iterator[Artifact]:
        """Land every file this page links that is not excluded or already had."""
        for url in links(page, page_url, self.file_pattern):
            if self.exclude_pattern is not None and self.exclude_pattern.search(url):
                continue
            if state.landed >= state.ceiling:
                log.info("portal.ceiling", source=self.meta.slug, landed=state.landed)
                state.full = True
                return
            if url in state.seen:
                continue
            state.seen.add(url)

            response = http.try_get(url)
            if response is None:
                continue

            yield Artifact(
                content=response.content,
                filename=filename_of(url, "download"),
                dataset=self.dataset,
                source_url=url,
                media_type=response.headers.get("content-type", "").split(";")[0] or None,
                published_at=published_at(response),
                partition=self.partition_for(url),
                metadata={
                    **self.file_metadata(url),
                    **provenance(response),
                    "index_url": page_url,
                },
            )
            state.landed += 1


class PageSource(PortalSource, abstract=True):
    """A portal that renders server-side, landed a page at a time.

    For the portals that publish no file and answer no API: a search result, a
    register, a table of contents. The page is the document, and an extractor
    reads it out of RAW later — which is the only arrangement that survives the
    portal rebuilding its markup, as several of these have done twice.
    """

    #: The pages to land. A source paginating over hundreds builds them in
    #: `page_urls` instead.
    urls: tuple[str, ...] = ()

    dataset: str = "pages"

    def page_urls(self, ctx: ScrapeContext) -> Sequence[str]:
        return self.urls

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        landed = 0
        with fetcher(headers=self.request_headers(), timeout=self.timeout) as http:
            for url in self.page_urls(ctx):
                if ctx.limit is not None and landed >= ctx.limit:
                    return
                response = http.try_get(url)
                if response is None:
                    continue
                year = year_in(url)
                yield Artifact(
                    content=response.content,
                    filename=filename_of(url, "page.html"),
                    dataset=self.dataset,
                    source_url=url,
                    media_type="text/html",
                    published_at=published_at(response),
                    partition=(f"year={year}",) if year else (),
                    metadata=provenance(response),
                )
                landed += 1


class GatedSource(Source, abstract=True):
    """A source that is registered but cannot be collected yet.

    Three things put a portal here: a subscription nobody has bought, an
    account nobody has opened, and a hostname that stopped resolving. In all
    three the dataset is real and worth naming — the catalogue should say the
    lake knows about AIS and does not hold it — and none of them can be fixed
    by trying harder at 3am.

    `access` says what would open it, in a sentence someone could act on.
    """

    #: What it would take. Shown in the error and worth keeping current.
    access: str = "access has not been arranged"

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        raise AccessNotProvisioned(f"{self.meta.slug}: {self.access}")
        yield  # pragma: no cover - unreachable, keeps this a generator
