"""OJK's Statistik Perbankan Indonesia workbooks into Bronze: the cells, and the
province series.

The SPI was OJK's monthly banking statistics until June 2025, one workbook per
edition, fifty-odd formatted sheets each. The generic workbook reader lands
every cell under `banking-statistics`, as it did before this reader existed, so
the whole workbook stays queryable. Beside the cells this reader produces
`ojk-spi-figures`: the province tables that continue as OJK's SJK cubes after
June 2025, read one figure per row so `scripts/normalize-ojk-spi.sh` can take
them into Silver.

Four tables, all commercial banks, all by province:

- **Composition of deposits** (1.33.a; 1.45.a before 2020): giro, savings and
  time deposits, each in Rupiah and in foreign exchange.
- **Credit by purpose and orientation** (3.12.a; 3.16.a and 4.16 before),
  with each province's non-performing credit on the row beneath it.
- **Branch offices** (1.43.a; 1.57 and 1.57.a before).
- **Credit to micro, small and medium enterprises** (3.22.a; 3.28.a and 4.31
  before), by where the project is.

The sheets are found by the name before the table number, which has not
changed while the numbers have. The columns are found by their headers rather
than by position: the older workbooks start a column further left, and put up
to two years of monthly tables side by side on one sheet.

**Each edition contributes its own month and no other.** A sheet holds earlier
months too — a composition table beside the twenty before it, a branch count
with a decade of columns — and the editions revise them. Two editions stating
the same month differently would be settled in Silver by which was retrieved
last, and the archive was retrieved in one walk, so that order says nothing
about which OJK published later. Taking a month only from the edition named
after it means the figure is the one OJK printed as that month's.

The edition's month is read from the edition's own title
(`Statistik Perbankan Indonesia - November 2022`). A composition block states
its month in its title, and a block for another month is skipped. A time-series
table's rightmost month column is the edition's; one that ends on another month
has not been brought up to date, and is skipped rather than filed under the
edition.

A province is qualified with `Provinsi`, as the SJK reader does, and the SPI's
Papua and Papua Barat are the provinces before the 2022 split, labelled as that
territory (program.md §11). The country is the table's own `Total` or `Jumlah`
row, not a sum: rows OJK could not place (`Lainnya`, `Tidak Teridentifikasi`)
are in it and in no province.

Figures stay as OJK wrote them, in billions of Rupiah, except where a series is
a sum of printed cells: a deposit type is its Rupiah and its foreign-exchange
columns added, and a credit breakdown's total is its categories added. The
sums are decimal. A sum with a part that is not a number is not made, rather
than made from the parts that are.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from .base import ExtractionError, Extractor, Landed
from .workbooks import ARCHIVE_SUFFIXES, WORKBOOK_SUFFIXES, _load, _sheet_rows

log = structlog.get_logger(__name__)

SOURCE_SLUG = "ojk-banking-spi"

#: Where the province series land in Bronze.
FIGURES_DATASET = "ojk-spi-figures"

PUBLISHER = "Otoritas Jasa Keuangan"

NATIONAL = "Indonesia"

BILLIONS = "IDR billion"
OFFICES = "offices"

#: How far across a sheet to read. The older composition sheets hold two years
#: of monthly tables side by side, fifteen columns each; the generic reader's
#: eighty columns stop in the middle of the second year.
MAX_COLUMNS = 600
MAX_ROWS = 300

MONTHS = {
    "januari": 1, "january": 1, "jan": 1,
    "februari": 2, "february": 2, "feb": 2, "peb": 2,
    "maret": 3, "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "mei": 5, "may": 5,
    "juni": 6, "june": 6, "jun": 6,
    "juli": 7, "july": 7, "jul": 7,
    "agustus": 8, "august": 8, "agt": 8, "ags": 8, "agu": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "oktober": 10, "october": 10, "okt": 10, "oct": 10,
    "november": 11, "nov": 11, "nop": 11,
    "desember": 12, "december": 12, "des": 12, "dec": 12,
}  # fmt: skip

_MONTH_YEAR = re.compile(r"\b([A-Za-z]+)\s+(\d{4})\b")
_NUMBERED = re.compile(r"^\d+\.?$")

#: Rows a table files under no province. In the country's total, not a place.
UNPLACED = ("lainnya", "tidak teridentifikasi", "others")

PRE_2022_PAPUA = {
    "papua": "Papua (wilayah sebelum pemekaran 2022)",
    "papua barat": "Papua Barat (wilayah sebelum pemekaran 2022)",
    # Papua Barat's name until 2007, which the branch table kept for longer.
    "irian jaya barat": "Papua Barat (wilayah sebelum pemekaran 2022)",
}


@dataclass(frozen=True, slots=True)
class Part:
    """A printed column, found by its header and the one beneath it."""

    top: str
    sub: str | None = None

    def matches(self, top: str, sub: str) -> bool:
        if not _folded(top).startswith(self.top):
            return False
        return self.sub is None or _folded(sub).startswith(self.sub)


@dataclass(frozen=True, slots=True)
class Series:
    """One series read off a composition table."""

    indicator: str
    name: str
    #: Each category, as it is written in Silver, and the columns it adds up.
    categories: tuple[tuple[str, tuple[Part, ...]], ...]
    #: The printed total, where there is one. Otherwise the categories added.
    total: Part | None = None
    #: Whether the figures are the non-performing row beneath each province.
    npl: bool = False


#: Each deposit type is printed twice, in Rupiah and in foreign exchange.
_DPK_TYPES = (
    ("1. Giro", (Part("giro", "rupiah"), Part("giro", "valas"))),
    ("2. Tabungan", (Part("tabungan", "rupiah"), Part("tabungan", "valas"))),
    ("3. Deposito", (Part("deposito", "rupiah"), Part("deposito", "valas"))),
)
_DPK_TOTAL = Part("total dpk", "total")

_PURPOSES = (
    ("001. Modal Kerja", (Part("modal kerja"),)),
    ("002. Investasi", (Part("investasi"),)),
    ("003. Konsumsi", (Part("konsumsi"),)),
)
_ORIENTATIONS = (
    ("001. Ekspor", (Part("orientasi", "ekspor"),)),
    ("002. Impor", (Part("orientasi", "impor"),)),
    ("003. Lainnya", (Part("orientasi", "lainnya"),)),
)

#: The composition tables, by the sheet name before the table number. The
#: categories are labelled as the SJK cubes label them, so a reader following
#: a series across July 2025 finds the same rows on both sides.
COMPOSITIONS: dict[str, tuple[Series, ...]] = {
    "komp dpk per lok": (
        Series(
            "ojk_spi_bu_dpk",
            "Commercial bank third-party funds by type (SPI)",
            _DPK_TYPES,
            _DPK_TOTAL,
        ),
        Series(
            "ojk_spi_bu_dpk_currency",
            "Commercial bank third-party funds by currency (SPI)",
            (
                ("001. Rupiah", (Part("total dpk", "rupiah"),)),
                ("002. Valas", (Part("total dpk", "valas"),)),
            ),
            _DPK_TOTAL,
        ),
    ),
    "kredit jp-op per lok.": (
        Series("ojk_spi_bu_credit", "Commercial bank credit by purpose (SPI)", _PURPOSES),
        Series(
            "ojk_spi_bu_credit_orientation",
            "Commercial bank credit by orientation (SPI)",
            _ORIENTATIONS,
        ),
        Series(
            "ojk_spi_bu_npl_purpose",
            "Commercial bank non-performing credit by purpose (SPI)",
            _PURPOSES,
            npl=True,
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class Timeline:
    """One series read off a table whose columns are months."""

    indicator: str
    name: str
    unit: str
    #: Where the rows start, for a table with more than one section: the
    #: MSME table's second section is credit by the old MKM ceiling, empty
    #: since 2013.
    section: Callable[[str], bool] | None = None


TIMELINES: dict[str, Timeline] = {
    "juml kc per lok": Timeline(
        "ojk_spi_bu_branch_offices",
        "Commercial bank branch offices (SPI)",
        OFFICES,
    ),
    "umkm lok. dati i": Timeline(
        "ojk_spi_bu_credit_msme",
        "Commercial bank credit to micro, small and medium enterprises by project location (SPI)",
        BILLIONS,
        section=lambda label: _folded(label).startswith(("umkm", "usaha mikro")),
    ),
}


class OjkSpiExtractor(Extractor):
    """Every cell of an SPI edition, and the province series in it."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        suffix = landed.path.suffix.lower()
        return landed.source_slug == SOURCE_SLUG and (
            suffix in WORKBOOK_SUFFIXES or suffix in ARCHIVE_SUFFIXES
        )

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        edition = edition_month(landed)
        if edition is None:
            log.warning("ojk_spi.no_edition_month", path=str(landed.path))

        dataset = landed.dataset or landed.path.stem
        row_number = 0
        for member, content in _workbooks(landed):
            try:
                workbook = _load(content)
            except ExtractionError:
                raise
            except Exception as exc:  # noqa: BLE001 - surfaced with the path attached
                raise ExtractionError(str(landed.path), f"unreadable workbook: {exc}") from exc
            try:
                for row in _sheet_rows(workbook, dataset):
                    if member is not None:
                        row["columns"]["__member__"] = member
                    yield row
                if edition is None:
                    continue
                for sheet in workbook.worksheets:
                    for figure in sheet_figures(sheet.title, _grid(sheet), edition):
                        row_number += 1
                        yield {
                            "dataset": FIGURES_DATASET,
                            "row_number": row_number,
                            "columns": figure,
                        }
            finally:
                workbook.close()


