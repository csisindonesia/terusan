"""HEESI: finding an edition, and turning its tables into records.

The table parsers are vendored and were tested against real PDFs in the
project they came from, so what is checked here is the seam: the edition
discovery, the identifiers composed for Silver, and the columns each figure
carries. One test loads the vendored configuration, because a sheet config
that stops parsing takes every table with it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from terusan_pipelines.extract import HeesiExtractor, Landed
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.extract.heesi import indicator_id
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.esdm import heesi as source_module
from terusan_pipelines.sources.esdm.heesi import Handbook, editions

#: How ESDM links the editions: one per year in the body, the newest repeated
#: in a sidebar, and the filenames spelled three different ways.
_HREF = "/assets/media/content/content-handbook-of-energy"
LINKS = [
    f"{_HREF}-and-economic-statistics-of-indonesia-2025.pdf",
    f"{_HREF}-and-economic-statistics-of-indonesia-2024.pdf",
    f"{_HREF}-and-economic-statistics-of-indonesia-2018-final-edition.pdf",
    f"{_HREF}-economic-statistics-of-indonesia-2010-c19rfkq.pdf",
    "/assets/media/content/some-other-publication.pdf",
    f"{_HREF}-and-economic-statistics-of-indonesia-2025.pdf",
]
PAGE = "<html><body>{}</body></html>".format(
    "".join(f'<a href="{link}">edition</a>' for link in LINKS)
)


# -- finding the editions ---------------------------------------------------


def test_every_edition_on_the_page_is_found_newest_first() -> None:
    assert [edition.year for edition in editions(PAGE)] == [2025, 2024, 2018, 2010]


def test_an_edition_linked_twice_is_one_edition() -> None:
    """ESDM links the newest edition in the body and again in a sidebar."""
    assert [edition.year for edition in editions(PAGE)].count(2025) == 1


def test_another_publication_is_not_a_handbook() -> None:
    assert all("handbook-of-energy" in edition.url for edition in editions(PAGE))


def test_the_year_is_read_before_a_filename_suffix() -> None:
    """`...-of-indonesia-2018-final-edition.pdf` states its year mid-name."""
    found = {edition.year: edition.filename for edition in editions(PAGE)}

    assert found[2018].endswith("2018-final-edition.pdf")


def test_a_page_without_handbooks_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rather than a guessed URL, which either 404s or lands last year's
    figures under this year's edition."""
    fetch(monkeypatch, "<html><body>Under maintenance</body></html>")

    with pytest.raises(ValueError, match="markup has changed"):
        list(Handbook().collect(ScrapeContext()))


# -- collecting -------------------------------------------------------------


class FakeResponse:
    def __init__(self, url: str, body: bytes) -> None:
        self.url = url
        self.content = body
        self.text = body.decode()
        self.headers: dict[str, str] = {}


class FakeHttp:
    def __init__(self, page: str) -> None:
        self.page = page
        self.requested: list[str] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.requested.append(url)
        if url.endswith(".pdf"):
            return FakeResponse(url, b"%PDF-1.7 pretend handbook")
        return FakeResponse(url, self.page.encode())


def fetch(monkeypatch: pytest.MonkeyPatch, page: str = PAGE) -> FakeHttp:
    http = FakeHttp(page)

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[FakeHttp]:
        yield http

    monkeypatch.setattr(source_module, "fetcher", fake_fetcher)
    return http


def test_a_run_takes_the_newest_edition_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fifteen editions of ten-megabyte PDFs is a deliberate pull, not a
    default one."""
    fetch(monkeypatch)

    artifacts = list(Handbook().collect(ScrapeContext()))

    assert len(artifacts) == 1
    assert artifacts[0].partition == ("edition=2025",)
    assert artifacts[0].metadata["edition"] == "2025"
    assert artifacts[0].published_at == date(2025, 12, 31)
    assert artifacts[0].media_type == "application/pdf"


def test_limit_asks_for_more_editions(monkeypatch: pytest.MonkeyPatch) -> None:
    artifacts = list(Handbook().collect(ScrapeContext(limit=3)))

    assert [artifact.metadata["edition"] for artifact in artifacts] == ["2025", "2024", "2018"]


def test_since_leaves_the_older_editions_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch(monkeypatch)

    artifacts = list(Handbook().collect(ScrapeContext(since=date(2025, 1, 1), limit=10)))

    assert [artifact.metadata["edition"] for artifact in artifacts] == ["2025"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test here reaches ESDM."""
    fetch(monkeypatch)


# -- identifiers ------------------------------------------------------------


def test_an_identifier_reads_as_the_series_it_names() -> None:
    assert indicator_id("coal-supply", "Production") == "heesi_coal_supply_production"
    assert (
        indicator_id("domestic-coal-sales", "Iron, Steel & Metallurgy")
        == "heesi_domestic_coal_sales_iron_steel_metallurgy"
    )


