"""WITS TradeStats and the RCA seed files into Bronze records.

Two readers, because two shapes of the same question arrive.

**`WitsTradeStatsExtractor`** reads the SDMX the API answers: one `<Series>`
per product group, one `<Obs>` per year. The generic readers would see an XML
document as prose. Each series becomes an indicator per measure and product
group — `wits_rca_84_85_machelec` — with the reporter as the place, so one
indicator holds Indonesia and its peers side by side.

**`RcaSeedExtractor`** reads the two HS six-digit working files `rca-seed`
lands, and does more than Bronze usually does: it sums. Five thousand HS
subheadings times thirty years times twenty-two economies is three million
rows, and none of them is a series a reader will look for. What they add up to
is: how much of each environmental goods list a country exports, what share of
its exports that is, how many of the list's products it has a comparative
advantage in, and — where the world's figure for every product is at hand —
the RCA of the list as a basket. Silver's normalizer maps a row to an
observation and does not aggregate, so a sum made anywhere else would be a sum
made nowhere (the same reasoning as `vews.py`). The product-level rows stay in
RAW, readable with one DuckDB query, for whoever needs a subheading.

The basket RCA is computed only from the Atlas panel. The Indonesia file from
WITS holds a row only where Indonesia exported, so the world's exports of a
listed product Indonesia does not sell are missing from it — about sixty of
the 561 listed codes in a typical year — and a basket RCA taken over what is
present would overstate Indonesia's advantage by leaving out exactly the
products it is absent from. Its export values, shares and product counts are
unaffected, and those it gives through 2025.

Neither reader bumps `PARSER_VERSION`: nothing has been extracted from either
source before.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from typing import Any
from xml.etree import ElementTree

from ..trade import ANY_LIST, EG_LISTS, environmental_goods, hs6
from .base import ExtractionError, Extractor, Landed

WITS_SOURCE = "wits-tradestats"
SEED_SOURCE = "rca-seed"

#: The Bronze collection every TradeStats row lands in, whichever indicator
#: the call asked for: they are one table of trade indicators by sector.
TRADESTATS_DATASET = "tradestats"

PUBLISHER_WITS = "World Bank WITS"
PUBLISHER_ATLAS = "Growth Lab at Harvard University"


# -- TradeStats ---------------------------------------------------------------

#: WITS's product groups, as its product listing names them, with the family
#: each belongs to. Two families both have a `Fuels`, and a series name that
#: said only "Fuels" would not say which.
PRODUCT_GROUPS: dict[str, str] = {
    "01-05_Animal": "HS 01–05 Animal",
    "06-15_Vegetable": "HS 06–15 Vegetable",
    "16-24_FoodProd": "HS 16–24 Food products",
    "25-26_Minerals": "HS 25–26 Minerals",
    "27-27_Fuels": "HS 27 Fuels",
    "28-38_Chemicals": "HS 28–38 Chemicals",
    "39-40_PlastiRub": "HS 39–40 Plastic or rubber",
    "41-43_HidesSkin": "HS 41–43 Hides and skins",
    "44-49_Wood": "HS 44–49 Wood",
    "50-63_TextCloth": "HS 50–63 Textiles and clothing",
    "64-67_Footwear": "HS 64–67 Footwear",
    "68-71_StoneGlas": "HS 68–71 Stone and glass",
    "72-83_Metals": "HS 72–83 Metals",
    "84-85_MachElec": "HS 84–85 Machinery and electrical",
    "86-89_Transport": "HS 86–89 Transportation",
    "90-99_Miscellan": "HS 90–99 Miscellaneous",
    "AgrRaw": "SITC agricultural raw materials",
    "Chemical": "SITC chemicals",
    "Food": "SITC food",
    "Fuels": "SITC fuels",
    "manuf": "SITC manufactures",
    "OresMtls": "SITC ores and metals",
    "Textiles": "SITC textiles",
    "Transp": "SITC machinery and transport equipment",
    "UNCTAD-SoP1": "Raw materials (stage of processing)",
    "UNCTAD-SoP2": "Intermediate goods (stage of processing)",
    "UNCTAD-SoP3": "Consumer goods (stage of processing)",
    "UNCTAD-SoP4": "Capital goods (stage of processing)",
    "Total": "All products",
}

#: WITS indicator code to (indicator prefix, series title, unit).
MEASURES: dict[str, tuple[str, str, str]] = {
    "RCA": ("wits_rca", "Revealed comparative advantage", "index, world = 1"),
    "XPRT-TRD-VL": ("wits_export_value", "Exports to the world", "US$ thousand"),
    "XPRT-PRDCT-SHR": ("wits_export_share", "Share of total exports", "percent"),
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def product_slug(code: str) -> str:
    """`01-05_Animal` → `01_05_animal`, for an indicator id."""
    return _NON_ALNUM.sub("_", code.lower()).strip("_")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class WitsTradeStatsExtractor(Extractor):
    """One Bronze record per reporter, product group, indicator and year."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == WITS_SOURCE and landed.path.suffix.lower() == ".xml"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        content = landed.path.read_bytes()
        if b"wits:error" in content[:2048]:
            # WITS answers 200 with an error wrapped in a string element when a
            # request is too large; landing cannot tell, the parse can.
            raise ExtractionError(str(landed.path), "WITS returned an error, not data")
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as exc:
            raise ExtractionError(str(landed.path), f"invalid SDMX: {exc}") from exc

        number = 0
        for series in root.iter():
            if _local(series.tag) != "Series":
                continue
            reporter = series.get("REPORTER") or ""
            product = series.get("PRODUCTCODE") or ""
            code = series.get("INDICATOR") or ""
            measure = MEASURES.get(code)
            if measure is None or not reporter:
                continue
            if code == "RCA" and product == "Total":
                # A country's RCA in everything it exports is 1 by definition.
                continue
            prefix, title, unit = measure
            group = PRODUCT_GROUPS.get(product, product)

            for obs in series:
                if _local(obs.tag) != "Obs":
                    continue
                value = obs.get("OBS_VALUE")
                year = obs.get("TIME_PERIOD")
                if value in (None, "") or not year:
                    continue
                number += 1
                yield {
                    "dataset": TRADESTATS_DATASET,
                    "row_number": number,
                    "columns": {
                        "indicator": f"{prefix}_{product_slug(product)}",
                        "series_name": f"{title}: {group}",
                        "measure": f"{code}/{product}",
                        "reporter": reporter,
                        "partner": series.get("PARTNER") or "",
                        "year": year,
                        "value": value,
                        "unit": unit,
                        "datasource": obs.get("DATASOURCE") or "",
                        "publisher": PUBLISHER_WITS,
                    },
                }

        if number == 0:
            raise ExtractionError(str(landed.path), "SDMX response holds no observations")