def _workbooks(landed: Landed) -> Iterator[tuple[str | None, bytes]]:
    """The workbooks an artifact holds: itself, or each one in its archive."""
    content = landed.path.read_bytes()
    if landed.path.suffix.lower() not in ARCHIVE_SUFFIXES:
        yield None, content
        return
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ExtractionError(str(landed.path), f"unreadable archive: {exc}") from exc
    with archive:
        members = [
            name
            for name in archive.namelist()
            if name.lower().endswith(tuple(WORKBOOK_SUFFIXES)) and not name.startswith("__MACOSX/")
        ]
        if not members:
            raise ExtractionError(str(landed.path), "archive holds no workbook")
        for member in members:
            yield member, archive.read(member)


def edition_month(landed: Landed) -> str | None:
    """The month an edition is named after, as `YYYY-MM`."""
    for text in (landed.extra.get("edition"), landed.original_filename):
        if text:
            found = month_of(str(text).replace("-", " "))
            if found:
                return found
    return None


def month_of(text: str) -> str | None:
    """The first month and year named in a text, as `YYYY-MM`."""
    for word, year in _MONTH_YEAR.findall(text):
        month = MONTHS.get(word.lower())
        if month:
            return f"{year}-{month:02d}"
    return None


def sheet_figures(title: str, grid: list[list[str]], edition: str) -> Iterator[dict[str, str]]:
    """The figures one sheet holds for the edition's month."""
    name = _folded(title.split("_", 1)[0])
    if name in COMPOSITIONS:
        yield from composition_figures(name, title, grid, edition)
    elif name in TIMELINES:
        yield from timeline_figures(TIMELINES[name], title, grid, edition)


