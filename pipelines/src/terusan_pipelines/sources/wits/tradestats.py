"""WITS TradeStats — revealed comparative advantage by sector, from the API.

WITS computes a set of trade indicators off UN Comtrade and publishes them
through an SDMX endpoint that needs no key: RCA, export values, product shares,
by reporter, partner and product group. The product groups are coarse — the 16
HS sections WITS draws (Animal, Vegetable, … Mach and Elec), the SITC groupings
UNCTAD uses (Food, Fuels, Manufactures, …) and UNCTAD's four stages of
processing — and that is the ceiling of what the API holds. RCA at the HS
six-digit level is not in it; WITS only offers that as a bulk download behind
a login, which is where the seed file in `tmp/rca` came from. See
`sources/wits/rca_seed.py`.

One call per reporter and indicator, every product group and every year at
once. The endpoint refuses anything larger — two reporters in one call comes
back as `Response too large due to client request` with a 200 — and a reporter
WITS holds nothing for (Timor-Leste, so far) answers 404 `NoRecordsFound`,
which the shared client treats as the endpoint having nothing rather than as a
failure.

Every year is asked for every time. A reporter's whole history is 70 KB, WITS
revises back years when Comtrade does, and landing is content-addressed, so an
unchanged month writes nothing.
"""

from __future__ import annotations

from collections.abc import Iterator

from ...trade import REPORTERS
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

API = "https://wits.worldbank.org/API/V1/SDMX/V21/datasource/tradestats-trade"

#: The world, as WITS names it. Trade with the world is what RCA is defined on.
PARTNER = "wld"

#: WITS's indicator codes, and what the Bronze dataset is called for each.
INDICATORS: dict[str, str] = {
    "RCA": "rca",
    "XPRT-TRD-VL": "export-value",
    "XPRT-PRDCT-SHR": "export-product-share",
}

SDMX_MEDIA_TYPE = "application/vnd.sdmx.structurespecificdata+xml"


def query_url(reporter: str, indicator: str) -> str:
    """One reporter's whole series for one indicator, every product group."""
    return (
        f"{API}/reporter/{reporter.lower()}/year/all/partner/{PARTNER}"
        f"/product/all/indicator/{indicator}"
    )


class TradeStats(ApiSource):
    """Sector RCA, export values and product shares for Indonesia and its peers."""

    meta = SourceMeta(
        slug="wits-tradestats",
        name="WITS — TradeStats trade indicators",
        organization="World Bank",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url="https://wits.worldbank.org/",
        # Indonesia and the economies it is compared against; the record
        # describes the collection, which is not Indonesia's alone.
        country=None,
        license="World Bank WITS terms of use — attribution required; underlying data UN Comtrade",
        update_frequency=UpdateFrequency.ANNUAL,
        # 66 calls a run. WITS publishes no limit; one a second is what an
        # unauthenticated public API should expect from a batch job.
        max_requests_per_second=1.0,
        # Monthly, mid-month. WITS picks up a reporter's year when Comtrade
        # loads it, which happens across the year rather than on a date.
        schedule="0 5 15 * *",
        notes=(
            "Sector-level only: 16 HS sections, UNCTAD's SITC groups and stages of "
            "processing. HS six-digit RCA is not in the API; the base years at that "
            "level come from the rca-seed source."
        ),
    )

    def endpoints_for(self, ctx: ScrapeContext, http: Fetcher) -> Iterator[Endpoint]:
        for indicator, dataset in INDICATORS.items():
            for reporter in REPORTERS:
                yield Endpoint(
                    dataset=f"tradestats-{dataset}",
                    url=query_url(reporter, indicator),
                    filename=f"wits-{reporter.lower()}-{indicator.lower()}.xml",
                    media_type=SDMX_MEDIA_TYPE,
                    partition=(f"reporter={reporter}",),
                    metadata={
                        "reporter": reporter,
                        "partner": PARTNER.upper(),
                        "indicator": indicator,
                        "document_type": "data_file",
                    },
                )
