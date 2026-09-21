"""Trading Economics: crawling the Indonesian indicator pages, and reading them.

Every test runs offline against fixture markup cut down from the real pages —
the same tag soup, single-quoted hrefs and header rows included, because those
are exactly what the parsing has to survive.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from terusan_pipelines.extract import Landed, TradingEconomicsExtractor
from terusan_pipelines.extract.trading_economics import indicator_key, indicator_title
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.trading_economics import indonesia
from terusan_pipelines.sources.trading_economics.indonesia import (
    TradingEconomicsIndonesia,
    indicator_slugs,
)

INDEX_HTML = """
<html><body>
<table class="table table-hover">
  <thead><tr><th></th><th>Terakhir</th><th>Sebelum Ini</th>
    <th>Tertinggi</th><th>Paling Rendah</th><th></th><th></th></tr></thead>
  <tbody>
    <tr><td><a href='/indonesia/currency'> Mata Uang </a></td>
      <td>17758</td><td>17774</td><td>18279</td><td>2096</td><td> </td><td>2026-09</td></tr>
    <tr><td><a href='/indonesia/inflation-cpi'> Tingkat Inflasi </a></td>
      <td>3.19</td><td>2.37</td><td>82.40</td><td>-1.17</td><td>Persen</td><td>2026-08</td></tr>
  </tbody>
</table>
<table class="table">
  <tr><td><a href='/indonesia/inflation-cpi'> Tingkat Inflasi </a></td>
    <td>3.19</td><td>2.37</td><td>82.40</td><td>-1.17</td><td>Persen</td><td>2026-08</td></tr>
</table>
<a href='/indonesia/calendar'>Kalender</a>
<a href='/indonesia/forecast'>Prakiraan</a>
<a href='/indonesia/indicators'>Indikator</a>
<a href="https://de.tradingeconomics.com/indonesia/indicators">de</a>
</body></html>
"""

DETAIL_HTML = """
<html><body>
<table class="table table-hover" id="calendar">
  <thead><tr><th>Kalender</td><th>GMT</th><th colspan="2">Referensi</th></th>
    <th>Realisasi</th><th>Sebelum Ini</th><th>Kesepakatan</th></tr></thead>
  <tr data-country="Indonesia" data-category="Foreign Exchange Reserves">
    <td>2026-08-07</td><td>03:00 AM</td>
    <td><div class='d-none'> Cadangan Devisa </div></td>
    <td id="reference"> Jul</td><td>$145.3B</td><td>$145.6B</td><td></td></tr>
  <tr><td>2026-09-07</td><td>03:00 AM</td>
    <td><div class='d-none'> Cadangan Devisa </div></td>
    <td id="reference"> Aug</td><td>$146.5B</td><td>$145.3B</td><td>$146.0B</td></tr>
</table>

<table class="table table-hover">
  <thead><tr><th></th><th>Terakhir</th><th>Sebelum Ini</th>
    <th>Satuan</th><th>Referensi</th></tr></thead>
  <tr class='datatable-row'>
    <td><a href='/indonesia/cash-reserve-ratio'>Rasio Persediaan Tunai</a></td>
    <td>9.00</td><td>9.00</td><td>Persen</td><td>Aug 2026</td></tr>
  <tr class='datatable-row'>
    <td><a href='/indonesia/foreign-exchange-reserves'>Cadangan Devisa</a></td>
    <td>146500.00</td><td>145300.00</td><td>Usd - Juta</td><td>Aug 2026</td></tr>
</table>

<table class="table" style="margin-bottom: 0px;">
  <thead><tr><th></th><th>Realisasi</th><th>Sebelum Ini</th><th>Tertinggi</th>
    <th>Paling Rendah</th><th>Tanggal</th><th>Satuan</th><th>Frekuensi</th><th></th></tr></thead>
  <tr><td></td><td>146500.00</td><td>145300.00</td><td>157090.00</td><td>27404.30</td>
    <td>2000 - 2026</td><td>Usd - Juta</td><td>Bulanan</td><td>Current Prices, NSA</td></tr>
