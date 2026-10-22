"""Google's government requests to remove content — the whole report, zipped.

Google publishes the report's data behind one link on
https://transparencyreport.google.com/government-removals/overview: a zip of
CSVs covering every country from 2009. Inside are two formats, and both are
kept. The current one, used since 2019, gives a row per country, requester,
product and reason with the outcome of every item. The `historical/` folder
holds the format used through 2018: country totals split into court orders and
executive requests, with Google's compliance percentage, and a product and
reason breakdown beside them. The README in the archive says as much, and it is
landed with the rest.

The archive is landed whole, every country in it. The observatory compares
Indonesia with its ASEAN neighbours, and narrowing to one country before landing
would mean asking Google again for figures already downloaded once (program.md
§2.1). The URL is Google's own published download, not an endpoint read out of
the report's JavaScript.
"""

from __future__ import annotations

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import ApiSource, Endpoint

REPORT_PAGE = "https://transparencyreport.google.com/government-removals/overview"

ARCHIVE_URL = "https://storage.googleapis.com/transparencyreport/google-government-removals.zip"

#: The Bronze dataset the archive lands under.
DATASET = "government-removals"


class GoogleGovernmentRemovals(ApiSource):
    """The report's data download, fetched as published."""

    meta = SourceMeta(
        slug="google-government-removals",
        name="Google Transparency Report — Government requests to remove content",
        organization="Google",
        category=Category.RESEARCH,
        source_type=SourceType.RESEARCH_REPOSITORY,
        collection_method=CollectionMethod.BULK_DOWNLOAD,
        base_url=REPORT_PAGE,
        country=None,
        license="Google Transparency Report; figures published for public use with attribution",
        # Google reports half-years, and publishes each one months after it
        # closes. A monthly check costs one request and lands nothing when the
        # archive is unchanged, since landing is content-addressed.
        update_frequency=UpdateFrequency.MONTHLY,
        schedule="0 4 3 * *",
        notes=(
            "Every country, 2009 onward. Since 2019 a row per requester, product and "
            "reason with item outcomes; through 2018 country totals by branch of "
            "government with a compliance percentage."
        ),
    )

    endpoints = (
        Endpoint(
            dataset=DATASET,
            url=ARCHIVE_URL,
            filename="google-government-removals.zip",
            media_type="application/zip",
            metadata={"report_page": REPORT_PAGE, "provider": "google"},
        ),
    )
