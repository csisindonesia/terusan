"""OpenStreetMap Indonesia, from Geofabrik's daily extract.

Geofabrik re-cuts the Indonesian extract every day: roads, buildings, land use
and every point of interest anyone has mapped, as one `.osm.pbf`. It is the
base map behind any question about what is *near* something — a mine, a
disaster, a polling station — and it is a gigabyte.

That size decides the shape of this source. A daily run landing a gigabyte is
360 GB a year of a file whose previous day is almost entirely the same bytes,
and content-addressed landing does not help: one changed node changes the whole
hash. So the default run lands what is small and says everything about the
edition — the index page, the checksum and the boundary polygon — and the
extract itself is fetched only when a run asks for it:

    terusan sources run osm-geofabrik --param full=true

which is the deliberate act it should be. The checksum landed daily is what
says whether a held extract is still current, which is the question the daily
run exists to answer.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..base import (
    Category,
    CollectionMethod,
    ScrapeContext,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..http import Fetcher
from ..portals import ApiSource, Endpoint, wants

INDEX = "https://download.geofabrik.de/asia/indonesia.html"
BASE = "https://download.geofabrik.de/asia"

#: The extract and its companions. Geofabrik keeps these names stable across
#: daily cuts, which is what makes `-latest-` addressable at all.
EXTRACT = f"{BASE}/indonesia-latest.osm.pbf"
CHECKSUM = f"{EXTRACT}.md5"
BOUNDARY = f"{BASE}/indonesia.poly"


class IndonesiaExtract(ApiSource):
    """The daily edition's metadata, and the extract on request."""

    meta = SourceMeta(
        slug="osm-geofabrik",
        name="OpenStreetMap Indonesia — Geofabrik extract",
        organization="Geofabrik GmbH / OpenStreetMap contributors",
        category=Category.GOVERNMENT,
        source_type=SourceType.RESEARCH_REPOSITORY,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=INDEX,
        license="ODbL-1.0 — OpenStreetMap contributors",
        update_frequency=UpdateFrequency.DAILY,
        max_requests_per_second=0.5,
        schedule="0 4 * * *",
        notes=(
            "Daily: the index page, the MD5 and the boundary polygon. The 1 GB "
            "extract only with params={'full': True}, because a daily gigabyte "
            "of a file that re-hashes whole is not a backup, it is a bill."
        ),
    )

    # The whole extract takes minutes on a slow link.
    timeout = 900.0

    def endpoints_for(self, ctx: ScrapeContext, http: Fetcher) -> Iterator[Endpoint]:
        yield Endpoint(
            dataset="osm-editions",
            url=CHECKSUM,
            filename="indonesia-latest.osm.pbf.md5",
            media_type="text/plain",
            metadata={"role": "checksum", "identifies": EXTRACT},
        )
        yield Endpoint(
            dataset="osm-editions",
            url=BOUNDARY,
            filename="indonesia.poly",
            media_type="text/plain",
            metadata={"role": "boundary", "coverage": "the cut Geofabrik calls Indonesia"},
        )
        yield Endpoint(
            dataset="osm-editions",
            url=INDEX,
            filename="indonesia.html",
            media_type="text/html",
            metadata={"role": "index", "coverage": "what this edition offers and in which formats"},
        )

        if wants(ctx, "full"):
            yield Endpoint(
                dataset="osm-extract",
                url=EXTRACT,
                filename="indonesia-latest.osm.pbf",
                media_type="application/octet-stream",
                metadata={"role": "extract", "note": "approximately 1 GB"},
            )
