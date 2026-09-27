"""Trading Economics market charts — daily commodity prices with history.

Indonesia is the world's largest thermal coal exporter and has had no live coal
price in the lake since Yahoo's API2 contract stopped printing in December
2025. Trading Economics' "Coal" page charts ICE Newcastle front-month futures —
the Asian seaborne benchmark Indonesian cargoes are priced against — and its
chart script reads the history from the same CloudFront host the economics
charts use, under `/markets/` rather than `/economics/`:

    GET {datasource}/markets/xal1:com?d1=2021-01-01&d2=2026-01-01&interval=1d&v=...

The body uses the same base64, XOR and gzip scheme (see `charts.py`), and
decodes to one object whose `series[0].data` is `[[unix_ts, value, change,
change_pct], ...]` with the unit on the series.

Two quirks the source works around:

* `span=max` is thinned to monthly points. Daily figures come from explicit
  `d1`/`d2` windows, five years at a time, back to December 2008.
* CloudFront caches on `v` and ignores `d1`/`d2`, so every request carries a
  `v` naming its own window and the day it was asked — otherwise a second
  window is answered with the first one's figures.

The payload lands still encoded, as the economics charts do.
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
from .charts import DEFAULT_DATASOURCE

#: How much history one request asks for. Five years of trading days is about
#: 1,250 points, which the endpoint returns daily.
WINDOW_YEARS = 5


@dataclass(frozen=True, slots=True)
class Window:
    start: date
    end: date


def windows(first: date, today: date, years: int = WINDOW_YEARS) -> list[Window]:
    """Consecutive date windows from `first` to `today`, `years` long at most."""
    spans: list[Window] = []
    start = first
    while start <= today:
        try:
            end = start.replace(year=start.year + years)
        except ValueError:  # 29 February
            end = start.replace(year=start.year + years, day=28)
        end = min(end, today)
        spans.append(Window(start, end))
        start = date.fromordinal(end.toordinal() + 1)
    return spans


def market_url(symbol: str, datasource: str = DEFAULT_DATASOURCE) -> str:
    return f"{datasource.rstrip('/')}/markets/{symbol.lower()}"


class TradingEconomicsMarket(Source, abstract=True):
    """One Trading Economics market chart, daily, in date windows."""

    #: Trading Economics' market symbol, e.g. `xal1:com`.
    symbol: str
    #: The Bronze dataset these land under.
    dataset: str
    #: The earliest day the endpoint answers daily for.
    first_day: date
    #: The commodity registry's id, carried to the extractor.
    commodity: str

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        today = date.today()
        first = max(ctx.since, self.first_day) if ctx.since else self.first_day
        url = market_url(self.symbol)
        with fetcher() as http:
            for window in windows(first, today):
                d1, d2 = window.start.isoformat(), window.end.isoformat()
                response = http.get(
                    url,
                    params={
                        "d1": d1,
                        "d2": d2,
                        "interval": "1d",
                        # The cache key. See the module docstring.
                        "v": f"{d1}_{d2}_{today.isoformat()}",
                    },
                )
                yield Artifact(
                    content=response.content,
                    filename=f"{self.dataset}-{d1}-{d2}.json",
                    dataset=self.dataset,
                    source_url=str(response.url),
                    media_type="application/json",
                    metadata={
                        "kind": "market",
                        "symbol": self.symbol,
                        "commodity": self.commodity,
                        "d1": d1,
                        "d2": d2,
                    },
                )


class NewcastleCoal(TradingEconomicsMarket):
    """ICE Newcastle thermal coal, front month, USD/t."""

    symbol = "xal1:com"
    dataset = "newcastle-coal"
    first_day = date(2008, 12, 1)
    commodity = "thermal-coal"

    meta = SourceMeta(
        slug="tradingeconomics-coal",
        name="Thermal coal — ICE Newcastle (Trading Economics)",
        organization="ICE Futures Europe (via Trading Economics)",
        category=Category.STATISTICS,
        source_type=SourceType.MARKET_DATA,
        collection_method=CollectionMethod.SCRAPE,
        base_url="https://tradingeconomics.com/commodity/coal",
        country=None,
        license=(
            "Trading Economics terms of use — undocumented chart endpoint, research "
            "use only; do not redistribute the raw series"
        ),
        update_frequency=UpdateFrequency.DAILY,
        max_requests_per_second=0.5,
        # After the ICE close in London.
        schedule="30 22 * * 1-5",
        notes=(
            "Daily closing price of the Newcastle front-month contract, the Asian "
            "seaborne benchmark, from December 2008. Replaces yahoo-thermal-coal "
            "(API2 CIF ARA, a European delivered price) which stopped printing in "
            "December 2025. Only a close: the endpoint carries no open, high or low."
        ),
    )
