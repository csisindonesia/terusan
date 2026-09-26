"""Kemendag's SP2KP crosstab into Bronze records.

The generic CSV reader cannot read this file, for three reasons at once.

It is UTF-16 with a byte-order mark, which a reader assuming UTF-8 sees as
alternating nulls. It is tab-separated. And the price column's *header is the
date* — `23/09/2026` — because the file is a Tableau crosstab of one day, not a
table with a date in it. Read generically, every day's landing would produce a
column named after that day and nothing tying the rows to a period at all.

So the date moves out of the header and onto each row, the same judgement the
Yahoo reader makes and the opposite of the one PIHPS gets. PIHPS keeps its
dates as columns because its grid genuinely holds many of them and Silver can
read a wide table whose headers are periods. Here there is exactly one, and a
single column whose name changes daily is a label in the wrong place.

Two figures per row, and both are worth keeping. `harga` is what the ministry's
enumerators found; `HET/HA` is the ceiling or reference price the government
set for that good — *Harga Eceran Tertinggi*, *Harga Acuan*. Holding them side
by side is what lets a reader ask the question the series exists to answer,
which is not "what does rice cost" but "is it selling above the ceiling". The
ceiling is blank for the goods that have none, and a blank stays blank.

Numbers are left as the ministry wrote them: `41.500` is forty-one thousand
five hundred rupiah, the dot an Indonesian thousands separator. Every value in
the file matches `\\d{1,3}(\\.\\d{3})*` — thirteen thousand of them, all with
exactly three digits behind the dot — so there is no decimal point anywhere to
mistake it for. Converting here would be Bronze deciding; the mapping in Silver
says `--number-format id` and the conversion happens where the unit is declared.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from .base import ExtractionError, Extractor, Landed

SOURCE_SLUG = "kemendag-sp2kp-prices"

#: Columns that describe the row rather than price it. Everything else in the
#: header is a date. Matched after stripping, because the export pads two of
#: them with a trailing space.
DIMENSIONS = {
    "No": None,
    "Kode Wilayah": "kode_wilayah",
    "Provinsi": "provinsi",
    "Kabupaten Kota": "kabupaten_kota",
    "Komoditas": "komoditas",
    "HET/HA": "ceiling",
}

#: How the crosstab writes a date in a column header.
DATE_FORMAT = "%d/%m/%Y"


class Sp2kpPricesExtractor(Extractor):
    """One Bronze record per regency, commodity and day."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() == ".csv"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        raw = landed.path.read_bytes()
        try:
            # `utf-16` rather than `utf-16-le`: the export carries a BOM, and
            # naming the endianness would leave it in the first header cell.
            text = raw.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise ExtractionError(str(landed.path), f"not UTF-16: {exc}") from None

        rows = csv.reader(io.StringIO(text), delimiter="\t")
        try:
            header = [cell.strip() for cell in next(rows)]
        except StopIteration:
            raise ExtractionError(str(landed.path), "crosstab is empty") from None

        dates: dict[int, str] = {}
        for position, cell in enumerate(header):
            if cell in DIMENSIONS:
                continue
            try:
                dates[position] = datetime.strptime(cell, DATE_FORMAT).date().isoformat()
            except ValueError:
                raise ExtractionError(
                    str(landed.path),
                    f"column {cell!r} is neither a known dimension nor a date",
                ) from None

        if not dates:
            # A crosstab with no date column priced nothing: the view answered,
            # and a run that lands it without noticing would report success.
            raise ExtractionError(str(landed.path), "crosstab holds no priced day")

        number = 0
        for row in rows:
            if not any(cell.strip() for cell in row):
                continue

            columns = {
                name: row[position].strip() if position < len(row) else ""
                for position, cell in enumerate(header)
                if (name := DIMENSIONS.get(cell)) is not None
            }

            for position, day in dates.items():
                number += 1
                price = row[position].strip() if position < len(row) else ""
                yield {
                    "dataset": landed.dataset or "sp2kp-food-prices",
                    "row_number": number,
                    "columns": {**columns, "date": day, "price": price},
                }
