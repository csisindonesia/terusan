"""BNPB's disaster figures, as published on data.bnpb.go.id.

Roughly two hundred spreadsheets across seventeen datasets: disaster events and
their impact by province and regency, emergency-status declarations, the
eruption responses at Lewotobi, Gunung Ibu and Ruang, and the population living
in each hazard zone. It is the national record of what happened and who it
happened to, and BNPB has published it as XLSX since 2000.

**The workbooks themselves cannot be fetched.** The portal sits behind
Cloudflare, and while `/api/3/action/` answers a plain client, every
`/dataset/.../download/...xlsx` URL returns a JavaScript challenge — a 403
carrying an HTML page named `.xlsx`. That is precisely the byte sequence
landing refuses, and rightly: an unattended run must not archive a challenge
page as a spreadsheet.

So the tables are collected through CKAN's datastore, which is the portal's own
parse of each uploaded workbook, served as JSON by the same API that lists
them. This is a compromise and worth naming as one: it is not the bytes BNPB
published (program.md §2.1), and re-reading it later cannot recover a merged
header or a footnote the datastore dropped. It is, however, BNPB's own
rendering rather than ours, it carries the column labels and definitions the
uploader wrote, and it is what is actually reachable.

The download is still attempted, once per run. The moment BNPB relaxes the
rule, or serves this path from a different origin, the workbooks land and the
datastore JSON stops being the only copy. Attempting it once rather than two
hundred times is the difference between checking and hammering: the challenge
is site-wide, so one refusal answers for every resource in the run.

About a quarter of the resources were never parsed into the datastore. With
downloads challenged those have no route at all, and the run says so — as a
count in the log rather than as a failure, because a dataset that BNPB never
parsed is not an outage this run can do anything about.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import structlog

from ..base import (
    Artifact,
    Category,
    CollectionMethod,
    ScrapeContext,
    Source,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..http import Fetcher, fetcher
from ..sniff import describe, looks_like_html, matches_extension
from .ckan import (
    DATASTORE_PAGE_SIZE,
    MAX_DATASTORE_ROWS,
    ORGANIZATION,
    PAGE_SIZE,
    PORTAL_URL,
    RESOURCE_FORMAT,
    Package,
    Resource,
    datastore_url,
    packages,
    search_url,
    total,
)

log = structlog.get_logger(__name__)

#: The catalogue itself, landed alongside the figures. A datastore page names
#: neither the dataset it belongs to nor the licence it was published under,
#: and the portal's HTML is unreachable — so this is the only record of what
#: BNPB was offering on the day.
CATALOGUE_DATASET = "bnpb-catalogue"

#: Statuses Cloudflare answers a challenged request with. Neither is retried by
#: the shared client, which is correct: they will say the same thing next time.
CHALLENGE_STATUS = frozenset({403, 503})

#: Below this share of reachable resources the run fails. One resource whose
#: datastore errors is noise; a third of them failing is an outage, and a
#: partial catalogue must not pass for a complete one.
MIN_SUCCESS_RATIO = 0.90

#: The API is quick and the tables are small; nothing here runs long.
TIMEOUT_SECONDS = 120.0


class Challenged(RuntimeError):
    """The portal served a bot challenge instead of the file."""


class PartialCatalogue(RuntimeError):
    """Too few tables were collected for the run to be treated as complete."""


class DisasterData(Source):
    """Every XLSX table BNPB's data centre publishes, newest datasets first."""

    meta = SourceMeta(
        slug="bnpb-disaster",
        name="BNPB — disaster events, impact and emergency status",
        organization="Badan Nasional Penanggulangan Bencana",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.API,
        base_url=PORTAL_URL,
        # Per dataset rather than per portal: most are ODC-BY, the 2024
        # compilation is ODbL, two are CC-BY and the population tables are
        # public domain. Each artifact carries the one it was published under.
        license="Varies per dataset (ODC-BY, ODbL, CC-BY, public domain)",
        # BNPB adds a dataset when an emergency ends or a year closes, not on a
        # calendar.
        update_frequency=UpdateFrequency.IRREGULAR,
        max_requests_per_second=1.0,
        # Weekly. The holding changes a few times a year, and landing is
        # content-addressed, so a week that changed nothing writes nothing.
        schedule="0 4 * * 2",
        notes=(
            "CKAN. The /download/ URLs sit behind a Cloudflare challenge, so tables "
            "are collected through the datastore API — BNPB's own parse of each "
            "workbook, not the workbook."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        produced = 0
        considered = 0
        reachable = 0
        collected = 0
        unparsed: list[str] = []

        with fetcher(timeout=TIMEOUT_SECONDS) as http:
            pages, catalogue = self._catalogue(http)
            for artifact in pages:
                yield artifact
                produced += 1

            if not catalogue:
                raise ValueError(
                    f"BNPB's catalogue lists no {RESOURCE_FORMAT} datasets "
                    f"under organization {ORGANIZATION!r}"
                )

            # Optimism, once. A challenge answers for the whole portal, so the
            # first refusal stands for the rest of the run.
            downloads_open = True

            for package in catalogue:
                for resource in package.resources:
                    if ctx.limit is not None and produced >= ctx.limit:
                        # A truncated run is a smoke test, and a success ratio
                        # over five of two hundred tables says nothing worth
                        # failing on.
                        return

                    if self._too_old(ctx, resource):
                        continue

                    considered += 1

                    workbook: bytes | None = None
                    if downloads_open:
                        try:
                            workbook = self._download(http, resource)
                        except Challenged as exc:
                            downloads_open = False
                            log.info("bnpb.downloads_challenged", detail=str(exc))

                    if workbook is None and not resource.datastore_active:
                        # Never parsed by CKAN, and the file itself is behind
                        # the challenge. Nothing to collect, and nothing this
                        # run can do about it.
                        unparsed.append(resource.resource_id)
                        continue

                    reachable += 1

                    if workbook is not None:
                        yield self._workbook(package, resource, workbook)
                        produced += 1
                        collected += 1
                        continue

                    landed = 0
                    for artifact in self._datastore(http, package, resource):
                        yield artifact
                        produced += 1
                        landed += 1
                        if ctx.limit is not None and produced >= ctx.limit:
                            return
                    if landed:
                        collected += 1

        log.info(
            "bnpb.collected",
            datasets=len(catalogue),
            considered=considered,
            reachable=reachable,
            collected=collected,
            unparsed=len(unparsed),
            downloads_open=downloads_open,
        )

        if considered and not collected:
            # Every route to every table closed at once. An incremental run
            # that skipped everything is not this — `considered` is zero there,
            # and a week in which BNPB published nothing is a success.
            raise PartialCatalogue(
                f"no BNPB table could be collected from {considered} resources: "
                f"{len(unparsed)} were neither downloadable nor parsed into the datastore"
            )
        if reachable and collected / reachable < MIN_SUCCESS_RATIO:
            raise PartialCatalogue(
                f"only {collected} of {reachable} reachable BNPB tables were collected, "
                f"below the {MIN_SUCCESS_RATIO:.0%} the run requires"
            )

    # -- the catalogue ------------------------------------------------------

    def _catalogue(self, http: Fetcher) -> tuple[list[Artifact], list[Package]]:
        """Every dataset in the holding, and the search pages that listed them.

        The pages land as received. They are what ties a table to its licence,
        its description and the dataset it belongs to, none of which a
        datastore page carries.
        """
        artifacts: list[Artifact] = []
        found: list[Package] = []
        start = 0

        while True:
            url = search_url(start=start, rows=PAGE_SIZE)
            response = http.get(url)
            body = response.json()
            page = packages(body)
            found.extend(page)

            artifacts.append(
                Artifact(
                    content=response.content,
                    filename=f"package-search-{start:05d}.json",
                    dataset=CATALOGUE_DATASET,
                    source_url=str(response.url),
                    media_type="application/json",
                    metadata={
                        "title": f"BNPB {RESOURCE_FORMAT} catalogue, datasets {start + 1}+",
                        "organization": ORGANIZATION,
                        "datasets": len(page),
                        "offset": start,
                        "total": total(body),
                    },
                )
            )

            start += PAGE_SIZE
            if start >= total(body) or not page:
                return artifacts, found

    # -- the two routes to a table ------------------------------------------

    def _download(self, http: Fetcher, resource: Resource) -> bytes | None:
        """The published workbook, or None if this one resource would not come.

        Raises `Challenged` when the portal answered a bot challenge, which is
        a statement about the portal rather than about this resource — the
        caller stops asking for the rest of the run.
        """
        try:
            response = http.get(resource.url)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in CHALLENGE_STATUS:
                raise Challenged(f"{resource.url} answered HTTP {status}") from exc
            log.warning("bnpb.download_failed", url=resource.url, status=status)
            return None
        except (httpx.HTTPError, OSError) as exc:
            log.warning("bnpb.download_failed", url=resource.url, error=str(exc)[:200])
            return None

        content = response.content
        if looks_like_html(content) or not matches_extension(content, "xlsx"):
            # A 200 carrying a challenge page is the other half of the same
            # behaviour, and landing would refuse it anyway. Refusing it here
            # names why.
            raise Challenged(f"{resource.url} answered {describe(content)} rather than a workbook")
        return content

    def _datastore(self, http: Fetcher, package: Package, resource: Resource) -> Iterator[Artifact]:
        """CKAN's parse of the workbook, a page of rows at a time."""
        offset = 0
        while offset < MAX_DATASTORE_ROWS:
            response = http.try_get(datastore_url(resource.resource_id, offset=offset))
            if response is None:
                return

            try:
                body = response.json()
            except ValueError:
                log.warning("bnpb.datastore_unreadable", resource=resource.resource_id)
                return

            result = body.get("result")
            if not body.get("success") or not isinstance(result, dict):
                log.warning(
                    "bnpb.datastore_refused",
                    resource=resource.resource_id,
                    detail=str(body.get("error"))[:200],
                )
                return

            records = result.get("records") or []
            if not records and offset:
                return

            yield Artifact(
                content=response.content,
                filename=resource.page_filename(offset),
                dataset=package.name,
                partition=package.partition,
                source_url=str(response.url),
                media_type="application/json",
                published_at=resource.modified.date() if resource.modified else None,
                metadata=self._provenance(package, resource)
                | {
                    "delivery": "datastore",
                    "row_offset": offset,
                    "rows": len(records),
                },
            )

            if len(records) < DATASTORE_PAGE_SIZE:
                return
            offset += DATASTORE_PAGE_SIZE

    def _workbook(self, package: Package, resource: Resource, content: bytes) -> Artifact:
        """The published file, for the day the challenge is lifted."""
        return Artifact(
            content=content,
            filename=resource.filename,
            dataset=package.name,
            partition=package.partition,
            source_url=resource.url,
            media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            published_at=resource.modified.date() if resource.modified else None,
            metadata=self._provenance(package, resource) | {"delivery": "download"},
        )

    @staticmethod
    def _provenance(package: Package, resource: Resource) -> dict[str, object]:
        """What the catalogue knows and the table does not."""
        return {
            "title": resource.title,
            "description": resource.description or package.notes,
            "package": package.name,
            "package_title": package.title,
            "resource_id": resource.resource_id,
            "license": package.license_id,
            "format": RESOURCE_FORMAT,
            "download_url": resource.url,
        }

    @staticmethod
    def _too_old(ctx: ScrapeContext, resource: Resource) -> bool:
        """Whether an incremental run should skip this resource.

        A resource with no timestamp is always collected: landing deduplicates
        on content, so re-fetching one of unknown age costs a request and
        writes nothing.
        """
        if ctx.since is None or resource.modified is None:
            return False
        return resource.modified.date() < ctx.since