</table>
</body></html>
"""


QUOTE_HTML = """
<html><body>
<table class="table table-hover">
  <thead><tr><th></th><th>Harga</th><th>Hari</th><th>%</th><th>Tahunan</th><th>Tanggal</th></tr></thead>
  <tr><td><a href='/indonesia/currency'>IDR</a></td>
    <td>17758</td><td>79.00</td><td>0.45%</td><td>8.62%</td><td>Sep/19</td></tr>
</table>
</body></html>
"""


def landed(tmp_path: Path, html: str, dataset: str) -> Landed:
    path = tmp_path / f"{dataset}.html"
    path.write_text(html)
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="abc",
        source_slug="tradingeconomics-indonesia",
        media_type="text/html",
        dataset=dataset,
    )


# -- discovery --------------------------------------------------------------


def test_indicator_slugs_skips_the_pages_that_are_not_indicators() -> None:
    slugs = indicator_slugs(INDEX_HTML)
    assert slugs == ["currency", "inflation-cpi"]


def test_indicator_slugs_are_sorted_and_deduplicated() -> None:
    # The index lists the headline indicators twice, once in their own
    # category and once in the summary table at the top.
    assert indicator_slugs(INDEX_HTML).count("inflation-cpi") == 1


# -- extraction -------------------------------------------------------------


def test_index_page_yields_one_row_per_indicator(tmp_path: Path) -> None:
    rows = list(
        TradingEconomicsExtractor().extract(landed(tmp_path, INDEX_HTML, "indonesia-indicators"))
    )

    assert [row["columns"]["indicator"] for row in rows] == ["currency", "inflation-cpi"]
    inflation = rows[1]["columns"]
    assert inflation["kind"] == "index"
    assert inflation["indicator_name"] == "Tingkat Inflasi"
    assert inflation["last"] == "3.19"
    assert inflation["unit"] == "Persen"
    assert inflation["reference"] == "2026-08"
    assert rows[0]["row_number"] == 1


def test_every_row_carries_the_country_the_page_is_for(tmp_path: Path) -> None:
    # Read off the URL rather than assumed: without it Silver cannot resolve
    # the figures to a place, and they normalize with no geography at all.
    page = landed(tmp_path, INDEX_HTML, "indonesia-indicators")
    page = Landed(
        path=page.path,
        document_id=page.document_id,
        content_hash=page.content_hash,
        source_slug=page.source_slug,
        source_url="https://id.tradingeconomics.com/indonesia/indicators",
        media_type=page.media_type,
        dataset=page.dataset,
    )

    rows = list(TradingEconomicsExtractor().extract(page))

    assert rows, "the index yields rows"
    assert all(row["columns"]["country"] == "Indonesia" for row in rows)


def test_a_page_without_a_url_still_names_its_country(tmp_path: Path) -> None:
    rows = list(
        TradingEconomicsExtractor().extract(landed(tmp_path, INDEX_HTML, "indonesia-indicators"))
    )

    assert all(row["columns"]["country"] == "Indonesia" for row in rows)


def test_detail_page_yields_the_calendar_and_the_summary(tmp_path: Path) -> None:
    rows = list(
        TradingEconomicsExtractor().extract(
            landed(tmp_path, DETAIL_HTML, "foreign-exchange-reserves")
        )
    )
    kinds = [row["columns"]["kind"] for row in rows]
    assert kinds == ["calendar", "calendar", "reading", "summary"]

    release = rows[1]["columns"]
    assert release["released_on"] == "2026-09-07"
    assert release["reference"] == "Aug"
    assert release["actual"] == "$146.5B"
    assert release["consensus"] == "$146.0B"

    summary = rows[3]["columns"]
    assert summary["actual"] == "146500.00"
    assert summary["highest"] == "157090.00"
    assert summary["unit"] == "Usd - Juta"
    assert summary["frequency"] == "Bulanan"
    # A column the page prints without a header keeps its position rather than
    # being dropped.
    assert summary["column_8"] == "Current Prices, NSA"


def test_the_neighbouring_indicators_are_not_extracted(tmp_path: Path) -> None:
    rows = list(
        TradingEconomicsExtractor().extract(
            landed(tmp_path, DETAIL_HTML, "foreign-exchange-reserves")
        )
    )
    # Each of those has a page of its own in the same run; landing them here
    # would put one reading in the lake a dozen times over.
    assert not any("Rasio Persediaan Tunai" in str(row["columns"]) for row in rows)


def test_a_page_yields_its_own_row_among_its_neighbours(tmp_path: Path) -> None:
    # The precise figure, and the period it belongs to. The index page rounds
    # to three significant digits and the summary block is dated "2000 - 2026",
    # so this row is the only place both facts appear together.
    rows = list(
        TradingEconomicsExtractor().extract(
            landed(tmp_path, DETAIL_HTML, "foreign-exchange-reserves")
        )
    )
    reading = next(row for row in rows if row["columns"]["kind"] == "reading")

    assert reading["columns"]["last"] == "146500.00"
    assert reading["columns"]["previous"] == "145300.00"
    assert reading["columns"]["unit"] == "Usd - Juta"
    assert reading["columns"]["reference"] == "Aug 2026"
    assert reading["columns"]["indicator_name"] == "Cadangan Devisa"
    # Filed under the collection, not under the page: one country's indicators
    # are one dataset, not a hundred datasets of one series each.
    assert reading["dataset"] == "indonesia-indicators"


def test_a_quote_table_is_not_read_as_a_reading(tmp_path: Path) -> None:
    # The currency and stock market pages print a live quote table where the
    # others print their neighbours. Its fourth column is the day's change,
    # and reading it as a period would date a figure by "0.45%".
    rows = list(TradingEconomicsExtractor().extract(landed(tmp_path, QUOTE_HTML, "currency")))

    assert not any(row["columns"]["kind"] == "reading" for row in rows)


def test_every_row_names_the_series_it_belongs_to(tmp_path: Path) -> None:
    rows = list(
        TradingEconomicsExtractor().extract(
            landed(tmp_path, DETAIL_HTML, "foreign-exchange-reserves")
        )
    )

    assert all(row["columns"]["indicator_key"] == "te_foreign_exchange_reserves" for row in rows)
    assert all(row["columns"]["indicator_title"] == "Foreign Exchange Reserves" for row in rows)


def test_an_identifier_names_the_vendor_that_published_it() -> None:
    # Trading Economics carries Bank Indonesia's figures at its own revision;
    # an unprefixed key would eventually collide with the agency's own series.
    assert indicator_key("housing-index") == "te_housing_index"


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("housing-index", "Housing Index"),
        ("gdp-growth-annual", "GDP Growth Annual"),
        ("balance-of-trade", "Balance of Trade"),
        ("house-price-index-yoy", "House Price Index YoY"),
        ("money-supply-m2", "Money Supply M2"),
        ("social-security-rate-for-companies", "Social Security Rate for Companies"),
    ],
)
def test_a_title_reads_as_a_name_rather_than_a_slug(slug: str, expected: str) -> None:
    # Taken from the URL, which is the English name on every language edition:
    # this is the Indonesian one, and its tables say "Indeks Perumahan".
    assert indicator_title(slug) == expected


def test_values_are_left_as_text(tmp_path: Path) -> None:
    rows = list(
        TradingEconomicsExtractor().extract(
            landed(tmp_path, DETAIL_HTML, "foreign-exchange-reserves")
        )
    )
    assert all(isinstance(value, str) for row in rows for value in row["columns"].values())


def test_only_this_source_is_claimed(tmp_path: Path) -> None:
    extractor = TradingEconomicsExtractor()
    mine = landed(tmp_path, INDEX_HTML, "indonesia-indicators")
    assert extractor.handles(mine)

    other = Landed(
        path=mine.path,
        document_id="doc_test",
        content_hash="abc",
        source_slug="bps-inflation",
        media_type="text/html",
    )
    assert not extractor.handles(other)


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


@pytest.fixture
def crawl(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    http = FakeHttp(
        {
            indonesia.INDEX_URL: INDEX_HTML,
            indonesia.indicator_url("currency"): DETAIL_HTML,
            # inflation-cpi is deliberately absent: an indicator the index
            # still links to after the page has gone.
        }
    )

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[FakeHttp]:
        yield http

    monkeypatch.setattr(indonesia, "fetcher", fake_fetcher)
    return http


def test_collect_lands_the_index_and_every_indicator(crawl: FakeHttp) -> None:
    artifacts = list(TradingEconomicsIndonesia().collect(ScrapeContext()))

    assert [a.dataset for a in artifacts] == ["indonesia-indicators", "currency"]
    assert all(a.media_type == "text/html" for a in artifacts)
    assert artifacts[0].metadata["indicator_count"] == 2
    assert artifacts[1].partition[0].startswith("year=")


def test_a_missing_indicator_page_does_not_end_the_run(crawl: FakeHttp) -> None:
    artifacts = list(TradingEconomicsIndonesia().collect(ScrapeContext()))

    assert indonesia.indicator_url("inflation-cpi") in crawl.requested
    assert "inflation-cpi" not in [a.dataset for a in artifacts]


def test_limit_stops_the_crawl(crawl: FakeHttp) -> None:
    artifacts = list(TradingEconomicsIndonesia().collect(ScrapeContext(limit=1)))

    # The index counts towards the limit, so a limit of one fetches nothing else.
    assert [a.dataset for a in artifacts] == ["indonesia-indicators"]
    assert crawl.requested == [indonesia.INDEX_URL]


def test_an_index_that_lists_nothing_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHttp({indonesia.INDEX_URL: "<html><body>no links</body></html>"})

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[FakeHttp]:
        yield http

    monkeypatch.setattr(indonesia, "fetcher", fake_fetcher)

    with pytest.raises(ValueError, match="changed shape"):
        list(TradingEconomicsIndonesia().collect(ScrapeContext()))


def test_an_index_that_lists_the_whole_site_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    many = "".join(
        f"<a href='/indonesia/indicator-{n}'>x</a>" for n in range(indonesia.MAX_INDICATORS + 1)
    )
    http = FakeHttp({indonesia.INDEX_URL: f"<html><body>{many}</body></html>"})

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[FakeHttp]:
        yield http

    monkeypatch.setattr(indonesia, "fetcher", fake_fetcher)

    with pytest.raises(ValueError, match="refusing to crawl"):
        list(TradingEconomicsIndonesia().collect(ScrapeContext()))


RATING_HTML = """
<html><body>
<table class="table table-hover" id="ctl00_GridView1">
  <tr bgcolor="WhiteSmoke"><th scope="col">Agency</th><th scope="col">Rating</th>
    <th scope="col">Outlook</th><th scope="col">Date</th></tr>
  <tr><td><b>S&amp;P</b></td><td class="bold">BBB</td>
    <td><span style="color: black">Stable</span></td><td>Apr 27 2022</td></tr>
  <tr><td><b>Moody's</b></td><td class="bold">Baa2</td>
    <td><span>Stable</span></td><td>Feb 10 2018</td></tr>
