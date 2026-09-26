"""SP2KP's national price series into Bronze records.

The generic JSON reader can read this file, and would lose the half of the fact
that is not in it. The response is a bare list —

    {"data": [{"tanggal_data": "2026-09-23", "harga": 14043}, ...]}

— and nothing in it says which good is priced. That was `variant_id` in the
request. Read generically, forty-two commodities become forty-two files of
identical shape that normalize into one series of overlapping dates.

The source records the variant beside the bytes (program.md §2.1: what the
scraper learned while fetching belongs in the metadata) and this reads it back
onto every row, so a Bronze record carries what, how much, in what unit, and
when.

The unit comes off the same record and is not assumed. Most of the basket is
priced by the kilogram, but cooking oil is by the litre, instant noodles by the
packet, toddler formula by the 400-gram tin and free-range chicken by the bird.
Asserting kilograms would be wrong four ways.

The API gives the quantity alone — `kg` — and never the currency. A row
carrying a unit of `kg` for a price is wrong where it lands, beside `IDR/kg`
from the same ministry's crosstab, so the currency is joined to it here. Both
survive: `satuan` as the API wrote it, `unit` as the figure reads.

Prices arrive as plain integers here — `14043`, not the crosstab's `14.043` —
because this is JSON rather than a spreadsheet export. No separator to
misread, and no `--number-format` argument that could mislead.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from .base import ExtractionError, Extractor, Landed

SOURCE_SLUG = "kemendag-sp2kp-national"

#: The master list, landed for replay rather than for figures. It has no
#: `variant_id` on its record, which is how it is told apart from a series.
CATALOGUE = "catalogue"


class Sp2kpNationalExtractor(Extractor):
    """One Bronze record per commodity and day."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return (
            landed.source_slug == SOURCE_SLUG
            and landed.path.suffix.lower() == ".json"
            and landed.extra.get("role") != CATALOGUE
        )

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        try:
            body = json.loads(landed.path.read_bytes())
        except json.JSONDecodeError as exc:
            raise ExtractionError(str(landed.path), f"invalid JSON: {exc}") from exc

        variant = landed.extra.get("variant_nama")
        if not variant:
            # Without it the rows cannot say what they price, and a series that
            # cannot name its good normalizes into whichever one it lands beside.
            raise ExtractionError(
                str(landed.path),
                "no variant_nama on the landing record; the series does not name its commodity",
            )

        points = body.get("data")
        if not isinstance(points, list):
            raise ExtractionError(str(landed.path), "response holds no series")

        # The API states the quantity — `kg`, `lt`, `bks`, `400gr`, `ekor` —
        # and not the currency, because a ministry pricing groceries for
        # Indonesians has no reason to say which money. A unit of `kg` on a
        # price is wrong in Silver, where it sits beside `IDR/kg` from the same
        # ministry's crosstab, so the two halves are put together here. Both
        # are carried: `satuan` is what the API said, `unit` is what the figure
        # means, and a reader who distrusts the second can rebuild it.
        satuan = str(landed.extra.get("satuan") or "").strip()
        unit = f"IDR/{satuan}" if satuan else ""
        komoditas = landed.extra.get("komoditas") or ""

        for number, point in enumerate(points, start=1):
            if not isinstance(point, dict):
                raise ExtractionError(str(landed.path), f"point {number} is not an object")

            price = point.get("harga")
            yield {
                "dataset": landed.dataset or "sp2kp-national-prices",
                "row_number": number,
                "columns": {
                    "date": str(point.get("tanggal_data") or ""),
                    "variant": str(variant),
                    "komoditas": str(komoditas),
                    "satuan": satuan,
                    "unit": unit,
                    # A null price is a day the ministry published none. It
                    # stays blank rather than becoming a zero.
                    "price": "" if price is None else str(price),
                },
            }
