"""SATUSEHAT's workforce planning dashboard into Bronze: its tables, and its series.

The generic readers get none of it right. The register is a DataTables
envelope, which the JSON reader turns into one row holding ten thousand;
the pages are HTML whose one table of interest (`#dtone`) has headers spanning
two rows, and whose year is in a sentence above the table rather than in it.

So, as with OJK's cubes, this reader produces two things:

- **The dashboard's own rows**, under the dataset they landed in — the
  register's regency rows, each vacancy table, each projection and workload
  table — every cell as text, keyed by the header the dashboard printed.
  Nothing normalizes these; they are here so the detail is queryable.
- **`kemkes-sdmk-figures`** — the tables recast as one figure per row, which
  `scripts/normalize-kemkes.sh` takes into Silver:

  - `kemkes_sdmk_workforce` and `kemkes_sdmk_workforce_family`: the
    register's headcounts summed per province and for the country, by
    profession and by the family of professions the dashboard files it under.
    Dated by the SISDMK snapshot the register page names. A profession is
    named with its family ("Bidan — Bidan Desa"), since the dashboard files a
    few under two. A register artifact
    is one family, so neither series has a total of its own: a total over
    every family would be a sum across seventy artifacts, which a reader of
    one artifact cannot take, and employment type is left to Bronze for the
    same reason — broken down by it, the family would be a second category.
  - `kemkes_sdmk_need`, `kemkes_sdmk_existing`, `kemkes_sdmk_gap`: the
    supply-demand projection per profession, province and year. Health
    workers are projected for 2023 to 2032; doctors only for the year the
    page shows. The country is the dashboard's own figure, from the summary
    above the table, not a sum of the provinces.
  - `kemkes_vacancy_*`: per province, workers needed to fill facilities with
    none of a priority profession, and the shortfall against the minimum
    staffing standard, by profession. Dated by the same snapshot, because the
    pages say their cut-off is SISDMK's first of the month and name no other.
    The country is the table's own `Total` row.

The workload analysis (`abk`) reaches Bronze but not Silver: nothing on its
pages says which year its need was computed for, and a figure with no period
is not an observation.

Three decisions are made here.

**Provinces are qualified with `Provinsi`**, which is how the geography
registry tells the province of Gorontalo from the regency inside it. The
dashboard's `YOGYAKARTA` is written as the registry names the province, since
"Provinsi Yogyakarta" is not a name anything uses.

**Only the summary's figures are re-read.** It prints thousands the Indonesian
way (`146.119`), and a count of people has no decimals, so a dot between
groups of three digits is a separator and nothing else. Table cells are plain
digits already and pass through as written; a cell that is not a number — a
dash, a blank — passes through too, for the normalizer to record as the
status it is.

**Sums are integers over the register's rows**, which are head counts. A row
whose count is not a whole number is left out of the sum and logged rather
than rounded.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterator
from html import unescape
from html.parser import HTMLParser
from typing import Any

import structlog

from .base import ExtractionError, Extractor, Landed

log = structlog.get_logger(__name__)

SOURCE_SLUG = "kemkes-sdmk-planning"

FIGURES_DATASET = "kemkes-sdmk-figures"

PUBLISHER = "Kementerian Kesehatan"

NATIONAL = "Indonesia"

#: The register's columns, in the order the endpoint sends them. Filtered to
#: a family it answers per regency; unfiltered, the page's default view, per
#: facility.
REGISTER_COLUMNS = {
    5: ("propinsi", "kabupaten_kota", "sdmk", "jenis_kepegawaian", "jumlah"),
    7: (
        "propinsi",
        "kabupaten_kota",
        "nama_faskes",
        "tipe_faskes",
        "sdmk",
        "jenis_kepegawaian",
        "jumlah",
    ),
}

#: Provinces the dashboard names otherwise than the geography registry does.
PROVINCE_NAMES = {"YOGYAKARTA": "DI Yogyakarta"}

#: How each vacancy page becomes a series. The per-hospital listing has no
#: province column and is left to Bronze.
VACANCY_SERIES = {
    "rs": ("kemkes_vacancy_hospital", "Specialists needed to fill hospital vacancies"),
    "labkes": (
        "kemkes_vacancy_labkes",
        "Health workers needed to fill public health lab vacancies",
    ),
    "pkm_9": (
        "kemkes_vacancy_puskesmas_9",
        "Health workers needed to fill puskesmas vacancies (9 professions)",
    ),
    "pkm_13": (
        "kemkes_vacancy_puskesmas_13",
        "Health workers needed to fill puskesmas vacancies (13 professions)",
    ),
    "pustu": ("kemkes_vacancy_pustu", "Health workers needed to fill pustu vacancies"),
    "skm_rs": (
        "kemkes_shortfall_hospital",
        "Specialist shortfall against the hospital minimum staffing standard",
    ),
    "skm_labkes": (
        "kemkes_shortfall_labkes",
        "Health worker shortfall against the public health lab minimum staffing standard",
    ),
    "skm_pkm_13": (
        "kemkes_shortfall_puskesmas_13",
        "Health worker shortfall against the puskesmas minimum staffing standard",
    ),
}

#: The register's two series: by profession, and by the family it is under.
WORKFORCE = ("kemkes_sdmk_workforce", "Health workers registered in SISDMK by profession")
WORKFORCE_FAMILY = (
    "kemkes_sdmk_workforce_family",
    "Health workers registered in SISDMK by profession family",
)

#: The projection's three measures, by the header's last word.
DEMAND_SERIES = {
    "kebutuhan": ("kemkes_sdmk_need", "Projected health workforce need"),
    "eksisting": ("kemkes_sdmk_existing", "Existing health workforce, as projected against need"),
    "gap": ("kemkes_sdmk_gap", "Health workforce gap against projected need"),
}

_SUMMARY = re.compile(
    r"Indonesia\s+Tahun\s+(\d{4})\s+Kebutuhan\s+(-?[\d.]+)\s+Eksisting\s+(-?[\d.]+)"
    r"\s+Gap\s+(-?[\d.]+)"
)
_GROUPED = re.compile(r"-?\d{1,3}(?:\.\d{3})+")
_INTEGER = re.compile(r"-?\d+")


class KemkesWorkforceExtractor(Extractor):
    """The workforce planning dashboard's tables, and the series read off them."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() in {
            ".json",
            ".html",
        }

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        if landed.path.suffix.lower() == ".json":
            yield from self._register(landed)
            return

        page = landed.path.read_bytes().decode("utf-8", "replace")
        grid = tables(page).get("dtone")
        if grid is None:
            raise ExtractionError(str(landed.path), "no #dtone table on the page")
        header, rows = split_header(grid.rows())
        dataset = landed.dataset or SOURCE_SLUG
        prefix = _row_prefix(landed)

        for index, row in enumerate(rows, start=1):
            yield {
                "dataset": dataset,
                "row_number": index,
                "columns": prefix | dict(zip(header, row, strict=True)),
            }

        kind = str(landed.extra.get("page") or "")
        if kind in VACANCY_SERIES:
            yield from vacancy_figures(kind, landed.extra.get("sisdmk_cutoff"), header, rows)
        elif kind.startswith("demand_"):
            yield from demand_figures(str(landed.extra.get("profession") or ""), page, header, rows)

    def _register(self, landed: Landed) -> Iterator[dict[str, Any]]:
        try:
            body = json.loads(landed.path.read_bytes(), parse_float=str, parse_int=str)
        except json.JSONDecodeError as exc:
            raise ExtractionError(str(landed.path), f"invalid JSON: {exc}") from exc
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise ExtractionError(str(landed.path), "no data list in the register")

        facts = [_register_row(row) for row in data]
        prefix = _row_prefix(landed)
        for index, columns in enumerate(facts, start=1):
            yield {
                "dataset": landed.dataset or SOURCE_SLUG,
                "row_number": index,
                "columns": prefix | columns,
            }
        yield from register_figures(
            landed.extra.get("sisdmk_cutoff"), str(landed.extra.get("family") or ""), facts
        )


