"""VEWS: picking the exports up, and counting a year out of one.

The extractor tests run against exports the test builds, cut down to the
shapes that matter: a row dated outside the year the file covers, a `-99` that
is not a zero, a blank cell that is, one province spelled two ways, and
Gorontalo — which names a province and a regency inside it, and so is the one
name the geography registry will not resolve unqualified.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path

import pytest

from terusan_pipelines.extract import Landed
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.extract.vews import (
    FIGURES_DATASET,
    INCIDENTS_DATASET,
    MEASURES,
    VewsCollectiveViolenceExtractor,
    clean_province,
    count,
    geo_label,
)
from terusan_pipelines.normalize.reference import geography_registry
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.vews.collective_violence import (
    CollectiveViolenceEarlyWarning,
    drop_dir,
    edition,
    exports,
)

COLUMNS = [
    "no",
    "incident_id",
    "date",
    "year",
    "province",
    "num_death",
    "num_injured",
    "fem_death",
    "fem_injured",
    "child_death",
    "child_injured",
    "infra_damage",
    "infra_destroyed",
    "intervene",
    "inc_desc",
]


def incident(
    ident: str,
    *,
    year: str = "2025",
    province: str = "JAWA TIMUR",
    deaths: str = "",
    injured: str = "",
    damaged: str = "",
    intervene: str = "TIDAK",
) -> dict[str, str]:
    return {
        "no": ident,
        "incident_id": ident,
        "date": f"01/01/{year[2:]}",
        "year": year,
        "province": province,
        "num_death": deaths,
        "num_injured": injured,
        "fem_death": "",
        "fem_injured": "",
        "child_death": "",
        "child_injured": "",
        "infra_damage": damaged,
        "infra_destroyed": "",
        "intervene": intervene,
        "inc_desc": "",
    }


def write_csv(path: Path, rows: list[dict[str, str]], *, columns: list[str] | None = None) -> Path:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns or COLUMNS, delimiter=";")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in (columns or COLUMNS)})
    # Google Sheets' exports carry a BOM and a tail of empty rows.
    path.write_text("﻿" + buffer.getvalue() + ";;;;;;;;;;;;;;\n", encoding="utf-8")
    return path


def landed(path: Path, year: str = "2025") -> Landed:
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="vews-collective-violence",
        dataset="collective-violence-early-warning",
        partition=(f"year={year}",),
        extra={"edition": year},
    )


def figures(rows: list[dict]) -> dict[tuple[str, str], int]:
    """The figure records, keyed by indicator and place."""
    return {
        (row["columns"]["indicator"], row["columns"]["geo"]): int(row["columns"]["value"])
        for row in rows
        if row["dataset"] == FIGURES_DATASET
    }


# -- picking the files up ---------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("VEWS Dataset (v.1.0) (2021).csv", "2021"),
        ("Yearly Dataset (2023) - VEWS Dataset (v.1.0) (Verified File)_.xlsx", "2023"),
        ("2024 Yearly Dataset - VEWS Dataset (v.1.0) (Verification File).xlsx", "2024"),
        ("Yearly Dataset 2025 - VEWS Dataset (v.1.0).xlsx", "2025"),
        ("VEWS Dataset v.1.0.xlsx", None),
    ],
)
def test_the_year_is_read_off_the_filename(filename: str, expected: str | None) -> None:
    """VEWS names every export for the year it covers, and the version number
    in the same name is not one."""
    assert edition(filename) == expected


def test_a_directory_yields_its_exports_and_nothing_else(tmp_path: Path) -> None:
    write_csv(tmp_path / "VEWS Dataset (2021).csv", [incident("a", year="2021")])
    (tmp_path / "Yearly Dataset 2025.xlsx").write_bytes(b"PK\x03\x04stub")
    # An open spreadsheet, a stray note, and a Finder file.
    (tmp_path / "~$Yearly Dataset 2025.xlsx").write_bytes(b"lock")
    (tmp_path / "notes.txt").write_text("coding queries")
    (tmp_path / ".DS_Store").write_bytes(b"")

    assert [p.name for p in exports(tmp_path)] == [
        "VEWS Dataset (2021).csv",
        "Yearly Dataset 2025.xlsx",
    ]


def test_an_export_without_a_year_is_skipped_rather_than_landed(tmp_path: Path) -> None:
    """Rather than landed under a guessed year: the year decides which figures
    the file is authoritative for, and a guess publishes someone else's."""
    write_csv(tmp_path / "VEWS Dataset (2021).csv", [incident("a", year="2021")])
    write_csv(tmp_path / "VEWS Dataset.csv", [incident("b", year="2021")])

    artifacts = list(
        CollectiveViolenceEarlyWarning().collect(ScrapeContext(params={"dir": str(tmp_path)}))
    )

    assert [a.filename for a in artifacts] == ["VEWS Dataset (2021).csv"]
    assert artifacts[0].partition == ("year=2021",)


