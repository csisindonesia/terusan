"""VEWS's yearly exports into Bronze: the incidents, and the year's figures.

VEWS publishes events, and this warehouse holds figures. An observation is one
indicator in one period in one place (program.md §10), and a collective
violence dataset is nothing of the sort: it is six thousand incidents, each
with a date, a district, two sides and a casualty count. Two brawls in the same
regency on the same day are two facts, and there is no indicator, period and
place that tells them apart.

So this reader produces both, into two Bronze collections:

- **`collective-violence-incidents`** — one row per coded incident, every
  column VEWS wrote, as text. The dataset as it actually is. Nothing normalizes
  it into Silver, because there is no series in it; it is here so the detail is
  queryable at all rather than sitting in a workbook.
- **`collective-violence-early-warning`** — the year's incidents counted, per
  province and for the country, which is the part Silver can hold. Ten series:
  how many incidents, how many people killed and hurt, how many of them women
  and children, how much was damaged and destroyed, and how often someone
  stepped in.

Counting in an extractor is more than Bronze usually does, and it is where the
counting has to happen: Silver's normalizer maps a row to an observation and
does not aggregate, so a count made anywhere else would be a count made
nowhere. RAW keeps the exports, so a different set of series can be read off
the same bytes later.

Four things decide what gets counted.

**The file's own year, and only that.** A yearly export carries a tail of
incidents dated to neighbouring years — the 2021 export reaches back to 2017
and forward into 2022, the 2025 one into 2026 — because a coder files an
incident when they read about it. Counted, those tails would have the 2025
export's six weeks of 2024 overwrite the 2024 export's whole year, and publish
a 90% fall in violence that is really a file boundary. The year comes from the
filename, landed as the partition; everything outside it stays in the incidents
collection and out of the figures.

**Blank is zero, `-99` is unknown.** VEWS leaves the casualty cell empty when
nobody was hurt and writes `-99` when the reporting did not say. Summing them
the same way would either invent casualties or lose the difference between a
quiet province and an uncoded one, so an empty cell contributes zero to the
sum and `-99` contributes nothing at all.

**A province is named, not numbered.** VEWS carries a `province_id` beside the
name, and it is a spreadsheet formula over the incident id — `=LEFT(E2,2)` —
so a mistyped id silently renumbers the province. Across the four exports some
two dozen rows have a code that contradicts a province name that is plainly
right: `JAWA BARAT` coded `31`, `MALUKU UTARA` coded `81`. The name is what a
coder typed and a verifier checked, so the name is what is used, qualified with
`Provinsi` — which is how the geography registry tells the province of
Gorontalo from the regency inside it. Three misspellings are corrected by name
below; nothing else is guessed at.

**Indonesia is counted from every incident**, including the handful whose
province does not resolve, so the national figure is the whole year and not the
sum of the provinces. A reader adding the provinces to Indonesia would count
the year twice; they are two levels of the same series, as they are for Bank
Indonesia's food prices.

A province with no incident in a year gets no row. VEWS covers the whole
country, so that is almost always a year with no coded violence — but it is
also what a province dropped from an export looks like, and the two are not
distinguishable from here.

`PARSER_VERSION` is not bumped. A bump re-reads the whole corpus, and it earns
that where a reader replaces one that already produced rows; nothing has ever
been extracted from this source.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from .base import ExtractionError, Extractor, Landed
from .tabular import decode

SOURCE_SLUG = "vews-collective-violence"

CSV_SUFFIXES = {".csv"}
WORKBOOK_SUFFIXES = {".xlsx", ".xlsm"}

#: One row per coded incident, as VEWS wrote it.
INCIDENTS_DATASET = "collective-violence-incidents"

#: The year's incidents counted into series.
FIGURES_DATASET = "collective-violence-early-warning"

PUBLISHER = "VEWS"

#: The column that says a row is an incident rather than a blank line. The
#: exports carry hundreds of empty rows below the data — a Google Form's sheet
#: extended past its responses — and a few analysis scratch rows beside them.
KEY_COLUMN = "incident_id"

#: Without these a row cannot be counted at all, so an export missing one is an
#: error rather than a year that quietly reads as zero.
REQUIRED_COLUMNS = (KEY_COLUMN, "year", "province")

#: VEWS's own code for "the reporting did not say". Distinct from an empty
#: cell, which is the coder saying nobody was hurt and nothing was damaged.
UNKNOWN = -99

#: What `intervene` says when a third party stepped in.
INTERVENED = "IYA"

INCIDENTS = "incidents"
PEOPLE = "people"
DEATHS = "deaths"
STRUCTURES = "structures"

#: The country, as the geography registry names it.
COUNTRY = "Indonesia"

#: Provinces VEWS writes more than one way, reduced to one spelling each.
#:
#: This has to happen here rather than at resolution, and the difference
#: matters. The geography registry would resolve `Sumatra Utara` and
#: `Sumatera Utara` to the same province quite happily — but by then the
#: counting is done, and an export that spells North Sumatra both ways would
#: have produced two tallies, which arrive in Silver as two different figures
#: claiming to be the same observation. Silver refuses that outright
#: (`CollapsedDimension`), so a variant nobody has seen before fails the run
#: rather than publishing half a province's violence. Which is the right
#: failure: adding a line here is the fix.
#:
#: Two kinds are collected. Misspellings — one row each, in one export —
#: where what was meant is not in doubt. And the same province under two real
#: names: Jakarta and Yogyakarta each have a formal name and a short one, and
#: DKI Jakarta was formally renamed Daerah Khusus Jakarta in 2024, so the
#: 2025 export uses both. The registry's own spelling wins, so the figures
#: read the way the rest of the warehouse does.
SPELLINGS = {
    # Misspellings.
    "KALIMANTAN BERAT": "KALIMANTAN BARAT",
    "KALIMANTANG TENGAH": "KALIMANTAN TENGAH",
    "PAPUA PENGUNUNGAN": "PAPUA PEGUNUNGAN",
    # Sumatra and Sumatera, both used, sometimes in the same export.
    "SUMATRA UTARA": "SUMATERA UTARA",
    "SUMATRA BARAT": "SUMATERA BARAT",
    "SUMATRA SELATAN": "SUMATERA SELATAN",
    # Jakarta: the old formal name, the new one (UU 2/2024), and the bare one.
    "DAERAH KHUSUS JAKARTA": "DKI JAKARTA",
    "DAERAH KHUSUS IBUKOTA JAKARTA": "DKI JAKARTA",
    "JAKARTA": "DKI JAKARTA",
    # Yogyakarta: the formal name and the abbreviation.
    "DAERAH ISTIMEWA YOGYAKARTA": "DI YOGYAKARTA",
    "YOGYAKARTA": "DI YOGYAKARTA",
}

#: Leading qualifiers VEWS sometimes types and this reader always adds back.
_QUALIFIER = re.compile(r"^(PROVINSI|PROV\.?)\s+")

_WHITESPACE = re.compile(r"\s+")

#: A number as a spreadsheet hands it over: `1`, `1.0`, `-99.0`.
_INTEGER = re.compile(r"^-?\d+(?:\.0+)?$")


@dataclass(frozen=True, slots=True)
class Measure:
    """One series read off the year's incidents."""

    #: The Silver indicator these figures belong to. Readable rather than a
    #: code, like the UCDP and PIHPS series: there are ten, and the identifier
    #: is what a reader sees in a URL.
    indicator: str

    #: What a reader sees beside the identifier.
    name: str

    unit: str

    #: The column summed, or None where the measure counts incidents rather
    #: than adding up what is in them.
    column: str | None = None

    #: The column the count is decided by, for a measure that counts a subset
    #: of the incidents. Carried as the publisher's own code, which is what a
    #: reader takes back to the codebook.
    code: str = KEY_COLUMN

    @property
    def measure(self) -> str:
        return self.column or self.code


