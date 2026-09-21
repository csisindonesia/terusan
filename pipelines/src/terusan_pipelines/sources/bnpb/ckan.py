"""Reading BNPB's CKAN catalogue.

data.bnpb.go.id runs CKAN, and two halves of it behave differently. The action
API — `/api/3/action/package_search`, `datastore_search` — answers a plain
client. The HTML pages and the `/download/` URLs do not: Cloudflare serves a
JavaScript challenge there, so the published workbook bytes are out of reach of
an unattended run. What is reachable is the same table through CKAN's
datastore, which is the portal's own rendering of the workbook its uploader
supplied. `disaster.py` explains what that costs.

This module is the reading half — the URLs and the shapes CKAN answers with —
kept separate so it can be tested without a network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

API_ROOT = "https://data.bnpb.go.id/api/3/action"

#: Pusat Data, Informasi dan Komunikasi Kebencanaan: the unit inside BNPB that
#: publishes the disaster figures. Named rather than searched for, so a search
#: that ranks another publisher higher one day cannot quietly land someone
#: else's spreadsheets under this source's slug.
ORGANIZATION = "pusdatinkom"

#: Only the spreadsheets. The same organization also publishes PDFs and
#: geospatial files, which are a different collection and a different parser.
RESOURCE_FORMAT = "XLSX"

PORTAL_URL = (
    f"https://data.bnpb.go.id/dataset/?res_format={RESOURCE_FORMAT}&organization={ORGANIZATION}"
)

#: Datasets per `package_search` page. CKAN caps `rows` at 1000 and the whole
#: holding is under twenty datasets, so this is one request in practice and a
#: loop that still works when BNPB publishes its hundredth.
PAGE_SIZE = 50

#: Rows per datastore page. The largest table here is under a thousand rows, so
#: again one request per resource, with paging that holds if that changes.
DATASTORE_PAGE_SIZE = 10_000

#: Stop paging a resource past this. A datastore that keeps answering full
#: pages is a bug in the loop rather than a million-row table at BNPB.
MAX_DATASTORE_ROWS = 1_000_000

#: A four-digit year in a dataset's name or title — `...-bencana-2025`,
#: `lewotobi-2024`, `datakejadian2000`. It becomes the RAW partition, which is
#: what lets a query for one year skip the other twenty.
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def search_url(start: int = 0, rows: int = PAGE_SIZE) -> str:
    """The catalogue page listing this organization's XLSX datasets.

    The two filter terms are ANDed by Solr, which is what the portal's own
    faceted URL does — this is that page, asked for as JSON.
    """
    query = urlencode(
        {
            "fq": f"res_format:{RESOURCE_FORMAT} organization:{ORGANIZATION}",
            "rows": rows,
            "start": start,
        }
    )
    return f"{API_ROOT}/package_search?{query}"


def datastore_url(resource_id: str, offset: int = 0, limit: int = DATASTORE_PAGE_SIZE) -> str:
    """One page of the table CKAN parsed out of an uploaded workbook."""
    query = urlencode({"resource_id": resource_id, "limit": limit, "offset": offset})
    return f"{API_ROOT}/datastore_search?{query}"


@dataclass(frozen=True, slots=True)
class Resource:
    """One uploaded XLSX file, as the catalogue describes it."""

    resource_id: str
    name: str
    description: str
    url: str
    #: Whether CKAN parsed the upload into its datastore. Roughly a quarter of
    #: these were never parsed, and with downloads challenged those have no
    #: route at all — which the run reports rather than hides.
    datastore_active: bool
    modified: datetime | None
    size_bytes: int | None

    @property
    def filename(self) -> str:
        """What the workbook lands as.

        The publisher's own filename where there is one: it carries the year
        and the table, and the RAW directory is content-addressed, so two
        resources sharing a name do not collide. The id is the fallback, which
        is honest about naming a file after nothing.
        """
        name = self.name.strip()
        if name.lower().endswith(".xlsx"):
            return name
        return f"{self.resource_id}.xlsx"

    def page_filename(self, offset: int) -> str:
        """What one datastore page lands as."""
        stem = self.filename.removesuffix(".xlsx").removesuffix(".XLSX")
        return f"{stem}-{offset:05d}.json"

    @property
    def title(self) -> str:
        """What a reader should see this called."""
        return self.name.strip() or self.description.strip() or self.resource_id


@dataclass(frozen=True, slots=True)
class Package:
    """One CKAN dataset: a published collection of resources."""

    name: str
    title: str
    notes: str
    license_id: str | None
    modified: datetime | None
    resources: tuple[Resource, ...]

    @property
    def year(self) -> str | None:
        match = _YEAR.search(self.name) or _YEAR.search(self.title)
        return match.group(0) if match else None

    @property
    def partition(self) -> tuple[str, ...]:
        """The RAW partition. Empty where the dataset spans years and says so."""
        year = self.year
        return (f"year={year}",) if year else ()


def packages(body: dict[str, Any]) -> tuple[Package, ...]:
    """The datasets in one `package_search` answer, XLSX resources only.

    Datasets whose resources are all some other format are dropped: the filter
    is applied by Solr at the dataset level, so a dataset holding one XLSX and
    six PDFs arrives whole.
    """
    if not body.get("success") or not isinstance(body.get("result"), dict):
        raise ValueError(f"BNPB did not return a catalogue: {str(body)[:200]}")

    found = [_package(raw) for raw in body["result"].get("results") or []]
    return tuple(package for package in found if package.resources)


def total(body: dict[str, Any]) -> int:
    """How many datasets match, which is what says whether to page again."""
    result = body.get("result")
    if not isinstance(result, dict):
        return 0
    try:
        return int(result.get("count") or 0)
    except (TypeError, ValueError):
        return 0


def _package(raw: dict[str, Any]) -> Package:
    return Package(
        name=str(raw.get("name") or ""),
        title=str(raw.get("title") or ""),
        notes=str(raw.get("notes") or ""),
        license_id=str(raw.get("license_id") or "") or None,
        modified=parse_timestamp(raw.get("metadata_modified")),
        resources=tuple(
            resource
            for resource in (_resource(item) for item in raw.get("resources") or [])
            if resource is not None
        ),
    )


def _resource(raw: dict[str, Any]) -> Resource | None:
    if str(raw.get("format") or "").upper() != RESOURCE_FORMAT:
        return None
    if str(raw.get("state") or "active") != "active":
        return None

    resource_id = str(raw.get("id") or "")
    url = str(raw.get("url") or "")
    if not resource_id or not url:
        return None

    size = raw.get("size")
    return Resource(
        resource_id=resource_id,
        name=str(raw.get("name") or ""),
        description=str(raw.get("description") or ""),
        url=url,
        datastore_active=bool(raw.get("datastore_active")),
        modified=parse_timestamp(raw.get("last_modified") or raw.get("created")),
        size_bytes=int(size) if isinstance(size, (int, float)) else None,
    )


def parse_timestamp(value: Any) -> datetime | None:
    """CKAN's ISO timestamps, which carry no zone and are occasionally absent."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