def test_an_empty_inbox_is_not_a_failure(tmp_path: Path) -> None:
    """The ordinary state of a machine nobody has handed the files to."""
    context = ScrapeContext(params={"dir": str(tmp_path / "nothing-here")})

    assert list(CollectiveViolenceEarlyWarning().collect(context)) == []


def test_the_drop_directory_can_be_said_per_run(tmp_path: Path) -> None:
    assert drop_dir(ScrapeContext(params={"dir": str(tmp_path)})) == tmp_path


# -- reading a value --------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("1", 1),
        ("1.0", 1),
        ("0", 0),
        # VEWS's own code for "the reporting did not say".
        ("-99", None),
        ("-99.0", None),
        # A coder's note in a numeric column.
        ("tidak jelas", None),
        ("", None),
    ],
)
def test_a_casualty_cell_reads_as_a_number_or_as_nothing(cell: str, expected: int | None) -> None:
    assert count(cell) == expected


def test_an_empty_cell_is_a_zero_and_minus_99_is_not(tmp_path: Path) -> None:
    """VEWS leaves the cell blank when nobody was hurt and writes -99 when the
    reporting did not say, so the two cannot be summed the same way."""
    path = write_csv(
        tmp_path / "VEWS 2025.csv",
        [
            incident("a", deaths="2"),
            incident("b", deaths=""),
            incident("c", deaths="-99"),
        ],
    )

    counted = figures(list(VewsCollectiveViolenceExtractor().extract(landed(path))))

    assert counted[("vews_deaths", "Indonesia")] == 2
    assert counted[("vews_incidents", "Indonesia")] == 3


def test_a_province_with_incidents_and_no_deaths_publishes_a_zero(tmp_path: Path) -> None:
    """Which is a finding. A province with no incident at all publishes
    nothing, because a year missing from an export and a quiet year are not
    distinguishable from here."""
    path = write_csv(tmp_path / "VEWS 2025.csv", [incident("a", province="BALI")])

    counted = figures(list(VewsCollectiveViolenceExtractor().extract(landed(path))))

    assert counted[("vews_deaths", "Provinsi BALI")] == 0
    assert ("vews_deaths", "Provinsi ACEH") not in counted


# -- naming the place -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("JAWA TIMUR", "JAWA TIMUR"),
        # A non-breaking space, doubled spaces, a qualifier the coder typed.
        ("DKI\xa0JAKARTA", "DKI JAKARTA"),
        ("SUMATRA  UTARA", "SUMATERA UTARA"),
        ("PROVINSI BENGKULU", "BENGKULU"),
        # The same province under two real names — Jakarta was renamed in 2024.
        ("DAERAH KHUSUS JAKARTA", "DKI JAKARTA"),
        ("DAERAH ISTIMEWA YOGYAKARTA", "DI YOGYAKARTA"),
        # And three misspellings, one row each across four exports.
        ("KALIMANTAN BERAT", "KALIMANTAN BARAT"),
        ("PAPUA PENGUNUNGAN", "PAPUA PEGUNUNGAN"),
    ],
)
def test_a_province_written_two_ways_is_one_province(raw: str, expected: str) -> None:
    assert clean_province(raw) == expected


def test_one_province_spelled_two_ways_is_counted_once(tmp_path: Path) -> None:
    """Counted twice, the two tallies reach Silver as two different figures
    claiming to be the same observation, and Silver refuses the lot."""
    path = write_csv(
        tmp_path / "VEWS 2025.csv",
        [
            incident("a", province="SUMATERA UTARA"),
            incident("b", province="SUMATRA UTARA"),
        ],
    )

    counted = figures(list(VewsCollectiveViolenceExtractor().extract(landed(path))))

    assert counted[("vews_incidents", "Provinsi SUMATERA UTARA")] == 2