#: What is counted. The actors, the form the violence took, the weapon and the
#: issue behind it are left: each is a category rather than a quantity, and
#: Silver holds no dimension they could vary along — an observation varies by
#: period, place and commodity, and "an attack without firearms, over land" is
#: none of those. They are in the incidents collection, where a query can group
#: by them.
MEASURES: tuple[Measure, ...] = (
    Measure(
        indicator="vews_incidents",
        name="Collective violence incidents",
        unit=INCIDENTS,
    ),
    Measure(
        indicator="vews_deaths",
        name="Deaths in collective violence",
        unit=DEATHS,
        column="num_death",
    ),
    Measure(
        indicator="vews_injured",
        name="People injured in collective violence",
        unit=PEOPLE,
        column="num_injured",
    ),
    # Women and children are counted separately by VEWS rather than derived,
    # and they are a subset of the totals above rather than an addition to
    # them.
    Measure(
        indicator="vews_female_deaths",
        name="Women and girls killed in collective violence",
        unit=DEATHS,
        column="fem_death",
    ),
    Measure(
        indicator="vews_female_injured",
        name="Women and girls injured in collective violence",
        unit=PEOPLE,
        column="fem_injured",
    ),
    Measure(
        indicator="vews_child_deaths",
        name="Children killed in collective violence",
        unit=DEATHS,
        column="child_death",
    ),
    Measure(
        indicator="vews_child_injured",
        name="Children injured in collective violence",
        unit=PEOPLE,
        column="child_injured",
    ),
    Measure(
        indicator="vews_infrastructure_damaged",
        name="Buildings and infrastructure damaged",
        unit=STRUCTURES,
        column="infra_damage",
    ),
    Measure(
        indicator="vews_infrastructure_destroyed",
        name="Buildings and infrastructure destroyed",
        unit=STRUCTURES,
        column="infra_destroyed",
    ),
    # How often anyone stepped in — police, army, village authority, anyone —
    # which is the half of an early warning dataset that is about the response
    # rather than the violence.
    Measure(
        indicator="vews_incidents_with_intervention",
        name="Collective violence incidents a third party intervened in",
        unit=INCIDENTS,
        code="intervene",
    ),
)


