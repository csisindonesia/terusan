"""OJK's SJK grid exports into Bronze: the fact rows, and the month's figures.

The generic JSON reader cannot read these. The portal answers
`{"data": [...], "totalCount": n}`, which a generic reader sees as one object
and turns into a single row holding a month of sixty thousand facts as a
string.

And the facts are not observations. A row of the credit cube is one bank
group's lending in one province, in one currency, for one purpose, to one
sector, at one quality grade — fifteen dimensions deep. An observation is one
indicator in one period in one place (program.md §10), and Silver's normalizer
maps a row to an observation without adding anything up. So this reader
produces both, as VEWS's does:

- **The cube's own dataset** (`ojk-commercial-bank-credit`, ...) — one record
  per fact row, every column OJK wrote, as text. The labels keep OJK's
  numbering (`"2. BPD"`, `"001. Rupiah"`). Nothing normalizes it; it is here
  so the detail is queryable.
- **`ojk-banking-figures`** — each month summed into the series the SPI
  printed: deposits by type and by currency, credit by purpose, by
  orientation and by business size, and non-performing credit, per province
  and for the country. `scripts/normalize-ojk.sh` takes these into Silver.

Summing is only possible over a whole month, and a month is only whole in an
artifact that holds all of it. The source lands a month per artifact; the first
run landed the credit cube in pages of ten thousand, and those pages are
skipped here entirely — their rows are in the whole-month artifact landed for
the same month, and reading both would state every fact twice.

Four decisions are made in the summing.

**The country is summed from every row**, including rows whose province is
blank or `Lainnya` (a bank's lending it did not place in a province). The
provinces therefore do not add up to Indonesia, by exactly that amount, which is
how the SPI printed it too.

**Commercial banks are reported on 34 provinces.** Their cubes have no Papua
Tengah, Papua Selatan, Papua Pegunungan or Papua Barat Daya, so their `Papua`
and `Papua Barat` are the provinces before the 2022 split, holding the new ones
inside them. Resolved to the Papua of today they would be larger than it by
three provinces with nothing to say so, so they are labelled as the old
territory and left unresolved (program.md §11). Rural banks report on 38.

**A province is named, not numbered, and qualified with `Provinsi`**, which is
how the geography registry tells the province of Gorontalo from the regency
inside it.

**Sums are decimal.** Figures are read as the text the portal sent, and the
national deposit total is within a few percent of 2^53 — close enough that a
float sum would not be trusted to the Rupiah.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from .base import ExtractionError, Extractor, Landed

log = structlog.get_logger(__name__)

SOURCE_SLUG = "ojk-banking-sjk"

#: Where the summed series land in Bronze.
FIGURES_DATASET = "ojk-banking-figures"

PUBLISHER = "Otoritas Jasa Keuangan"

#: The country, as the geography registry names it.
NATIONAL = "Indonesia"

#: Rows a cube files under no province. Counted into the country, not shown as
#: a place.
UNPLACED = frozenset({"", "lainnya"})

#: Commercial banks' cubes report the provinces before Papua was divided in
#: 2022. Labelled so they do not resolve to the smaller provinces of today.
PRE_2022_PAPUA = {
    "papua": "Papua (wilayah sebelum pemekaran 2022)",
    "papua barat": "Papua Barat (wilayah sebelum pemekaran 2022)",
}

#: The cubes reported on 34 provinces.
THIRTY_FOUR_PROVINCE_CUBES = frozenset({"BU_DanaPihakKetiga", "BU_KreditdanPembiayaanEntitas"})

NPL_GRADES = ("003.", "004.", "005.")  # Kurang Lancar, Diragukan, Macet
MSME_SIZES = ("001.", "002.", "003.")  # Kecil, Menengah, Mikro


@dataclass(frozen=True, slots=True)
class Series:
    """One series read off a cube: what is summed, and what it is broken by."""

    indicator: str
    name: str
    table: str
    value_column: str
    #: The column whose values become the series' categories, beside a total
    #: with no category. None for a series that is only the total.
    category_column: str | None = None
    #: Which rows count at all, for a series that is a subset of the cube.
    keep: Callable[[dict[str, str]], bool] | None = None


SERIES: tuple[Series, ...] = (
    Series(
        "ojk_bu_dpk",
        "Commercial bank third-party funds by type",
        "BU_DanaPihakKetiga",
        "Nilai",
        "JenisDPK",
    ),
    Series(
        "ojk_bu_dpk_currency",
        "Commercial bank third-party funds by currency",
        "BU_DanaPihakKetiga",
        "Nilai",
        "MataUang",
    ),
    Series(
        "ojk_bu_credit",
        "Commercial bank credit by purpose",
        "BU_KreditdanPembiayaanEntitas",
        "Nilai",
        "JenisPenggunaan",
    ),
    Series(
        "ojk_bu_credit_orientation",
        "Commercial bank credit by orientation",
        "BU_KreditdanPembiayaanEntitas",
        "Nilai",
        "OrientasiPenggunaan",
    ),
    Series(
        "ojk_bu_npl",
        "Commercial bank non-performing credit by grade",
        "BU_KreditdanPembiayaanEntitas",
        "Nilai",
        "KualitasKredit",
        keep=lambda row: row.get("KualitasKredit", "").startswith(NPL_GRADES),
    ),
    Series(
        "ojk_bu_credit_msme",
        "Commercial bank credit to micro, small and medium enterprises",
        "BU_KreditdanPembiayaanEntitas",
        "Nilai",
        "KategoriUsaha",
        keep=lambda row: row.get("KategoriUsaha", "").startswith(MSME_SIZES),
    ),
    Series(
        "ojk_bpr_dpk",
        "Rural bank third-party funds by type",
        "BPR_DanaPihakKetiga",
        "TotalDana",
        "DPK",
    ),
)


class OjkSjkExtractor(Extractor):
    """Fact rows, and the month's figures, from one month of an SJK cube."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() == ".json"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        try:
            body = json.loads(landed.path.read_bytes(), parse_float=str, parse_int=str)
        except json.JSONDecodeError as exc:
            raise ExtractionError(str(landed.path), f"invalid JSON: {exc}") from exc

        rows = body.get("data") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            raise ExtractionError(str(landed.path), "no data list in the grid export")

        if not _whole_month(landed, len(rows)):
            log.info("ojk_sjk.partial_page_skipped", path=str(landed.path))
            return

        table = str(landed.extra.get("table") or "")
        month = str(landed.extra.get("month") or "")
        facts = [
            {str(key): "" if value is None else str(value) for key, value in row.items()}
            for row in rows
            if isinstance(row, dict)
        ]

        for index, columns in enumerate(facts, start=1):
            yield {
                "dataset": landed.dataset or SOURCE_SLUG,
                "row_number": index,
                # Which cube the row came from. The dataset says it too, but
                # the cube name is what OJK's portal and its documentation use.
                "columns": {"table": table} | columns,
            }

        yield from figures(table, month, facts)


