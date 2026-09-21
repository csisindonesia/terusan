"""Commodity futures, daily.

Chosen for what Indonesia sells and buys rather than for what a commodities
desk would watch: palm oil and thermal coal are its two largest export
earners, Brent sets the reference its fuel is priced against, and coffee,
cocoa, copper and gold each carry a real export or import line.

Every symbol here was checked against the API before being declared — Yahoo
answers 200 with an error object for a ticker it does not carry, so an
unverified symbol would register a source that can never land anything.
`FCPO=F`, the Bursa Malaysia palm contract, is one such: it does not exist on
this endpoint, and `CPO=F` (the CME's USD-denominated Malaysian palm oil
calendar) is used instead.

Units are not declared here. Yahoo quotes coffee and sugar in US cents and
copper in dollars, and the response says which per instrument — so the figure
carries the currency the exchange gave it rather than one this file asserted.
"""

from __future__ import annotations

from ..base import Category, CollectionMethod, SourceMeta, SourceType, UpdateFrequency
from .markets import YahooDailyIndex, chart_url


class YahooCommodity(YahooDailyIndex, abstract=True):
    """A daily futures series. Same shape as an index; different instrument."""


def _meta(slug: str, name: str, symbol: str, notes: str, *, active: bool = True) -> SourceMeta:
    return SourceMeta(
        slug=slug,
        name=name,
        organization="Yahoo Finance",
        category=Category.STATISTICS,
        source_type=SourceType.MARKET_DATA,
        collection_method=CollectionMethod.API,
        base_url=chart_url(symbol),
        # The contract is priced on a foreign exchange, not in Indonesia. The
        # relevance is Indonesian; the figure is not.
        country=None,
        license="Yahoo Finance terms of use — personal, non-commercial research",
        update_frequency=UpdateFrequency.DAILY,
        max_requests_per_second=2.0,
        # After the US close, on weekdays.
        schedule="0 22 * * 1-5",
        active=active,
        notes=notes,
    )


class PalmOil(YahooCommodity):
    symbol = "CPO=F"
    dataset = "palm-oil"
    meta = _meta(
        "yahoo-palm-oil",
        "Crude palm oil — CME Malaysian calendar (USD)",
        "CPO=F",
        "Indonesia's largest agricultural export. The Malaysian contract is the "
        "regional benchmark; Indonesia has no listed futures of its own. Thinly "
        "traded — about one session in six carries no price.",
    )


class ThermalCoal(YahooCommodity):
    symbol = "MTF=F"
    dataset = "thermal-coal"
    meta = _meta(
        "yahoo-thermal-coal",
        "Thermal coal — API2 CIF ARA (Argus/McCloskey)",
        "MTF=F",
        "Stopped printing on 2025-12-26 — Yahoo still answers with dated rows "
        "but no prices, so a run lands bytes and adds no figures. Left "
        "registered because 1,074 priced sessions through 2025 are real history, "
        "and inactive because there is nothing further to collect. Indonesia is "
        "the largest thermal coal exporter, so a live replacement is worth "
        "finding: API2 is a European delivered price in any case, not an "
        "Indonesian FOB one.",
        active=False,
    )


class BrentCrude(YahooCommodity):
    symbol = "BZ=F"
    dataset = "brent-crude"
    meta = _meta(
        "yahoo-brent-crude",
        "Brent crude oil",
        "BZ=F",
        "The reference Indonesian fuel prices and the ICP are read against.",
    )


class Gold(YahooCommodity):
    symbol = "GC=F"
    dataset = "gold"
    meta = _meta("yahoo-gold", "Gold — COMEX", "GC=F", "A significant Indonesian export.")


class Copper(YahooCommodity):
    symbol = "HG=F"
    dataset = "copper"
    meta = _meta(
        "yahoo-copper",
        "Copper — COMEX",
        "HG=F",
        "Indonesia exports copper concentrate and, since the smelting rules, refined metal.",
    )


class Coffee(YahooCommodity):
    symbol = "KC=F"
    dataset = "coffee"
    meta = _meta(
        "yahoo-coffee",
        "Coffee — ICE Arabica",
        "KC=F",
        "Quoted in US cents, not dollars. Indonesia is a top-five producer, "
        "though mostly of robusta rather than this contract's arabica.",
    )


class Cocoa(YahooCommodity):
    symbol = "CC=F"
    dataset = "cocoa"
    meta = _meta(
        "yahoo-cocoa",
        "Cocoa — ICE",
        "CC=F",
        "Indonesia is a major grinder as well as a grower, so this is an input "
        "price as much as an export one.",
    )
