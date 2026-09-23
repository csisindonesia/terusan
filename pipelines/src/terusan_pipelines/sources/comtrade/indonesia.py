"""UN Comtrade — Indonesia's trade with every partner, year by year.

Comtrade is the independent account of what Kemendag and BPS publish from the
Indonesian side: the same shipments, reported by 200-odd customs authorities,
which is what makes it worth holding beside them rather than instead of them.

The public preview endpoint is what this uses. The full API wants a subscription
key and returns the same rows with a higher ceiling; the preview caps a call at
500 rows, which is why a call here asks for one year, one flow and the
commodity total — Indonesia's partner list fits inside that, and the detailed
HS breakdown does not. When a key is provisioned, `PREVIEW` becomes the keyed
path and the loop is unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from urllib.parse import urlencode

from ..base import (
    Category,
    CollectionMethod,
    ScrapeContext,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from ..http import Fetcher
from ..portals import ApiSource, Endpoint

PREVIEW = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

#: Indonesia, in UN M49. Comtrade keys reporters by this and not by ISO.
REPORTER = 360

#: The commodity total. The partner breakdown at this level is what fits in a
#: preview call; chapter-level detail is a second source and a key.
COMMODITY = "TOTAL"

#: Comtrade revises for years after first publication, so a run re-fetches a
#: window rather than only the latest year. Ten years is two decades of
#: revisions in practice and still twenty small calls.
WINDOW_YEARS = 10

FLOWS = {"M": "imports", "X": "exports"}


#: The partner code for the world, and the transport mode covering all of them.
#: Comtrade reports the world total once per mode of transport as well as
#: across them, so a figure taken on `partnerCode=0` alone is whichever mode
#: came first in the response.
WORLD = 0
ALL_MODES = 0


def query_url(year: int, flow: str, *, world_only: bool = False) -> str:
    """One Comtrade call: a year, a flow, and either every partner or the total.

    Both are asked for, because the 500-row cap makes them different requests
    rather than one answer read two ways. A year of imports runs to two hundred
    partners times six modes of transport, and the row that sums them can fall
    outside the cap — which is exactly what happened to imports for 2021 to
    2025, where the breakdown arrived and the total did not.
    """
    query: dict[str, object] = {
        "reporterCode": REPORTER,
        "period": year,
        "cmdCode": COMMODITY,
        "flowCode": flow,
    }
    if world_only:
        query["partnerCode"] = WORLD
        query["motCode"] = ALL_MODES
    return f"{PREVIEW}?{urlencode(query)}"


class IndonesiaTrade(ApiSource):
    """Annual imports and exports by partner, as Comtrade holds them."""

    meta = SourceMeta(
        slug="comtrade-indonesia",
        name="UN Comtrade — Indonesia annual trade by partner",
        organization="United Nations Statistics Division",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url="https://comtradeplus.un.org/",
        country="ID",
        license="UN Comtrade terms of use — attribution required",
        update_frequency=UpdateFrequency.ANNUAL,
        # The public preview endpoint rate-limits hard: five seconds apart
        # still draws 429s, which the client then has to back off from. Ten is
        # what it tolerates, and a full window — ten years, two flows, the
        # breakdown and the total — is forty calls, so a run takes minutes and
        # happens monthly.
        max_requests_per_second=0.1,
        # Monthly. Comtrade loads a country's year when that country reports,
        # and revises for years afterwards.
        schedule="0 6 12 * *",
        notes=(
            "Public preview endpoint, capped at 500 rows per call: two calls per "
            "year and flow — the partner breakdown, and the world total asked "
            "for on its own because the cap can cut it off. A subscription key "
            "raises the cap and opens the HS breakdown."
        ),
    )

    def endpoints_for(self, ctx: ScrapeContext, http: Fetcher) -> Iterator[Endpoint]:
        # Comtrade publishes a year well after it ends, so the newest year worth
        # asking for is the last complete one.
        latest = date.today().year - 1
        first = ctx.since.year if ctx.since else latest - WINDOW_YEARS + 1

        for year in range(first, latest + 1):
            for flow, name in FLOWS.items():
                common = {
                    "reporter": REPORTER,
                    "flow": flow,
                    "flow_name": name,
                    "period": year,
                    "commodity": COMMODITY,
                    "endpoint": "public preview",
                }

                yield Endpoint(
                    dataset=f"trade-{name}",
                    url=query_url(year, flow),
                    filename=f"comtrade-{REPORTER}-{name}-{year}.json",
                    partition=(f"year={year}",),
                    metadata={**common, "scope": "by partner"},
                )

                yield Endpoint(
                    dataset=f"trade-{name}-total",
                    url=query_url(year, flow, world_only=True),
                    filename=f"comtrade-{REPORTER}-{name}-total-{year}.json",
                    partition=(f"year={year}",),
                    metadata={**common, "scope": "world, all modes of transport"},
                )
