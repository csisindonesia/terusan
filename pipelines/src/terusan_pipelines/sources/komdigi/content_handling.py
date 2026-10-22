"""Komdigi's figures on negative internet content handled and blocked.

The ministry's open data portal, https://data.komdigi.go.id/opendata, lists the
datasets its Directorate General of Digital Space Supervision (Ditjen
Pengawasan Ruang Digital) publishes on content handling: how many items of
"negative content" it handled on each social media platform in a year, and how
many websites it blocked by category. They are the government's side of the
figures the platforms report, and the observatory reads them beside each other
without treating them as the same count (docs/observatory/methodology.md).

The portal is an Inertia app: each page carries its data as JSON in a
`data-page` attribute, and that JSON names each file by its address on the
portal's internal CKAN — `http://172.30.103.58:5000/dataset/...` — which is not
reachable from outside. The portal's own page rewrites that address onto
`/warehouse/` on the public host before fetching it, and so does this source;
it is the download the portal's Unduh button makes, not an endpoint read off a
private network.

Which datasets are wanted is found rather than listed. The portal's search is
asked for the terms the ministry titles them with, and every result published by
the supervising directorate whose name says content or blocking is taken. A
pinned list would miss the 2025 edition the day it is published, which is the
edition the observatory most needs.

Each dataset's own page lands beside its files. It is the only record of the
title — which is where the year is stated — and of the description.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlencode

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
from ..portals import BROWSER_HEADERS, Endpoint, provenance, published_at, year_in

log = structlog.get_logger(__name__)

PORTAL = "https://data.komdigi.go.id"

SEARCH = f"{PORTAL}/opendata/dataset"

#: The terms the ministry titles these datasets with. Two, because the
#: website figures say "pemblokiran" and the social media figures say
#: "penanganan konten", and neither search finds both.
QUERIES = ("penanganan konten", "pemblokiran", "konten negatif")

#: The directorate that publishes them. Matched loosely: the portal spells
#: organisation names in capitals in one place and title case in another.
PUBLISHER = re.compile(r"pengawasan\s+ruang\s+digital", re.IGNORECASE)

#: What a dataset's slug has to say to be one of these. The directorate also
#: publishes figures on other things — electronic system registrations — that
#: are not content restrictions.
SUBJECT = re.compile(r"konten|blokir", re.IGNORECASE)

#: Bronze datasets: the files, and the pages describing them.
DATASET = "content-handling"
PAGE_DATASET = "dataset-pages"

#: Pages of search results one run reads per query. Each holds ten; the
#: directorate publishes a handful of these datasets, and a search that runs
#: past five pages has matched something it should not.
MAX_PAGES = 5

_DATA_PAGE = re.compile(r'data-page="([^"]+)"')

#: The scheme and host of an internal CKAN address, which the portal replaces
#: with its public `/warehouse/` path.
_HOST = re.compile(r"^https?://[^/]+/")


def page_props(page: str) -> dict[str, Any]:
    """The JSON an Inertia page carries, or nothing where it carries none."""
    match = _DATA_PAGE.search(page)
    if not match:
        return {}
    try:
        return json.loads(html.unescape(match.group(1))).get("props") or {}
    except (ValueError, AttributeError):
        return {}


def search_results(props: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """The datasets on one page of search results, and how many pages there are."""
    results = (props.get("datasetsArray") or {}).get("results") or {}
    if isinstance(results, list):
        return results, 1
    return list(results.get("data") or []), int(results.get("last_page") or 1)


def wanted(dataset: dict[str, Any]) -> bool:
    """Whether a search result is one of the content handling datasets."""
    organization = dataset.get("organization") or {}
    publisher = " ".join(str(organization.get(k) or "") for k in ("title", "name"))
    return bool(PUBLISHER.search(publisher)) and bool(SUBJECT.search(dataset.get("name") or ""))


def public_url(resource_url: str) -> str:
    """Where the portal itself fetches a file its CKAN names internally."""
    return _HOST.sub(f"{PORTAL}/warehouse/", resource_url, count=1)


class KomdigiContentHandling(Source):
    """Negative content handled on social media, and websites blocked."""

    meta = SourceMeta(
        slug="komdigi-content-handling",
        name="Komdigi — Penanganan konten internet negatif",
        organization="Kementerian Komunikasi dan Digital",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=f"{PORTAL}/opendata",
        license="Satu Data Indonesia open data",
        update_frequency=UpdateFrequency.MONTHLY,
        schedule="20 4 3 * *",
        notes=(
            "Ditjen Pengawasan Ruang Digital: negative content handled per social media "
            "platform by year, and websites blocked by content category."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        landed = 0
        with fetcher(headers=dict(BROWSER_HEADERS)) as http:
            for dataset in self._datasets(http):
                if ctx.limit is not None and landed >= ctx.limit:
                    return
                for artifact in self._dataset(dataset, http):
                    yield artifact
                    landed += 1

    def _datasets(self, http: Fetcher) -> Iterator[dict[str, Any]]:
        """Every wanted dataset the searches find, once each."""
        seen: set[str] = set()
        for query in QUERIES:
            page_number, last_page = 1, 1
            while page_number <= min(last_page, MAX_PAGES):
                params = {"q": query, **({"page": page_number} if page_number > 1 else {})}
                response = http.try_get(f"{SEARCH}?{urlencode(params)}")
                if response is None:
                    break
                results, last_page = search_results(page_props(response.text))
                for dataset in results:
                    name = dataset.get("name") or ""
                    if name and name not in seen and wanted(dataset):
                        seen.add(name)
                        yield dataset
                page_number += 1
        if not seen:
            # The searches answered and matched nothing: the portal renamed the
            # directorate or the datasets, and a green run landing nothing would
            # read as a ministry that stopped publishing.
            raise RuntimeError(
                "komdigi-content-handling: no content handling dataset found on the "
                "portal; the search terms or the publisher name must be found again"
            )

    def _dataset(self, dataset: dict[str, Any], http: Fetcher) -> Iterator[Artifact]:
        name = dataset["name"]
        title = dataset.get("title") or name
        organization = (dataset.get("organization") or {}).get("title")
        described = {
            "provider": "komdigi",
            "dataset_name": name,
            "dataset_title": title,
            "dataset_notes": dataset.get("notes"),
            "organization": organization,
            "metadata_modified": dataset.get("metadata_modified"),
        }

        page_url = f"{SEARCH}/{name}"
        page = http.try_get(page_url)
        if page is not None:
            yield Endpoint(
                dataset=PAGE_DATASET,
                url=page_url,
                filename=f"{name}.html",
                media_type="text/html",
                metadata={**described, "role": "detail"},
            ).artifact(page)

        for resource in dataset.get("resources") or []:
            internal = resource.get("url") or ""
            if not internal:
                continue
            url = public_url(internal)
            response = http.try_get(url)
            if response is None:
                continue
            filename = internal.rsplit("/", 1)[-1] or f"{resource.get('id')}.bin"
            year = year_in(resource.get("name"), title)
            yield Artifact(
                content=response.content,
                filename=filename,
                dataset=DATASET,
                source_url=url,
                media_type=_media_type(resource.get("format"), filename),
                published_at=published_at(response),
                partition=(f"year={year}",) if year else (),
                metadata={
                    **described,
                    **provenance(response),
                    "resource_id": resource.get("id"),
                    "resource_name": resource.get("name"),
                    "resource_format": resource.get("format"),
                    "internal_url": internal,
                    "index_url": page_url,
                },
            )


def _media_type(declared: str | None, filename: str) -> str | None:
    """The file's type from what the portal declares, then from its name.

    The portal's warehouse answers every file as `application/octet-stream`,
    which says nothing; landing checks the bytes against this either way.
    """
    kind = (declared or filename.rsplit(".", 1)[-1]).strip().lower()
    return {
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xls": "application/vnd.ms-excel",
        "csv": "text/csv",
        "pdf": "application/pdf",
        "json": "application/json",
    }.get(kind)
