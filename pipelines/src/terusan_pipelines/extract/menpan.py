"""The SKB 3 Menteri's holiday tables into Bronze records, by OCR.

KemenPANRB publishes the decrees as scans with no text layer, so the annex —
two ruled tables, national holidays and cuti bersama —

    NO. | TANGGAL          | HARI                    | KETERANGAN
    5.  | 10-11 Maret      | Rabu-Kamis              | Idul Fitri 1448 Hijriah
    2.  | 9,12, dan 15     | Selasa, Jumat,          | Idul Fitri 1448 Hijriah
        | Maret            | dan Senin               |

— is read from the page image. Tesseract read over a whole page mixes the
columns and loses the ruled rows, so the rules are found first and each cell is
read on its own:

1. every page is rendered at 300 dpi and straightened — the scans lean by up to
   a degree, enough that no pixel row lies along a table's rule;
2. the horizontal rules are the pixel rows that are mostly ink, the vertical
   ones the columns that are ink the whole height of a table;
3. each cell is read by Tesseract's Indonesian model, and the header row names
   the columns.

A row is kept only when its dates fall on the weekdays the decree prints
beside them. OCR misreads a 6 as a 5 far more often than a table misprints a
date, and a misread date checked against its weekday fails six times in seven;
a row that fails is logged and left out rather than guessed at. A decree that
yields fewer national holidays than any year has had is refused whole, since a
partial calendar read as a complete one would put a working day where Lebaran
was.

What the dates are called stays as printed ("Isra Mikraj Nabi Muhammad S.A.W.
1448 Hijriah"), OCR slips included. Naming the holiday is Silver's.

Requires the `tesseract` binary with the `ind` language, and pypdfium2, numpy
and Pillow, which the `extract` extra brings.
"""

from __future__ import annotations

import difflib
import io
import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

import structlog

from .base import ExtractionError, Extractor, Landed

if TYPE_CHECKING:
    from PIL.Image import Image

log = structlog.get_logger(__name__)

SOURCE_SLUG = "menpan-hari-libur"

NATIONAL = "libur_nasional"
COLLECTIVE = "cuti_bersama"

#: Fewer national holidays than this and the annex was not read: every year
#: since 2020 has had at least fifteen.
MIN_NATIONAL_HOLIDAYS = 12

DPI = 300

MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "januari",
            "februari",
            "maret",
            "april",
            "mei",
            "juni",
            "juli",
            "agustus",
            "september",
            "oktober",
            "november",
            "desember",
        ),
        start=1,
    )
}

#: `date.weekday()` order.
WEEKDAYS = ("senin", "selasa", "rabu", "kamis", "jumat", "sabtu", "minggu")


# --- reading a date cell -------------------------------------------------


def _closest(word: str, choices: tuple[str, ...] | list[str], cutoff: float = 0.75) -> str | None:
    word = word.lower().replace("'", "")
    if word in choices:
        return word
    found = difflib.get_close_matches(word, choices, n=1, cutoff=cutoff)
    return found[0] if found else None


#: The row's number, where a broken rule let it into the date's cell:
#: `10. | 29 Mei`, `5 | 26 Desember`.
_ENTRY_PREFIX = re.compile(r"^\s*\S{1,4}\s*\|\s*|^\s*\d{1,2}\s*\.\s+(?=\d)")


_DATE_TOKENS = re.compile(r"\d{1,2}|[A-Za-z']+|[-–—]")


def parse_dates(cell: str, year: int) -> list[date]:
    """Every date a TANGGAL cell names, ranges spelled out.

    `1 Januari`, `10-11 Maret`, `31 Maret-1 April`, `9,12, dan 15 Maret`,
    `28 dan 30 Oktober`, `29 April, 4 Mei, 5 Mei, dan 6 Mei`. A cell that
    reads as none of these — the dash of a year with no cuti bersama — names
    nothing.
    """
    # (day, month or None, joined to the previous day by a dash)
    days: list[list[Any]] = []
    dash = False
    for token in _DATE_TOKENS.findall(_ENTRY_PREFIX.sub("", cell)):
        if token.isdigit():
            days.append([int(token), None, dash])
            dash = False
        elif token in "-–—":
            dash = bool(days)
        else:
            month = _closest(token, list(MONTHS))
            if month is None:
                continue  # `dan`, OCR noise
            # A month names every day since the last one that had a month.
            for pending in reversed(days):
                if pending[1] is not None:
                    break
                pending[1] = MONTHS[month]

    found: list[date] = []
    for day, month, ranged in days:
        if month is None:
            return []
        try:
            current = date(year, month, day)
        except ValueError:
            return []
        if ranged and found:
            start = found[-1]
            span = (current - start).days
            if not 0 < span <= 7:
                return []
            found.extend(date.fromordinal(start.toordinal() + n) for n in range(1, span))
        found.append(current)
    return found


