"""World Bank GDP — a paginated JSON API.

Ported from an earlier warehouse. The shape is worth keeping: pages are landed
exactly as returned, never merged or reshaped here. Keeping RAW byte-identical
to the response is what makes replay possible when the parser improves
(program.md §2.1).
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

BASE_URL = "https://api.worldbank.org/v2/country/all/indicator/NY.GDP.MKTP.CD"

#: A guard against a runaway `pages` value from upstream. A malformed envelope
#: claiming sixty thousand pages should stop the run, not walk them.
MAX_PAGES = 100

PER_PAGE = 20_000


class WorldBankGDP(Source):
    meta = SourceMeta(
        slug="worldbank-gdp",
        name="World Bank — GDP (current US$)",
        organization="World Bank",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url=BASE_URL,
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

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        with fetcher(limiter=None) as http:
            page, total_pages = 1, 1

            while page <= total_pages and page <= MAX_PAGES:
                response = http.get(
                    BASE_URL, params={"format": "json", "per_page": PER_PAGE, "page": page}
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
                    dataset="gdp",
                    source_url=str(response.url),
                    media_type="application/json",
                    metadata={
                        "page": page,
                        "total_pages": total_pages,
                        "etag": response.headers.get("etag"),
                        "last_modified": response.headers.get("last-modified"),
                    },
                )

                if ctx.limit is not None and page >= ctx.limit:
                    return
                page += 1
