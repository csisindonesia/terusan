"""UCDP: finding the release, and reading Indonesia out of it.

The extractor tests run against an archive the test builds, cut down to the
shapes that matter: two countries so the filter has something to reject, a
year of zeroes so a zero is not mistaken for a gap, an empty cell so a gap is
not mistaken for a zero, and the `Version` column UCDP states the release in.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

from terusan_pipelines.extract import Landed, UcdpOrganizedViolenceExtractor
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.extract.ucdp import MEASURES
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.ucdp import organized_violence
from terusan_pipelines.sources.ucdp.organized_violence import (
    OrganizedViolence,
    archive_url,
)

PAGE = """
<html><body>
  <div class="dataset-entry">
    <h3>UCDP Georeferenced Event Dataset (GED) Global <span>version 26.1</span></h3>
    <a href="https://ucdp.uu.se/downloads/ged/ged261-csv.zip">CSV</a>
  </div>
  <div class="dataset-entry">
    <h3>UCDP Country-Year Dataset on Organized Violence <span>version 26.1</span></h3>
    <a href="https://ucdp.uu.se/downloads/organizedviolencecy/organizedviolencecy-261-csv.zip">CSV</a>
    <a href="https://ucdp.uu.se/downloads/organizedviolencecy/organizedviolencecy-261-xlsx.zip">Excel</a>
  </div>
