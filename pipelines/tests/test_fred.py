"""FRED: reading the search listing, and reading a series download.

Every test runs offline against markup cut down from the real search pages —
the whitespace, the meta line that holds three facts separated by commas, and
the coverage span with its parenthetical update, because those are what the
parsing has to survive.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from terusan_pipelines.extract import FredExtractor, FredSeriesPageExtractor, Landed
from terusan_pipelines.extract.fred import indicator_id, period_label
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.fred import indonesia
from terusan_pipelines.sources.fred.indonesia import (
    FredIndonesia,
    csv_url,
    search_results,
    search_url,
    series_url,
)

SEARCH_HTML = """
<html><body>
<div id="search-results">
  <ul id="search-results-list">
    <li class="search-list-item">
      <div class="search-series">
        <div class="search-series-title">
          <a href="/series/WUIIDN" aria-label="World Uncertainty Index for Indonesia"
             class="series-title search-series-title-gtm test-world-uncertainty-index">
            World Uncertainty Index for Indonesia
          </a>
        </div>
        <div class="search-series-meta">
          <span class="search-result-meta">
            Index,
            Quarterly,
            Not Seasonally Adjusted                        </span>
          <span class="search-result-meta-dates float-end-search d-md-block">
            Q2 1952                            to
            Q1 2026                            (May 7)                        </span>
        </div>
        <div class="search-series-notes notes-two-line">
          <p>
            The World Uncertainty Index counts the word in country reports.
          </p>
        </div>
      </div>
    </li>
    <li class="search-list-item">
      <div class="search-series">
        <div class="search-series-title">
          <a href="/series/CCUSMA02IDM618N" class="series-title search-series-title-gtm">
            Currency Conversions: US Dollar Exchange Rate: Average of Daily Rates:
            National Currency: USD for Indonesia
          </a>
        </div>
        <div class="search-series-meta">
          <span class="search-result-meta">
            Growth rate same period previous year, Monthly, Not Seasonally Adjusted</span>
          <span class="search-result-meta-dates">Jan 1967 to Jul 2026 (2025-05-15)</span>
        </div>
      </div>
    </li>
    <li class="search-list-item">
      <div class="search-series">
        <div class="search-series-title">A release, not a series</div>
      </div>
    </li>
  </ul>
</div>
</body></html>
"""

EMPTY_HTML = (
    '<html><body><div id="search-results"><ul id="search-results-list"></ul></div></body></html>'
)

SERIES_PAGE_HTML = """
<html><head><title>World Uncertainty Index for Indonesia (WUIIDN) | FRED | St. Louis Fed</title>
</head><body>
<div id="notes-content" class="panel-body">
  <p class="col-12 col-md-6 float-start"><strong>Source:</strong>
    <a class="note-source series-source series-focus" rel="nofollow"
       href="https://worlduncertaintyindex.com" target="_blank">Ahir, Bloom and Furceri
    <i class="fas fa-external-link-alt" aria-hidden="true"></i></a>&nbsp;
  <p class="col-12 col-md-6 float-start"><strong>Source:</strong>
    <a class="note-source series-source series-focus" rel="nofollow"
       href="https://www.imf.org" target="_blank">International Monetary Fund
    <i class="fas fa-external-link-alt" aria-hidden="true"></i></a>&nbsp;
  <p class="col-12 col-md-6 float-start"><strong>Release:</strong>
    <a class="note-release series-release series-focus" rel="nofollow"
       href="https://worlduncertaintyindex.com" target="_blank">World Uncertainty Index
    <i class="fas fa-external-link-alt" aria-hidden="true"></i></a>&nbsp;
  <p class="series-notes mb-2">The index counts the word &quot;uncertain&quot; in country
    reports.<br><br>A higher number means more uncertainty. See
    <a href="https://www.policyuncertainty.com">the paper</a> for the method.</p>