def composition_figures(
    name: str, sheet: str, grid: list[list[str]], edition: str
) -> Iterator[dict[str, str]]:
    """A composition table's figures, from the block for the edition's month."""
    for start, end in _blocks(grid):
        title = _cell(grid, _title_row(grid, start), start)
        if month_of(title.split("\n(")[0]) != edition:
            continue
        header = _header_row(grid, start, end)
        if header is None:
            log.warning("ojk_spi.no_header", sheet=sheet, edition=edition)
            return
        columns = _column_labels(grid, header, start + 2, end)
        places = _places(grid, header + 2, start, start + 1)
        for series in COMPOSITIONS[name]:
            yield from _series_figures(series, sheet, edition, columns, places)
        return
    log.info("ojk_spi.month_not_in_sheet", sheet=sheet, edition=edition)


def _series_figures(
    series: Series,
    sheet: str,
    edition: str,
    columns: dict[int, tuple[str, str]],
    places: list[tuple[str, dict[int, str], dict[int, str]]],
) -> Iterator[dict[str, str]]:
    def find(part: Part) -> int | None:
        found = [index for index, (top, sub) in columns.items() if part.matches(top, sub)]
        return found[0] if len(found) == 1 else None

    wanted = [(label, [find(part) for part in parts]) for label, parts in series.categories]
    total = find(series.total) if series.total else None
    if any(index is None for _, indices in wanted for index in indices) or (
        series.total and total is None
    ):
        log.warning("ojk_spi.columns_not_found", sheet=sheet, indicator=series.indicator)
        return

    for place, figures, npl in places:
        row = npl if series.npl else figures
        if not row:
            continue
        categories = [(label, _sum(row, indices)) for label, indices in wanted]
        if total is not None:
            overall = row.get(total, "")
        else:
            parts = [value for _, value in categories]
            overall = _plain(sum(map(Decimal, parts))) if all(_number(p) for p in parts) else ""
        for label, value in [("", overall), *categories]:
            if value == "":
                continue
            yield _figure(
                series.indicator, series.name, sheet, edition, place, label, value, BILLIONS
            )


