"""Exchange rates: what is collected, and what the chart reader makes of it.

The pairs are a declaration, and the two things that can go wrong with a
declaration are a ticker nobody checked and a key that collides with another
pair's. Both are checked here. The live symbols themselves were verified
against Yahoo before being written down; what a test can hold is that the
table stays internally consistent and that a run asks for every row of it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import pytest

from terusan_pipelines.extract import Landed, YahooChartExtractor
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.yahoo_finance import currencies
from terusan_pipelines.sources.yahoo_finance.currencies import (
    DATASET,
    PAIRS,
    IndonesianExchangeRates,
)

# -- the declaration --------------------------------------------------------


def test_every_pair_has_its_own_ticker_and_key() -> None:
    """A repeated key would have two pairs normalizing into one series, and the
    second would quietly overwrite the first."""
    assert len({pair.symbol for pair in PAIRS}) == len(PAIRS)
    assert len({pair.key for pair in PAIRS}) == len(PAIRS)


def test_the_five_the_rupiah_is_read_against_are_all_here() -> None:
    assert {"usd-idr", "eur-idr", "jpy-idr", "gbp-idr", "usd-cny"} <= {p.key for p in PAIRS}


def test_the_asean_crosses_yahoo_carries_are_all_here() -> None:
    assert {"sgd-idr", "myr-idr", "thb-idr"} <= {p.key for p in PAIRS}


def test_the_yuan_is_quoted_against_the_dollar_and_says_so() -> None:
    """Yahoo has no yuan / rupiah history, so this pair is on the other basis.
    A reader who missed that would read 6.7 as rupiah."""
    (yuan,) = [pair for pair in PAIRS if pair.key == "usd-cny"]

    assert yuan.symbol == "USDCNY=X"
    assert "rupiah" in yuan.notes


# -- collecting -------------------------------------------------------------


def chart(symbol: str, currency: str, gmtoffset: int = 0) -> dict[str, Any]:
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": symbol,
                        "currency": currency,
                        "exchangeTimezoneName": "Europe/London",
                        "gmtoffset": gmtoffset,
                    },
                    "timestamp": [1632096000, 1632182400],
                    "indicators": {
                        "quote": [
                            {
                                "open": [14250.0, None],
                                "high": [14275.0, None],
                                "low": [14230.0, None],
                                "close": [14260.0, None],
                                "volume": [0, None],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


class FakeResponse:
    def __init__(self, url: str, body: dict[str, Any]) -> None:
        self.url = url
        self.body = body

    def json(self) -> dict[str, Any]:
        return self.body


class FakeHttp:
    def __init__(self) -> None:
        self.requested: list[tuple[str, dict[str, Any]]] = []
        self.answer: dict[str, Any] | None = None

    def get(self, url: str, params: dict[str, Any] | None = None, **kwargs: object) -> FakeResponse:
        self.requested.append((url, dict(params or {})))
        symbol = url.rsplit("/", 1)[-1]
        return FakeResponse(url, self.answer or chart(symbol, "IDR"))


@pytest.fixture
def yahoo(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    http = FakeHttp()

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[FakeHttp]:
        yield http

    monkeypatch.setattr(currencies, "fetcher", fake_fetcher)
    return http


def test_one_artifact_per_pair_all_under_one_dataset(yahoo: FakeHttp) -> None:
    """Eight collections that happen to sit beside each other is not what a
    reader asking for the exchange rate wants."""
    artifacts = list(IndonesianExchangeRates().collect(ScrapeContext()))

    assert len(artifacts) == len(PAIRS)
    assert {a.dataset for a in artifacts} == {DATASET}
    assert [a.filename for a in artifacts] == [f"{pair.key}.json" for pair in PAIRS]


def test_the_ticker_travels_with_the_bytes(yahoo: FakeHttp) -> None:
    """Normalization selects a pair by ticker, so a file that arrived without
    one could not be split back out."""
    artifacts = list(IndonesianExchangeRates().collect(ScrapeContext()))

    assert [a.metadata["symbol"] for a in artifacts] == [pair.symbol for pair in PAIRS]


def test_a_full_pull_asks_for_five_years(yahoo: FakeHttp) -> None:
    list(IndonesianExchangeRates().collect(ScrapeContext()))

    _, params = yahoo.requested[0]
    years = (params["period2"] - params["period1"]) / (365 * 24 * 3600)

    assert round(years) == 5
    assert params["interval"] == "1d"


def test_an_incremental_run_starts_where_it_was_told(yahoo: FakeHttp) -> None:
    list(IndonesianExchangeRates().collect(ScrapeContext(since=date(2026, 9, 1))))

    _, params = yahoo.requested[0]

    assert date.fromtimestamp(params["period1"]) == date(2026, 9, 1)


def test_an_unknown_ticker_fails_rather_than_landing_an_error_object(yahoo: FakeHttp) -> None:
    """Yahoo answers 200 with an error body for a symbol it does not carry, so
    the status cannot be what a run is judged on."""
    yahoo.answer = {"chart": {"result": None, "error": {"code": "Not Found"}}}

    with pytest.raises(ValueError, match="no chart data"):
        list(IndonesianExchangeRates().collect(ScrapeContext()))


# -- reading it back --------------------------------------------------------


def test_a_landed_rate_transposes_into_dated_rows(tmp_path) -> None:
    """The same extractor the commodities use: it keys off the `yahoo-` prefix,
    so an exchange rate needs no reader of its own."""
    path = tmp_path / "eur-idr.json"
    path.write_bytes(json.dumps(chart("EURIDR=X", "IDR")).encode())
    landed = Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="yahoo-exchange-rates",
        dataset=DATASET,
    )

    extractor = YahooChartExtractor()
    assert extractor.handles(landed)

    rows = list(extractor.extract(landed))

    assert [r["columns"]["symbol"] for r in rows] == ["EURIDR=X", "EURIDR=X"]
    assert rows[0]["columns"]["date"] == "2021-09-20"
    assert rows[0]["columns"]["close"] == "14260.0"
    assert rows[0]["columns"]["currency"] == "IDR"
    # A weekend arrives dated and unpriced. Dropping it would close the gap and
    # make five sessions look like seven.
    assert rows[1]["columns"]["close"] == ""


def test_a_summer_rate_is_dated_by_the_london_session_not_by_utc(tmp_path) -> None:
    """Yahoo keeps foreign exchange on Europe/London and stamps each bar at
    local midnight. Under British Summer Time that instant is 23:00 UTC the day
    before, so reading the epoch as UTC files Monday's rate as Sunday — and a
    trading week of five rates as Sunday through Thursday."""
    body = chart("IDR=X", "IDR", gmtoffset=3600)
    # 2021-09-26T23:00:00Z — midnight in London on Monday the 27th.
    body["chart"]["result"][0]["timestamp"] = [1632697200, 1632783600]
    path = tmp_path / "usd-idr.json"
    path.write_bytes(json.dumps(body).encode())
    landed = Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="yahoo-exchange-rates",
        dataset=DATASET,
    )

    rows = list(YahooChartExtractor().extract(landed))

    assert [r["columns"]["date"] for r in rows] == ["2021-09-27", "2021-09-28"]


def test_an_exchange_that_states_no_offset_is_still_read_as_utc(tmp_path) -> None:
    """The reading every other instrument has always had. Jakarta opens at
    09:00 and New York in the morning, so the stamp already falls on the
    session's own date and the offset changes nothing."""
    body = chart("^JKSE", "IDR")
    del body["chart"]["result"][0]["meta"]["gmtoffset"]
    body["chart"]["result"][0]["timestamp"] = [1632096000]
    path = tmp_path / "ihsg.json"
    path.write_bytes(json.dumps(body).encode())
    landed = Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="yahoo-ihsg",
        dataset="ihsg",
    )

    (row,) = list(YahooChartExtractor().extract(landed))

    assert row["columns"]["date"] == "2021-09-20"


