"""SIPRI Milex: finding the workbook, and reading Indonesia out of it.

The extractor tests run against a workbook the test builds, cut down to the
shapes that matter: a preamble of a different length on every sheet, a header
row that starts with `Country`, region headings sharing the country column,
absence markers in two spellings, and a blue cell — which is the only place
the workbook says a figure is a SIPRI estimate.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.styles import Font

from terusan_pipelines.extract import Landed, SipriMilexExtractor
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.extract.sipri import measure_for, number_text
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.sipri import milex
from terusan_pipelines.sources.sipri.milex import MilitaryExpenditure, workbook_url

PAGE = """
<html><body>
  <p>The database is updated annually.</p>
  <a href="//www.sipri.org/sites/default/files/SIPRI-Milex-data-1949-2025_v1.2.xlsx">
    Download the SIPRI Military Expenditure Database (Excel)
  </a>
</body></html>
"""

#: Blue, as the workbook writes an estimate.
BLUE = Font(color="FF0000FF")


# -- finding the download ---------------------------------------------------


def test_the_link_is_read_off_the_page() -> None:
    assert workbook_url(PAGE) == (
        "https://www.sipri.org/sites/default/files/SIPRI-Milex-data-1949-2025_v1.2.xlsx"
    )


def test_a_relative_link_resolves_against_the_page() -> None:
    page = '<a href="/sites/default/files/SIPRI-Milex-data-1949-2024.xlsx">Excel</a>'

    assert workbook_url(page).startswith("https://www.sipri.org/sites/")


def test_another_workbook_on_the_page_is_not_the_database() -> None:
    """SIPRI links its arms-transfers spreadsheets from the same templates."""
    page = (
        '<a href="/files/SIPRI-Arms-Transfers-2025.xlsx">Arms transfers</a>'
        '<a href="/files/SIPRI-Milex-data-1949-2025.xlsx">Milex</a>'
    )

    assert workbook_url(page).endswith("SIPRI-Milex-data-1949-2025.xlsx")


def test_a_page_without_the_link_is_an_error() -> None:
    """Rather than a guessed URL: the guess either 404s or lands last year's
    figures under this year's edition, and nobody would notice either."""
    with pytest.raises(ValueError, match="markup has changed"):
        workbook_url("<html><body>Under maintenance</body></html>")


# -- collecting -------------------------------------------------------------