def timeline_figures(
    timeline: Timeline, sheet: str, grid: list[list[str]], edition: str
) -> Iterator[dict[str, str]]:
    """A time-series table's figures, from its column for the edition's month."""
    header = _header_row(grid, 0, _width(grid))
    if header is None:
        log.warning("ojk_spi.no_header", sheet=sheet, edition=edition)
        return
    label_column = next(
        index
        for index, text in enumerate(grid[header])
        if _folded(text).startswith(("lokasi", "kelompok", "keterangan"))
    )
    months = grid[header + 1] if header + 1 < len(grid) else []
    column = max((i for i, text in enumerate(months) if _month_abbrev(text)), default=None)
    if column is None or _month_abbrev(months[column]) != int(edition[5:]):
        log.info("ojk_spi.month_not_in_sheet", sheet=sheet, edition=edition)
        return

    first = header + 2
    if timeline.section is not None:
        found = next(
            (
                row
                for row in range(first, len(grid))
                if timeline.section(_cell(grid, row, label_column))
            ),
            None,
        )
        if found is None:
            log.warning("ojk_spi.section_not_found", sheet=sheet, edition=edition)
            return
        first = found + 1

    for place, figures, _ in _places(grid, first, label_column, label_column + 1):
        value = figures.get(column, "")
        if value != "":
            yield _figure(
                timeline.indicator, timeline.name, sheet, edition, place, "", value, timeline.unit
            )


def _places(
    grid: list[list[str]], first: int, number_column: int, name_column: int
) -> list[tuple[str, dict[int, str], dict[int, str]]]:
    """Each province's row, and the non-performing row beneath it, to the total.

    The country is the total row, and the table ends there: below it are the
    notes, or a second section.
    """
    out: list[tuple[str, dict[int, str], dict[int, str]]] = []
    for row in range(first, len(grid)):
        number = _cell(grid, row, number_column)
        name = _cell(grid, row, name_column)
        values = {i: text for i, text in enumerate(grid[row]) if i > name_column and text}
        if _folded(name).startswith("npl") or _folded(number).startswith("npl"):
            if out:
                out[-1][2].update(values)
            continue
        if _folded(number).startswith(("total", "jumlah")) or _folded(name).startswith(
            ("total", "jumlah")
        ):
            out.append((NATIONAL, values, {}))
            continue
        if _NUMBERED.match(number) and name:
            if out and out[-1][0] == NATIONAL:
                break
            place = province(name)
            out.append((place or "", values, {}))
            continue
        if out and out[-1][0] == NATIONAL and not _folded(name).startswith("npl"):
            break
    return [entry for entry in out if entry[0]]


