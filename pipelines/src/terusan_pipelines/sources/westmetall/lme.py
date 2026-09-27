"""The London Metal Exchange's official prices, as Westmetall republishes them.

Nickel is Indonesia's largest metal export and tin its oldest, and the price
both are sold against is the LME's. The exchange sells that price; Westmetall,
a German metals trader, has printed the daily official settlement, the
three-month price and warehouse stocks on its site since 2008, one HTML table
per metal per year:

    GET /en/markdaten.php?action=table&field=LME_Ni_cash&year=2026

Nothing else free carries the LME series back that far. Yahoo has no nickel or
tin contract at all, and Shanghai's — which Sina serves — is a different
market in a different currency.

One artifact per metal per year, landed as the page arrives. A past year's page
is byte-for-byte stable, so a full pull re-landing it is free; the current
year's page grows by a row a day, and each night's copy supersedes the last in
Silver by retrieval time.

The figures are the LME's, and the LME licenses them. Westmetall publishes them
openly, which makes them fine to read for research; republishing the raw series
is a question for the LME, not for this file.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

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

TABLE_URL = "https://www.westmetall.com/en/markdaten.php"

#: The first year Westmetall's tables reach back to.
FIRST_YEAR = 2008


@dataclass(frozen=True, slots=True)
class Metal:
    #: Westmetall's field name, e.g. `LME_Ni_cash`.
    field: str
    #: The Bronze dataset the metal lands under.
    dataset: str
    #: The commodity registry's id, carried so the extractor can name it.
    commodity: str


METALS: tuple[Metal, ...] = (
    Metal("LME_Ni_cash", "lme-nickel", "nickel"),
    Metal("LME_Sn_cash", "lme-tin", "tin"),
    Metal("LME_Cu_cash", "lme-copper", "copper"),
    Metal("LME_Al_cash", "lme-aluminium", "aluminium"),
    Metal("LME_Zn_cash", "lme-zinc", "zinc"),
    Metal("LME_Pb_cash", "lme-lead", "lead"),
)


def years_to_fetch(ctx: ScrapeContext, today: date) -> range:
    """The years one run asks for.

    An incremental run asks from the year `since` falls in — the nightly's
    week-long window is one page, or two across New Year. A full pull asks for
    everything Westmetall holds.
    """
    first = max(ctx.since.year, FIRST_YEAR) if ctx.since else FIRST_YEAR
    return range(first, today.year + 1)


class WestmetallLme(Source):
    """Six LME base metals: official cash settlement, three-month, stocks."""

    metals: tuple[Metal, ...] = METALS

    meta = SourceMeta(
        slug="westmetall-lme",
        name="LME official prices — nickel, tin, copper, aluminium, zinc, lead",
        organization="London Metal Exchange (via Westmetall)",
        category=Category.STATISTICS,
        source_type=SourceType.MARKET_DATA,
        collection_method=CollectionMethod.SCRAPE,
        base_url=TABLE_URL,
        country=None,
        license=(
            "LME official prices are LME data; republished openly by Westmetall. "
            "Research use; check LME terms before redistributing the raw series."
        ),
        update_frequency=UpdateFrequency.DAILY,
        max_requests_per_second=1.0,
        # The LME's official prices settle at lunchtime in London; 20:00 here is
        # after the afternoon session in any season.
        schedule="0 20 * * 1-5",
        notes=(
            "One HTML table per metal per year, 2008 onwards: the cash settlement "
            "and three-month prices in USD/t and LME warehouse stocks in tonnes. "
            "A full pull is 6 metals × 19 years of pages; the nightly asks for "
            "the current year only."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        years = years_to_fetch(ctx, date.today())
        with fetcher() as http:
            for metal in self.metals:
                for year in years:
                    params = {"action": "table", "field": metal.field, "year": year}
                    response = http.get(TABLE_URL, params=params)
                    yield Artifact(
                        content=response.content,
                        filename=f"{metal.dataset}-{year}.html",
                        dataset=metal.dataset,
                        source_url=str(response.url),
                        media_type="text/html",
                        partition=(f"year={year}",),
                        metadata={
                            "field": metal.field,
                            "commodity": metal.commodity,
                            "year": year,
                        },
                    )
