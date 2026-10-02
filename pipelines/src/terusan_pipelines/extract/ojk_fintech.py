"""OJK's fintech lending (LPBBTI) workbooks into Bronze: the cells, and the
province series.

OJK publishes the statistics of peer-to-peer lending as one workbook a month,
linked from a news article. The generic workbook reader lands every cell under
`p2p-lending`, as it did before this reader existed, so the whole workbook
stays queryable. Beside the cells this reader produces `ojk-fintech-figures`:
the nine tables that break lending down by province, read one figure per row
so `scripts/normalize-ojk-fintech.sh` can take them into Silver.

- **Funds provided by lenders** (Tabel 5): lender accounts and the funds they
  provided.
- **Loans disbursed to borrowers** (Tabel 6): borrower accounts and the loans
  disbursed to them.
- **Outstanding loans and TWP 90** (Tabel 9): active borrower accounts,
  outstanding loans, and the share of them more than ninety days late.
- **Since each platform was founded** (Tabel 13 to 18): lender and borrower
  accounts, their transactions, the funds provided and the loans disbursed,
  each accumulated.

A table is found by its title, not its sheet: the sheets are named by the
table's number, and a number says nothing about the table once OJK adds one
before it. The columns of Tabel 5, 6 and 9 are found by the label beneath each
month, because each month spans two or three of them.

**Every month in a workbook is read, not only the edition's.** A workbook holds
the thirteen months to its own, and later editions restate the earlier ones
when they are revised. Silver keeps the figure retrieved last, and the source
fetches only the latest edition, once a month, so the last retrieved is the
last published. A backfill walking the archive would break that, and would
need the rule `extract/ojk_spi.py` follows: each edition for its own month.

A province is qualified with `Provinsi`. These tables list all thirty-eight,
so Papua is the province after the 2022 split. The country is the table's own
`JUMLAH` row, not a sum: lending from abroad (`Luar Negeri`) is in it and in no
province. The island groups above the provinces (`a. Jawa`, `b. Luar Jawa`)
are subtotals and are not read.

Figures stay as OJK wrote them: amounts in billions of Rupiah, counts as
counts, and TWP 90 as the fraction its cell holds, 0.026 for the 2.60% the
sheet displays. Its unit says so rather than the figure being multiplied here.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog

from .base import ExtractionError, Extractor, Landed
from .ojk_spi import _cell, _folded, _grid, _number, _plain
from .workbooks import WORKBOOK_SUFFIXES, _load, _sheet_rows

log = structlog.get_logger(__name__)

SOURCE_SLUG = "ojk-fintech-p2p"

#: Where the province series land in Bronze.
FIGURES_DATASET = "ojk-fintech-figures"

PUBLISHER = "Otoritas Jasa Keuangan"

NATIONAL = "Indonesia"

BILLIONS = "IDR billion"
ACCOUNTS = "accounts"
ENTITIES = "entities"
SHARE = "share of outstanding loans"

_TABLE_NUMBER = re.compile(r"^tabel\s+\d+\s*")
_MONTH = re.compile(r"^(\d{4})-(\d{2})")
_NUMBERED = re.compile(r"^\d+\.\s*(.+)$")


@dataclass(frozen=True, slots=True)
class Series:
    """One series read off a table, from the columns whose label it names."""

    indicator: str
    name: str
    unit: str
    #: How the label beneath each month begins. None where every month is
    #: one column and its label is the same boilerplate throughout.
    labels: tuple[str, ...] | None = None


#: The tables, by how their Indonesian title begins once its number is gone.
TABLES: dict[str, tuple[Series, ...]] = {
    "dana yang diberikan oleh pemberi pinjaman": (
        Series(
            "ojk_p2p_lender_accounts",
            "Fintech lending lender accounts",
            ACCOUNTS,
            ("jumlah rekening pemberi pinjaman",),
        ),
        Series(
            "ojk_p2p_lender_funds",
            "Fintech lending funds provided by lenders",
            BILLIONS,
            ("jumlah dana yang diberikan",),
        ),
    ),
    "penyaluran pinjaman kepada penerima pinjaman": (
        # Printed `Jumlah Penerima Pinjaman (akun)`; the indicator catalogue
        # quotes it with `Rekening`, which the next edition may well adopt.
        Series(
            "ojk_p2p_borrower_accounts",
            "Fintech lending borrower accounts",
            ACCOUNTS,
            ("jumlah penerima pinjaman", "jumlah rekening penerima pinjaman"),
        ),
        Series(
            "ojk_p2p_disbursement",
            "Fintech lending loans disbursed",
            BILLIONS,
            ("jumlah penyaluran pinjaman",),
        ),
    ),
    "outstanding pinjaman dan twp": (
        Series(
            "ojk_p2p_active_borrower_accounts",
            "Fintech lending active borrower accounts",
            ENTITIES,
            ("jumlah rekening penerima pinjaman aktif",),
        ),
        Series(
            "ojk_p2p_outstanding",
            "Fintech lending outstanding loans",
            BILLIONS,
            ("outstanding pinjaman",),
        ),
        Series(
            "ojk_p2p_twp90",
            "Fintech lending TWP 90 (loans more than 90 days late)",
            SHARE,
            ("twp 90", "twp90"),
        ),
    ),
    "akumulasi jumlah rekening pemberi pinjaman": (
        Series(
            "ojk_p2p_lender_accounts_accumulated",
            "Fintech lending lender accounts, accumulated",
            ENTITIES,
        ),
    ),
    "akumulasi jumlah rekening penerima pinjaman": (
        Series(
            "ojk_p2p_borrower_accounts_accumulated",
            "Fintech lending borrower accounts, accumulated",
            ENTITIES,
        ),
    ),
    "akumulasi jumlah transaksi pemberi pinjaman": (
        Series(
            "ojk_p2p_lender_transactions_accumulated",
            "Fintech lending lender transactions, accumulated",
            ACCOUNTS,
        ),
    ),
    "akumulasi jumlah transaksi penerima pinjaman": (
        Series(
            "ojk_p2p_borrower_transactions_accumulated",
            "Fintech lending borrower transactions, accumulated",
            ACCOUNTS,
        ),
    ),
    "akumulasi dana yang diberikan oleh pemberi pinjaman": (
        Series(
            "ojk_p2p_lender_funds_accumulated",
            "Fintech lending funds provided by lenders, accumulated",
            BILLIONS,
        ),
    ),
    "akumulasi penyaluran pinjaman kepada penerima pinjaman": (
        Series(
            "ojk_p2p_disbursement_accumulated",
            "Fintech lending loans disbursed, accumulated",
            BILLIONS,
        ),
    ),
}


class OjkFintechExtractor(Extractor):
    """Every cell of a fintech lending workbook, and the province series in it."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() in WORKBOOK_SUFFIXES

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        try:
            workbook = _load(landed.path.read_bytes())
        except ExtractionError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced with the path attached
            raise ExtractionError(str(landed.path), f"unreadable workbook: {exc}") from exc

        dataset = landed.dataset or landed.path.stem
        row_number = 0
        try:
            yield from _sheet_rows(workbook, dataset)
            for sheet in workbook.worksheets:
                for figure in sheet_figures(sheet.title, _grid(sheet)):
                    row_number += 1
                    yield {"dataset": FIGURES_DATASET, "row_number": row_number, "columns": figure}
        finally:
            workbook.close()


