"""Yahoo Finance chart API — daily index prices.

The endpoint `yfinance` itself calls. Used directly rather than through that
package because a source lands what it received: `yfinance` hands back a parsed
`DataFrame`, and landing a rendering of it would put a parser between the
publisher and RAW — exactly what replay exists to avoid (program.md §2.1). The
JSON below is the original, and `yfinance` remains a perfectly good way to read
the same figures interactively.

One response covers the whole window, so this is one artifact per run rather
than a page loop.
"""

from __future__ import annotations

import json
import time
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

API_ROOT = "https://query1.finance.yahoo.com/v8/finance/chart"

#: How far back a full pull reaches. Five years of trading days is about 1,200
#: points — one response, and enough to read a cycle rather than a mood.
DEFAULT_YEARS = 5

SECONDS_PER_YEAR = 365 * 24 * 3600


def chart_url(symbol: str) -> str:
    return f"{API_ROOT}/{symbol}"


class YahooDailyIndex(Source, abstract=True):
    """Daily open, high, low and close for one index.

    Each subclass names its symbol; the shape is the same for every instrument
    Yahoo carries.
    """

    #: Yahoo's ticker, e.g. `^JKSE`.
    symbol: str
    #: The Bronze dataset these land under.
    dataset: str
    years: int = DEFAULT_YEARS

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        end = int(time.time())
        # `ctx.since` narrows an incremental run; a full pull takes the window.
        start = (
            int(time.mktime(ctx.since.timetuple()))
            if ctx.since
            else end - self.years * SECONDS_PER_YEAR
        )

        with fetcher() as http:
            response = http.get(
                chart_url(self.symbol),
                params={"period1": start, "period2": end, "interval": "1d"},
            )
            body = response.json()

            result = (body.get("chart") or {}).get("result")
            if not result:
                # Yahoo answers 200 with an error object for an unknown symbol,
                # so a missing result is the failure rather than the status.
                error = (body.get("chart") or {}).get("error")
                raise ValueError(f"no chart data for {self.symbol}: {error}")

            yield Artifact(
                # Re-serialized compactly: the API does not promise stable
                # whitespace, and unstable bytes would defeat the
                # content-addressed landing that makes a re-run free.
                content=json.dumps(body, separators=(",", ":")).encode(),
                filename=f"{self.dataset}.json",
                dataset=self.dataset,
                source_url=str(response.url),
                media_type="application/json",
                metadata={
                    "symbol": self.symbol,
                    "interval": "1d",
                    "period1": start,
                    "period2": end,
                },
            )


class JakartaCompositeIndex(YahooDailyIndex):
    """IHSG — Indeks Harga Saham Gabungan, the Jakarta Composite Index."""

    symbol = "^JKSE"
    dataset = "ihsg"
    meta = SourceMeta(
        slug="yahoo-ihsg",
        name="IHSG — Indeks Harga Saham Gabungan (Jakarta Composite Index)",
        organization="Yahoo Finance",
        category=Category.STATISTICS,
        source_type=SourceType.MARKET_DATA,
        collection_method=CollectionMethod.API,
        base_url=chart_url("^JKSE"),
        country="ID",
        license="Yahoo Finance terms of use — personal, non-commercial research",
        update_frequency=UpdateFrequency.DAILY,
        max_requests_per_second=2.0,
        # 18:00 WIB, after the Jakarta close at 16:00 (this runs in the server's
        # timezone; the figures themselves are dated by the exchange).
        schedule="0 18 * * 1-5",
        notes=(
            "One artifact per run holding the whole window. Open, high, low and "
            "close become four series — Silver stores one figure per observation."
        ),
    )
