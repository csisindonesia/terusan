"""Yahoo Finance chart JSON into Bronze records.

The generic JSON extractor cannot read this. Yahoo answers *column-wise* — one
array of timestamps beside parallel arrays of opens, highs, lows and closes —
so a generic reader yields five rows of a thousand numbers each rather than a
thousand rows of five figures.

Transposing them is the whole job, and folding the running quote into the
session it belongs to — Yahoo sends a bar Yahoo is still quoting twice, once
with a null close and again with the live price. Nothing else is interpreted: a
null stays a null, and deciding what a missing close means belongs to Silver.
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
        # Seconds the exchange is ahead of UTC on the day of the response.
        # Yahoo states it; absent, the epoch is read as UTC, which is what the
        # reader did before any instrument needed otherwise.
        offset = int(meta.get("gmtoffset") or 0)

        # Keyed by session so the running quote folds into the bar it belongs
        # to. Insertion order is Yahoo's, which is chronological.
        sessions: dict[str, dict[str, str]] = {}

        for index, epoch in enumerate(timestamps):
            # Dated by the exchange's own clock, not by UTC. Yahoo stamps each
            # bar at the session open, and for Jakarta (09:00, UTC+7) and New
            # York (open in the morning, UTC-4) that instant already falls on
            # the session's own date, so the two readings agree.
            #
            # They do not agree for foreign exchange, which Yahoo keeps on
            # Europe/London and stamps at local midnight: under British Summer
            # Time that is 23:00 UTC the previous day, and reading it as UTC
            # dated every summer rate a day early — Monday's rate filed as
            # Sunday, and a week of five rates spread over Sunday to Thursday.
            # Adding the offset back recovers the date the exchange meant.
            day = datetime.fromtimestamp(int(epoch) + offset, tz=UTC).date().isoformat()

            columns = sessions.setdefault(
                day,
                {"date": day, "symbol": symbol, "currency": currency},
            )
            for field in FIELDS:
                series = quotes.get(field) or []
                value = _as_text(series[index]) if index < len(series) else ""
                # A session Yahoo is still quoting arrives twice: once as its
                # own bar, open and high and low filled and the close still
                # null, and again as a final timestamp carrying the running
                # price. Two rows for one day is not what "one record per
                # trading day" means, and Silver refuses a period holding two
                # figures outright. The later reading supersedes the earlier —
                # and a blank in it does not erase what the earlier one had,
                # which keeps a genuinely unpriced holiday unpriced.
                if value or field not in columns:
                    columns[field] = value

        for number, columns in enumerate(sessions.values(), start=1):
            yield {
                "dataset": landed.dataset or "yahoo",
                "row_number": number,
                "columns": columns,
            }
