"""World Bank indicator API — a paginated JSON API.

Ported from an earlier warehouse. The shape is worth keeping: pages are landed
exactly as returned, never merged or reshaped here. Keeping RAW byte-identical
to the response is what makes replay possible when the parser improves
(program.md §2.1).

The World Bank publishes thousands of indicators through one endpoint shape, so
a source is a few lines of declaration rather than a file of its own logic —
`WorldBankIndicator` holds the fetching and each subclass names its series.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

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
from ..http import fetcher

API_ROOT = "https://api.worldbank.org/v2/country/all/indicator"

#: A guard against a runaway `pages` value from upstream. A malformed envelope
#: claiming sixty thousand pages should stop the run, not walk them.
MAX_PAGES = 100

PER_PAGE = 20_000


def indicator_url(code: str) -> str:
    return f"{API_ROOT}/{code}"


class WorldBankIndicator(Source, abstract=True):
    """One World Bank series. Subclasses set `indicator_code` and `meta`."""

    #: The World Bank's own code, e.g. NY.GDP.MKTP.CD.
    indicator_code: str

    #: The Bronze dataset the pages land under. Separate per series so a
    #: normalization run reads one indicator rather than filtering the lot.
    dataset: str

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        url = indicator_url(self.indicator_code)

        with fetcher() as http:
            page, total_pages = 1, 1

            while page <= total_pages and page <= MAX_PAGES:
                response = http.get(
                    url, params={"format": "json", "per_page": PER_PAGE, "page": page}
                )
                body = response.json()

                # World Bank answers [metadata, [records...]]. A different shape
                # means the API changed, and guessing at it would land rubbish.
                if not isinstance(body, list) or len(body) < 2:
                    raise ValueError(f"unexpected API envelope on page {page}: {str(body)[:200]}")

                total_pages = int(body[0].get("pages", 1))

                yield Artifact(
                    # Re-serialized compactly rather than kept verbatim: the API
                    # does not promise stable whitespace, and unstable bytes
                    # would defeat content-addressed deduplication.
                    content=json.dumps(body, separators=(",", ":")).encode(),
                    filename=f"page-{page:03d}.json",
                    dataset=self.dataset,
                    source_url=str(response.url),
                    media_type="application/json",
                    metadata={
                        "indicator_code": self.indicator_code,
                        "page": page,
                        "total_pages": total_pages,
                        "etag": response.headers.get("etag"),
                        "last_modified": response.headers.get("last-modified"),
                    },
                )

                if ctx.limit is not None and page >= ctx.limit:
                    return
                page += 1


class WorldBankGDP(WorldBankIndicator):
    indicator_code = "NY.GDP.MKTP.CD"
    dataset = "gdp"
    meta = SourceMeta(
        slug="worldbank-gdp",
        name="World Bank — GDP (current US$)",
        organization="World Bank",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url=indicator_url("NY.GDP.MKTP.CD"),
        country=None,
        license="CC-BY-4.0",
        update_frequency=UpdateFrequency.ANNUAL,
        max_requests_per_second=4.0,
        # Daily, 06:00. The series updates rarely, but a cheap check beats
        # discovering a revision months late; landing is content-addressed, so
        # an unchanged day writes nothing.
        schedule="0 6 * * *",
        notes="Ported from the lake warehouse. One artifact per API page.",
    )


class WorldBankPopulation(WorldBankIndicator):
    indicator_code = "SP.POP.TOTL"
    dataset = "population"
    meta = SourceMeta(
        slug="worldbank-population",
        name="World Bank — Population, total",
        organization="World Bank",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url=indicator_url("SP.POP.TOTL"),
        country=None,
        license="CC-BY-4.0",
        update_frequency=UpdateFrequency.ANNUAL,
        max_requests_per_second=4.0,
        schedule="0 6 * * *",
    )
