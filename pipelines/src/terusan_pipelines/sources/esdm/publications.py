"""ESDM — the statistics the energy ministry publishes.

The publication shelf holds the oil and gas statistics, the electricity
statistics, the mineral and coal statistics, and the yearly performance
reports, each its own PDF on the same page. They land here so that an extractor
can be written against bytes we hold rather than against a page that may be
rebuilt first.

The annual Handbook of Energy and Economic Statistics is skipped here. It is on
the same page, and it lands through `esdm-heesi`, which holds one edition per
year as a document. Landing it from both would put the same PDF under two
collections and make "how many editions do we hold" a question with two
answers.
"""

from __future__ import annotations

import re
from typing import Any

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import FileIndexSource

STATISTICS_PAGE = "https://www.esdm.go.id/id/publikasi/statistik"

#: What `esdm-heesi` lands, matched on the words in its filename.
_HANDBOOK = re.compile(r"handbook-of-energy", re.IGNORECASE)


class Publications(FileIndexSource):
    """The statistical publications, excluding the handbook."""

    meta = SourceMeta(
        slug="esdm-publications",
        name="ESDM — Publikasi statistik sektor energi",
        organization="Kementerian Energi dan Sumber Daya Mineral",
        category=Category.STATISTICS,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=STATISTICS_PAGE,
        license="ESDM terms of use",
        update_frequency=UpdateFrequency.ANNUAL,
        max_requests_per_second=1.0,
        schedule="0 4 6 * *",
        notes=(
            "Oil and gas, electricity, and mineral and coal statistics. The "
            "annual handbook is excluded: it lands through esdm-heesi."
        ),
    )

    dataset = "esdm-statistics"
    index_dataset = "esdm-statistics-listing"
    index_urls = (STATISTICS_PAGE,)
    file_pattern = re.compile(r"\.pdf(\?|$)", re.IGNORECASE)
    exclude_pattern = _HANDBOOK
    max_files = 40

    def file_metadata(self, url: str) -> dict[str, Any]:
        return {"publication_page": STATISTICS_PAGE}