def parse_weekdays(cell: str) -> tuple[list[int], bool]:
    """The weekdays a HARI cell names, and whether it names a range."""
    # Looser than months: the names are unlike one another, and the scans
    # blur them (`Minssu`).
    names = [_closest(word, WEEKDAYS, cutoff=0.6) for word in re.findall(r"[A-Za-z']+", cell)]
    return [WEEKDAYS.index(n) for n in names if n], bool(re.search(r"[-–—]", cell))


def weekdays_agree(dates: list[date], cell: str) -> bool:
    """Whether the dates fall on the weekdays the decree prints beside them."""
    weekdays, ranged = parse_weekdays(cell)
    if not dates or not weekdays:
        return False
    actual = [d.weekday() for d in dates]
    if len(weekdays) == len(actual):
        return weekdays == actual
    # `Rabu-Kamis` beside a range: its ends.
    if ranged and len(weekdays) == 2:
        return weekdays == [actual[0], actual[-1]]
    return False


# --- finding the tables --------------------------------------------------


def _runs(mask: Any) -> list[tuple[int, int]]:
    """(start, end) of each stretch of True."""
    import numpy as np

    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[::2], edges[1::2] - 1, strict=True))


def _straighten(image: Image) -> Image:
    """The page rotated so its rules lie flat.

    The angle is the one at which the ink's row profile is sharpest: rules and
    lines of text concentrate into few pixel rows when level.
    """
    import numpy as np
    from PIL import Image as PILImage

    small = image.convert("L").resize((image.width // 4, image.height // 4))

    def sharpness(angle: float) -> float:
        ink = np.asarray(small.rotate(angle, fillcolor=255)) < 160
        return float((ink.mean(axis=1) ** 2).sum())

    best = max((a / 10 for a in range(-30, 31, 2)), key=sharpness)
    best = max((best + a / 100 for a in range(-10, 11, 2)), key=sharpness)
    if abs(best) < 0.05:
        return image
    return image.rotate(best, fillcolor=255, resample=PILImage.Resampling.BICUBIC)


def _threshold(pixels: Any) -> int:
    """Otsu's grey level between paper and ink.

    The older scans print their rules in a pale grey that a fixed threshold
    set for the newer ones reads as paper.
    """
    import numpy as np

    share = np.bincount(pixels.ravel(), minlength=256) / pixels.size
    below = np.cumsum(share)
    mean_below = np.cumsum(share * np.arange(256))
    between = (mean_below[-1] * below - mean_below) ** 2 / (below * (1 - below) + 1e-12)
    return int(np.argmax(between))


def _longest_run(mask: Any) -> int:
    return max((b - a + 1 for a, b in _runs(mask)), default=0)


def _ruled(ink: Any) -> Any:
    """Which pixel rows are a table's horizontal rule.

    A rule is one unbroken stroke across at least two fifths of the page, once
    the specks a scan drops out of it are filled. A line of text is broken at
    every space.
    """
    import numpy as np

    closed = ink.copy()
    for shift in range(1, 9):
        closed[:, shift:] |= ink[:, :-shift]
    width = ink.shape[1]
    return np.array([_longest_run(row) > width * 0.4 for row in closed])


@dataclass(slots=True)
class _Table:
    #: Each row's cells, as images: read here, and read again another way
    #: when the first reading does not check out.
    rows: list[list[Image]]
    top: int


def _tables(page: Image) -> Iterator[_Table]:
    import numpy as np

    pixels = np.asarray(page)
    ink = pixels <= _threshold(pixels)
    # A rule may still wander a few pixels after straightening: the older
    # scans bow where the page lay unevenly on the glass.
    thick = ink.copy()
    for shift in range(1, 4):
        thick[shift:] |= ink[:-shift]
        thick[:-shift] |= ink[shift:]
    rules = [(a + b) // 2 for a, b in _runs(_ruled(thick))]

    # Each stretch between two rules is a row when rules cross it top to
    # bottom, and the gap between two tables when none do — the heading
    # between them is text, not ruled.
    tables: list[list[tuple[int, int, list[int]]]] = []
    current: list[tuple[int, int, list[int]]] = []
    for upper, lower in zip(rules, rules[1:], strict=False):
        if lower - upper < 30:
            continue  # a doubled rule
        band = ink[upper + 3 : lower - 3]
        wide = band.copy()
        wide[:, 1:] |= band[:, :-1]
        wide[:, :-1] |= band[:, 1:]
        for shift in range(1, 5):
            wide[shift:] |= wide[:-shift]
        columns = [(a + b) // 2 for a, b in _runs(wide.mean(axis=0) > 0.85)]
        if len(columns) < 3:
            if current:
                tables.append(current)
                current = []
            continue
        current.append((upper, lower, columns))
    if current:
        tables.append(current)

    for bands in tables:
        columns = _continuous(ink, bands)
        rows = [
            [
                page.crop((left + 6, upper + 5, right - 6, lower - 5))
                for left, right in zip(columns, columns[1:], strict=False)
            ]
            for upper, lower, _ in bands
        ]
        yield _Table(rows=rows, top=bands[0][0])


def _continuous(ink: Any, bands: list[tuple[int, int, list[int]]], near: int = 12) -> list[int]:
    """The rules that run down the whole table.

    Found row by row, a tall letter in a short row can pass for one, and so
    can a left-aligned column's first digits stacked row on row. Neither
    crosses the space between rows, which a rule does: a candidate is kept
    where there is ink nearly the whole height of the table.
    """
    positions = sorted(x for _, _, columns in bands for x in columns)
    clusters: list[list[int]] = []
    for x in positions:
        if clusters and x - clusters[-1][-1] <= near:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    top, bottom = bands[0][0], bands[-1][1]
    kept: list[int] = []
    for cluster in clusters:
        x = sum(cluster) // len(cluster)
        strip = ink[top:bottom, max(0, x - 4) : x + 5].any(axis=1)
        if strip.mean() > 0.9 and (not kept or x - kept[-1] >= 30):
            kept.append(x)
    return kept


def _tesseract(image: Image, psm: int) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", "ind", "--psm", str(psm)],
        input=buffer.getvalue(),
        capture_output=True,
        check=False,
    )
    return result.stdout.decode("utf-8", errors="replace")


def read_block(image: Image) -> str:
    """A cell read as a block of text, on one line."""
    return " ".join(_tesseract(image, 6).split())


def read_enlarged(image: Image) -> str:
    """A cell read at half as large again: the older scans' small type."""
    from PIL import Image as PILImage

    size = (image.width * 3 // 2, image.height * 3 // 2)
    return " ".join(_tesseract(image.resize(size, PILImage.Resampling.LANCZOS), 6).split())


def read_sparse(image: Image) -> str:
    """A cell read as scattered words, for one whose rule crosses the text."""
    return " ".join(_tesseract(image, 11).split())


#: Tried in turn on a row whose dates do not fall on its weekdays.
READERS = (read_block, read_enlarged, read_sparse)


def _is_header(row: list[str]) -> bool:
    words = {re.sub(r"[^A-Z]", "", text.upper()) for text in row}
    return any(difflib.SequenceMatcher(None, w, "TANGGAL").ratio() > 0.75 for w in words) and any(
        w == "HARI" for w in words
    )


def _section_named(text: str) -> str | None:
    """The section the last line naming one names.

    The annex's title names both — "Hari Libur Nasional dan Cuti Bersama" —
    so a line naming both is the title, not a heading.
    """
    for line in reversed(text.splitlines()):
        squeezed = re.sub(r"[^A-Z]", "", line.upper())
        collective = "CUTI" in squeezed or "BERSAMA" in squeezed
        national = "LIBUR" in squeezed
        if collective and national:
            continue
        if collective:
            return COLLECTIVE
        if national:
            return NATIONAL
    return None


@dataclass(frozen=True, slots=True)
class Holiday:
    section: str
    date: date
    weekday: str
    name: str
    entry: str
    span: int


def _checked(row: list[str], year: int) -> tuple[list[date], int] | None:
    """The row's dates and the cell they are in, when their weekdays agree."""
    # The date is the first cell that reads as one, the weekday the next: a
    # broken rule merges or splits cells, so a column's position in the
    # header does not say where its text is in every row.
    at = next((i for i, cell in enumerate(row[:-1]) if parse_dates(cell, year)), None)
    if at is None:
        return None
    dates = parse_dates(row[at], year)
    return (dates, at) if weekdays_agree(dates, row[at + 1]) else None


def read_annex(
    pages: list[Image],
    year: int,
    readers: tuple[Any, ...] = READERS,
    heading: Any = None,
) -> tuple[list[Holiday], int]:
    """Every checked holiday the decree's tables list, and the rows refused."""
    heading = heading or (lambda image: _tesseract(image, 6))
    found: list[Holiday] = []
    refused = 0
    section = NATIONAL
    tables_seen = 0
    for page in pages:
        page = _straighten(page.convert("L"))
        for table in _tables(page):
            # The heading above a table says which it is. Where it cannot be
            # read, the second table after the first is the cuti bersama —
            # unless it carries no header, and so continues the one before
            # from the previous page.
            named = _section_named(
                heading(page.crop((0, max(0, table.top - 300), page.width, table.top - 4)))
            )
            first = [readers[0](cell) for cell in table.rows[0]]
            if named:
                section = named
            elif tables_seen and _is_header(first):
                section = COLLECTIVE
            tables_seen += 1

            for number, cells in enumerate(table.rows):
                row = first if number == 0 else [readers[0](cell) for cell in cells]
                if _is_header(row):
                    continue
                joined = " ".join(row)
                if (switch := _section_named(joined)) and not re.search(r"\d", joined):
                    # A heading caught between two tables' rules.
                    section = switch
                    continue
                checked = _checked(row, year)
                for reader in readers[1:]:
                    if checked is not None:
                        break
                    if not re.search(r"\d", joined):
                        break  # a blank row, or the dash of a year with none
                    row = [reader(cell) for cell in cells]
                    checked = _checked(row, year)
                if checked is None:
                    if re.search(r"\d.*[A-Za-z]{3}", joined):
                        refused += 1
                        log.warning("menpan.row_refused", year=year, row=row)
                    continue
                dates, at = checked
                weekday, name = row[at + 1], " ".join(row[at + 2 :])
                entry = " ".join(row[:at])
                for span, day in enumerate(dates):
                    found.append(Holiday(section, day, weekday, name, entry, span))
    return found, refused


def render(path: Any) -> list[Image]:
    import pypdfium2 as pdfium  # type: ignore[import-untyped]

    document = pdfium.PdfDocument(str(path))
    try:
        return [page.render(scale=DPI / 72).to_pil() for page in document]
    finally:
        document.close()


class MenpanHolidaysExtractor(Extractor):
    """One Bronze record per holiday date a decree's annex lists."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() == ".pdf"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        if shutil.which("tesseract") is None:
            raise ExtractionError(
                str(landed.path), "tesseract is not installed; the decrees are scans"
            )
        year = landed.extra.get("target_year")
        if not year:
            raise ExtractionError(str(landed.path), "landing record names no target year")

        holidays, refused = read_annex(render(landed.path), int(year))
        national = sum(1 for h in holidays if h.section == NATIONAL)
        if national < MIN_NATIONAL_HOLIDAYS:
            raise ExtractionError(
                str(landed.path),
                f"read {national} national holidays for {year} ({refused} rows refused); "
                "the annex was not read",
            )
        log.info(
            "menpan.annex_read",
            year=year,
            national=national,
            collective=len(holidays) - national,
            refused=refused,
        )
        for number, holiday in enumerate(holidays, start=1):
            yield {
                "dataset": landed.dataset or "skb-hari-libur",
                "row_number": number,
                # Bronze holds text; Silver types it.
                "columns": {
                    "date": holiday.date.isoformat(),
                    "year": str(year),
                    "kind": holiday.section,
                    "name": holiday.name,
                    "weekday": holiday.weekday,
                    "entry": holiday.entry,
                    "day_of_span": str(holiday.span + 1),
                    "decree_id": str(landed.extra.get("decree_id") or ""),
                    "decree_title": str(landed.extra.get("title") or ""),
                    "decree_enacted": str(landed.extra.get("enacted") or ""),
                    "amends": "true" if landed.extra.get("amends") else "false",
                    "rows_refused": str(refused),
                },
            }
