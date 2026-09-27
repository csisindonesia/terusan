"""Chinese metal futures, daily, from Sina Finance.

China buys most of Indonesia's nickel — as ore before the 2020 export ban, as
nickel pig iron, matte and stainless since — and prices it on the Shanghai
Futures Exchange rather than in London. The two markets part company often
enough to matter: SHFE is quoted in yuan including VAT, and moves with Chinese
stainless demand that the LME only partly sees. `westmetall-lme` carries the
London price; this is the other half.

Sina serves the whole daily history of an exchange's continuous contract as
one JSON array, the endpoint its own quote pages read:

    GET /futures/api/json.php/InnerFuturesNewService.getDailyKLine?symbol=NI0

Each point is `{"d": date, "o", "h", "l", "c", "v": volume, "p": open interest,
"s": settlement}`. The `0` suffix is Sina's continuous main contract: it rolls
from one delivery month to the next as volume moves, so a jump on a roll date
can be the roll rather than the market. No field says when it rolled.

The endpoint takes no date range — every run gets the full history, which is a
few hundred kilobytes — so `--since` has nothing to narrow. The response lands
as received.
"""

from __future__ import annotations

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

API_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/json.php/InnerFuturesNewService.getDailyKLine"
)

#: Every contract here is quoted per metric tonne in yuan. The exchanges price
#: them that way and Sina restates nothing.
UNIT = "CNY/t"


class SinaFuture(Source, abstract=True):
    """The daily bars of one Chinese exchange's continuous contract."""

    #: Sina's symbol, e.g. `NI0`.
    symbol: str
    #: The Bronze dataset these land under.
    dataset: str

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        with fetcher() as http:
            response = http.get(API_URL, params={"symbol": self.symbol})
            body = response.content
            # Sina answers an unknown symbol with 200 and `null`, not an error.
            if not body.strip().startswith(b"["):
                raise ValueError(f"no daily bars for {self.symbol}: {body[:80]!r}")
            yield Artifact(
                content=body,
                filename=f"{self.dataset}.json",
                dataset=self.dataset,
                source_url=str(response.url),
                media_type="application/json",
                metadata={"symbol": self.symbol, "unit": UNIT},
            )


def _meta(slug: str, name: str, exchange: str, symbol: str, notes: str) -> SourceMeta:
    return SourceMeta(
        slug=slug,
        name=name,
        organization=f"{exchange} (via Sina Finance)",
        category=Category.STATISTICS,
        source_type=SourceType.MARKET_DATA,
        collection_method=CollectionMethod.API,
        base_url=f"{API_URL}?symbol={symbol}",
        country=None,
        license="Sina Finance terms of use — personal, non-commercial research",
        update_frequency=UpdateFrequency.DAILY,
        max_requests_per_second=1.0,
        # Shanghai and Dalian close their day session at 15:00 China time,
        # 14:00 WIB; the night session's bar belongs to the next day.
        schedule="0 17 * * 1-5",
        notes=notes,
    )


class ShfeNickel(SinaFuture):
    symbol = "NI0"
    dataset = "shfe-nickel"
    meta = _meta(
        "sina-shfe-nickel",
        "Nickel — Shanghai Futures Exchange (CNY/t)",
        "Shanghai Futures Exchange",
        "NI0",
        "Listed March 2015. Priced in yuan with VAT; the LME price is in "
        "westmetall-lme. The gap between the two is the Chinese premium "
        "Indonesian NPI and matte exporters sell into.",
    )


class ShfeTin(SinaFuture):
    symbol = "SN0"
    dataset = "shfe-tin"
    meta = _meta(
        "sina-shfe-tin",
        "Tin — Shanghai Futures Exchange (CNY/t)",
        "Shanghai Futures Exchange",
        "SN0",
        "Listed March 2015. China is the largest buyer of Indonesian tin; the "
        "LME price is in westmetall-lme.",
    )


class ShfeStainless(SinaFuture):
    symbol = "SS0"
    dataset = "shfe-stainless"
    meta = _meta(
        "sina-shfe-stainless",
        "Stainless steel — Shanghai Futures Exchange (CNY/t)",
        "Shanghai Futures Exchange",
        "SS0",
        "Listed September 2019. Where most of Indonesia's nickel ends up: "
        "Morowali and Weda Bay feed Chinese-owned stainless mills, and the "
        "LME has no stainless contract.",
    )


class ShfeAluminium(SinaFuture):
    symbol = "AL0"
    dataset = "shfe-aluminium"
    meta = _meta(
        "sina-shfe-aluminium",
        "Aluminium — Shanghai Futures Exchange (CNY/t)",
        "Shanghai Futures Exchange",
        "AL0",
        "History from 2005.",
    )


class ShfeZinc(SinaFuture):
    symbol = "ZN0"
    dataset = "shfe-zinc"
    meta = _meta(
        "sina-shfe-zinc",
        "Zinc — Shanghai Futures Exchange (CNY/t)",
        "Shanghai Futures Exchange",
        "ZN0",
        "History from 2007.",
    )


class DceIronOre(SinaFuture):
    symbol = "I0"
    dataset = "dce-iron-ore"
    meta = _meta(
        "sina-dce-iron-ore",
        "Iron ore — Dalian Commodity Exchange (CNY/t)",
        "Dalian Commodity Exchange",
        "I0",
        "Listed October 2013. China's domestic iron ore price; the seaborne "
        "benchmark in dollars is yahoo-iron-ore.",
    )


class DceCokingCoal(SinaFuture):
    symbol = "JM0"
    dataset = "dce-coking-coal"
    meta = _meta(
        "sina-dce-coking-coal",
        "Coking coal — Dalian Commodity Exchange (CNY/t)",
        "Dalian Commodity Exchange",
        "JM0",
        "Listed March 2013. Metallurgical coal, not the thermal coal Indonesia "
        "mostly exports — but China's own coal price, and the nearest live one "
        "since yahoo-thermal-coal stopped printing.",
    )
