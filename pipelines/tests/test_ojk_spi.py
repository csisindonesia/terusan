"""OJK's SPI archive: walking back past the ten editions the page still lists."""

from __future__ import annotations

import pytest

from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.ojk import banking
from terusan_pipelines.sources.ojk.banking import BankingStatistics, unlisted_edition
from terusan_pipelines.sources.ojk.legacy.banking_spi import SpiEdition

LISTED = [
    SpiEdition("Statistik Perbankan Indonesia - Juni 2025", 2025, 6, "p/juni-2025"),
    SpiEdition("Statistik Perbankan Indonesia - Mei 2025", 2025, 5, "p/mei-2025"),
]


def _months(editions) -> list[tuple[int, int]]:
    return [(e.year, e.month) for e in editions]


@pytest.fixture
def archive(monkeypatch: pytest.MonkeyPatch) -> set[tuple[int, int]]:
    """Which unlisted months still have a page. Set per test."""
    present: set[tuple[int, int]] = set()

    def exists(url: str) -> bool:
        return any(unlisted_edition(y, m).page_url == url for y, m in present)

    monkeypatch.setattr(banking, "_page_exists", exists)
    return present


def test_a_routine_run_takes_the_newest_listed_edition(archive) -> None:
    editions = BankingStatistics()._editions(ScrapeContext(), LISTED)
    assert _months(editions) == [(2025, 6)]


def test_a_full_pull_walks_back_past_the_listed_editions(archive) -> None:
    archive.update({(2025, 4), (2025, 3), (2024, 12)})

    editions = list(BankingStatistics()._editions(ScrapeContext(params={"full": "true"}), LISTED))

    # January and February 2025 missing is a gap, not the archive's start.
    assert _months(editions) == [(2025, 6), (2025, 5), (2025, 4), (2025, 3), (2024, 12)]


def test_a_year_without_pages_is_where_the_archive_starts(archive) -> None:
    archive.update({(2025, 4), (2023, 1)})

    editions = list(BankingStatistics()._editions(ScrapeContext(params={"full": "true"}), LISTED))

    assert (2023, 1) not in _months(editions)


def test_unlisted_pages_are_named_as_the_listed_ones_are() -> None:
    edition = unlisted_edition(2023, 12)

    assert edition.page_url.endswith("/Pages/Statistik-Perbankan-Indonesia---Desember-2023.aspx")
    assert edition.label == "Statistik Perbankan Indonesia - Desember 2023"


def test_the_bare_workbook_is_preferred_to_the_zip(monkeypatch: pytest.MonkeyPatch) -> None:
    class Page:
        text = (
            '<a href="/Documents/SPI.zip">zip</a><a href="/Documents/SPI.pdf">pdf</a>'
            '<a href="/Documents/SPI.xlsx">xlsx</a>'
        )

    monkeypatch.setattr(banking.banking_spi, "_get", lambda url: Page())

    assert banking._workbook_url(LISTED[0]).endswith("/Documents/SPI.xlsx")


def test_an_older_edition_lands_its_zip(monkeypatch: pytest.MonkeyPatch) -> None:
    class Page:
        text = (
            '<a href="/Documents/SPI.pdf">pdf</a><a href="/Documents/SPI%20Juni%202018.zip">z</a>'
        )

    monkeypatch.setattr(banking.banking_spi, "_get", lambda url: Page())

    assert banking._workbook_url(LISTED[0]).endswith("SPI%20Juni%202018.zip")


def test_an_escaped_absolute_link_is_read_as_the_url_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Page:
        text = '<a href="https&#58;//www.ojk.go.id/Documents/SPI%20Mei%202024.zip">z</a>'

    monkeypatch.setattr(banking.banking_spi, "_get", lambda url: Page())

    assert (
        banking._workbook_url(LISTED[0]) == "https://www.ojk.go.id/Documents/SPI%20Mei%202024.zip"
    )
