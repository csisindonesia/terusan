"""TikTok's Government Removal Requests Report — the CSV behind the page.

The report page at https://www.tiktok.com/transparency/en/government-removal-requests/
carries one Download link: a CSV on TikTok's CDN holding every half-year the
report covers, one row per country, period and measure. Each release replaces
it with a new file under a new path — `2025H2_CLIGR/Final/Downloads/...` — so
the link is read off the page each run rather than pinned. A pinned URL keeps
answering after the next release and quietly serves the previous one.

What the file holds was checked against the 2025 H2 release: country rows from
January–June 2022, and for 2019–2021 only a worldwide total of requests — the
per-country figures for those years are on the report's archived pages and not
in the download. docs/observatory/source_registry.md carries the detail.

The page lands too. It is the only record of what TikTok called the file on
the day, and of the fact that an older file stopped being linked.
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

REPORT_PAGE = "https://www.tiktok.com/transparency/en/government-removal-requests/"

#: The Bronze dataset the CSV lands under.
DATASET = "government-removal-requests"


class TikTokGovernmentRemovals(FileIndexSource):
    """The report's CSV download, found on the report page."""

    meta = SourceMeta(
        slug="tiktok-government-removals",
        name="TikTok — Government Removal Requests Report",
        organization="TikTok",
        category=Category.RESEARCH,
        source_type=SourceType.RESEARCH_REPOSITORY,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=REPORT_PAGE,
        country=None,
        license="TikTok Transparency Center; figures published for public use with attribution",
        update_frequency=UpdateFrequency.MONTHLY,
        schedule="10 4 3 * *",
        notes=(
            "Requests, content and accounts specified, and how many were actioned under "
            "local law or community guidelines, per country and half-year from 2022."
        ),
    )

    index_urls = (REPORT_PAGE,)

    #: The government removals CSV and nothing else the page links. `GRFCR` is
    #: TikTok's own code for this report, and appears in the file's path and in
    #: its `aspect` column.
    file_pattern = re.compile(r"tiktokcdn\.com/.+GRFCR[^/]*\.csv$", re.IGNORECASE)

    dataset = DATASET
    index_dataset = "report-pages"

    #: One file per release. Anything more is the page linking something new,
    #: which is worth landing once and looking at, not walking.
    max_files = 5

    def file_metadata(self, url: str) -> dict[str, str]:
        release = re.search(r"/(\d{4}H[12])_", url)
        return {
            "provider": "tiktok",
            "report_page": REPORT_PAGE,
            **({"release": release.group(1)} if release else {}),
        }

    def partition_for(self, url: str) -> tuple[str, ...]:
        # The release, not a year: a file holding 2019 through 2025 is not
        # about any one of them, and the release is what tells two files apart.
        release = re.search(r"/(\d{4}H[12])_", url)
        return (f"release={release.group(1)}",) if release else ()
