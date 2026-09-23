"""Transport statistics — passengers, freight and the fleet, by mode.

How many people and how many tonnes moved by road, rail, sea and air, port by
port and airport by airport. For an archipelago it is the connectivity measure
everything else is read against: a regency's prices, its access to services and
its exposure to a disaster all depend on what reaches it.

The ministry files these as publications rather than as data, under post
categories — `Statistik-Dan-BIT` for the statistics books and the newsletter
and booklet categories beside it. Those pages land, and the workbooks and PDFs
they link land with them.
"""

from __future__ import annotations

import re

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import FileIndexSource

SITE = "https://dephub.go.id"


class TransportStatistics(FileIndexSource):
    """The statistics and publication categories."""

    meta = SourceMeta(
        slug="kemenhub-statistics",
        name="Kemenhub — Statistik dan publikasi perhubungan",
        organization="Kementerian Perhubungan",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=SITE,
        license="Public sector information",
        update_frequency=UpdateFrequency.ANNUAL,
        max_requests_per_second=0.5,
        schedule="0 6 17 * *",
        notes=(
            "Passenger and freight volumes by mode, and the operational "
            "statistics books. Filed as publications, not as datasets."
        ),
    )

    dataset = "transport-statistics"
    index_dataset = "transport-listing"
    index_urls = (
        f"{SITE}/post/kategori/Statistik-Dan-BIT",
        f"{SITE}/post/kategori/publikasi-daftar-publikasi",
        f"{SITE}/post/kategori/publikasi-booklet",
    )
    file_pattern = re.compile(r"\.(pdf|xlsx?|csv|zip)(\?|$)", re.IGNORECASE)
    max_files = 50
