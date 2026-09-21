"""Meta's Movement Distribution, as published on HDX.

How far people travel from where they live, aggregated per district and
fortnight from Facebook location histories. Meta releases it through HDX as one
CSV per date range, and HDX's CKAN API lists those resources — so the releases
are discovered from `package_show` rather than from a tracker spreadsheet
somebody keeps by hand. The earlier scrapper read a Google Sheet of resource
ids, which is a second copy of this listing and goes stale the moment Meta
publishes again.

Two things about the shape of this source are worth stating plainly, because
they are not what the rest of the warehouse does.

**The files are global and large.** Each release is around a hundred megabytes
covering every country Meta reports on, and Indonesia is a slice of it. The
CKAN datastore would filter server-side, as the World Bank source does in its
URL, and with an API token `datastore_search` does answer. It still is not what
lands: filtering before landing would make the source a parser, and RAW would
no longer hold what was published (program.md §2.1). So the public CSV lands
whole, and Indonesia is selected in extraction.

**A token is optional and changes nothing about what is collected.** Set
`HDX_API_TOKEN` and every request carries it, which is what keeps HDX from
rate-limiting an anonymous caller off a hundred-megabyte download and what a
non-public dataset would need. Unset, the package listing and the CSVs are
public and the run collects the same bytes.

**A run therefore takes the newest release only.** There are a dozen-odd
releases and a full pull is over a gigabyte, so `--limit` is what asks for more
and `--since` is what a scheduled run uses. Landing is content-addressed, so a
release already in RAW costs a download and writes nothing.

The package listing is landed alongside the data. It is the only record of
which resources existed on the day, and a bare CSV states neither its date
range nor its license.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

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
from ..credentials import bearer, credentials
from ..http import fetcher

log = structlog.get_logger(__name__)

#: The CKAN dataset. Named rather than searched for: a search that ranks
#: "movement" differently one day lands a different dataset under this source's
#: slug, and provenance would quietly stop meaning anything.
PACKAGE = "movement-distribution"

API_ROOT = "https://data.humdata.org/api/3/action"
PACKAGE_PAGE = f"https://data.humdata.org/dataset/{PACKAGE}"

DATASET = "movement-distribution"
LISTING_DATASET = "movement-distribution-releases"

#: A hundred megabytes over a slow connection.
TIMEOUT_SECONDS = 600.0

#: The date range a release covers, as far as its resource name says. Meta
#: names them a dozen different ways — `2026-08-01_to_08-16`,
#: `2026_June_27-2026_July_01`, `1 June - 15 June, 2026` — so only the leading
#: ISO date is read, which every recent name carries.
_ISO_DATE = re.compile(r"(?P<year>20\d{2})[-_](?P<month>\d{2})[-_](?P<day>\d{2})")


def package_url() -> str:
    return f"{API_ROOT}/package_show?id={PACKAGE}"


@dataclass(frozen=True, slots=True)
class Release:
    """One CSV resource: a date range of movement figures."""

    resource_id: str
    name: str
    url: str
    modified: datetime | None

    @property
    def filename(self) -> str:
        """A filename that says which resource this is.

        Meta's own names collide across releases — two are called
        `Movement Distribution Maps...csv` with only the dates between them —
        and the resource id is what HDX guarantees unique.
        """
        return f"{self.resource_id}.csv"

    @property
    def covers(self) -> str | None:
        match = _ISO_DATE.search(self.name)
        if match is None:
            return None
        return f"{match.group('year')}-{match.group('month')}-{match.group('day')}"


def _modified(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def releases(package: dict[str, Any]) -> list[Release]:
    """The CSV releases in a CKAN package, newest first.

    The readme PDF and any non-CSV resource are left out: they are
    documentation, and landing them under a data dataset would put a leaflet
    where the figures belong.
    """
    found = [
        Release(
            resource_id=str(resource.get("id") or ""),
            name=str(resource.get("name") or ""),
            url=str(resource.get("download_url") or resource.get("url") or ""),
            modified=_modified(resource.get("last_modified") or resource.get("created")),
        )
        for resource in package.get("resources", [])
        if str(resource.get("format") or "").upper() == "CSV"
    ]
    usable = [release for release in found if release.resource_id and release.url]
    return sorted(usable, key=lambda r: (r.modified is not None, r.modified), reverse=True)


class MovementDistribution(Source):
    """Meta's movement distribution releases, newest first."""

    meta = SourceMeta(
        slug="hdx-meta-movement-distribution",
        name="Meta Movement Distribution (via HDX)",
        organization="Data for Good at Meta",
        category=Category.RESEARCH,
        source_type=SourceType.RESEARCH_REPOSITORY,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=PACKAGE_PAGE,
        # The files are global; Indonesia is selected in extraction.
        country=None,
        license="CC-BY-4.0",
        # Meta publishes roughly every fortnight, irregularly.
        update_frequency=UpdateFrequency.IRREGULAR,
        max_requests_per_second=1.0,
        # Weekly, which catches a fortnightly release without downloading a
        # hundred megabytes a day.
        schedule="0 5 * * 1",
        notes="One CSV per date range, global. Indonesia is selected at extraction.",
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        # The token where there is one. httpx drops an Authorization header on
        # a cross-origin redirect, and HDX redirects the CSV to its object
        # store — so the credential reaches HDX and not Meta's bucket, which
        # is what should happen and is worth knowing rather than discovering.
        headers = bearer(credentials().hdx_api_token)
        log.info("hdx.collect", package=PACKAGE, authenticated=bool(headers))

        with fetcher(timeout=TIMEOUT_SECONDS, headers=headers) as http:
            response = http.get(package_url())
            body = response.json()
            if not body.get("success") or not isinstance(body.get("result"), dict):
                raise ValueError(f"HDX did not return a package for {PACKAGE!r}: {str(body)[:200]}")
            package = body["result"]

            available = releases(package)
            if not available:
                raise ValueError(f"HDX package {PACKAGE!r} lists no CSV resources")

            # What was on offer today, kept because a CSV carries neither its
            # date range nor the license it was published under.
            yield Artifact(
                content=json.dumps(package, ensure_ascii=False, separators=(",", ":")).encode(),
                filename="package.json",
                dataset=LISTING_DATASET,
                source_url=str(response.url),
                media_type="application/json",
                metadata={
                    "package": PACKAGE,
                    "resources": len(available),
                    "license": package.get("license_id"),
                    "last_modified": package.get("last_modified"),
                },
            )

            if ctx.since:
                available = [
                    release
                    for release in available
                    if release.modified is None or release.modified.date() >= ctx.since
                ]

            # One release unless asked for more: each is around a hundred
            # megabytes, and the archive is over a gigabyte.
            wanted = available[: ctx.limit] if ctx.limit else available[:1]

            for release in wanted:
                data = http.get(release.url)
                published = release.modified.date() if release.modified else None

                yield Artifact(
                    content=data.content,
                    filename=release.filename,
                    dataset=DATASET,
                    source_url=release.url,
                    media_type="text/csv",
                    published_at=published,
                    # The release's own start date where its name states one,
                    # so a query for a month reads one partition rather than
                    # the whole archive.
                    partition=self._partition(release, published),
                    metadata={
                        "resource_id": release.resource_id,
                        "resource_name": release.name,
                        "covers_from": release.covers,
                        "package": PACKAGE,
                        "last_modified": data.headers.get("last-modified"),
                        "etag": data.headers.get("etag"),
                    },
                )

    @staticmethod
    def _partition(release: Release, published: date | None) -> tuple[str, ...]:
        covers = release.covers
        if covers:
            return (f"year={covers[:4]}", f"month={covers[5:7]}")
        if published:
            return (f"year={published.year}", f"month={published.month:02d}")
        # A resource that states no date at all: landed unpartitioned rather
        # than under a guessed month, which would file it wrongly forever.
        log.warning("hdx.undated_release", resource=release.resource_id, name=release.name)
        return ()
