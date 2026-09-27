"""Sina's daily futures bars into Bronze records.

The generic JSON reader would read this — it is a plain array of objects — and
land columns named `d`, `o`, `h`, `l`, `c`, `v`, `p` and `s`, which nobody
mapping a series should have to decode. This names them, and joins on the unit,
which Sina never states: every contract it carries from Shanghai and Dalian is
quoted in yuan per tonne. The symbol comes off the landing record, since the
body does not repeat it.

Nothing is typed. The prices arrive as strings (`"125320.000"`) and stay so.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from .base import ExtractionError, Extractor, Landed

SOURCE_PREFIX = "sina-"

#: Sina's one-letter keys, and what they are.
FIELDS = {
    "d": "date",
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
    "s": "settlement",
    "v": "volume",
    "p": "open_interest",
}


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


class SinaFuturesExtractor(Extractor):
    """One Bronze record per contract and trading day."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return (
            landed.source_slug.startswith(SOURCE_PREFIX) and landed.path.suffix.lower() == ".json"
        )

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        try:
            body = json.loads(landed.path.read_bytes())
        except json.JSONDecodeError as exc:
            raise ExtractionError(str(landed.path), f"invalid JSON: {exc}") from exc

        if not isinstance(body, list):
            raise ExtractionError(str(landed.path), "response is not a list of daily bars")

        symbol = str(landed.extra.get("symbol") or "")
        unit = str(landed.extra.get("unit") or "CNY/t")

        for number, point in enumerate(body, start=1):
            if not isinstance(point, dict):
                raise ExtractionError(str(landed.path), f"bar {number} is not an object")
            columns = {name: _as_text(point.get(key)) for key, name in FIELDS.items()}
            columns["symbol"] = symbol
            columns["unit"] = unit
            yield {
                "dataset": landed.dataset or "sina",
                "row_number": number,
                "columns": columns,
            }
