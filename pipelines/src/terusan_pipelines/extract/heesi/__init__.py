"""The HEESI handbook into Bronze records.

The generic PDF reader turns the handbook into two hundred pages of prose. The
figures in it are tables, and finding them means knowing that "Domestic Coal
Sales" sits in chapter 6 under a title the table of contents repeats, that the
energy balance is a matrix keyed by a numeric code, and that the 2025 edition
dropped a column the 2024 edition had. That knowledge arrived with the
`heesi-dfd` project and is kept in `vendored/`, unchanged.

This module is the seam between it and the warehouse: one Bronze record per
`(table, series, year)` figure, with the identifier the series will carry in
Silver composed here — where the handbook's own table keys and column labels
are still at hand — rather than in a normalization flag.

Fourteen tables are configured, which is not the whole handbook. Adding a
fifteenth is an entry in `vendored/sheets.yaml`, not code here.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from ..base import ExtractionError, Extractor, Landed

SOURCE_SLUG = "esdm-heesi"

PDF_SUFFIXES = {".pdf"}

#: Identifiers are prefixed so a HEESI series cannot collide with another
#: publisher's table of the same name.
NAMESPACE = "heesi"

#: Rows the vendored extractor emits to say it could not read a table. They
#: carry no figure, so they are not observations — but they are why a table is
#: missing, so they are logged rather than dropped silently.
_FAILURE_LABELS = {"__ERROR__", "__NEEDS_MANUAL__"}

_UNSLUG = re.compile(r"[^a-z0-9]+")


def indicator_id(key: str, column: str, code: str | None = None) -> str:
    """The Silver identifier for one handbook series.

    Readable rather than a hash: there are a few hundred of these, and
    `heesi_coal_supply_production` in a URL is worth more than eight characters
    of base36.

    The energy balance's `code` is part of the identifier because its column
    labels are energy types — `Hydro Power`, `Geothermal` — repeated under
    every balance line. Without the code, production and consumption of
    hydropower would land on one series holding both.
    """
    parts = [NAMESPACE, key, code or "", column]
    slug = "_".join(_UNSLUG.sub("_", part.strip().lower()).strip("_") for part in parts if part)
    return re.sub(r"_+", "_", slug)


def _series_name(column: str, key: str, code: str) -> str:
    """What a reader sees: the column, and the table it came out of.

    The energy balance's code goes in too, because `Hydro Power (neraca-energi)`
    alone would name one series for production and another for consumption.
    """
    return f"{column} ({key} {code})" if code else f"{column} ({key})"


def _text(value: Any) -> str:
    """A cell as Bronze keeps it: text, or empty where there is nothing."""
    if value is None:
        return ""
    # pandas gives NaN for a missing number and `int` for a year read off the
    # page; neither should reach Bronze as "nan" or "2025.0".
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        if value.is_integer():
            return str(int(value))
        return repr(value)
    if isinstance(value, bool):
        return "1" if value else ""
    return str(value).strip()


class HeesiExtractor(Extractor):
    """One Bronze record per handbook figure."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() in PDF_SUFFIXES

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        from .vendored import extract_edition, load_sheets

        try:
            frame = extract_edition(landed.path)
        except Exception as exc:  # noqa: BLE001 - surfaced with the path attached
            raise ExtractionError(str(landed.path), f"unreadable handbook: {exc}") from exc

        units = {sheet.key: sheet.unit for sheet in load_sheets()}
        edition = str((landed.extra or {}).get("edition") or "")
        number = 0
        failures: list[str] = []

        for row in frame.to_dict("records"):
            key = _text(row.get("key"))
            column = _text(row.get("xlsx_col"))
            if column in _FAILURE_LABELS:
                failures.append(f"{key}: {column} {_text(row.get('error'))}".strip())
                continue

            year = _text(row.get("year"))
            value = _text(row.get("value"))
            if not key or not column or not year:
                continue

            code = _text(row.get("code"))
            number += 1
            yield {
                "dataset": landed.dataset or "handbook",
                "row_number": number,
                "columns": {
                    "indicator": indicator_id(key, column, code or None),
                    "table": key,
                    "series_name": _series_name(column, key, code),
                    "series": column,
                    "year": year,
                    "value": value,
                    # Stated per table in the handbook's own words, which is
                    # sometimes two units for one sheet — the energy price
                    # table quotes both rupiah and dollars per BOE. Left as
                    # written rather than split on a guess.
                    "unit": units.get(key, ""),
                    # The energy balance line this figure belongs to, empty for
                    # every other table.
                    "code": code,
                    # The handbook's own table number, and the identifier the
                    # earlier project gave the series. Carried so a figure can
                    # be traced back to both.
                    "data_id": _text(row.get("data_id")),
                    "variable_id": _text(row.get("variable_id")),
                    # A column the publisher prints as the sum of the others.
                    # Kept, because it is a published figure and it checks the
                    # parts — but flagged, so a normalization run can exclude
                    # it instead of double-counting the table.
                    "is_total": "1" if column.strip().lower().startswith("total") else "",
                    "country": "Indonesia",
                    "edition": edition,
                    "publisher": "ESDM",
                },
            }

        if failures:
            # Not fatal: thirteen tables read is worth having, and the report
            # is what says the fourteenth needs attention.
            import structlog

            structlog.get_logger(__name__).warning(
                "heesi.tables_failed", path=str(landed.path), tables=failures[:10]
            )
        if number == 0:
            raise ExtractionError(
                str(landed.path),
                "no table in the handbook could be read; its layout has changed",
            )


__all__ = ["HeesiExtractor", "indicator_id"]
