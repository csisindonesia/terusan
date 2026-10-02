"""KPK's enforcement statistics page into Bronze, one figure per row.

The page is seven tables, each a breakdown down its rows and the years across
its columns —

    NO | INSTANSI            | 2004 | 2005 | ... | 2026 | JUMLAH
    1  | DPR RI              |      |      | ... |      | 91
    2  | Kementerian/Lembaga | 1    | 5    | ... | 22   | 567

— and every cell under a year becomes one record in `kpk-penindakan-figures`,
which `scripts/normalize-kpk.sh` takes into Silver. A table is recognised by
the label over its row names (`INSTANSI`, `JABATAN`, `PROVINSI`, ...), not by
its position, because KPK has reordered the page before. The first table has
no such label and is recognised by its rows, the stages of a case.

Which years a table holds is read from its own header. They are not the same
across the page: the verdicts-by-court table skips 2021 to 2024.

**The totals are not read.** The `JUMLAH` row and column are KPK's sums of the
cells beside them, which Silver can take itself, and keeping them would count
every case twice in any sum over the categories. They also do not agree from
table to table (1,820 cases by agency, 1,827 by offence, 1,828 by region), and
that is KPK's to reconcile, not this reader's.

**The regions are not places.** The region table groups by island (`Jawa`,
`Sumatera`), by the central government, and by two foreign countries, so the
row is a category of the national figure rather than a geography.

**The current year is year to date.** The page states how far it runs
(`Updated : Per-Maret 2026 | TW I`), and that is carried as each figure's
release, so a reader can tell 2026's 35 inquiries from a full year's.

A cell KPK leaves empty stays empty. It almost certainly means no cases, but
the page does not say so, and a blank is Silver's to classify, not this
reader's to turn into zero.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import structlog
from selectolax.parser import HTMLParser, Node

from .base import ExtractionError, Extractor, Landed

log = structlog.get_logger(__name__)

SOURCE_SLUG = "kpk-penindakan"

#: Where the figures land in Bronze.
FIGURES_DATASET = "kpk-penindakan-figures"

PUBLISHER = "Komisi Pemberantasan Korupsi"

#: KPK's caseload is national; every breakdown is a category of it.
GEO = "Indonesia"

CASES = "cases"
PERSONS = "persons"

_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_AS_OF = re.compile(r"Updated\s*:\s*(Per-\s*\w+\s+\d{4})", re.IGNORECASE)
_TOTALS = {"jumlah", "total"}


@dataclass(frozen=True, slots=True)
class Series:
    indicator: str
    name: str
    unit: str


#: The tables, by the label over their row names.
TABLES: dict[str, Series] = {
    "instansi": Series("kpk_cases_by_agency", "KPK corruption cases by agency", CASES),
    "jabatan": Series(
        "kpk_defendants_by_position", "KPK corruption defendants by post held", PERSONS
    ),
    "jenis perkara": Series("kpk_cases_by_offence", "KPK corruption cases by offence", CASES),
    "provinsi": Series("kpk_cases_by_region", "KPK corruption cases by region", CASES),
    "inkracht": Series(
        "kpk_final_verdicts_by_court", "KPK cases with a final verdict, by court", CASES
    ),
    "jenis kelamin": Series("kpk_defendants_by_sex", "KPK corruption defendants by sex", PERSONS),
}

#: The first table, which has no label and is known by its rows.
STAGES = Series("kpk_cases_by_stage", "KPK corruption cases by stage", CASES)
_STAGE_ROWS = {"penyelidikan", "penyidikan", "penuntutan", "inkracht", "eksekusi"}


def _text(node: Node) -> str:
    text = unicodedata.normalize("NFKC", node.text(separator=" "))
    return re.sub(r"\s+", " ", text).strip()


def _folded(text: str) -> str:
    return text.casefold().strip()


def _rows(table: Node) -> list[list[str]]:
    return [[_text(cell) for cell in row.css("td, th")] for row in table.css("tr")]


def as_of(page: str) -> str:
    """`Per-Maret 2026`, the point the page's figures run to; empty if unsaid."""
    match = _AS_OF.search(page)
    return re.sub(r"\s+", " ", match.group(1)) if match else ""


def series_of(rows: list[list[str]], label_column: int) -> Series | None:
    """Which of the page's tables this is, by its label or else its rows."""
    label = _folded(rows[0][label_column])
    if label in TABLES:
        return TABLES[label]
    names = {_folded(row[label_column]) for row in rows[1:] if len(row) > label_column}
    if label == "" and names >= _STAGE_ROWS:
        return STAGES
    return None


def table_figures(rows: list[list[str]], release: str) -> Iterator[dict[str, str]]:
    """One figure per category and year in one table; nothing for a table not known."""
    if not rows:
        return
    header = rows[0]
    years = {index: cell for index, cell in enumerate(header) if _YEAR.match(cell)}
    if not years:
        return
    # The row names sit in the column just before the first year; a `NO`
    # column, where there is one, is before that.
    label_column = min(years) - 1
    if label_column < 0:
        return
    series = series_of(rows, label_column)
    if series is None:
        log.warning("kpk_penindakan.unknown_table", label=header[label_column])
        return

    for row in rows[1:]:
        if len(row) <= label_column:
            continue
        category = row[label_column]
        if not category or _folded(category) in _TOTALS:
            continue
        for index, year in years.items():
            yield {
                "indicator": series.indicator,
                "name": series.name,
                "category": category,
                "period": year,
                "value": row[index] if index < len(row) else "",
                "geo": GEO,
                "unit": series.unit,
                "release": release,
                "publisher": PUBLISHER,
            }


class KpkPenindakanExtractor(Extractor):
    """The seven tables of KPK's enforcement statistics, one figure per row."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        # The page is landed under its URL's last segment, which has no suffix.
        return landed.source_slug == SOURCE_SLUG

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        page = landed.path.read_bytes().decode("utf-8", errors="replace")
        tree = HTMLParser(page)
        body = tree.body
        release = as_of(_text(body) if body is not None else "")

        row_number = 0
        for table in tree.css("table"):
            for figure in table_figures(_rows(table), release):
                row_number += 1
                yield {"dataset": FIGURES_DATASET, "row_number": row_number, "columns": figure}

        if row_number == 0:
            raise ExtractionError(str(landed.path), "page holds none of KPK's statistics tables")