def clean_province(raw: str) -> str:
    """A province as VEWS wrote it, reduced to the name itself.

    Non-breaking spaces (`DKI\xa0JAKARTA`), doubled spaces and a `Provinsi`
    the coder typed are all noise between the same two names.
    """
    text = unicodedata.normalize("NFKD", raw or "")
    text = _WHITESPACE.sub(" ", text).strip().upper()
    text = _QUALIFIER.sub("", text)
    return SPELLINGS.get(text, text)


def geo_label(province: str) -> str:
    """The place a row is filed under, as the geography registry reads it.

    Qualified rather than bare: `Gorontalo` names both a province and a
    regency inside it, and the registry refuses a name two areas answer to.
    Every province is qualified rather than that one, so the rows stay
    uniform — `Provinsi` is stripped as noise for the 37 that were never
    ambiguous.
    """
    return f"Provinsi {province}"


def count(value: str) -> int | None:
    """A casualty or damage cell as a number, or None where it says nothing.

    An empty cell is zero — VEWS leaves it blank when nobody was hurt — so it
    is the caller that decides, not this. `-99` is VEWS's "not reported", and
    anything that is not a whole number is a coder's note in a numeric column.
    """
    text = (value or "").strip()
    if not text or not _INTEGER.match(text):
        return None
    number = int(float(text))
    return None if number <= UNKNOWN else number


@dataclass(slots=True)
class Tally:
    """One place's year: the incidents, and what they added up to."""

    incidents: int = 0
    intervened: int = 0
    sums: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, row: dict[str, str]) -> None:
        self.incidents += 1
        if (row.get("intervene") or "").strip().upper() == INTERVENED:
            self.intervened += 1
        for measure in MEASURES:
            if measure.column is None:
                continue
            # `+= 0` rather than `setdefault`: a province that had incidents
            # and no deaths publishes a zero, which is a finding. A province
            # with no incidents at all never reaches here and publishes
            # nothing.
            self.sums[measure.column] += count(row.get(measure.column, "")) or 0

    def value(self, measure: Measure) -> int:
        if measure.indicator == "vews_incidents":
            return self.incidents
        if measure.indicator == "vews_incidents_with_intervention":
            return self.intervened
        return self.sums[measure.column or ""]


def read_delimited(content: bytes) -> list[dict[str, str]]:
    """A VEWS CSV export. Semicolon-separated, but not promised to stay so."""
    text = decode(content)
    sample = text[:4096]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [_row(row.keys(), row.values()) for row in reader]


def read_workbook(content: bytes, path: str) -> list[dict[str, str]]:
    """The incident sheet of a VEWS workbook.

    The sheet is found rather than named: it is called `OVERALL` in one export,
    `Form responses 1` in the next and `(Authentic) VEWS Yearly Dataset` in the
    third, and each export carries a dozen sheets of the analysis VEWS did on
    it beside the data. The one with an `incident_id` column is the data.

    Read for values rather than formulae — several columns are `=SUM(...)` or
    `=YEAR(...)` over their neighbours, and a formula string is not a figure.
    """
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise ExtractionError(path, "openpyxl is not installed; add the `agencies` extra") from exc

    workbook = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    try:
        for sheet in workbook.worksheets:
            rows = sheet.iter_rows(values_only=True)
            header = next(rows, None)
            if header is None:
                continue
            names = [_text(cell) for cell in header]
            if KEY_COLUMN not in {name.lower() for name in names}:
                continue
            return [_row(names, values) for values in rows]
    finally:
        workbook.close()

    raise ExtractionError(path, f"no sheet in the workbook has an {KEY_COLUMN!r} column")


