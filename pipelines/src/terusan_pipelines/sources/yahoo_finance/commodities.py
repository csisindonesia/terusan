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
        "and inactive because there is nothing further to collect. The live "
        "replacement is tradingeconomics-coal — ICE Newcastle, the Asian "
        "benchmark, which is nearer Indonesian coal than API2's European "
        "delivered price ever was.",
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


# -- Metals and steel --------------------------------------------------------
#
# What Indonesia mines, smelts or buys to build with. Nickel and tin are the
# two it matters most for and neither is here: Yahoo carries no contract for
# either (`NI=F`, `LN=F`, `TIN=F` and `SN=F` all answer "Not Found"). Their LME
# prices arrive through `westmetall-lme`, and Shanghai's through Sina.


class Aluminium(YahooCommodity):
    symbol = "ALI=F"
    dataset = "aluminium"
    meta = _meta(
        "yahoo-aluminium",
        "Aluminium — COMEX",
        "ALI=F",
        "Inalum smelts it at Kuala Tanjung, and the bauxite export ban is meant "
        "to push the rest of the ore through domestic refineries. Thin next to "
        "the LME contract; `westmetall-lme` carries that price too.",
    )


class Zinc(YahooCommodity):
    symbol = "ZNC=F"
    dataset = "zinc"
    meta = _meta(
        "yahoo-zinc",
        "Zinc — COMEX",
        "ZNC=F",
        "A small Indonesian export next to nickel and copper, and an input to "
        "galvanised steel. Thinly traded; the LME price is in `westmetall-lme`.",
    )


class IronOre(YahooCommodity):
    symbol = "TIO=F"
    dataset = "iron-ore"
    meta = _meta(
        "yahoo-iron-ore",
        "Iron ore 62% Fe CFR China — COMEX (TSI)",
        "TIO=F",
        "The seaborne benchmark, delivered to China. Indonesia is a minor "
        "exporter; the reason to watch it is Chinese steel demand, which also "
        "sets what Indonesian nickel pig iron and stainless are worth.",
    )


class Silver(YahooCommodity):
    symbol = "SI=F"
    dataset = "silver"
    meta = _meta(
        "yahoo-silver",
        "Silver — COMEX",
        "SI=F",
        "A by-product of Indonesian gold and copper mines, Grasberg's above all.",
    )


class Platinum(YahooCommodity):
    symbol = "PL=F"
    dataset = "platinum"
    meta = _meta(
        "yahoo-platinum",
        "Platinum — NYMEX",
        "PL=F",
        "Not mined in Indonesia. Here because it and palladium are the "
        "catalytic-converter metals, and their price moves with the combustion "
        "car market that Indonesia's nickel-for-batteries bet is against.",
    )


class Palladium(YahooCommodity):
    symbol = "PA=F"
    dataset = "palladium"
    meta = _meta(
        "yahoo-palladium",
        "Palladium — NYMEX",
        "PA=F",
        "Not mined in Indonesia; see yahoo-platinum for why it is followed.",
    )


class HotRolledCoil(YahooCommodity):
    symbol = "HRC=F"
    dataset = "hot-rolled-coil"
    meta = _meta(
        "yahoo-hot-rolled-coil",
        "Hot-rolled coil steel — US Midwest (CRU), COMEX",
        "HRC=F",
        "A US domestic price in dollars per short ton, not a seaborne one. The "
        "nearest free daily read on steel, which is where most nickel ends up "
        "by way of stainless.",
    )