def test_every_place_the_extractor_names_resolves(tmp_path: Path) -> None:
    """Against the real registry, including Gorontalo — which names a province
    and a regency inside it, and is why every province is qualified."""
    registry = geography_registry()
    path = write_csv(
        tmp_path / "VEWS 2025.csv",
        [
            incident("a", province="GORONTALO"),
            incident("b", province="PAPUA"),
            incident("c", province="DAERAH KHUSUS JAKARTA"),
            incident("d", province="PROVINSI BENGKULU"),
        ],
    )

    places = {
        geo for _, geo in figures(list(VewsCollectiveViolenceExtractor().extract(landed(path))))
    }

    assert places == {
        "Indonesia",
        "Provinsi GORONTALO",
        "Provinsi PAPUA",
        "Provinsi DKI JAKARTA",
        "Provinsi BENGKULU",
    }
    for place in places:
        assert registry.resolve(place, on=date(2025, 6, 1)).resolved, place


def test_gorontalo_alone_is_the_name_that_needs_qualifying() -> None:
    """The reason `geo_label` exists, asserted rather than described."""
    registry = geography_registry()

    assert not registry.resolve("GORONTALO").resolved
    assert registry.resolve(geo_label("GORONTALO")).identifier == "ID-75"


# -- the year an export covers ----------------------------------------------


def test_only_the_year_the_export_covers_is_counted(tmp_path: Path) -> None:
    """An export carries a tail of incidents dated to its neighbours, because
    a coder files an incident when they read about it. Counted, the 2025
    export's six weeks of 2024 would overwrite the 2024 export's whole year."""
    path = write_csv(
        tmp_path / "VEWS 2025.csv",
        [
            incident("a", year="2025"),
            incident("b", year="2025"),
            incident("c", year="2024"),
            incident("d", year="2026"),
        ],
    )

    rows = list(VewsCollectiveViolenceExtractor().extract(landed(path, "2025")))
    counted = figures(rows)

    assert counted[("vews_incidents", "Indonesia")] == 2
    assert {row["columns"]["year"] for row in rows if row["dataset"] == FIGURES_DATASET} == {"2025"}
    # And the tail is kept: it is the dataset, it is simply not this year's.
    incidents = [row for row in rows if row["dataset"] == INCIDENTS_DATASET]
    assert len(incidents) == 4


def test_the_country_is_the_whole_year_and_not_the_sum_of_the_provinces(
    tmp_path: Path,
) -> None:
    """An incident whose province was never coded still happened."""
    path = write_csv(
        tmp_path / "VEWS 2025.csv",
        [
            incident("a", province="BALI"),
            incident("b", province=""),
        ],
    )

    counted = figures(list(VewsCollectiveViolenceExtractor().extract(landed(path))))

    assert counted[("vews_incidents", "Indonesia")] == 2
    assert counted[("vews_incidents", "Provinsi BALI")] == 1


def test_an_export_holding_nothing_from_its_own_year_is_an_error(tmp_path: Path) -> None:
    """The filename and the rows disagree, and counting either would be a
    guess about which one is wrong."""
    path = write_csv(tmp_path / "VEWS 2025.csv", [incident("a", year="2019")])

    with pytest.raises(ExtractionError, match="holds no incident dated to it"):
        list(VewsCollectiveViolenceExtractor().extract(landed(path, "2025")))


def test_a_landing_that_does_not_say_which_year_is_an_error(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "VEWS 2025.csv", [incident("a")])
    anonymous = Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="vews-collective-violence",
    )

    with pytest.raises(ExtractionError, match="does not say which year"):
        list(VewsCollectiveViolenceExtractor().extract(anonymous))


# -- the shape of the export ------------------------------------------------


def test_a_renamed_measure_column_is_an_error(tmp_path: Path) -> None:
    """Rather than a series that sums to zero across every province — which
    reads from the portal exactly like a year in which the violence stopped."""
    columns = [c for c in COLUMNS if c != "num_death"]
    path = write_csv(tmp_path / "VEWS 2025.csv", [incident("a")], columns=columns)

    with pytest.raises(ExtractionError, match="num_death"):
        list(VewsCollectiveViolenceExtractor().extract(landed(path)))


