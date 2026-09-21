"""SEKI's `.xls` tables into Bronze records.

A SEKI table is a formatted sheet, not a dataset. Row 41 of table 8.1 is the
composite CPI; nothing in the file says so. The period header sits two rows
above the figures, the year appears once per block — sometimes over January,
sometimes over December — and the same sheet carries a decade of archived
layouts under their own tabs.

So the mapping is declared, in `reference/seki/variables.csv`: which row of
which sheet holds which series, at which frequency, in which unit. That file
came from the standalone SEKI scrapper, where it was worked out by reading the
workbooks, and it is the only thing that turns `E41:CC41` into "composite
consumer price index, monthly, index points".

The column-to-period logic is ported from that scrapper, comments and all,
because it encodes what the workbooks actually do:

- The year cell anchors a block rather than labelling a column, so years are
  propagated forward from wherever the anchor sits, with January and Q1 read
  as the start of a new block.
- A block with no anchor inside it is the one after the last anchored block,
  which is how SEKI writes a year it has not got round to labelling.
- Yearly tables put the year on the header row itself and leave the period row
  empty.

One deliberate departure: figures stay text, as everything in Bronze does
(program.md §6). The scrapper coerced them to floats and dropped what would
not parse, which is Silver's decision to make here — and Silver records a
refusal instead of losing it.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from ..sources.bank_indonesia.seki import TABLES_DATASET
from .base import ExtractionError, Extractor, Landed
from .tabular import number_text

log = structlog.get_logger(__name__)

SOURCE_SLUG = "bi-seki"

XLS_SUFFIXES = {".xls", ".xlsx"}

#: Identifiers are prefixed by the publication, not the agency: Bank Indonesia
#: publishes SEKI alongside its surveys, and both carry a CPI.
NAMESPACE = "seki"

#: The declared mapping. Committed reference data rather than code, for the
#: same reason geography is: it changes when Bank Indonesia moves a row, and
#: the diff is the record of that (program.md §11).
REFERENCE_ROOT = Path(__file__).resolve().parents[4] / "reference"
VARIABLES_CSV = REFERENCE_ROOT / "seki" / "variables.csv"

MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "mei": 5, "may": 5,
    "jun": 6, "jul": 7, "ags": 8, "agt": 8, "agu": 8, "aug": 8,
    "sep": 9, "okt": 10, "oct": 10, "nov": 11, "des": 12, "dec": 12,
}  # fmt: skip

QUARTER = re.compile(r"^q\s*([1-4])", re.IGNORECASE)

#: `E41:CC41` — one row, two column letters. A multi-row range would describe
#: a block rather than a series, and there is no single figure to take from it.
_RANGE = re.compile(r"^(?P<from_col>[A-Z]+)(?P<from_row>\d+):(?P<to_col>[A-Z]+)(?P<to_row>\d+)$")


class SpecError(ValueError):
    """A variable specification that cannot be applied."""


@dataclass(frozen=True, slots=True)
class VariableSpec:
    """One series: where it sits, and what it is."""

    slug: str
    description: str
    unit: str
    #: Monthly | Quarterly | Yearly, deciding how a header becomes a period.
    time_freq: str
    table: str
    sheet: str
    #: 0-based, as the sheet is indexed once read.
    row_index: int
    period_row_index: int
    note: str = ""
    technical_notes: str = ""

    @property
    def indicator_id(self) -> str:
        return f"{NAMESPACE}_{re.sub(r'[^a-z0-9]+', '_', self.slug.lower()).strip('_')}"


def row_of(cell_range: str) -> int:
    """The 0-based row a single-row range refers to. `D31:T31` is 30."""
    match = _RANGE.match((cell_range or "").strip().replace(" ", "").upper())
    if match is None:
        raise SpecError(f"not a cell range: {cell_range!r}")
    if match.group("from_row") != match.group("to_row"):
        raise SpecError(f"range spans more than one row: {cell_range!r}")
    return int(match.group("from_row")) - 1


def load_specs(path: Path | None = None) -> list[VariableSpec]:
    """Read the declared mapping, skipping its comment header."""
    path = path or VARIABLES_CSV
    if not path.exists():
        raise ExtractionError(str(path), "SEKI variable reference is missing")

    specs: list[VariableSpec] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for record in csv.DictReader(line for line in handle if not line.startswith("#")):
            try:
                specs.append(
                    VariableSpec(
                        slug=(record.get("slug") or "").strip(),
                        description=(record.get("description") or "").strip(),
                        unit=(record.get("unit") or "").strip(),
                        time_freq=(record.get("time_freq") or "").strip(),
                        table=(record.get("table") or "").strip(),
                        sheet=(record.get("sheet") or "").strip(),
                        row_index=row_of(record.get("cell", "")),
                        period_row_index=row_of(record.get("month_cell", "")),
                        note=(record.get("note") or "").strip(),
                        technical_notes=(record.get("technical_notes") or "").strip(),
                    )
                )
            except SpecError as exc:
                # One malformed row must not cost the other two hundred, but a
                # series nobody can locate is worth seeing rather than missing.
                log.warning("seki.bad_spec", slug=record.get("slug"), error=str(exc))
    if not specs:
        raise ExtractionError(str(path), "SEKI variable reference holds no usable rows")
    return specs


@lru_cache(maxsize=1)
def specs_by_table(path: str | None = None) -> dict[str, tuple[VariableSpec, ...]]:
    """The mapping grouped by SEKI table, which is how artifacts arrive."""
    grouped: dict[str, list[VariableSpec]] = {}
    for spec in load_specs(Path(path) if path else None):
        grouped.setdefault(spec.table.upper(), []).append(spec)
    return {table: tuple(items) for table, items in grouped.items()}


# ---- the ported header logic ---------------------------------------------


def _clean_header(value: object) -> str:
    return str(value).strip().replace("*", "").strip() if value not in (None, "") else ""


def _parse_month(label: str) -> int | None:
    cleaned = _clean_header(label).lower()
    return MONTH_NAMES.get(cleaned[:3]) if cleaned else None


def _parse_quarter(label: str) -> int | None:
    cleaned = _clean_header(label)
    match = QUARTER.match(cleaned) if cleaned else None
    return int(match.group(1)) if match else None


def _year_of(value: object) -> int | None:
    """A year, where a header cell holds one. SEKI writes it as a number."""
    if value in (None, ""):
        return None
    if isinstance(value, int | float):
        year = int(value)
        return year if 1900 <= year <= 2100 else None
    if isinstance(value, str):
        try:
            year = int(float(value.strip()))
        except ValueError:
            return None
        return year if 1900 <= year <= 2100 else None
    return None


def _column_years(sheet: Any, year_row: int, header_row: int) -> dict[int, int]:
    """Map each column to the year its block belongs to.

    Ported: the year cell anchors a block rather than labelling a column, and
    recent editions move the anchor from January to December. So the anchors
    are collected first, the blocks are cut at every January or Q1, and a
    block with no anchor inside it takes the previous block's year plus one.
    """
    explicit = {
        column: year
        for column in range(sheet.ncols)
        if (year := _year_of(sheet.cell_value(year_row, column))) is not None
    }

    starts = [
        column
        for column in range(sheet.ncols)
        if (label := _clean_header(sheet.cell_value(header_row, column)))
        and (_parse_month(label) == 1 or _parse_quarter(label) == 1)
    ]
    if not starts:
        # A yearly table: every column carrying a year is its own period.
        return explicit

    blocks = [(start, starts[index + 1] if index + 1 < len(starts) else sheet.ncols)
              for index, start in enumerate(starts)]  # fmt: skip

    years: list[int | None] = []
    for index, (start, end) in enumerate(blocks):
        inside = [year for column, year in explicit.items() if start <= column < end]
        if inside:
            years.append(inside[0])
        elif index > 0 and years and years[-1] is not None:
            years.append(years[-1] + 1)
        else:
            years.append(None)

    first_known = next((index for index, year in enumerate(years) if year is not None), None)
    if first_known is None:
        return {}
    # Walk backwards from the first anchored block for the ones before it.
    for index in range(first_known - 1, -1, -1):
        following = years[index + 1]
        years[index] = following - 1 if following is not None else None

    mapped: dict[int, int] = {}
    for (start, end), year in zip(blocks, years, strict=True):
        if year is None:
            continue
        for column in range(start, end):
            mapped[column] = year
    return mapped


def column_periods(sheet: Any, header_row: int, time_freq: str) -> dict[int, tuple[str, str]]:
    """`{column: (period label, the header as written)}` for the data columns.

    The label is the period as Silver reads one — `2026`, `2026-Q2`, `2026-06`
    — rather than the first day of the period, which a daily-resolution reader
    would take literally.
    """
    periods: dict[int, tuple[str, str]] = {}
    frequency = time_freq.strip().lower()

    if frequency == "yearly":
        # The mapping's period row points at the year row itself here.
        for column in range(sheet.ncols):
            year = _year_of(sheet.cell_value(header_row, column))
            if year is not None:
                periods[column] = (f"{year:04d}", str(year))
        return periods

    years = _column_years(sheet, max(0, header_row - 1), header_row)
    for column in range(sheet.ncols):
        raw = sheet.cell_value(header_row, column)
        label = _clean_header(raw)
        year = years.get(column)
        if not label or year is None:
            continue
        if frequency == "quarterly":
            quarter = _parse_quarter(label)
            if quarter is not None:
                periods[column] = (f"{year:04d}-Q{quarter}", str(raw).strip())
        elif frequency == "monthly":
            month = _parse_month(label)
            if month is not None:
                periods[column] = (f"{year:04d}-{month:02d}", str(raw).strip())
    return periods


#: Cells that say there is no figure. Left out rather than carried: SEKI marks
#: a period it has not published with a dash, and Silver would otherwise hold
#: a decade of empty observations per series.
_ABSENT = {"", "-", "--", "*", "**", "n.a", "n.a.", "na", "..", "...", "…", "x"}


def _is_absent(text: str) -> bool:
    return "".join(text.split()).lower() in _ABSENT


class SekiExtractor(Extractor):
    """One Bronze record per SEKI figure named in the reference mapping."""

    target = "records"

    def __init__(self, variables: Path | None = None) -> None:
        self._variables = variables

    def handles(self, landed: Landed) -> bool:
        return (
            landed.source_slug == SOURCE_SLUG
            and landed.path.suffix.lower() in XLS_SUFFIXES
            # The index page and the catalogue land under their own dataset and
            # are HTML and JSON; only the tables are workbooks.
            and (landed.dataset or "") != "index"
        )

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        table = str((landed.extra or {}).get("table_id") or landed.path.stem).upper()
        specs = specs_by_table(str(self._variables) if self._variables else None).get(table, ())
        if not specs:
            # Most of SEKI's 108 tables carry no mapped series yet. That is
            # not a failure: the table is landed, and a mapping can be added
            # later and replayed over the same bytes.
            log.debug("seki.unmapped_table", table=table)
            return

        workbook = self._open(landed)
        try:
            number = 0
            for spec in specs:
                for row in self._series_rows(workbook, spec, landed, table):
                    number += 1
                    row["row_number"] = number
                    yield row
        finally:
            workbook.release_resources()

    # ---- internals -----------------------------------------------------

    @staticmethod
    def _open(landed: Landed) -> Any:
        try:
            import xlrd
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise ExtractionError(
                str(landed.path), "xlrd is not installed; add the `agencies` extra"
            ) from exc

        try:
            # Bank Indonesia publishes legacy BIFF, which openpyxl cannot open.
            return xlrd.open_workbook(file_contents=landed.path.read_bytes())
        except Exception as exc:  # noqa: BLE001 - surfaced with the path attached
            raise ExtractionError(str(landed.path), f"unreadable workbook: {exc}") from exc

    def _series_rows(
        self, workbook: Any, spec: VariableSpec, landed: Landed, table: str
    ) -> Iterator[dict[str, Any]]:
        try:
            sheet = workbook.sheet_by_name(spec.sheet)
        except Exception:  # noqa: BLE001 - xlrd raises a bare XLRDError
            # A rebased table arrives with its sheets renumbered, and reading
            # the wrong sheet would file one series' figures under another.
            log.warning("seki.sheet_missing", table=table, sheet=spec.sheet, slug=spec.slug)
            return

        if spec.row_index >= sheet.nrows or spec.period_row_index >= sheet.nrows:
            log.warning(
                "seki.row_out_of_range",
                table=table,
                sheet=spec.sheet,
                slug=spec.slug,
                rows=sheet.nrows,
            )
            return

        periods = column_periods(sheet, spec.period_row_index, spec.time_freq)
        if not periods:
            log.warning("seki.no_periods", table=table, sheet=spec.sheet, slug=spec.slug)
            return

        seen: set[str] = set()
        for column in sorted(periods):
            period, written = periods[column]
            # Ported: several tables repeat a period for budget and realised
            # figures, and the first column is the one the mapping describes.
            if period in seen:
                continue
            value = number_text(sheet.cell_value(spec.row_index, column))
            if _is_absent(value):
                continue
            seen.add(period)

            yield {
                # The collection, not the RAW folder the bytes sit in.
                # `handles` has already turned the index away, so every
                # record here is a table — and naming it outright means a
                # replay over bytes landed under the old folder still files
                # them under the dataset the registry knows.
                "dataset": TABLES_DATASET,
                "columns": {
                    "indicator": spec.indicator_id,
                    "series_name": spec.description or spec.slug,
                    "code": spec.slug,
                    "period": period,
                    # The header as SEKI wrote it — `Q1*`, `Jan**` — so a
                    # provisional figure can still be told apart from a final
                    # one after the period has been normalized.
                    "period_raw": written,
                    "value": value,
                    "unit": spec.unit,
                    "frequency": spec.time_freq,
                    "table": table,
                    "sheet": spec.sheet,
                    "country": "Indonesia",
                    "publisher": "Bank Indonesia",
                    # What the table says about itself, carried once per figure
                    # rather than looked up in a PDF later.
                    "notes": spec.technical_notes,
                    "table_title": str((landed.extra or {}).get("title") or ""),
                },
            }


__all__ = [
    "MONTH_NAMES",
    "SekiExtractor",
    "VariableSpec",
    "column_periods",
    "load_specs",
    "row_of",
]