def figures(table: str, month: str, facts: list[dict[str, str]]) -> Iterator[dict[str, Any]]:
    """The month's series, summed from one cube's fact rows."""
    row_number = 0
    for series in SERIES:
        if series.table != table:
            continue
        sums: dict[tuple[str, str | None], Decimal] = defaultdict(Decimal)
        for row in facts:
            if series.keep is not None and not series.keep(row):
                continue
            value = _decimal(row.get(series.value_column, ""))
            if value is None:
                continue
            place = province(table, row.get("DATI1", ""))
            category = (row.get(series.category_column or "", "") or "").strip() or None
            breakdowns = [None, category] if series.category_column and category else [None]
            places = [NATIONAL, place] if place else [NATIONAL]
            for where in places:
                for what in breakdowns:
                    sums[(where, what)] += value

        for (where, what), total in sorted(sums.items(), key=lambda item: _order(item[0])):
            row_number += 1
            yield {
                "dataset": FIGURES_DATASET,
                "row_number": row_number,
                "columns": {
                    "indicator": series.indicator,
                    "name": series.name,
                    "table": table,
                    "period": month,
                    "geo": where,
                    "category": what or "",
                    "value": _plain(total),
                    "unit": "IDR",
                    "publisher": PUBLISHER,
                },
            }


def province(table: str, label: str) -> str | None:
    """A cube's province as the geography registry should read it."""
    name = " ".join(label.split())
    if name.lower() in UNPLACED:
        return None
    if table in THIRTY_FOUR_PROVINCE_CUBES and name.lower() in PRE_2022_PAPUA:
        return PRE_2022_PAPUA[name.lower()]
    if name.lower().startswith("provinsi "):
        return name
    return f"Provinsi {name}"


def _whole_month(landed: Landed, rows: int) -> bool:
    """Whether this artifact holds its month entire, rather than one page of it."""
    try:
        offset = int(landed.extra.get("row_offset") or 0)
        expected = int(landed.extra.get("rows_in_month") or rows)
    except (TypeError, ValueError):
        return True
    return offset == 0 and rows == expected


def _decimal(text: str) -> Decimal | None:
    if not text.strip():
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _plain(value: Decimal) -> str:
    """A sum as plain digits: no exponent, no trailing zeros after the point."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _order(key: tuple[str, str | None]) -> tuple[bool, str, str]:
    where, what = key
    return (where != NATIONAL, where, what or "")