</table>
</body></html>
"""

#: The manufacturing PMI's page, which carries neither a summary block nor its
#: own row: the series is licensed from S&P Global and shown to subscribers only.
PEERS_ONLY_HTML = """
<html><body>
<table class="table table-hover">
  <thead><tr><th></th><th>Terakhir</th><th>Sebelum Ini</th>
    <th>Satuan</th><th>Referensi</th></tr></thead>
  <tr><td><a href='/indonesia/business-confidence'>Indeks Keyakinan Bisnis</a></td>
    <td>12.97</td><td>10.11</td><td>Poin</td><td>Jun 2026</td></tr>
</table>
</body></html>
"""


def test_the_credit_rating_page_yields_one_row_per_rating(tmp_path: Path) -> None:
    rows = list(TradingEconomicsExtractor().extract(landed(tmp_path, RATING_HTML, "rating")))

    assert [row["columns"]["kind"] for row in rows] == ["rating", "rating"]
    first = rows[0]["columns"]
    assert first["agency"] == "S&P"
    assert first["rating"] == "BBB"
    assert first["outlook"] == "Stable"
    assert first["rated_on"] == "Apr 27 2022"
    assert rows[1]["columns"]["agency"] == "Moody's"


def test_a_page_with_only_its_neighbours_yields_nothing(tmp_path: Path) -> None:
    rows = list(
        TradingEconomicsExtractor().extract(landed(tmp_path, PEERS_ONLY_HTML, "manufacturing-pmi"))
    )

    # Silence rather than a neighbour's figure under this indicator's name.
    # The reading comes from the index page instead.
    assert rows == []
