"""KPK's enforcement statistics — every case it has taken on since 2004.

The commission publishes its caseload as one page of tables, restated each
quarter: the cases at each stage from inquiry to execution, the agencies they
came out of, the posts the accused held, the offences charged, the region, the
court that made each verdict final, and the defendants by sex. It is the only
official count of what the country's anti-corruption body actually prosecutes,
and it reaches back to the commission's first year.

There is no file behind the page and no API; the tables are the publication.
The page is landed whole and `extract/kpk_penindakan.py` reads the tables out
of RAW, so a quarter's restatement is a new artifact beside the last one rather
than an overwrite of it.
"""

from __future__ import annotations

from ..base import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..portals import PageSource

PAGE_URL = "https://www.kpk.go.id/id/publikasi-data/statistik/penindakan-2"


class Penindakan(PageSource):
    """KPK's enforcement caseload by stage, agency, post, offence and region."""

    meta = SourceMeta(
        slug="kpk-penindakan",
        name="Statistik Penindakan KPK",
        organization="Komisi Pemberantasan Korupsi",
        category=Category.GOVERNMENT,
        source_type=SourceType.OFFICIAL_PORTAL,
        collection_method=CollectionMethod.SCRAPE,
        base_url=PAGE_URL,
        license="Public sector information",
        update_frequency=UpdateFrequency.QUARTERLY,
        max_requests_per_second=0.5,
        # KPK restates the page some weeks after each quarter closes; asking on
        # the fifteenth of every month catches it within a month either way.
        schedule="0 7 15 * *",
        notes=(
            "One HTML page of seven tables, 2004 to the current quarter. The "
            "current year's column is year to date, as of the quarter the page "
            "names ('Per-Maret 2026 | TW I')."
        ),
    )

    dataset = "kpk-penindakan"
    urls = (PAGE_URL,)