def test_the_energy_balance_code_is_part_of_the_identifier() -> None:
    """Its columns are energy types repeated under every balance line, so
    `Hydro Power` alone would hold production and consumption at once."""
    production = indicator_id("neraca-energi", "Hydro Power", "100")
    consumption = indicator_id("neraca-energi", "Hydro Power", "300")

    assert production != consumption
    assert production == "heesi_neraca_energi_100_hydro_power"


# -- reading a handbook -----------------------------------------------------


class Frame:
    """Enough of a DataFrame for the seam: rows, as dicts."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def to_dict(self, _: str) -> list[dict[str, Any]]:
        return self._rows


def landed(tmp_path: Path) -> Landed:
    path = tmp_path / "content-handbook-of-energy-and-economic-statistics-of-indonesia-2025.pdf"
    path.write_bytes(b"%PDF-1.7 pretend handbook")
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="esdm-heesi",
        dataset="handbook",
        extra={"edition": "2025"},
    )


ROWS = [
    {
        "data_id": 9.0,
        "key": "coal-supply",
        "xlsx_col": "Production",
        "variable_id": 18.0,
        "year": 2025,
        "value": 817478697.0,
        "combined": True,
        "code": None,
    },
    {
        "data_id": 7.0,
        "key": "domestic-coal-sales",
        "xlsx_col": "Total",
        "variable_id": None,
        "year": 2025,
        "value": 1.5e8,
        "combined": False,
        "code": None,
    },
    {
        "data_id": None,
        "key": "neraca-energi",
        "xlsx_col": "Hydro Power",
        "variable_id": None,
        "year": 2025,
        "value": 57094.0,
        "combined": False,
        "code": 100.0,
    },
]


def read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: list[dict]) -> list[dict]:
    from terusan_pipelines.extract.heesi import vendored

    monkeypatch.setattr(vendored, "extract_edition", lambda _: Frame(rows))
    return list(HeesiExtractor().extract(landed(tmp_path)))


def test_every_figure_becomes_a_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = read(tmp_path, monkeypatch, ROWS)

    assert [row["columns"]["indicator"] for row in rows] == [
        "heesi_coal_supply_production",
        "heesi_domestic_coal_sales_total",
        "heesi_neraca_energi_100_hydro_power",
    ]
    assert [row["row_number"] for row in rows] == [1, 2, 3]


def test_a_figure_keeps_its_digits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`1.5e+08` is not what the handbook prints."""
    rows = read(tmp_path, monkeypatch, ROWS)

    assert rows[0]["columns"]["value"] == "817478697"
    assert rows[1]["columns"]["value"] == "150000000"
    assert rows[0]["columns"]["year"] == "2025"


def test_the_unit_comes_from_the_table_it_was_read_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The handbook states it in a chapter heading, not in the cells."""
    rows = read(tmp_path, monkeypatch, ROWS)

    assert rows[0]["columns"]["unit"] == "ton"
    assert rows[2]["columns"]["unit"] == "thousand BOE"


def test_a_published_total_is_flagged_rather_than_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It checks the parts, and it double-counts the table if normalized with
    them — so it is kept, and a mapping can exclude it."""
    rows = read(tmp_path, monkeypatch, ROWS)

    assert [row["columns"]["is_total"] for row in rows] == ["", "1", ""]


def test_the_edition_rides_onto_every_figure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = read(tmp_path, monkeypatch, ROWS)

    assert {row["columns"]["edition"] for row in rows} == {"2025"}
    assert {row["columns"]["country"] for row in rows} == {"Indonesia"}


def test_a_table_that_could_not_be_read_does_not_become_a_figure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The vendored parsers report a failure as a row. It is not an
    observation, and thirteen tables read is still worth having."""
    rows = read(
        tmp_path,
        monkeypatch,
        [
            *ROWS,
            {
                "key": "harga-energi",
                "xlsx_col": "__ERROR__",
                "year": None,
                "value": None,
                "error": "SectionNotFound",
            },
            {"key": "neraca-energi", "xlsx_col": "__NEEDS_MANUAL__", "year": None, "value": None},
        ],
    )

    assert len(rows) == 3


def test_a_handbook_nothing_could_be_read_from_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ExtractionError, match="no table in the handbook"):
        read(tmp_path, monkeypatch, [])


def test_only_this_source_is_claimed(tmp_path: Path) -> None:
    """A PDF from anywhere else is prose, and the generic reader handles it."""
    mine = landed(tmp_path)
    other = Landed(
        path=mine.path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="bpk-regulations",
        dataset="documents",
    )

    assert HeesiExtractor().handles(mine)
    assert not HeesiExtractor().handles(other)


# -- the vendored configuration --------------------------------------------


def test_the_sheet_configuration_loads() -> None:
    """A config that stops parsing takes every table with it, so it is checked
    here rather than at the next release."""
    from terusan_pipelines.extract.heesi.vendored import load_sheets

    sheets = load_sheets()

    assert len(sheets) == 14
    assert {sheet.orientation for sheet in sheets} <= {
        "years_as_rows",
        "years_as_columns",
        "matrix",
        "derived_from_matrix",
    }
    assert all(sheet.key and sheet.title_regex for sheet in sheets)