def table_of(title: str) -> tuple[Series, ...] | None:
    """The series a table holds, by its title."""
    # The Indonesian title is the first line; the English one follows. Two of
    # the accumulated tables spell it `Akumlasi`.
    folded = _folded(title.split("\n", 1)[0]).replace("akumlasi", "akumulasi")
    folded = _TABLE_NUMBER.sub("", folded)
    for prefix, series in TABLES.items():
        if folded.startswith(prefix):
            return series
    return None


def sheet_figures(sheet: str, grid: list[list[str]]) -> Iterator[dict[str, str]]:
    """The figures one sheet holds, for every month it holds."""
    title = next((text for row in grid[:3] for text in row if text), "")
    series = table_of(title)
    if series is None:
        return
    header = next(
        (
            row
            for row in range(min(8, len(grid)))
            if _folded(_cell(grid, row, 0)).startswith("lokasi")
        ),
        None,
    )
    if header is None:
        log.warning("ojk_fintech.no_header", sheet=sheet)
        return

    months = _months(grid[header])
    places = _places(grid, header + 2)
    if not months or not any(place == NATIONAL for place, _ in places):
        log.warning("ojk_fintech.table_not_read", sheet=sheet, months=len(months))
        return

    for one in series:
        columns = {
            column: month
            for column, month in months.items()
            if one.labels is None or _folded(_cell(grid, header + 1, column)).startswith(one.labels)
        }
        if len(set(columns.values())) != len(columns):
            # Two columns under one month: a label that begins another's.
            log.warning("ojk_fintech.columns_ambiguous", sheet=sheet, indicator=one.indicator)
            continue
        if not columns:
            log.warning("ojk_fintech.columns_not_found", sheet=sheet, indicator=one.indicator)
            continue
        for place, row in places:
            for column, month in columns.items():
                value = row.get(column, "")
                if value != "":
                    yield _figure(one, sheet, month, place, value)


def _months(header: list[str]) -> dict[int, str]:
    """Each value column's month, as `YYYY-MM`, carried across its merged cell.

    The months are dates, most of them the first of the month and some the
    last — `2025-11-30` — which name the same month either way.
    """
    out: dict[int, str] = {}
    month = None
    for column, text in enumerate(header):
        if column < 2:
            continue
        found = _MONTH.match(text)
        if found:
            month = f"{found[1]}-{found[2]}"
        if month:
            out[column] = month
    return out


def _places(grid: list[list[str]], first: int) -> list[tuple[str, dict[int, str]]]:
    """Each province's row, and the country's, which ends the table."""
    out: list[tuple[str, dict[int, str]]] = []
    for row in range(first, len(grid)):
        name = _cell(grid, row, 1)
        values = {i: text for i, text in enumerate(grid[row]) if i > 1 and text}
        if _folded(name) == "jumlah":
            out.append((NATIONAL, values))
            break
        numbered = _NUMBERED.match(name)
        if numbered:
            out.append((f"Provinsi {' '.join(numbered[1].split())}", values))
    return out


def _figure(series: Series, sheet: str, period: str, place: str, value: str) -> dict[str, str]:
    # openpyxl writes a small float as `1e-05`, which is a number but not one
    # the normalizer reads; the digits are written out instead.
    if _number(value):
        value = _plain(Decimal(value))
    return {
        "indicator": series.indicator,
        "name": series.name,
        "table": sheet,
        "period": period,
        "geo": place,
        "value": value,
        "unit": series.unit,
        "publisher": PUBLISHER,
    }
