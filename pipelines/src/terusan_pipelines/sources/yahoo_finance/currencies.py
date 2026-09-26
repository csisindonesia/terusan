"""Exchange rates, daily.

What the rupiah is worth, against the currencies Indonesia actually settles
in: the dollar it prices its oil and coal against, the euro, yen and pound its
debt is partly denominated in, and the Singapore, Malaysian and Thai currencies
it trades across a land and sea border with.

One source rather than one per pair, because a reader asking for "the exchange
rate" wants the table, not eight collections that happen to sit beside each
other. Eight requests per run against an endpoint that answers in about a
second is not a burden worth splitting.

Two things about the symbols are worth knowing, because both were found by
asking the API rather than by assuming:

`XXXIDR=X` — a cross quoted in rupiah — exists for the dollar, euro, yen,
pound, Singapore dollar, ringgit and baht, with five years behind it. It does
*not* exist with any history for the yuan, the peso, the dong, the Brunei
dollar, the riel, the kip or the kyat: Yahoo answers `CNYIDR=X` and `PHPIDR=X`
with a single row dated today and 404s the rest. So the yuan is carried here
as `USDCNY=X`, quoted in yuan per dollar, and the cross against the rupiah is
left to whoever needs it. Deriving it here would put a number into Silver that
no one published, which is the one thing a source must not do.

That leaves the Philippines, Vietnam, Brunei, Cambodia, Laos and Myanmar out of
the ASEAN set. Each is available on the dollar basis — `USDPHP=X`, `USDVND=X`,
`USDBND=X`, `USDKHR=X`, `USDLAK=X`, `USDMMK=X`, all with five years — and
adding them is one line each in `PAIRS` below, at the cost of a table that
quotes some currencies in rupiah and others in dollars.

Units are not declared here. The response states the quote currency per
instrument, and the pair's own key says which currency is being priced: a
figure under `eur_idr_rate_close` in IDR is rupiah per euro.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

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
from .markets import DEFAULT_YEARS, chart_artifact, chart_url, chart_window

#: The Bronze dataset every pair lands under. One table, eight series.
DATASET = "exchange-rates"


@dataclass(frozen=True, slots=True)
class Pair:
    """One currency pair, as Yahoo carries it."""

    #: Yahoo's ticker.
    symbol: str
    #: The pair in the order it is quoted, lowercased: `eur-idr` is rupiah per
    #: euro. Names the landed file and, underscored, the Silver series.
    key: str
    #: What the series is called where a reader sees it.
    name: str
    notes: str


PAIRS: tuple[Pair, ...] = (
    # -- the five the rupiah is read against ---------------------------------
    Pair(
        "IDR=X",
        "usd-idr",
        "US dollar / rupiah",
        "Yahoo names the dollar leg by the quote currency alone. The reference "
        "rate for Indonesian trade, debt and fuel pricing.",
    ),
    Pair(
        "EURIDR=X",
        "eur-idr",
        "Euro / rupiah",
        "The EU is Indonesia's largest non-Asian export market.",
    ),
    Pair(
        "JPYIDR=X",
        "jpy-idr",
        "Japanese yen / rupiah",
        "Rupiah per single yen, not per hundred — Yahoo quotes the unit yen, so "
        "the figure is a few hundred rupiah rather than tens of thousands.",
    ),
    Pair(
        "GBPIDR=X",
        "gbp-idr",
        "Pound sterling / rupiah",
        "Carried for the sterling leg of Indonesian external debt.",
    ),
    Pair(
        "USDCNY=X",
        "usd-cny",
        "US dollar / Chinese yuan",
        "China is Indonesia's largest trading partner, but Yahoo has no yuan / "
        "rupiah history — `CNYIDR=X` answers with today's row and nothing "
        "behind it. Quoted in yuan per dollar; against the rupiah it is this "
        "divided into `usd-idr`, which is a calculation for the reader rather "
        "than a figure to store.",
    ),
    # -- ASEAN, where a rupiah cross exists ----------------------------------
    Pair(
        "SGDIDR=X",
        "sgd-idr",
        "Singapore dollar / rupiah",
        "Singapore is the largest single source of foreign investment into "
        "Indonesia and the region's pricing centre.",
    ),
    Pair(
        "MYRIDR=X",
        "myr-idr",
        "Malaysian ringgit / rupiah",
        "The palm oil benchmark is a ringgit price, so this is the rate the "
        "CPO contract reads into rupiah through.",
    ),
    Pair(
        "THBIDR=X",
        "thb-idr",
        "Thai baht / rupiah",
        "The third ASEAN cross Yahoo carries against the rupiah.",
    ),
)


class IndonesianExchangeRates(Source):
    """Daily open, high, low and close for each pair in `PAIRS`."""

    years = DEFAULT_YEARS

    meta = SourceMeta(
        slug="yahoo-exchange-rates",
        name="Exchange rates — rupiah crosses and the yuan",
        organization="Yahoo Finance",
        category=Category.STATISTICS,
        source_type=SourceType.MARKET_DATA,
        collection_method=CollectionMethod.API,
        base_url=chart_url("IDR=X"),
        # The rupiah is the subject even where the quote is struck offshore.
        country="ID",
        license="Yahoo Finance terms of use — personal, non-commercial research",
        update_frequency=UpdateFrequency.DAILY,
        max_requests_per_second=2.0,
        # After the New York close, which is where the FX week ends.
        schedule="0 23 * * 1-5",
        notes=(
            "Eight pairs, one artifact each, all landing under one dataset. Five "
            "years by default; an incremental run takes the window since it last "
            "ran. A weekend or holiday arrives as a dated row with no prices, "
            "which Bronze keeps and Silver blanks."
        ),
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        start, end = chart_window(ctx, self.years)
        with fetcher() as http:
            for pair in PAIRS:
                yield chart_artifact(
                    http,
                    pair.symbol,
                    DATASET,
                    start,
                    end,
                    filename=f"{pair.key}.json",
                )