</div>
</body></html>
"""

SERIES_CSV = b"""observation_date,WUIIDN
1952-04-01,0.0000000
1952-07-01,0.1011839
1952-10-01,.
"""


def landed(tmp_path: Path, *, name: str = "WUIIDN.csv", content: bytes = SERIES_CSV, **extra):
    path = tmp_path / name
    path.write_bytes(content)
    metadata = {
        "series_id": "WUIIDN",
        "title": "World Uncertainty Index for Indonesia",
        "units": "Index",
        "frequency": "Quarterly",
        "seasonal_adjustment": "Not Seasonally Adjusted",
    }
    metadata.update(extra)
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="abc",
        source_slug="fred-indonesia",
        source_url=series_url("WUIIDN"),
        media_type="text/csv",
        dataset=indonesia.SERIES_DATASET,
        extra=metadata,
    )


def landed_page(tmp_path: Path, html: str = SERIES_PAGE_HTML, **extra):
    path = tmp_path / "WUIIDN.html"
    path.write_text(html)
    metadata = {"series_id": "WUIIDN", "title": "World Uncertainty Index for Indonesia"}
    metadata.update(extra)
    return Landed(
        path=path,
        document_id="doc_page",
        content_hash="def",
        source_slug="fred-indonesia",
        source_url=series_url("WUIIDN"),
        media_type="text/html",
        dataset=indonesia.SERIES_PAGE_DATASET,
        extra=metadata,
    )


# -- the listing ------------------------------------------------------------


def test_search_results_read_every_series_on_the_page() -> None:
    results = search_results(SEARCH_HTML, page=1)

    assert [r.series_id for r in results] == ["WUIIDN", "CCUSMA02IDM618N"]
    assert results[0].title == "World Uncertainty Index for Indonesia"
    assert results[0].page == 1


def test_the_meta_line_is_split_from_the_right() -> None:
    """The units are the part that varies, and one of them carries a comma."""
    first, second = search_results(SEARCH_HTML)

    assert (first.units, first.frequency) == ("Index", "Quarterly")
    assert first.seasonal_adjustment == "Not Seasonally Adjusted"
    assert second.units == "Growth rate same period previous year"
    assert second.frequency == "Monthly"


def test_units_keep_the_commas_they_are_written_with() -> None:
    """FRED prints "Births per 1,000 Women Ages 15-19" as the units.

    Split on every comma, that becomes two fields and the series ends up
    measured in "000 Women Ages 15-19".
    """
    html = SEARCH_HTML.replace(
        "Growth rate same period previous year, Monthly, Not Seasonally Adjusted",
        "Births per 1,000 Women Ages 15-19, Annual, Not Seasonally Adjusted",
    )
    _, series = search_results(html)

    assert series.units == "Births per 1,000 Women Ages 15-19"
    assert series.frequency == "Annual"
    assert series.seasonal_adjustment == "Not Seasonally Adjusted"


def test_the_coverage_span_is_split_from_its_update() -> None:
    first, second = search_results(SEARCH_HTML)

    assert (first.observation_start, first.observation_end) == ("Q2 1952", "Q1 2026")
    # Kept as printed: FRED writes a month and a day, an ISO date or "13 hours
    # ago" depending on how recent it is, and the missing year is not ours to
    # invent.
    assert first.updated == "May 7"
    assert second.updated == "2025-05-15"


def test_a_result_block_with_no_series_link_is_not_a_series() -> None:
    assert len(search_results(SEARCH_HTML)) == 2


def test_a_title_spanning_lines_reads_as_one_line() -> None:
    _, currency = search_results(SEARCH_HTML)
    assert currency.title.startswith("Currency Conversions: US Dollar Exchange Rate")
    assert "\n" not in currency.title


# -- identifiers ------------------------------------------------------------


def test_the_indicator_id_is_a_short_code() -> None:
    """A FRED title is too long for a URL and too like its neighbours to cut."""
    identifier = indicator_id("NASDAQNQID55LMN")

    assert len(identifier) == 8
    assert identifier.isalnum()
    assert identifier.islower()


def test_the_indicator_id_is_the_same_every_run() -> None:
    """Normalization rebuilds an indicator from scratch each run.

    An identifier that changed between runs would orphan the previous run's
    partition and break every link anyone had saved.
    """
    assert indicator_id("WUIIDN") == indicator_id("WUIIDN")


def test_series_sharing_a_title_keep_separate_identifiers() -> None:
    """FRED publishes one measure at three frequencies under one title."""
    assert indicator_id("CCUSMA02IDM618N") != indicator_id("CCUSMA02IDQ618N")


# -- periods ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("published", "frequency", "expected"),
    [
        ("1952-04-01", "Quarterly", "1952-Q2"),
        ("1967-01-01", "Monthly", "1967-01"),
        ("1960-01-01", "Annual", "1960"),
        ("2020-07-01", "Semiannual", "2020-S2"),
        # A daily series is already dated by the day it refers to.
        ("2026-09-17", "Daily", "2026-09-17"),
        # Five-yearly is not annual, and saying so would date a figure to a
        # year it does not describe.
        ("2015-01-01", "5 Year", "2015-01-01"),
        ("2015-01-01", "", "2015-01-01"),
    ],
)
def test_a_date_is_restated_at_the_series_frequency(
    published: str, frequency: str, expected: str
) -> None:
    assert period_label(published, frequency) == expected


# -- extraction -------------------------------------------------------------


def test_every_observation_becomes_a_record(tmp_path: Path) -> None:
    rows = list(FredExtractor().extract(landed(tmp_path)))

    assert [row["columns"]["date"] for row in rows] == [
        "1952-04-01",
        "1952-07-01",
        "1952-10-01",
    ]
    assert rows[0]["row_number"] == 1
    assert rows[0]["dataset"] == indonesia.SERIES_DATASET


def test_the_listing_metadata_travels_onto_each_row(tmp_path: Path) -> None:
    """The CSV states none of it: its columns are the date and the series id."""
    rows = list(FredExtractor().extract(landed(tmp_path)))
    columns = rows[0]["columns"]

    assert columns["series_id"] == "WUIIDN"
    assert columns["units"] == "Index"
    assert columns["frequency"] == "Quarterly"
    assert columns["seasonal_adjustment"] == "Not Seasonally Adjusted"
    assert columns["indicator"] == indicator_id("WUIIDN")


def test_a_quarterly_series_is_dated_by_its_quarter(tmp_path: Path) -> None:
    rows = list(FredExtractor().extract(landed(tmp_path)))
    assert [row["columns"]["period"] for row in rows] == ["1952-Q2", "1952-Q3", "1952-Q4"]


def test_a_missing_observation_is_carried_across_as_written(tmp_path: Path) -> None:
    """FRED writes a lone dot. Bronze does not decide what it means."""
    rows = list(FredExtractor().extract(landed(tmp_path)))
    assert rows[-1]["columns"]["value"] == "."


def test_only_a_series_about_indonesia_is_called_indonesian(tmp_path: Path) -> None:
    elsewhere = list(
        FredExtractor().extract(landed(tmp_path, title="Real Gross Domestic Product for Malaysia"))
    )
    assert all(row["columns"]["country"] == "" for row in elsewhere)
    assert all(
        row["columns"]["country"] == "Indonesia"
        for row in FredExtractor().extract(landed(tmp_path))
    )


def test_a_series_that_only_trades_with_indonesia_is_not_indonesian(tmp_path: Path) -> None:
    """Utah's exports to Indonesia are Utah's figures."""
    rows = list(
        FredExtractor().extract(landed(tmp_path, title="Value of Exports to Indonesia from Utah"))
    )

    assert rows
    assert all(row["columns"]["country"] == "" for row in rows)


def test_an_empty_download_yields_nothing(tmp_path: Path) -> None:
    assert list(FredExtractor().extract(landed(tmp_path, content=b""))) == []
    assert list(FredExtractor().extract(landed(tmp_path, content=b"observation_date\n"))) == []


def test_only_this_source_is_claimed(tmp_path: Path) -> None:
    extractor = FredExtractor()
    mine = landed(tmp_path)
    assert extractor.handles(mine)

    other = Landed(
        path=mine.path,
        document_id="doc_test",
        content_hash="abc",
        source_slug="tradingeconomics-indonesia",
        media_type="text/csv",
    )
    assert not extractor.handles(other)


def test_the_search_pages_are_left_to_the_html_reader(tmp_path: Path) -> None:
    page = tmp_path / "search-page-01.html"
    page.write_text(SEARCH_HTML)
    listing = Landed(
        path=page,
        document_id="doc_test",
        content_hash="abc",
        source_slug="fred-indonesia",
        media_type="text/html",
        dataset=indonesia.SEARCH_DATASET,
    )
    assert not FredExtractor().handles(listing)


# -- series pages -----------------------------------------------------------


def test_a_series_page_yields_one_record(tmp_path: Path) -> None:
    """One record, not one per observation: it is a paragraph of prose, and
    copying it onto every figure says the same thing a thousand times."""
    rows = list(FredSeriesPageExtractor().extract(landed_page(tmp_path)))

    assert len(rows) == 1
    assert rows[0]["dataset"] == indonesia.SERIES_PAGE_DATASET
    assert rows[0]["columns"]["indicator"] == indicator_id("WUIIDN")


def test_the_page_names_every_agency_behind_the_series(tmp_path: Path) -> None:
    """FRED credits three for one series often enough."""
    columns = next(iter(FredSeriesPageExtractor().extract(landed_page(tmp_path))))["columns"]

    assert columns["publisher"] == "Ahir, Bloom and Furceri; International Monetary Fund"
    assert columns["release"] == "World Uncertainty Index"


def test_the_notes_keep_their_paragraphs(tmp_path: Path) -> None:
    """FRED writes them as <br><br>, and three paragraphs run together into a
    wall of prose nobody reads."""
    columns = next(iter(FredSeriesPageExtractor().extract(landed_page(tmp_path))))["columns"]

    assert columns["notes"].startswith('The index counts the word "uncertain" in country')
    assert "\n\n" in columns["notes"]
    assert "policyuncertainty" not in columns["notes"], "a link is markup, not prose"


def test_the_title_loses_the_code_and_the_site(tmp_path: Path) -> None:
    columns = next(iter(FredSeriesPageExtractor().extract(landed_page(tmp_path))))["columns"]

    assert columns["title"] == "World Uncertainty Index for Indonesia"


def test_the_search_listing_is_not_a_series_page(tmp_path: Path) -> None:
    """It is HTML from the same source, and it describes sixty series."""
    path = tmp_path / "search-page-01.html"
    path.write_text(SEARCH_HTML)
    listing = Landed(
        path=path,
        document_id="doc_test",
        content_hash="abc",
        source_slug="fred-indonesia",
        media_type="text/html",
        dataset=indonesia.SEARCH_DATASET,
    )

    assert not FredSeriesPageExtractor().handles(listing)
    assert FredSeriesPageExtractor().handles(landed_page(tmp_path))


# -- collection -------------------------------------------------------------


class FakeResponse:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text
        self.content = text.encode()


class FakeHttp:
    """Enough of `Fetcher` for a crawl, with a record of what was asked for."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.requested.append(url)
        return FakeResponse(url, self.pages[url])

    def try_get(self, url: str, **kwargs: object) -> FakeResponse | None:
        self.requested.append(url)
        page = self.pages.get(url)
        return None if page is None else FakeResponse(url, page)


