"""Satu Data Pertanian — production, area and yield, crop by crop.

What Indonesia grows, where, and how much of it: rice, maize, palm oil, coffee,
cocoa and the rest, by province and by year, with harvested area and yield
beside the production figure. Commodity prices already arrive from the futures
markets through the Yahoo sources; this is the physical side of the same
question, and the two only mean something together.

The portal renders its dataset listing server-side and offers the figures as
documents behind it, so the listing pages land and the documents follow from
them. The ministry's older statistics portal at `11ap.pertanian.go.id` carries
the long series and is linked from here; it is left for a source of its own
rather than followed, because a crawl that wanders onto a second host under
this slug is a crawl nobody can account for afterwards.
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

SITE = "https://satudata.pertanian.go.id"


class AgricultureData(FileIndexSource):
    """The dataset listing, and the documents it links."""

    meta = SourceMeta(
        slug="kementan-satudata",
        name="Satu Data Pertanian",
        organization="Kementerian Pertanian",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=SITE,
        license="Public sector information",
        update_frequency=UpdateFrequency.IRREGULAR,
        max_requests_per_second=0.5,
        schedule="0 6 16 * *",
        notes=(
            "Production, harvested area and yield by commodity and province. "
            "The long series live on the ministry's older statistics portal, "
            "which is a source of its own when something needs them."
        ),
    )

    dataset = "agriculture-statistics"
    index_dataset = "agriculture-listing"
    index_urls = (f"{SITE}/datasets", f"{SITE}/datasets/infografis")
    # Documents only. The portal also links the Satu Data regulations, which
    # are law and belong to the BPK sources rather than here.
    file_pattern = re.compile(r"\.(pdf|xlsx?|csv)(\?|$)", re.IGNORECASE)
    exclude_pattern = re.compile(r"/docs/regulasi/", re.IGNORECASE)
    # The listing links 277 dataset pages and no files; each dataset page links
    # its own workbook. Without following one level this source lands a
    # catalogue and no figures.
    follow_pattern = re.compile(r"/datasets/detail_data/\d+", re.IGNORECASE)
    max_followed = 120
    max_files = 60