# -- the seed files -----------------------------------------------------------

_ALL_LISTS = (*EG_LISTS, ANY_LIST)


def _read_frame(landed: Landed) -> Any:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise ExtractionError(
            SEED_SOURCE, "pandas is not installed; add the `agencies` extra"
        ) from exc

    suffix = landed.path.suffix.lower()
    if suffix == ".dta":
        return pd.read_stata(landed.path, convert_categoricals=False)
    if suffix == ".parquet":
        return pd.read_parquet(io.BytesIO(landed.path.read_bytes()))
    raise ExtractionError(str(landed.path), f"unexpected seed file type {suffix}")


def _number(value: float | int | str) -> str:
    """A figure as Bronze text: counts as integers, sums and ratios in full.

    `repr` is Python's shortest exact form, which `g` is not — it rounds to six
    digits, and a year's exports are twelve.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    return repr(float(value))


def _row(
    dataset: str,
    indicator: str,
    name: str,
    geo: str,
    year: int,
    value: float | int | str,
    unit: str,
    publisher: str,
    measure: str,
) -> dict[str, str]:
    return {
        "dataset": dataset,
        "indicator": indicator,
        "series_name": name,
        "measure": measure,
        "reporter": geo,
        "year": str(int(year)),
        "value": _number(value),
        "unit": unit,
        "publisher": publisher,
    }


def indonesia_rows(frame: Any, dataset: str) -> Iterator[dict[str, str]]:
    """The WITS Indonesia file, summed per goods list and year."""
    frame = frame[frame["hs92"].notna() & frame["year"].notna()].copy()
    frame["code"] = frame["hs92"].map(hs6)
    # The goods lists were merged on through a many-to-many concordance, so an
    # HS92 code that split into several later codes appears once per split
    # with its trade repeated. Once per code and year is the trade.
    frame = frame.drop_duplicates(["code", "year"])
    lists = environmental_goods()
    publisher = "World Bank WITS (UN Comtrade), compiled by CSIS"

    for year, rows in frame.groupby("year"):
        total = float(rows["idntotalexport"].iloc[0])
        advantaged = rows["rca"] >= 1
        yield _row(
            dataset,
            "wits_hs6_products_rca",
            "HS6 products with a revealed comparative advantage",
            "IDN",
            year,
            int(advantaged.sum()),
            "count of HS6 products",
            publisher,
            "rca>=1",
        )
        for goods in _ALL_LISTS:
            basket = rows["code"].isin(lists[goods.key])
            exports = float(rows.loc[basket, "idnexport"].sum())
            yield _row(
                dataset,
                f"wits_hs6_eg_exports_{goods.key}",
                f"Exports of {goods.name}",
                "IDN",
                year,
                exports,
                "US$ thousand",
                publisher,
                goods.key,
            )
            if total:
                yield _row(
                    dataset,
                    f"wits_hs6_eg_export_share_{goods.key}",
                    f"{goods.name}, share of total exports",
                    "IDN",
                    year,
                    exports / total * 100,
                    "percent",
                    publisher,
                    goods.key,
                )
            yield _row(
                dataset,
                f"wits_hs6_eg_products_rca_{goods.key}",
                f"{goods.name} with a revealed comparative advantage",
                "IDN",
                year,
                int((basket & advantaged).sum()),
                "count of HS6 products",
                publisher,
                goods.key,
            )


#: The country-level measures the Atlas file carries on every row.
ATLAS_COUNTRY_MEASURES: dict[str, tuple[str, str]] = {
    "eci": ("Economic Complexity Index", "index"),
    "coi": ("Complexity Outlook Index", "index"),
    "diversity": ("Diversity: products with RCA ≥ 1", "count of HS products"),
    "growth_proj": ("Growth projection, next ten years", "percent per year"),
}


def atlas_rows(frame: Any, dataset: str) -> Iterator[dict[str, str]]:
    """The Atlas panel, per country and year: the goods lists, and complexity."""
    frame = frame.copy()
    frame["code"] = frame["product_hs92_code"].map(hs6)
    lists = environmental_goods()

    # The world's exports of each product in each year, which every row of that
    # product repeats. Taken over the products any kept economy traded, which
    # between China, Japan and India is effectively every product there is.
    world = frame.drop_duplicates(["year", "code"])[["year", "code", "wld_export_value"]]
    world_totals = frame.groupby("year")["wld_total_export_value"].first()

    world_basket: dict[tuple[int, str], float] = {}
    for goods in _ALL_LISTS:
        in_basket = world[world["code"].isin(lists[goods.key])]
        for year, value in in_basket.groupby("year")["wld_export_value"].sum().items():
            world_basket[(int(year), goods.key)] = float(value)

    country_total = frame["country_total_export_value"].where(
        frame["country_total_export_value"] > 0
    )
    world_share = frame["wld_export_value"] / frame["wld_total_export_value"]
    frame["advantaged"] = (frame["export_value"] / country_total) / world_share >= 1

    for (country, year), rows in frame.groupby(["country_iso3_code", "year"]):
        year = int(year)
        first = rows.iloc[0]

        for column, (name, unit) in ATLAS_COUNTRY_MEASURES.items():
            value = first.get(column)
            if value is None or value != value:  # NaN
                continue
            # Stored as float32, published to three decimals: `0.28` arrives as
            # 0.2800000011920929, and six significant digits is what was meant.
            value = format(float(value), ".6g")
            yield _row(
                dataset,
                f"atlas_{column}",
                name,
                str(country),
                year,
                value,
                unit,
                PUBLISHER_ATLAS,
                column,
            )

        total = float(first["country_total_export_value"] or 0)
        world_total = float(world_totals.get(year) or 0)
        for goods in _ALL_LISTS:
            basket = rows["code"].isin(lists[goods.key])
            exports = float(rows.loc[basket, "export_value"].sum())
            yield _row(
                dataset,
                f"atlas_eg_exports_{goods.key}",
                f"Exports of {goods.name}",
                str(country),
                year,
                exports,
                "US$",
                PUBLISHER_ATLAS,
                goods.key,
            )
            yield _row(
                dataset,
                f"atlas_eg_products_rca_{goods.key}",
                f"{goods.name} with a revealed comparative advantage",
                str(country),
                year,
                int((basket & rows["advantaged"]).sum()),
                "count of HS6 products",
                PUBLISHER_ATLAS,
                goods.key,
            )
            world_exports = world_basket.get((year, goods.key), 0.0)
            if total and world_total and world_exports:
                yield _row(
                    dataset,
                    f"atlas_eg_rca_{goods.key}",
                    f"Revealed comparative advantage in {goods.name}",
                    str(country),
                    year,
                    (exports / total) / (world_exports / world_total),
                    "index, world = 1",
                    PUBLISHER_ATLAS,
                    goods.key,
                )


class RcaSeedExtractor(Extractor):
    """The seed files, summed into series per goods list, country and year."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SEED_SOURCE and landed.path.suffix.lower() in {
            ".dta",
            ".parquet",
        }

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        frame = _read_frame(landed)
        columns = set(frame.columns)
        dataset = landed.dataset or ""

        if {"idnexport", "wldexport", "hs92", "rca"} <= columns:
            rows = indonesia_rows(frame, dataset or "rca-indonesia-hs6")
        elif {"country_iso3_code", "product_hs92_code", "export_value"} <= columns:
            rows = atlas_rows(frame, dataset or "rca-atlas-hs6")
        else:
            raise ExtractionError(str(landed.path), "neither the WITS nor the Atlas RCA file")

        number = 0
        for row in rows:
            number += 1
            yield {"dataset": row.pop("dataset"), "row_number": number, "columns": row}
        if number == 0:
            raise ExtractionError(str(landed.path), "no rows to sum")
