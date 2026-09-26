"""UCDP's organized violence archive into Bronze records, for Indonesia.

The archive holds one CSV: a row per country and year, seventy-odd columns
wide, where a column is a measure — deaths in state-based conflict, their low
and high bounds, deaths in non-state conflict, civilians killed — and the
column name is the only place that measure is stated. The generic readers
cannot serve it. The zipped-workbook reader claims every `.zip` and finds no
workbook inside this one; the generic CSV reader would put every country UCDP
reports on into an Indonesian warehouse's Bronze, as one row of seventy values
whose meanings nothing downstream could recover.

So this reader knows the shape. It keeps Indonesia — country 850, which is the
country the URL https://ucdp.uu.se/country/850 names — and turns one wide row
into one record per measure, each naming the series it belongs to. The rest of
the world stays in RAW, where it is if that choice is ever revisited
(program.md §2.1).

Three things are read that a bare cell does not carry:

- **The measure.** `sb_total_deaths_best` and `os_total_deaths_best` are both
  counts of the dead in the same year, and adding them would double-count
  nothing while charting them as one series would mean nothing. Each column is
  its own indicator.
- **The bound.** UCDP publishes a best estimate with a low and a high, because
  a death toll in a conflict is a range and reporting the best figure alone
  states a precision UCDP does not claim. The bounds are separate series, so a
  reader can see the range rather than infer it.
- **Zero as a figure.** A year with no non-state conflict is written `0`, and
  that is a finding — Indonesia has had none since 2016 — not a missing value.
  Only an empty cell yields no record.

`PARSER_VERSION` is not bumped for this reader. A bump makes every extractor
re-read the whole corpus, and it earns that where a reader replaces one that
already produced rows. Nothing has ever been extracted from this source, so its
first run reads it under the current version like any newly landed document.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from .base import ExtractionError, Extractor, Landed
from .tabular import decode

SOURCE_SLUG = "ucdp-organized-violence"

ARCHIVE_SUFFIXES = {".zip"}

#: Indonesia in the Gleditsch & Ward numbering UCDP uses, and the number in
#: the country page's URL. Matched on the code rather than on `country`,
#: which is a display name and has been spelled differently across releases.
COUNTRY_ID = "850"

COUNTRY = "Indonesia"

#: UCDP's own bounds, and what each is called beside the series name. `best`
#: carries no suffix: it is the figure UCDP reports, and a series called
#: "Deaths in state-based conflict" should be that one.
BOUNDS: tuple[tuple[str, str, str], ...] = (
    ("best", "", ""),
    ("low", "_low", ", low estimate"),
    ("high", "_high", ", high estimate"),
)

DEATHS = "deaths"
DYADS = "dyads"


@dataclass(frozen=True, slots=True)
class Measure:
    """One column of the country-year table: a series, and what it counts."""

    #: The column in UCDP's CSV. Also kept on the row as the publisher's own
    #: code, which is what a reader takes back to the codebook.
    column: str

    #: The Silver indicator these figures belong to. Readable rather than a
    #: code, like the SIPRI series: there are two dozen, and
    #: `ucdp_state_based_deaths` in a URL is worth more than eight characters
    #: of base36.
    indicator: str

    #: What a reader sees beside the identifier.
    name: str

    unit: str = DEATHS

    #: Which of UCDP's estimates this is — best, low or high — carried onto
    #: the row so the three series can be recognised as one range.
    bound: str = "best"


def _bounded(column: str, indicator: str, name: str) -> tuple[Measure, ...]:
    """A measure UCDP publishes as a best estimate with bounds.

    The column names are not consistent across the file — `sb_total_deaths_best`
    beside `sb_intrastate_deaths_best` — so the template carries a `{bound}`
    rather than a suffix being appended here.
    """
    return tuple(
        Measure(
            column=column.format(bound=bound),
            indicator=f"{indicator}{suffix}",
            name=f"{name}{label}",
            bound=bound,
        )
        for bound, suffix, label in BOUNDS
    )


#: What is read. The dyad names, ids and `_exist` flags are left: they are the
#: actors in each conflict rather than a quantity, and a series whose values
#: are "Government of Indonesia - OPM" is not a series.
MEASURES: tuple[Measure, ...] = (
    # -- state-based conflict: a government against an organised opponent ---
    *_bounded(
        "sb_total_deaths_{bound}",
        "ucdp_state_based_deaths",
        "Deaths in state-based conflict",
    ),
    *_bounded(
        "sb_intrastate_deaths_{bound}",
        "ucdp_intrastate_deaths",
        "Deaths in intrastate conflict",
    ),
    # Kept although Indonesia's figures are zero throughout: a zero here is
    # UCDP saying no interstate conflict was fought on Indonesian soil that
    # year, which is a fact about the country and not a gap in the data.
    *_bounded(
        "sb_interstate_deaths_{bound}",
        "ucdp_interstate_deaths",
        "Deaths in interstate conflict",
    ),
    # -- non-state conflict: organised groups fighting each other -----------
    *_bounded(
        "ns_total_deaths_{bound}",
        "ucdp_non_state_deaths",
        "Deaths in non-state conflict",
    ),
    # -- one-sided violence: an armed actor killing civilians ---------------
    *_bounded(
        "os_total_deaths_{bound}",
        "ucdp_one_sided_deaths",
        "Deaths from one-sided violence",
    ),
    # -- all three together, as the country page's headline figure ----------
    *_bounded(
        "cumulative_total_deaths_in_orgvio_{bound}",
        "ucdp_organized_violence_deaths",
        "Deaths in organized violence",
    ),
    # -- who the dead were, across all three forms of violence --------------
    #
    # Published as a best estimate only, so no bounds: UCDP bounds the total,
    # not its division between combatants and civilians.
    Measure(
        column="cumulative_total_deaths_civilians_in_orgvio",
        indicator="ucdp_civilian_deaths",
        name="Civilians killed in organized violence",
    ),
    Measure(
        column="cumulative_total_deaths_parties_in_orgvio",
        indicator="ucdp_combatant_deaths",
        name="Combatants killed in organized violence",
    ),
    # UCDP's own category for a death it could not attribute to either side.
    # Kept rather than folded into the total: it is what the total's
    # uncertainty is made of.
    Measure(
        column="cumulative_total_deaths_unknown_in_orgvio",
        indicator="ucdp_unattributed_deaths",
        name="Deaths in organized violence, side unknown",
    ),
    # -- how much violence, rather than how lethal --------------------------
    #
    # A dyad is one pair of fighting actors. The count is how many were active
    # in the country that year, which moves independently of the death toll: a
    # year of many small conflicts and a year of one large one are different
    # facts about a country and read the same through fatalities alone.
    Measure(
        column="sb_dyad_count",
        indicator="ucdp_state_based_dyads",
        name="Active state-based conflict dyads",
        unit=DYADS,
    ),
    Measure(
        column="ns_dyad_count",
        indicator="ucdp_non_state_dyads",
        name="Active non-state conflict dyads",
        unit=DYADS,
    ),
    Measure(
        column="os_dyad_count",
        indicator="ucdp_one_sided_actors",
        name="Actors committing one-sided violence",
        unit=DYADS,
    ),
)

#: The columns that say which country and year a row is about. Without them
#: nothing on the row can be filed, so their absence is an error rather than a
#: row that fails to match.
_KEY_COLUMNS = ("country_id", "year")


def _member(archive: zipfile.ZipFile) -> str:
    """The CSV inside the archive.

    UCDP ships exactly one, named for the release —
    `OrganizedViolenceCYDataSet26_1.csv` — so it is found rather than named.
    """
    members = [
        name
        for name in archive.namelist()
        # Archives from macOS carry a shadow copy of every file.
        if name.lower().endswith(".csv") and not name.startswith("__MACOSX/")
    ]
    if not members:
        raise ExtractionError(SOURCE_SLUG, "archive holds no CSV")
    return members[0]


class UcdpOrganizedViolenceExtractor(Extractor):
    """One Bronze record per Indonesian figure in the country-year table."""

    target = "records"

    def __init__(self, country_id: str = COUNTRY_ID) -> None:
        self._country_id = country_id

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == SOURCE_SLUG and landed.path.suffix.lower() in ARCHIVE_SUFFIXES

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(landed.path.read_bytes()))
        except zipfile.BadZipFile as exc:
            raise ExtractionError(str(landed.path), f"unreadable archive: {exc}") from exc

        with archive:
            member = _member(archive)
            reader = csv.DictReader(io.StringIO(decode(archive.read(member))))
            self._check_columns(landed, reader.fieldnames)

            number = 0
            for row in reader:
                if (row.get("country_id") or "").strip() != self._country_id:
                    continue
                for record in self._records(row, landed):
                    number += 1
                    yield {
                        "dataset": landed.dataset or "organized-violence",
                        "row_number": number,
                        "columns": record,
                    }

            if number == 0:
                # Not an empty file: the table parsed and holds no figure for
                # the one country this reader is for, which means UCDP
                # renumbered the countries or dropped Indonesia from the
                # release.
                raise ExtractionError(
                    str(landed.path),
                    f"{member} holds no figures for country {self._country_id}; "
                    "UCDP's country numbering or its coverage has changed",
                )

    def _check_columns(self, landed: Landed, fieldnames: Sequence[str] | None) -> None:
        """Refuse a table that has lost a column we read.

        A missing column would otherwise yield no records for that series, and
        a series that stops arriving looks from the portal exactly like a
        country where the violence stopped.
        """
        present = set(fieldnames or ())
        missing = [column for column in _KEY_COLUMNS if column not in present]
        missing += [measure.column for measure in MEASURES if measure.column not in present]
        if missing:
            raise ExtractionError(
                str(landed.path),
                f"table is missing {len(missing)} expected columns "
                f"({', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}); "
                "UCDP has reshaped the dataset and the measures must be re-read",
            )

    def _records(self, row: dict[str, str], landed: Landed) -> Iterator[dict[str, str]]:
        year = (row.get("year") or "").strip()
        # UCDP states the release in a column of its own, which is the only
        # place the figures say which version produced them once the file is
        # out of its archive.
        edition = (row.get("Version") or "").strip() or str(
            (landed.extra or {}).get("edition") or ""
        )

        for measure in MEASURES:
            value = (row.get(measure.column) or "").strip()
            if not value:
                # UCDP writes a year with no violence as 0, so an empty cell is
                # the dataset not covering the measure rather than a zero.
                continue

            yield {
                "indicator": measure.indicator,
                "series_name": measure.name,
                # The display name rather than the code: it is what resolves to
                # a place, and the code UCDP numbers countries by is not one
                # this warehouse's geography registry knows.
                "country": (row.get("country") or COUNTRY).strip(),
                "country_id": self._country_id,
                "year": year,
                "value": value,
                "unit": measure.unit,
                "measure": measure.column,
                "bound": measure.bound,
                "edition": edition,
                "publisher": "UCDP",
            }