def register_figures(
    cutoff: str | None, family: str, facts: list[dict[str, str]]
) -> Iterator[dict[str, Any]]:
    """One family's headcounts, per province and for the country."""
    if not cutoff:
        log.warning("kemkes_sdmk.register_undated")
        return
    sums: dict[tuple[str, str, str], int] = defaultdict(int)
    skipped = 0
    for fact in facts:
        count = fact.get("jumlah", "").strip()
        if not _INTEGER.fullmatch(count):
            skipped += 1
            continue
        profession = _within(family, " ".join(fact.get("sdmk", "").split()))
        where = province(fact.get("propinsi", ""))
        for place in (NATIONAL, where) if where else (NATIONAL,):
            if family:
                sums[(WORKFORCE_FAMILY[0], place, family)] += int(count)
            if profession:
                sums[(WORKFORCE[0], place, profession)] += int(count)
    if skipped:
        log.warning("kemkes_sdmk.counts_not_whole", family=family, rows=skipped)

    names = dict((WORKFORCE, WORKFORCE_FAMILY))
    # Each series whole, the country first in it.
    ordered = sorted(sums, key=lambda key: (key[0], key[1] != NATIONAL, key[1], key[2]))
    for row_number, (indicator, where, what) in enumerate(ordered, start=1):
        total = sums[(indicator, where, what)]
        yield _figure(row_number, indicator, names[indicator], cutoff, where, what, str(total))