def test_an_export_of_blank_rows_is_an_error(tmp_path: Path) -> None:
    """Every export carries hundreds of them below the data; one that is
    nothing but blank rows is a file that did not export."""
    path = write_csv(tmp_path / "VEWS 2025.csv", [])

    with pytest.raises(ExtractionError, match="no row carries"):
        list(VewsCollectiveViolenceExtractor().extract(landed(path)))


def test_every_measure_is_published_for_every_place(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "VEWS 2025.csv",
        [incident("a", province="BALI"), incident("b", province="ACEH")],
    )

    counted = figures(list(VewsCollectiveViolenceExtractor().extract(landed(path))))

    assert len(counted) == len(MEASURES) * 3
    assert {measure.unit for measure in MEASURES} == {
        "incidents",
        "deaths",
        "people",
        "structures",
    }


def test_the_incidents_are_kept_column_for_column(tmp_path: Path) -> None:
    """The collection the figures are counted from, so a reader can group the
    same incidents by actor, weapon or issue — none of which Silver holds."""
    path = write_csv(
        tmp_path / "VEWS 2025.csv",
        [incident("a", province="BALI", deaths="1", intervene="IYA")],
    )

    rows = [
        row
        for row in VewsCollectiveViolenceExtractor().extract(landed(path))
        if row["dataset"] == INCIDENTS_DATASET
    ]

    assert len(rows) == 1
    assert rows[0]["columns"]["incident_id"] == "a"
    assert rows[0]["columns"]["province"] == "BALI"
    assert rows[0]["columns"]["intervene"] == "IYA"


def test_an_intervention_is_counted_only_where_someone_intervened(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "VEWS 2025.csv",
        [
            incident("a", intervene="IYA"),
            incident("b", intervene="TIDAK"),
            # VEWS's third answer: the reporting did not say.
            incident("c", intervene="TIDAK JELAS"),
        ],
    )

    counted = figures(list(VewsCollectiveViolenceExtractor().extract(landed(path))))

    assert counted[("vews_incidents_with_intervention", "Indonesia")] == 1


# -- the workbooks ----------------------------------------------------------


def write_workbook(path: Path, rows: list[dict[str, object]]) -> Path:
    """An export the way VEWS ships one: the data among a dozen analysis
    sheets, under a name that changes every year, with the numbers as numbers.
    """
    from openpyxl import Workbook

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Analysis"
    summary.append(["PROVINSI", "JUMLAH"])
    summary.append(["JAWA TIMUR", 12])

    sheet = workbook.create_sheet("(Authentic) VEWS Yearly Dataset")
    # The exports disagree about the case of their own headers.
    sheet.append([column.capitalize() for column in COLUMNS])
    for row in rows:
        sheet.append([row.get(column, "") for column in COLUMNS])

    workbook.create_sheet("COUNTIF")
    workbook.save(path)
    return path


def test_the_data_sheet_is_found_among_the_analysis_sheets(tmp_path: Path) -> None:
    """It is called `OVERALL` in one export, `Form responses 1` in the next and
    `(Authentic) VEWS Yearly Dataset` in the third. The one with an
    `incident_id` column is the data."""
    path = write_workbook(
        tmp_path / "Yearly Dataset 2025.xlsx",
        [
            # A spreadsheet hands its numbers over as floats.
            {**incident("a", province="BALI"), "year": 2025, "num_death": 2},
            {**incident("b", province="BALI"), "year": 2025, "num_injured": -99},
        ],
    )

    counted = figures(list(VewsCollectiveViolenceExtractor().extract(landed(path))))

    assert counted[("vews_incidents", "Provinsi BALI")] == 2
    assert counted[("vews_deaths", "Provinsi BALI")] == 2
    assert counted[("vews_injured", "Provinsi BALI")] == 0


def test_a_workbook_with_no_incident_sheet_is_an_error(tmp_path: Path) -> None:
    from openpyxl import Workbook

    path = tmp_path / "Yearly Dataset 2025.xlsx"
    workbook = Workbook()
    workbook.active.append(["PROVINSI", "JUMLAH"])
    workbook.save(path)

    with pytest.raises(ExtractionError, match="no sheet in the workbook"):
        list(VewsCollectiveViolenceExtractor().extract(landed(path)))
