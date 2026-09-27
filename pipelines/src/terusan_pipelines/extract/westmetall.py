"""Westmetall's LME tables into Bronze records.

The generic HTML reader would land a year of LME prices as one blob of prose.
The page is one table —

    date              | LME Nickel Cash-Settlement | LME Nickel 3-month | LME Nickel stock
    25. September 2026| 16,050.00                  | 16,230.00          | 284,946

— and each row becomes one record. Two things are interpreted, because Silver
cannot read them as printed: the date, written the German way in English
(`25. September 2026`), is restated as ISO; and the unit, which the page never
states, is joined on — the LME prices base metals in US dollars per tonne and
counts its stocks in tonnes. The figures keep their thousands separators;
`--number-format en` reads them in Silver.

A cell Westmetall leaves blank or dashes out stays blank. What a missing
settlement means is Silver's to decide.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

from selectolax.parser import HTMLParser

from .base import ExtractionError, Extractor, Landed

SOURCE_SLUG = "westmetall-lme"

#: The LME's price unit for every base metal it lists.
PRICE_UNIT = "USD/t"
STOCK_UNIT = "t"

#: What the page prints for a day with no figure.
BLANKS = {"", "-", "–", "—", "n/a"}


#: Spelled out rather than left to `%B`, which reads month names in whatever
#: locale the process runs under.
MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}


def parse_date(text: str) -> str | None:
    """`25. September 2026` → `2026-09-25`; None for a row that is not a day."""
    parts = text.replace(".", " ").split()
    if len(parts) != 3 or parts[1].lower() not in MONTHS:
        return None
    try:
        return date(int(parts[2]), MONTHS[parts[1].lower()], int(parts[0])).isoformat()
    except ValueError:
        return None


def _cell(text: str) -> str:
    text = text.strip()
    return "" if text.lower() in BLANKS else text


class WestmetallLmeExtractor(Extractor):
    """One Bronze record per metal and trading day."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() in {
            ".html",
            ".htm",
        }

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        tree = HTMLParser(landed.path.read_bytes().decode("utf-8", errors="replace"))
        table = tree.css_first("table")
        if table is None:
            raise ExtractionError(str(landed.path), "page holds no table")

        commodity = str(landed.extra.get("commodity") or "")
        number = 0
        for row in table.css("tr"):
            cells = [cell.text(strip=True) for cell in row.css("td, th")]
            if len(cells) < 4:
                continue
            day = parse_date(cells[0])
            # The header, and any repeat of it, is not a day.
            if day is None:
                continue
            number += 1
            yield {
                "dataset": landed.dataset or "lme",
                "row_number": number,
                "columns": {
                    "date": day,
                    "commodity": commodity,
                    "cash_settlement": _cell(cells[1]),
                    "three_month": _cell(cells[2]),
                    "stock": _cell(cells[3]),
                    "unit": PRICE_UNIT,
                    "stock_unit": STOCK_UNIT,
                },
            }

        if number == 0:
            raise ExtractionError(str(landed.path), "table holds no dated rows")