</body></html>
"""

ARCHIVE_URL = "https://ucdp.uu.se/downloads/organizedviolencecy/organizedviolencecy-261-csv.zip"


# -- finding the release ----------------------------------------------------


def test_the_archive_is_read_off_the_page() -> None:
    assert archive_url(PAGE) == (ARCHIVE_URL, "26.1")


def test_another_dataset_on_the_page_is_not_this_one() -> None:
    """Every UCDP dataset is linked from the same page under the same markup,
    and GED's archive is thirty-nine megabytes of events this source does not
    collect."""
    url, _ = archive_url(PAGE)

    assert "organizedviolencecy" in url


def test_a_relative_link_resolves_against_the_page() -> None:
    page = '<a href="/downloads/organizedviolencecy/organizedviolencecy-271-csv.zip">CSV</a>'

    assert archive_url(page) == (
        "https://ucdp.uu.se/downloads/organizedviolencecy/organizedviolencecy-271-csv.zip",
        "27.1",
    )


def test_a_page_without_the_link_is_an_error() -> None:
    """Rather than a guessed URL: the guess either 404s or lands last year's
    figures under a version read off the filename we guessed."""
    with pytest.raises(ValueError, match="markup has changed"):
        archive_url("<html><body>Under maintenance</body></html>")


# -- collecting -------------------------------------------------------------


class FakeResponse:
    def __init__(self, url: str, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.content = body
        self.headers = headers or {}

    @property
    def text(self) -> str:
        # Lazily, and tolerantly: one of these responses is a ZIP, and httpx
        # only decodes a body when something asks for its text.
        return self.content.decode(errors="replace")


class FakeHttp:
    def __init__(self, pages: dict[str, FakeResponse]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.requested.append(url)
        return self.pages[url]


@pytest.fixture
def download(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    http = FakeHttp(
        {
            organized_violence.DOWNLOADS_PAGE: FakeResponse(
                organized_violence.DOWNLOADS_PAGE, PAGE.encode()
            ),
            ARCHIVE_URL: FakeResponse(
                ARCHIVE_URL,
                build_archive(),
                {"last-modified": "Mon, 08 Jun 2026 20:31:08 GMT", "etag": '"1dbdab5"'},
            ),
        }
    )

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[FakeHttp]:
        yield http

    monkeypatch.setattr(organized_violence, "fetcher", fake_fetcher)
    return http


def test_the_archive_lands_as_published(download: FakeHttp) -> None:
    (artifact,) = list(OrganizedViolence().collect(ScrapeContext()))

    assert artifact.dataset == organized_violence.DATASET
    assert artifact.filename == "organizedviolencecy-261-csv.zip"
    assert artifact.source_url == ARCHIVE_URL
    assert artifact.published_at == date(2026, 6, 8)


def test_the_edition_partitions_the_release(download: FakeHttp) -> None:
    """One archive per release, and the version is what a citation states."""
    (artifact,) = list(OrganizedViolence().collect(ScrapeContext()))

    assert artifact.partition == ("edition=26.1",)
    assert artifact.metadata["edition"] == "26.1"
    assert artifact.metadata["title"].endswith("26.1")


def test_a_release_older_than_since_is_not_landed(download: FakeHttp) -> None:
    artifacts = list(OrganizedViolence().collect(ScrapeContext(since=date(2026, 9, 1))))

    assert artifacts == []


def test_the_release_is_landed_when_since_predates_it(download: FakeHttp) -> None:
    artifacts = list(OrganizedViolence().collect(ScrapeContext(since=date(2026, 1, 1))))

    assert len(artifacts) == 1


# -- reading the table ------------------------------------------------------

#: Every column the extractor reads, plus the ones that say whose year a row
#: is. Built from the measures themselves: a test listing them by hand would
#: pass while the reader looked for a column the file no longer has.
COLUMNS = ("country", "country_id", "year", *(m.column for m in MEASURES), "Version")


def _row(country: str, country_id: str, year: str, **figures: str) -> str:
    values = {"country": country, "country_id": country_id, "year": year, "Version": "26.1"}
    values.update(figures)
    return ",".join(values.get(column, "0") for column in COLUMNS)


def build_archive(**overrides: str) -> bytes:
    """The country-year archive in miniature: two countries, three years."""
    lines = [
        ",".join(COLUMNS),
        _row(
            "Indonesia",
            "850",
            "2024",
            sb_total_deaths_best="57",
            sb_total_deaths_low="39",
            sb_total_deaths_high="63",
            os_total_deaths_best="23",
            cumulative_total_deaths_in_orgvio_best="80",
            cumulative_total_deaths_civilians_in_orgvio="30",
            sb_dyad_count="1",
        ),
        # A year of zeroes: Indonesia has had no non-state conflict since 2016,
        # which is a finding rather than a missing figure.
        _row("Indonesia", "850", "2025", sb_total_deaths_best="104", **overrides),
        _row("Malaysia", "820", "2024", sb_total_deaths_best="4"),
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("OrganizedViolenceCYDataSet26_1.csv", "\n".join(lines))
    return buffer.getvalue()


def landed_archive(tmp_path: Path, content: bytes | None = None) -> Landed:
    path = tmp_path / "organizedviolencecy-261-csv.zip"
    path.write_bytes(content if content is not None else build_archive())
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="ucdp-organized-violence",
        dataset="organized-violence",
        extra={"edition": "26.1"},
    )


def columns_of(rows: list[dict], indicator: str) -> list[dict[str, str]]:
    return [row["columns"] for row in rows if row["columns"]["indicator"] == indicator]


@pytest.fixture
def rows(tmp_path: Path) -> list[dict]:
    return list(UcdpOrganizedViolenceExtractor().extract(landed_archive(tmp_path)))


def test_only_indonesia_is_kept(rows: list[dict]) -> None:
    """The archive is global and the warehouse is not. Malaysia stays in RAW."""
    assert {row["columns"]["country"] for row in rows} == {"Indonesia"}


def test_every_measure_becomes_its_own_indicator(rows: list[dict]) -> None:
    """One column counts the dead in state-based conflict and the next counts
    the dyads that fought. Landing them on one indicator would chart 57 beside
    1 and call both organized violence."""
    assert {row["columns"]["indicator"] for row in rows} == {m.indicator for m in MEASURES}


def test_the_year_becomes_the_period(rows: list[dict]) -> None:
    state_based = columns_of(rows, "ucdp_state_based_deaths")

    assert [(row["year"], row["value"]) for row in state_based] == [("2024", "57"), ("2025", "104")]


def test_the_bounds_are_their_own_series(rows: list[dict]) -> None:
    """A death toll in a conflict is a range, and Silver observations carry a
    value rather than one. Published apart, the range is still readable."""
    low = columns_of(rows, "ucdp_state_based_deaths_low")
    high = columns_of(rows, "ucdp_state_based_deaths_high")

    assert (low[0]["value"], high[0]["value"]) == ("39", "63")
    assert (low[0]["bound"], high[0]["bound"]) == ("low", "high")


def test_a_zero_is_a_figure(rows: list[dict]) -> None:
    """Indonesia has had no non-state conflict deaths since 2016. Dropping the
    zeroes would leave a gap where the finding is."""
    non_state = columns_of(rows, "ucdp_non_state_deaths")

    assert [row["value"] for row in non_state] == ["0", "0"]


def test_an_empty_cell_yields_no_record(tmp_path: Path) -> None:
    """Which a zero would claim UCDP counted and found none."""
    landed = landed_archive(tmp_path, build_archive(os_total_deaths_best=""))
    rows = list(UcdpOrganizedViolenceExtractor().extract(landed))

    assert [row["year"] for row in columns_of(rows, "ucdp_one_sided_deaths")] == ["2024"]


def test_the_unit_travels_per_series(rows: list[dict]) -> None:
    """One series counts the dead and the next counts the conflicts they died
    in. Asserting one unit would say twelve people where twelve wars were."""
    assert columns_of(rows, "ucdp_state_based_deaths")[0]["unit"] == "deaths"
    assert columns_of(rows, "ucdp_state_based_dyads")[0]["unit"] == "dyads"


def test_the_release_rides_on_every_row(rows: list[dict]) -> None:
    """UCDP revises past years, so a figure that does not say which release it
    came from cannot be reconciled with a published table."""
    assert {row["columns"]["edition"] for row in rows} == {"26.1"}


def test_a_table_missing_a_column_is_an_error(tmp_path: Path) -> None:
    """A series that stops arriving looks from the portal exactly like a
    country where the violence stopped."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("OrganizedViolenceCY.csv", "country,country_id,year\nIndonesia,850,2025")

    landed = landed_archive(tmp_path, buffer.getvalue())

    with pytest.raises(ExtractionError, match="reshaped"):
        list(UcdpOrganizedViolenceExtractor().extract(landed))


def test_a_release_without_indonesia_is_an_error(tmp_path: Path) -> None:
    """Rather than an empty extraction, which reads downstream as a year
    nobody was killed."""
    landed = landed_archive(tmp_path)
    extractor = UcdpOrganizedViolenceExtractor(country_id="999")

    with pytest.raises(ExtractionError, match="country numbering"):
        list(extractor.extract(landed))


def test_an_archive_without_a_csv_is_an_error(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "see the codebook")

    with pytest.raises(ExtractionError, match="no CSV"):
        list(UcdpOrganizedViolenceExtractor().extract(landed_archive(tmp_path, buffer.getvalue())))


def test_the_extractor_claims_only_its_own_archives(tmp_path: Path) -> None:
    """`ZippedWorkbookExtractor` claims every `.zip`, so this one has to be
    specific about which archives are its own."""
    landed = landed_archive(tmp_path)
    other = Landed(
        path=landed.path,
        document_id="doc_other",
        content_hash="1" * 64,
        source_slug="bi-consumer-survey",
    )

    assert UcdpOrganizedViolenceExtractor().handles(landed)
    assert not UcdpOrganizedViolenceExtractor().handles(other)