def _within(family: str, profession: str) -> str:
    """A profession named with the family it is filed under.

    The dashboard files some professions under two families —
    "Subspesialis Gastrohepatologi" is both a specialist and a subspecialist,
    counting different doctors — and a category must name one group of
    people. A reader of one family's artifact cannot see the others, so every
    profession is qualified, not only the ones that happen to repeat today.
    """
    if not family or not profession or profession == family:
        return profession
    return f"{family} — {profession}"


def vacancy_figures(
    kind: str, cutoff: str | None, header: list[str], rows: list[list[str]]
) -> Iterator[dict[str, Any]]:
    """A vacancy table, one figure per province and profession."""
    if not cutoff:
        log.warning("kemkes_sdmk.vacancies_undated", page=kind)
        return
    indicator, name = VACANCY_SERIES[kind]
    row_number = 0
    for row in rows:
        where = province(row[0])
        if where is None:
            continue
        for label, cell in zip(header[1:], row[1:], strict=True):
            # The table's own total column is the series' total.
            what = "" if label.lower() == "total" else label
            row_number += 1
            yield _figure(row_number, indicator, name, cutoff, where, what, cell)


def demand_figures(
    profession: str, page: str, header: list[str], rows: list[list[str]]
) -> Iterator[dict[str, Any]]:
    """A projection page: need, existing and gap per province, and the country."""
    summary = _SUMMARY.search(_text(page))
    if summary is None:
        # The year is only in the summary. Without it the page cannot be dated.
        log.warning("kemkes_sdmk.projection_undated", profession=profession)
        return
    year = summary.group(1)
    measures = {
        index: DEMAND_SERIES[label.rsplit(" - ", 1)[-1].strip().lower()]
        for index, label in enumerate(header)
        if label.rsplit(" - ", 1)[-1].strip().lower() in DEMAND_SERIES
    }
    national = dict(zip(("kebutuhan", "eksisting", "gap"), summary.groups()[1:], strict=True))

    row_number = 0
    for key, value in national.items():
        indicator, name = DEMAND_SERIES[key]
        row_number += 1
        yield _figure(row_number, indicator, name, year, NATIONAL, profession, _ungrouped(value))
    for row in rows:
        where = province(row[0])
        if where is None or where == NATIONAL:
            continue
        for index, (indicator, name) in measures.items():
            row_number += 1
            yield _figure(row_number, indicator, name, year, where, profession, row[index])


def province(label: str) -> str | None:
    """A dashboard province as the geography registry should read it.

    The tables' `Total` row is the country.
    """
    name = " ".join(label.split())
    if not name:
        return None
    if name.lower() in {"total", "indonesia", "seluruh indonesia"}:
        return NATIONAL
    return f"Provinsi {PROVINCE_NAMES.get(name.upper(), name)}"


# ---- the page's tables ------------------------------------------------------


