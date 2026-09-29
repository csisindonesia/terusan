"""BPS's data tables into Bronze records.

A `list/model/data` response is a cube, and its cells are keyed by a string
nobody would guess from one example:

    "datacontent": {"53005430126189": 3.16, ...}

That is five ids concatenated with no separator — the region (`5300`), the
variable (`543`), the breakdown (`0`), the year (`126`) and the sub-period
(`189`, February). The ids are variable-length, so the key cannot be cut apart;
it is rebuilt instead, from the lists the same response carries — `vervar`,
`turvar`, `tahun` and `turtahun` — and looked up. A combination that is not in
the table is a cell BPS did not publish, and makes no record.

The generic JSON reader would see one object with a nested mapping of opaque
numbers and land that. Read here, a record carries the region, the breakdown,
the period and the value, each with BPS's own label beside it.

Two readings are made on the way, both reversible from the columns kept:

**The region code** is BPS's four-digit `vervar` — `1100` for Aceh, `1106` for
Aceh Tengah, `9999` for the country — rewritten as the registry spells it: `11`
for a province, `11.06` for a regency or city, `IDN` for Indonesia. The raw
value survives as `vervar`, and a table whose `vervar` is not a region code
gets an empty `region_code` (see `region_code`).

**The place to resolve on** is `geo`: the code where the code can be trusted,
and BPS's label where it cannot (see `geo_key`).

**The series** is the variable and its breakdown (`2805.2344`: speakers of
Indonesian by age group, the total). Where a table's rows are not places — age
groups, sectors, education levels — the row is `category`, a dimension of the
series rather than part of its name: a table of sixteen age groups is one
series across sixteen categories. Split into one series per row, as it once
was, most of them held a single figure, and a reader had no way to see the
table they came from.

That regrouping did not bump `PARSER_VERSION`. A bump re-reads the whole
corpus, and for the news that means coding every article again, the doubtful
ones by a paid model, to change nothing but BPS. BPS's Bronze is a partition of
its own (`source_id=bps-indicators`), so it was rebuilt alone: the partition
moved aside and the source extracted again.

**The period** is the year plus the sub-period label: a month name makes a
month (`2026-02`), `Semester 1 (Maret)` the month in its brackets — the survey
month, which is what the figure describes — `Triwulan I` a quarter, and `Tahun`
or `Tahunan` the year. The label survives as `period_label`, so an annual
average published beside monthly figures can be told apart and excluded.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterator
from functools import cache
from typing import Any

from ..identifiers import short_id
from .base import ExtractionError, Extractor, Landed

SOURCE_SLUG = "bps-indicators"

#: BPS's code for the country in a table of regions.
NATIONAL_VERVAR = "9999"

_ANNUAL = {"tahun", "tahunan"}

#: BPS's month labels. Its own spelling only: the API writes `Februari` and
#: `Agustus`, never an abbreviation.
_MONTHS = {
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
_BRACKETED = re.compile(r"\(([^)]+)\)")
_QUARTER = re.compile(r"^(?:triwulan|tw|kuartal)\s+(i{1,3}|iv|[1-4])$")
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}


#: What `labelvervar` says when a table's rows are places: `38 Provinsi`,
#: `Kota Inflasi`, `150 Kabupaten/Kota Inflasi (2022=100)`. `Wilayah` is not
#: among them — BPS uses it for the split between town and village.
_REGIONAL = re.compile(r"provinsi|kabupaten|kota inflasi", re.IGNORECASE)

#: BPS's way of saying a figure has no unit.
_NO_UNIT = {"", "tidak ada satuan", "tidak ada"}

#: A breakdown that is not one: BPS's label for a table with no `turvar`.
_NO_BREAKDOWN = {"", "tidak ada"}

_TAG = re.compile(r"<[^>]+>")


def clean(value: Any) -> str:
    """A BPS label as text: tags out, entities read, whitespace collapsed.

    Sector tables bold their headings — `<b>A. Pertanian</b>` — and indent
    their children with spaces.
    """
    text = html.unescape(_TAG.sub("", str(value or "")))
    return " ".join(text.split())


def is_regional(labelvervar: Any) -> bool:
    return bool(_REGIONAL.search(str(labelvervar or "")))


def series_key(var_id: str, turvar: str) -> str:
    """BPS's own coordinates for one series: the variable and its breakdown."""
    return f"{var_id}.{turvar}"