def _text(cell: Any) -> str:
    return "" if cell is None else str(cell).strip()


def _row(names: Sequence[Any], values: Sequence[Any]) -> dict[str, str]:
    """One row, keyed by a lower-cased header.

    Lower-cased because the exports disagree with each other about the case of
    their own headers — `No` in one year, `no` in the next — and a reader that
    cared would have to know which year it was reading.
    """
    row: dict[str, str] = {}
    for name, value in zip(names, values, strict=False):
        key = _text(name).lower()
        if key:
            row[key] = _text(value)
    return row


class VewsCollectiveViolenceExtractor(Extractor):
    """One export into its incidents, and into the year they add up to."""

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() in (
            CSV_SUFFIXES | WORKBOOK_SUFFIXES
        )

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        path = str(landed.path)
        content = landed.path.read_bytes()

        if landed.path.suffix.lower() in CSV_SUFFIXES:
            rows = read_delimited(content)
        else:
            rows = read_workbook(content, path)

        incidents = [row for row in rows if (row.get(KEY_COLUMN) or "").strip()]
        self._check_columns(path, rows, incidents)

        number = 0
        for row in incidents:
            number += 1
            yield {
                "dataset": INCIDENTS_DATASET,
                "row_number": number,
                "columns": row,
            }

        year = self._edition(landed, path)
        for record in self._figures(incidents, year, path):
            number += 1
            yield {
                "dataset": FIGURES_DATASET,
                "row_number": number,
                "columns": record,
            }

    def _check_columns(
        self, path: str, rows: list[dict[str, str]], incidents: list[dict[str, str]]
    ) -> None:
        """Refuse an export that has lost a column the figures are read from.

        A renamed column would otherwise sum to zero across every province,
        and a series that goes to zero looks from the portal exactly like a
        year in which the violence stopped.
        """
        if not rows:
            raise ExtractionError(path, "export holds no rows")
        if not incidents:
            raise ExtractionError(path, f"no row carries an {KEY_COLUMN!r}; the export is empty")

        present = set(incidents[0])
        expected = [*REQUIRED_COLUMNS, *(m.column for m in MEASURES if m.column), "intervene"]
        missing = [column for column in expected if column not in present]
        if missing:
            raise ExtractionError(
                path,
                f"export is missing {len(missing)} expected columns "
                f"({', '.join(missing)}); VEWS has reshaped the dataset and "
                "the measures must be re-read",
            )

    def _edition(self, landed: Landed, path: str) -> str:
        """The year the export is authoritative for.

        From the landing, not from the rows: the rows carry several years and
        the file covers one, which is the whole reason the source reads it off
        the filename.
        """
        year = str((landed.extra or {}).get("edition") or "")
        if not year:
            for segment in landed.partition:
                if segment.startswith("year="):
                    year = segment.removeprefix("year=")
        if not year:
            raise ExtractionError(
                path,
                "the landing does not say which year this export covers, so its "
                "incidents cannot be told from the tail it carries of other years",
            )
        return year

    def _figures(
        self, incidents: list[dict[str, str]], year: str, path: str
    ) -> Iterator[dict[str, str]]:
        """The year's incidents, counted per province and for the country."""
        provinces: dict[str, Tally] = defaultdict(Tally)
        national = Tally()

        for row in incidents:
            if (row.get("year") or "").strip().split(".")[0] != year:
                continue
            national.add(row)
            province = clean_province(row.get("province", ""))
            if province:
                provinces[province].add(row)

        if national.incidents == 0:
            # Not a quiet year: the export named for this year holds nothing
            # dated to it, which means the filename and the rows disagree.
            raise ExtractionError(
                path,
                f"the export covers {year} and holds no incident dated to it",
            )

        for measure in MEASURES:
            yield self._record(measure, COUNTRY, "country", national, year)
            for province, tally in sorted(provinces.items()):
                yield self._record(
                    measure, geo_label(province), "province", tally, year, published=province
                )

    @staticmethod
    def _record(
        measure: Measure,
        geo: str,
        level: str,
        tally: Tally,
        year: str,
        published: str = "",
    ) -> dict[str, str]:
        return {
            "indicator": measure.indicator,
            "series_name": measure.name,
            "geo": geo,
            "geo_level": level,
            # VEWS's own spelling, kept beside the name the registry resolves:
            # it is what a reader finds if they go back to the export.
            "province": published,
            "year": year,
            "value": str(tally.value(measure)),
            "unit": measure.unit,
            "measure": measure.measure,
            "incidents": str(tally.incidents),
            "edition": year,
            "publisher": PUBLISHER,
        }