class Grid:
    """A table built cell by cell, with rowspan and colspan expanded."""

    def __init__(self) -> None:
        self.cells: list[list[str]] = []
        self.taken: set[tuple[int, int]] = set()
        self.row = -1

    def start_row(self) -> None:
        self.row += 1
        while len(self.cells) <= self.row:
            self.cells.append([])

    def add(self, text: str, colspan: int, rowspan: int) -> None:
        if self.row < 0:
            self.start_row()
        col = 0
        while (self.row, col) in self.taken:
            col += 1
        for dr in range(rowspan):
            for dc in range(colspan):
                r, c = self.row + dr, col + dc
                self.taken.add((r, c))
                while len(self.cells) <= r:
                    self.cells.append([])
                line = self.cells[r]
                while len(line) <= c:
                    line.append("")
                line[c] = text

    def rows(self) -> list[list[str]]:
        width = max((len(r) for r in self.cells), default=0)
        return [r + [""] * (width - len(r)) for r in self.cells if any(r)]


class _Tables(HTMLParser):
    """Every `<table>` on a page, by id, nested tables kept apart."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: dict[str, Grid] = {}
        self._stack: list[Grid] = []
        self._cell: list[str] | None = None
        self._span = (1, 1)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "table":
            grid = self.found.setdefault(a.get("id") or f"_{len(self.found)}", Grid())
            self._stack.append(grid)
        elif tag == "tr" and self._stack:
            self._stack[-1].start_row()
        elif tag in {"td", "th"} and self._stack:
            self._cell = []
            self._span = (_span(a.get("colspan")), _span(a.get("rowspan")))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None:
            if self._stack:
                self._stack[-1].add(" ".join("".join(self._cell).split()), *self._span)
            self._cell = None
        elif tag == "table" and self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def tables(page: str) -> dict[str, Grid]:
    parser = _Tables()
    parser.feed(page)
    return parser.found


def split_header(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """Leading header rows merged column-wise ("Perawat - Kebutuhan"), and the rest.

    A header row is one with no number past its first cell; a table whose
    first row has numbers still gives that row up as its header.
    """
    rows = list(rows)
    head = []
    while rows and not any(_number(c) for c in rows[0][1:]):
        head.append(rows.pop(0))
    if not head and rows:
        head.append(rows.pop(0))
    width = max((len(r) for r in head + rows), default=0)
    names = []
    for c in range(width):
        parts = [h[c] for h in head if c < len(h) and h[c]]
        names.append(" - ".join(dict.fromkeys(parts)) or f"col_{c + 1}")
    # Two columns printed under one name would lose one to the other in a
    # record keyed by header.
    seen: dict[str, int] = defaultdict(int)
    unique = []
    for name in names:
        seen[name] += 1
        unique.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return unique, [r + [""] * (width - len(r)) for r in rows]


# ---- helpers ------------------------------------------------------------------


def _register_row(row: object) -> dict[str, str]:
    if isinstance(row, dict):
        return {str(k): "" if v is None else str(v) for k, v in row.items()}
    if isinstance(row, list):
        columns = REGISTER_COLUMNS.get(len(row)) or tuple(f"col_{i + 1}" for i in range(len(row)))
        return {
            column: "" if value is None else str(value)
            for column, value in zip(columns, row, strict=True)
        }
    return {}


def _row_prefix(landed: Landed) -> dict[str, str]:
    """What the artifact says about its rows that the table does not."""
    keys = ("group", "family", "profession", "year", "sisdmk_cutoff")
    return {k: str(landed.extra[k]) for k in keys if landed.extra.get(k)}


def _figure(
    row_number: int,
    indicator: str,
    name: str,
    period: str,
    geo: str,
    category: str,
    value: str,
) -> dict[str, Any]:
    return {
        "dataset": FIGURES_DATASET,
        "row_number": row_number,
        "columns": {
            "indicator": indicator,
            "name": name,
            "period": period,
            "geo": geo,
            "category": category,
            "value": value,
            "unit": "persons",
            "publisher": PUBLISHER,
        },
    }


def _ungrouped(value: str) -> str:
    """A summary figure with its thousands dots taken out."""
    return value.replace(".", "") if _GROUPED.fullmatch(value) else value


def _number(cell: str) -> bool:
    return bool(re.fullmatch(r"-?[\d.,]+%?", cell.strip())) and any(ch.isdigit() for ch in cell)


def _span(value: str | None) -> int:
    try:
        return max(1, int(value or 1))
    except ValueError:
        return 1


def _text(page: str) -> str:
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I)
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", page)).split())


def _order(item: tuple[tuple[str, str], int]) -> tuple[bool, str, str]:
    (where, what), _ = item
    return (where != NATIONAL, where, what)