class FakeResponse:
    def __init__(self, url: str, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.content = body
        self.text = body.decode()
        self.headers = headers or {}


class FakeHttp:
    def __init__(self, pages: dict[str, FakeResponse]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.requested.append(url)
        return self.pages[url]


WORKBOOK_URL = "https://www.sipri.org/sites/default/files/SIPRI-Milex-data-1949-2025_v1.2.xlsx"


@pytest.fixture
def download(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    http = FakeHttp(
        {
            milex.DATABASE_PAGE: FakeResponse(milex.DATABASE_PAGE, PAGE.encode()),
            WORKBOOK_URL: FakeResponse(
                WORKBOOK_URL,
                b"PK\x03\x04 pretend workbook",
                {"last-modified": "Mon, 27 Apr 2026 15:20:25 GMT", "etag": '"e13b8"'},
            ),
        }
    )

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[FakeHttp]:
        yield http

    monkeypatch.setattr(milex, "fetcher", fake_fetcher)
    return http


def test_the_workbook_lands_as_published(download: FakeHttp) -> None:
    (artifact,) = list(MilitaryExpenditure().collect(ScrapeContext()))

    assert artifact.dataset == milex.DATASET
    assert artifact.filename == "SIPRI-Milex-data-1949-2025_v1.2.xlsx"
    assert artifact.source_url == WORKBOOK_URL
    assert artifact.published_at == date(2026, 4, 27)


def test_the_edition_is_read_off_the_filename(download: FakeHttp) -> None:
    """It is stated nowhere else in the response, and it is what tells one
    release from the next once both are in RAW."""
    (artifact,) = list(MilitaryExpenditure().collect(ScrapeContext()))

    assert artifact.partition == ("edition=2025",)
    assert artifact.metadata["version"] == "1.2"
    assert artifact.metadata["covers_from"] == "1949"
    assert artifact.metadata["covers_to"] == "2025"


def test_a_release_older_than_since_is_not_landed(download: FakeHttp) -> None:
    artifacts = list(MilitaryExpenditure().collect(ScrapeContext(since=date(2026, 6, 1))))

    assert artifacts == []


def test_the_release_is_landed_when_since_predates_it(download: FakeHttp) -> None:
    artifacts = list(MilitaryExpenditure().collect(ScrapeContext(since=date(2026, 1, 1))))

    assert len(artifacts) == 1


# -- reading the workbook ---------------------------------------------------


def _sheet(book: Workbook, title: str, preamble: list[str], header: list[object]) -> object:
    sheet = book.create_sheet(title)
    for line in preamble:
        sheet.append([line])
    sheet.append(header)
    return sheet


def build_workbook(path: Path) -> Path:
    """A Milex workbook in miniature, with each sheet's own quirks kept."""
    book = Workbook()
    book.remove(book.active)

    # The regional totals sheet has no country column at all.
    totals = book.create_sheet("Regional totals")
    totals.append(["Military expenditure by region"])
    totals.append(["Asia & Oceania", 100, 200])

    constant = _sheet(
        book,
        "Constant (2024) US$",
        ["Military expenditure by country", "Figures are in US $m."],
        ["Country", "", "Notes", 1974, 1975, 2025],
    )
    constant.append(["Asia & Oceania"])
    constant.append(["South East Asia"])
    constant.append(["Indonesia", None, "62", "...", 3500.5, 15329.508033])
    constant.append(["Malaysia", None, "71", 1000, 1100, 4200])
    # Blue: SIPRI's estimate, stated in the preamble and encoded in the font.
    constant.cell(row=6, column=5).font = BLUE

    share = _sheet(
        book,
        "Share of GDP",
        ["Military expenditure as percentage of GDP", "Countries are grouped by region."],
        ["Country", "Notes", 1974, 2025],
    )
    share.append(["Indonesia", "62", ". .", 0.010446886384200042])

    local = _sheet(
        book,
        "Local currency calendar years",
        ["Military expenditure in local currency"],
        ["Country", "Currency", "Notes", 1974, 2025],
    )
    local.append(["Indonesia", "Rupiah    ", "62", "xxx", 247525700000000])

    financial = _sheet(
        book,
        "Local currency financial years",
        ["Military expenditure in local currency, financial years"],
        ["Country", "Currency", "Fiscal Year", "Notes", 2025],
    )
    financial.append(["Indonesia", "Rupiah", "Till: 1999 Apr.-Mar.", "62", 247525700000000])

    book.save(path)
    return path


def landed_workbook(tmp_path: Path, name: str = "SIPRI-Milex-data-1949-2025.xlsx") -> Landed:
    return Landed(
        path=build_workbook(tmp_path / name),
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="sipri-milex",
        dataset="milex",
        extra={"covers_to": "2025"},
    )


def columns_of(rows: list[dict], indicator: str) -> list[dict[str, str]]:
    return [row["columns"] for row in rows if row["columns"]["indicator"] == indicator]


@pytest.fixture
def rows(tmp_path: Path) -> list[dict]:
    return list(SipriMilexExtractor().extract(landed_workbook(tmp_path)))


def test_every_measure_sheet_becomes_its_own_indicator(rows: list[dict]) -> None:
    """One sheet is US$ at constant prices and the next is the same figures as
    a share of GDP. Landing them on one indicator would chart 15329 beside
    0.01 and call both military expenditure."""
    assert {row["columns"]["indicator"] for row in rows} == {
        "sipri_milex_constant_usd",
        "sipri_milex_share_gdp",
        "sipri_milex_local_currency",
    }


def test_only_indonesia_is_kept(rows: list[dict]) -> None:
    assert {row["columns"]["country"] for row in rows} == {"Indonesia"}


def test_a_year_column_becomes_the_period(rows: list[dict]) -> None:
    constant = columns_of(rows, "sipri_milex_constant_usd")

    assert [row["year"] for row in constant] == ["1975", "2025"]
    assert [row["value"] for row in constant] == ["3500.5", "15329.508033"]


def test_the_constant_price_base_year_rides_on_the_unit(rows: list[dict]) -> None:
    """SIPRI rebases every release, so the unit cannot be hardcoded: the same
    series is constant 2024 prices this year and 2025 prices next."""
    constant = columns_of(rows, "sipri_milex_constant_usd")

    assert constant[0]["unit"] == "US$ m., constant 2024 prices and exchange rates"


def test_a_share_is_not_called_a_percentage(rows: list[dict]) -> None:
    """The cell holds 0.0104 under a `0.00%` format. Naming that percent would
    be wrong by a hundred, and multiplying it here would put an interpretation
    into Bronze."""
    (share,) = columns_of(rows, "sipri_milex_share_gdp")

    assert share["unit"] == "share of GDP"
    assert float(share["value"]) == pytest.approx(0.0104468863842)


def test_local_currency_takes_its_unit_from_the_currency_column(rows: list[dict]) -> None:
    (local,) = columns_of(rows, "sipri_milex_local_currency")

    assert local["unit"] == "Rupiah, current prices"
    assert local["currency"] == "Rupiah"


def test_a_rupiah_figure_keeps_its_digits(rows: list[dict]) -> None:
    """`str(2.475257e+14)` is what Python makes of it, and the raw value is
    what someone checks against the published table."""
    (local,) = columns_of(rows, "sipri_milex_local_currency")

    assert local["value"] == "247525700000000"


def test_absence_markers_yield_no_figure(rows: list[dict]) -> None:
    """`...`, `. .` and `xxx` are SIPRI saying there is no figure. Carried
    through, Indonesia's pre-1974 gap becomes observations asserting nothing."""
    assert all(row["columns"]["year"] != "1974" for row in rows)


def test_a_sipri_estimate_says_so(rows: list[dict]) -> None:
    """Blue is an estimate, and the colour does not survive into Bronze."""
    constant = columns_of(rows, "sipri_milex_constant_usd")

    assert [row["estimate"] for row in constant] == ["1", ""]
    assert [row["uncertain"] for row in constant] == ["", ""]


def test_the_footnote_marks_come_along(rows: list[dict]) -> None:
    """They point into the Footnotes sheet, which says what a country's
    figures include — conscription, pensions, paramilitaries."""
    assert {row["columns"]["notes"] for row in rows} == {"62"}


def test_the_financial_years_sheet_is_left_alone() -> None:
    """Its periods are fiscal years the sheet describes in prose, which this
    warehouse cannot express and would silently read as calendar years."""
    assert measure_for("Local currency financial years") is None
    assert measure_for("Regional totals") is None
    assert measure_for("Footnotes") is None


def test_the_edition_rides_onto_every_row(rows: list[dict]) -> None:
    assert {row["columns"]["edition"] for row in rows} == {"2025"}


def test_rows_are_numbered_in_order(rows: list[dict]) -> None:
    assert [row["row_number"] for row in rows] == list(range(1, len(rows) + 1))


def test_a_workbook_without_indonesia_is_an_error(tmp_path: Path) -> None:
    """Silence here is the failure worth catching: a renamed row or a reshaped
    sheet would otherwise extract cleanly and produce nothing."""
    book = Workbook()
    sheet = book.active
    sheet.title = "Current US$"
    sheet.append(["Military expenditure by country"])
    sheet.append(["Country", "Notes", 2025])
    sheet.append(["Malaysia", "71", 4200])
    path = tmp_path / "SIPRI-Milex-data-1949-2025.xlsx"
    book.save(path)

    landed = Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="sipri-milex",
        dataset="milex",
    )

    with pytest.raises(ExtractionError, match="no figures for Indonesia"):
        list(SipriMilexExtractor().extract(landed))


def test_only_this_source_is_claimed(tmp_path: Path) -> None:
    """The generic workbook reader would turn the file into a cell per country
    keyed by column letter, and nothing downstream could read it back."""
    landed = landed_workbook(tmp_path)

    assert SipriMilexExtractor().handles(landed)

    other = Landed(
        path=landed.path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="ojk-banking-spi",
        dataset="banking-statistics",
    )
    assert not SipriMilexExtractor().handles(other)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (247525700000000, "247525700000000"),
        (2.475257e14, "247525700000000"),
        (0.010446886384200042, "0.010446886384200042"),
        (None, ""),
        ("  62 ", "62"),
    ],
)
def test_a_number_is_written_as_digits(value: object, expected: str) -> None:
    assert number_text(value) == expected


def test_a_workbook_that_is_not_one_fails_with_its_path(tmp_path: Path) -> None:
    path = tmp_path / "SIPRI-Milex-data-1949-2025.xlsx"
    path.write_bytes(b"PK\x03\x04 not really a workbook")
    landed = Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="sipri-milex",
        dataset="milex",
    )

    with pytest.raises(ExtractionError, match="unreadable workbook"):
        list(SipriMilexExtractor().extract(landed))
