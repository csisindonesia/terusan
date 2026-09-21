"""Yahoo Finance chart JSON into Bronze records.

The generic JSON extractor cannot read this. Yahoo answers *column-wise* — one
array of timestamps beside parallel arrays of opens, highs, lows and closes —
so a generic reader yields five rows of a thousand numbers each rather than a
thousand rows of five figures.

Transposing them is the whole job. Nothing is interpreted here: a null stays a
null, and deciding what a missing close means belongs to Silver.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from .base import ExtractionError, Extractor, Landed

SOURCE_PREFIX = "yahoo-"

#: The quote fields carried across, in the order a candle is read in.
FIELDS = ("open", "high", "low", "close", "volume")


def _as_text(value: Any) -> str:
    """A cell as Bronze holds it: text, with nothing invented for a gap."""
    return "" if value is None else str(value)


class YahooChartExtractor(Extractor):
    """One Bronze record per trading day."""

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

        results = (body.get("chart") or {}).get("result") or []
        if not results:
            raise ExtractionError(str(landed.path), "chart response holds no result")

        result = results[0]
        timestamps = result.get("timestamp") or []
        quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        meta = result.get("meta") or {}
        symbol = str(meta.get("symbol") or "")
        currency = str(meta.get("currency") or "")

        for number, epoch in enumerate(timestamps, start=1):
            # Dated in UTC. The exchange's own date is what matters for a daily
            # bar, and Yahoo timestamps each bar at the session open, so the two
            # agree for Jakarta (UTC+7) on every ordinary trading day.
            day = datetime.fromtimestamp(int(epoch), tz=UTC).date().isoformat()

            columns = {
                "date": day,
                "symbol": symbol,
                "currency": currency,
            }
            for field in FIELDS:
                series = quotes.get(field) or []
                columns[field] = _as_text(series[number - 1]) if number - 1 < len(series) else ""

            yield {
                "dataset": landed.dataset or "yahoo",
                "row_number": number,
                "columns": columns,
            }