def crawl_over(monkeypatch: pytest.MonkeyPatch, pages: dict[str, str]) -> FakeHttp:
    http = FakeHttp(pages)

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[FakeHttp]:
        yield http

    monkeypatch.setattr(indonesia, "fetcher", fake_fetcher)
    return http


@pytest.fixture
def crawl(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    return crawl_over(
        monkeypatch,
        {
            search_url(1): SEARCH_HTML,
            search_url(2): EMPTY_HTML,
            series_url("WUIIDN"): SERIES_PAGE_HTML,
            csv_url("WUIIDN"): SERIES_CSV.decode(),
            # CCUSMA02IDM618N is deliberately absent: a series the listing
            # still carries after the download has gone.
        },
    )


def test_collect_lands_the_listing_the_page_and_the_figures(crawl: FakeHttp) -> None:
    artifacts = list(FredIndonesia().collect(ScrapeContext()))

    # The page as well as the download: it is the only place FRED states who
    # publishes the series and what it counts.
    assert [a.dataset for a in artifacts] == [
        indonesia.SEARCH_DATASET,
        indonesia.SERIES_PAGE_DATASET,
        indonesia.SERIES_DATASET,
    ]
    assert artifacts[0].metadata["series_ids"] == ["WUIIDN", "CCUSMA02IDM618N"]
    assert artifacts[1].filename == "WUIIDN.html"
    assert artifacts[2].filename == "WUIIDN.csv"
    # The series page, not the CSV endpoint: this is what a citation points at.
    assert artifacts[2].source_url == series_url("WUIIDN")
    assert artifacts[2].metadata["frequency"] == "Quarterly"
    assert artifacts[2].partition[0].startswith("year=")


def test_an_exhausted_search_stops_the_crawl(crawl: FakeHttp) -> None:
    list(FredIndonesia().collect(ScrapeContext()))

    assert search_url(2) in crawl.requested
    # Page 2 came back empty, so pages 3 to 12 were never asked for.
    assert search_url(3) not in crawl.requested


def test_a_withdrawn_series_does_not_end_the_run(crawl: FakeHttp) -> None:
    artifacts = list(FredIndonesia().collect(ScrapeContext()))

    assert csv_url("CCUSMA02IDM618N") in crawl.requested
    assert len([a for a in artifacts if a.dataset == indonesia.SERIES_DATASET]) == 1


def test_limit_counts_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    """As it does for every other source, and as the runner enforces it."""
    crawl_over(
        monkeypatch,
        {
            search_url(1): SEARCH_HTML,
            search_url(2): EMPTY_HTML,
            series_url("CCUSMA02IDM618N"): SERIES_PAGE_HTML,
            csv_url("WUIIDN"): SERIES_CSV.decode(),
            csv_url("CCUSMA02IDM618N"): SERIES_CSV.decode(),
        },
    )

    artifacts = list(FredIndonesia().collect(ScrapeContext(limit=2)))

    assert [a.dataset for a in artifacts] == [
        indonesia.SEARCH_DATASET,
        indonesia.SERIES_PAGE_DATASET,
    ]
    # Sorted by identifier, so a limited run is comparable between crawls.
    assert artifacts[1].metadata["series_id"] == "CCUSMA02IDM618N"


def test_a_search_page_that_lists_nothing_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    crawl_over(monkeypatch, {search_url(1): "<html><body>moved</body></html>"})

    with pytest.raises(ValueError, match="markup has changed"):
        list(FredIndonesia().collect(ScrapeContext()))


def test_a_search_page_listing_the_whole_site_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = '<li class="search-list-item"><a href="/series/S{n}" class="series-title">S{n}</a></li>'
    many = "".join(item.format(n=n) for n in range(indonesia.MAX_RESULTS_PER_PAGE + 1))
    crawl_over(monkeypatch, {search_url(1): f"<html><body>{many}</body></html>"})

    with pytest.raises(ValueError, match="markup has changed"):
        list(FredIndonesia().collect(ScrapeContext()))