def test_the_running_quote_folds_into_the_session_it_belongs_to(tmp_path) -> None:
    """Yahoo sends the session it is still quoting twice: once as its own bar,
    open and high and low filled and the close still null, and again as a final
    timestamp carrying the live price. Two rows for one day is not one record
    per trading day, and Silver refuses a period holding two figures."""
    body = chart("IDR=X", "IDR", gmtoffset=3600)
    result = body["chart"]["result"][0]
    # Midnight London on the 22nd and the 23rd, then the 23rd still running.
    result["timestamp"] = [1790031600, 1790118000, 1790149998]
    result["indicators"]["quote"][0] = {
        "open": [17843.0, 17878.0, 17878.0],
        "high": [17899.0, 17878.0, 17878.0],
        "low": [17779.8, 17783.0, 17783.0],
        "close": [17846.0, None, 17795.0],
        "volume": [0, 0, 0],
    }
    path = tmp_path / "usd-idr.json"
    path.write_bytes(json.dumps(body).encode())
    landed = Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="yahoo-exchange-rates",
        dataset=DATASET,
    )

    rows = list(YahooChartExtractor().extract(landed))

    assert [r["columns"]["date"] for r in rows] == ["2026-09-22", "2026-09-23"]
    assert [r["row_number"] for r in rows] == [1, 2]
    assert rows[1]["columns"]["close"] == "17795.0"
    assert rows[1]["columns"]["open"] == "17878.0"


def test_a_holiday_quoted_once_and_never_priced_stays_unpriced(tmp_path) -> None:
    """Superseding must not become filling in: a day Yahoo dates and never
    prices is a shut market, and inventing a close would close the gap."""
    body = chart("IDR=X", "IDR", gmtoffset=3600)
    result = body["chart"]["result"][0]
    result["timestamp"] = [1790031600, 1790118000]
    result["indicators"]["quote"][0] = {
        "open": [17843.0, None],
        "high": [17899.0, None],
        "low": [17779.8, None],
        "close": [17846.0, None],
        "volume": [0, None],
    }
    path = tmp_path / "usd-idr.json"
    path.write_bytes(json.dumps(body).encode())
    landed = Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="yahoo-exchange-rates",
        dataset=DATASET,
    )

    rows = list(YahooChartExtractor().extract(landed))

    assert len(rows) == 2
    assert rows[1]["columns"]["date"] == "2026-09-23"
    assert rows[1]["columns"]["close"] == ""