def indicator_id(series: str) -> str:
    return short_id(SOURCE_SLUG, series)


def series_name(title: str, breakdown: str) -> str:
    parts = [title]
    if breakdown.lower() not in _NO_BREAKDOWN:
        parts.append(breakdown)
    return " — ".join(parts)


def categories_of(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Each row's category, by `vervar`, unique within the table.

    BPS flattens a hierarchy into one list, so a label can appear twice:
    `Mobil Penumpang` is imported both as a consumer good and as capital
    goods, each under its own heading. Once the row stopped being part of
    the series' identity, the two collided on one observation. A repeated
    label is qualified by the heading above it — the nearest row BPS bolds or
    writes in capitals — and by BPS's row id where even that repeats.
    """
    labels = [(str(row.get("val")), clean(row.get("label"))) for row in rows]
    seen: dict[str, int] = {}
    for _, label in labels:
        seen[label] = seen.get(label, 0) + 1

    named: dict[str, str] = {}
    heading = ""
    for row, (vervar, label) in zip(rows, labels, strict=True):
        raw = str(row.get("label") or "")
        if "<b>" in raw.lower() or (label.isupper() and any(c.isalpha() for c in label)):
            heading = label
        name = label
        if seen[label] > 1 and heading and heading != label:
            name = f"{label} — {heading}"
        named[vervar] = name

    counts: dict[str, int] = {}
    for name in named.values():
        counts[name] = counts.get(name, 0) + 1
    return {
        vervar: (f"{name} [{vervar}]" if counts[name] > 1 else name)
        for vervar, name in named.items()
    }


def region_code(vervar: str) -> str:
    """BPS's four-digit region id, as the geography registry keys it.

    Empty for anything else. Not every table keys its rows by region code: the
    150-city inflation table (var 2249) numbers its cities 1 to 151, so `1` is
    Aceh Tengah there and not a province. A code that is not a region code is
    left out rather than rewritten into one, and those tables resolve on the
    `region` label instead.
    """
    if vervar == NATIONAL_VERVAR:
        return "IDN"
    if len(vervar) == 4 and vervar.isdigit() and vervar[:2] != "00":
        province, rest = vervar[:2], vervar[2:]
        return province if rest == "00" else f"{province}.{rest}"
    return ""


_PREFIX = re.compile(r"^(kabupaten|kab\.?)\s+")


def _same_place(a: str, b: str) -> bool:
    """Whether two spellings name one regency: case, spacing and a leading
    `Kab`/`Kabupaten` aside. `Kota` is kept — Kota Bandung is not Bandung."""

    def norm(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", _PREFIX.sub("", text.strip().lower()))

    return norm(a) == norm(b)


@cache
def _registry_names() -> dict[str, tuple[str, ...]]:
    """Every name the regency registry gives each code it answers to.

    Imported here rather than at the top: the registry lives in `normalize`,
    which imports extraction, and a module-level import would be circular.
    """
    from ..normalize.reference import load_regencies

    names: dict[str, list[str]] = {}
    for regency in load_regencies():
        spellings = [regency.name, *regency.aliases]
        for code in (regency.bps_code, *regency.aliases):
            if code and re.fullmatch(r"\d{2}\.?\d{2}", code):
                dotted = code if "." in code else f"{code[:2]}.{code[2:]}"
                names.setdefault(dotted, []).extend(spellings)
    return {code: tuple(found) for code, found in names.items()}


def geo_key(vervar: str, label: str) -> str:
    """What to resolve a row's place on.

    A province's code, always: the province registry is keyed by BPS's own
    codes, checked against this API. A regency's code only when the registry
    calls that code by the name BPS gives the row. The regency registry keys
    several provinces by Kemendagri's numbering rather than BPS's — BPS's 64.03
    is Kutai Kartanegara, the registry's is Berau; BPS's 95.01 is Merauke, the
    registry's Jayawijaya — and a code taken on trust files one regency's
    figures under another, silently and plausibly. Where the names disagree,
    or the code is not in the registry, the row resolves on BPS's name.

    A table that does not code its rows at all resolves on the name too.
    """
    code = region_code(vervar)
    if not code:
        return label
    if "." not in code:
        return code
    known = _registry_names().get(code, ())
    if any(_same_place(label, name) for name in known):
        return code
    return label


def period_of(year: str, label: str) -> str:
    """A period Silver's parser reads, from BPS's year and sub-period label."""
    text = label.strip().lower()
    if not text or text in _ANNUAL:
        return year
    bracketed = _BRACKETED.search(text)
    if bracketed:
        text = bracketed.group(1).strip()
    if text in _MONTHS:
        return f"{year}-{_MONTHS[text]:02d}"
    quarter = _QUARTER.match(text)
    if quarter:
        q = quarter.group(1)
        return f"{year}Q{_ROMAN.get(q) or int(q)}"
    # Unrecognised: handed on as written, so it fails visibly in Silver as an
    # unparseable period rather than being filed under the wrong one here.
    return f"{label.strip()} {year}"


def number(value: Any) -> str:
    """A cell as text, never in exponent form."""
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        text = repr(value)
        return f"{value:f}".rstrip("0").rstrip(".") if "e" in text else text
    return str(value)


class BpsDataExtractor(Extractor):
    """One Bronze record per row, breakdown and period, naming its series."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() == ".json"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        try:
            body = json.loads(landed.path.read_bytes())
        except json.JSONDecodeError as exc:
            raise ExtractionError(str(landed.path), f"invalid JSON: {exc}") from exc

        cells = body.get("datacontent")
        variables = body.get("var") or []
        if not isinstance(cells, dict) or not variables:
            raise ExtractionError(str(landed.path), "not a BPS data table: no datacontent or var")

        variable = variables[0]
        var_id = str(variable.get("val"))
        title = clean(variable.get("label"))
        unit = (
            "" if clean(variable.get("unit")).lower() in _NO_UNIT else clean(variable.get("unit"))
        )

        rows_are_places = is_regional(body.get("labelvervar"))
        rows = body.get("vervar") or []
        breakdowns = body.get("turvar") or [{"val": 0, "label": ""}]
        years = body.get("tahun") or []
        periods = body.get("turtahun") or [{"val": 0, "label": ""}]
        # An annual figure beside sub-annual ones is a summary of them, and a
        # series changing frequency halfway charts as nonsense; marked so the
        # normalization can leave it out, kept so nothing is lost.
        sub_annual = any(clean(p.get("label")).lower() not in _ANNUAL for p in periods)

        categories = {} if rows_are_places else categories_of(rows)

        number_ = 0
        for row in rows:
            vervar = str(row.get("val"))
            row_label = clean(row.get("label"))
            if rows_are_places:
                geo, category = geo_key(vervar, row_label), ""
            else:
                # A national table broken down by something other than place:
                # age group, sector, urban and rural. The row is the series'
                # category, and the place is the country.
                geo, category = "IDN", categories[vervar]
            for breakdown in breakdowns:
                turvar = str(breakdown.get("val"))
                breakdown_label = clean(breakdown.get("label"))
                series = series_key(var_id, turvar)
                name = series_name(title, breakdown_label)
                for year in years:
                    th = str(year.get("val"))
                    year_label = clean(year.get("label"))
                    for period in periods:
                        turth = str(period.get("val"))
                        key = f"{vervar}{var_id}{turvar}{th}{turth}"
                        if key not in cells:
                            continue
                        number_ += 1
                        period_label = clean(period.get("label"))
                        summary = sub_annual and period_label.lower() in _ANNUAL
                        yield {
                            "dataset": landed.dataset or SOURCE_SLUG,
                            "row_number": number_,
                            "columns": {
                                "indicator": indicator_id(series),
                                "series_code": series,
                                "series_name": name,
                                "var_id": var_id,
                                "variable": title,
                                "subject": clean((body.get("subject") or [{}])[0].get("label")),
                                "unit": unit,
                                "vervar": vervar,
                                "vervar_kind": clean(body.get("labelvervar")),
                                "region_code": region_code(vervar) if rows_are_places else "",
                                "region": row_label if rows_are_places else "",
                                "category": category,
                                "geo": geo,
                                "turvar": turvar,
                                "breakdown": breakdown_label,
                                "year": year_label,
                                "period_label": period_label,
                                "period_kind": "summary" if summary else "",
                                "period": period_of(year_label, period_label),
                                "value": number(cells[key]),
                            },
                        }
