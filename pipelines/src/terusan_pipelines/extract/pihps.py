"""Bank Indonesia's food price grid into Bronze records.

The generic JSON reader can read this file. That is the problem: it reads it
faithfully and loses the half of the fact that is not in it.

PIHPS answers a query, and two of the four things a row is about are in the
query rather than the response. The grid says `Beras, 16,350 on 10/09/2026`; it
does not say which province, and it does not say whether that is a traditional
market's price or a farmgate one. Those were request parameters, and nothing in
the returned bytes recalls them. Landed through the generic reader, every
province's rice looks like the same series, and a farmgate price averages into
a retail one.

The source records them beside the bytes instead (program.md §2.1: what the
scraper learned while fetching belongs in the metadata, not folded into the
file). This extractor reads them back off that record and puts them on every
row, so a Bronze row carries all four dimensions: what, where, which market,
and when.

The dates stay as columns. They are the grid's own shape, the mapping in Silver
already knows how to read a wide table whose headers are periods, and unpivoting
here would mean Bronze had made a judgement that Bronze is meant to defer.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from .base import ExtractionError, Extractor, Landed

SOURCE_SLUG = "bi-pihps-food-prices"

#: What the grid's `level` column means. The portal nests a category over its
#: varieties — `Beras` over `Beras Kualitas Medium I` — and encodes the nesting
#: positionally, as a number with no key beside it.
#:
#: Named here because the two are not interchangeable and a reader cannot tell
#: them apart from the name alone. A category's price is the average of the
#: varieties beneath it, so summing a column that mixes them double-counts.
LEVELS = {1: "category", 2: "variety"}

#: The indicator each market's prices belong to, and what to call it.
#:
#: Named here rather than left to the price type's own key because the key is
#: the portal's word and the indicator is the warehouse's: `producer` alone,
#: sitting in a list beside `food_price_traditional`, says nothing about what
#: it prices or who published it.
INDICATORS = {
    "traditional": ("food_price_traditional", "Food price — traditional market"),
    "modern": ("food_price_modern", "Food price — modern market"),
    "wholesale": ("food_price_wholesale", "Food price — wholesaler"),
    "producer": ("food_price_producer", "Food price — producer"),
}


class PihpsPricesExtractor(Extractor):
    """One Bronze record per commodity row, with the query's own dimensions."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() == ".json"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        try:
            rows = json.loads(landed.path.read_bytes())
        except json.JSONDecodeError as exc:
            raise ExtractionError(str(landed.path), f"invalid JSON: {exc}") from exc

        if not isinstance(rows, list):
            raise ExtractionError(str(landed.path), "expected a list of grid rows")

        extra = landed.extra or {}
        geo_name = extra.get("geo_name")
        price_type = extra.get("price_type")
        if not geo_name or not price_type or price_type not in INDICATORS:
            # Landed before the source recorded them, or by something else
            # entirely. Guessing would file the rows under the wrong place or
            # the wrong market, which is worse than not reading them: a figure
            # under the wrong province is invisible as an error.
            raise ExtractionError(
                str(landed.path),
                "no usable geo_name/price_type in the landing record; re-run the "
                "source so the query's own dimensions are landed beside the bytes",
            )

        indicator, series_name = INDICATORS[price_type]

        for number, row in enumerate(rows, start=1):
            name = row.get("name")
            if not name:
                continue

            level = row.get("level")
            columns: dict[str, str] = {
                "commodity": str(name),
                "level": "" if level is None else str(level),
                "level_name": LEVELS.get(level, ""),
                "geo": str(geo_name),
                "geo_level": str(extra.get("geo_level", "")),
                "price_type": str(price_type),
                "price_type_name": str(extra.get("price_type_name", "")),
                # What the row's series is, and what to print for it. The four
                # markets are four indicators, not four readings of one, and
                # these two columns are what `normalize-each` splits them on
                # and names them from.
                "indicator": indicator,
                "series_name": series_name,
                # Every commodity the portal prices is quoted per kilogram, as
                # its own reference endpoint states for all thirty-one.
                "unit": "IDR/kg",
            }
            # The dates, kept as the grid gives them: `dd/mm/yyyy` headers over
            # price cells. A price not yet posted comes through as "-", which
            # Silver reads as missing rather than as zero.
            columns.update({k: "" if v is None else str(v) for k, v in row.items() if "/" in k})

            yield {
                "dataset": landed.dataset or "food-prices",
                "row_number": number,
                "columns": columns,
            }