def province(label: str) -> str | None:
    """A table's province as the geography registry should read it."""
    name = " ".join(label.split("/")[0].split())
    folded = name.lower()
    if not name or folded.startswith(UNPLACED):
        return None
    if folded in PRE_2022_PAPUA:
        return PRE_2022_PAPUA[folded]
    if folded.startswith("provinsi "):
        return name
    return f"Provinsi {name}"


def _figure(
    indicator: str,
    name: str,
    sheet: str,
    period: str,
    place: str,
    category: str,
    value: str,
    unit: str,
) -> dict[str, str]:
    # openpyxl writes a small float as `1e-05`, which is a number but not one
    # the normalizer reads; the digits are written out instead.
    if _number(value):
        value = _plain(Decimal(value))
    return {
        "indicator": indicator,
        "name": name,
        "table": sheet,
        "period": period,
        "geo": place,
        "category": category,
        "value": value,
        "unit": unit,
        "publisher": PUBLISHER,
    }


def _grid(sheet: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    for index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
        if index > MAX_ROWS:
            break
        rows.append(["" if value is None else str(value).strip() for value in row[:MAX_COLUMNS]])
    return rows


def _blocks(grid: list[list[str]]) -> list[tuple[int, int]]:
    """The column spans of the tables side by side on a sheet, by their titles."""
    starts = sorted(
        {
            column
            for row in grid[:4]
            for column, text in enumerate(row)
            if _folded(text).startswith("tabel")
        }
    )
    width = _width(grid)
    return [
        (start, starts[i + 1] if i + 1 < len(starts) else width) for i, start in enumerate(starts)
    ]


def _title_row(grid: list[list[str]], column: int) -> int:
    return next(
        row
        for row in range(min(4, len(grid)))
        if _folded(_cell(grid, row, column)).startswith("tabel")
    )


def _header_row(grid: list[list[str]], start: int, end: int) -> int | None:
    """The row whose first cell names the location column."""
    for row in range(min(12, len(grid))):
        for column in range(start, min(end, start + 2)):
            if _folded(_cell(grid, row, column)).startswith(
                ("lokasi", "kelompok bank", "keterangan")
            ):
                return row
    return None


def _column_labels(
    grid: list[list[str]], header: int, start: int, end: int
) -> dict[int, tuple[str, str]]:
    """Each column's header and the one beneath it, the header carried across the
    columns its merged cell spans."""
    labels: dict[int, tuple[str, str]] = {}
    top = ""
    for column in range(start, end):
        text = _cell(grid, header, column)
        if _folded(text).startswith(("location", "lokasi")):
            break
        top = text or top
        labels[column] = (top, _cell(grid, header + 1, column))
    return labels


def _sum(row: dict[int, str], indices: list[int | None]) -> str:
    texts = [row.get(index, "") for index in indices if index is not None]
    if len(texts) == 1:
        return texts[0]
    if not texts or not all(_number(text) for text in texts):
        return ""
    return _plain(sum(map(Decimal, texts)))


def _number(text: str) -> bool:
    try:
        return Decimal(text).is_finite()
    except InvalidOperation:
        return False


def _plain(value: Decimal) -> str:
    """A sum as plain digits: no exponent, no trailing zeros after the point."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _month_abbrev(text: str) -> int | None:
    word = re.split(r"[^A-Za-z]", text.strip(), maxsplit=1)[0].lower()
    return MONTHS.get(word)


def _cell(grid: list[list[str]], row: int, column: int) -> str:
    if row >= len(grid) or column >= len(grid[row]):
        return ""
    return grid[row][column]


def _width(grid: list[list[str]]) -> int:
    return max((len(row) for row in grid), default=0)


def _folded(text: str) -> str:
    return " ".join(text.lower().split())
