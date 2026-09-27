"""Daily metal prices: Westmetall's LME tables, Sina's Chinese futures, Yahoo's
metals — and whether each reaches Bronze naming its date, its unit and its good.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from terusan_pipelines.extract import (
    DEFAULT_EXTRACTORS,
    Landed,
    SinaFuturesExtractor,
    WestmetallLmeExtractor,
)
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.extract.westmetall import parse_date
from terusan_pipelines.sources import registry
from terusan_pipelines.sources.base import ScrapeContext
from terusan_pipelines.sources.westmetall import FIRST_YEAR, METALS, years_to_fetch

# The shape Westmetall serves, trimmed: one table, a header row, dated rows,
# newest first.
LME_PAGE = """<html><head><meta charset="UTF-8"></head><body>
<table >
<tr><th>date</th><th>LME Nickel Cash-Settlement</th><th>LME Nickel 3-month</th>
<th>LME Nickel stock</th></tr>
<tr><td>25. September 2026</td><td>16,050.00</td><td>16,230.00</td><td>284,946</td></tr>
<tr><td>24. September 2026</td><td>16,320.00</td><td>16,475.00</td><td>278,898</td></tr>
<tr><td>02. January 2026</td><td>-</td><td>16,915.00</td><td>255,282</td></tr>
</table></body></html>"""

SINA_BARS = [
    {
        "d": "2026-09-23",
        "o": "126000.000",
        "h": "126500.000",
        "l": "125100.000",
        "c": "125660.000",
        "v": "112594",
        "p": "132124",
        "s": "126150.000",
    },
    {
        "d": "2026-09-24",
        "o": "126120.000",
        "h": "126280.000",
        "l": "124800.000",
        "c": "125320.000",
        "v": "132223",
        "p": "132548",
        "s": "125350.000",
    },
]


def landed_lme(tmp_path, body: str = LME_PAGE) -> Landed:
    path = tmp_path / "lme-nickel-2026.html"
    path.write_text(body, encoding="utf-8")
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="westmetall-lme",
        dataset="lme-nickel",
        extra={"field": "LME_Ni_cash", "commodity": "nickel", "year": 2026},
    )


def landed_sina(tmp_path, body=None) -> Landed:
    path = tmp_path / "shfe-nickel.json"
    path.write_bytes(json.dumps(SINA_BARS if body is None else body).encode())
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="sina-shfe-nickel",
        dataset="shfe-nickel",
        extra={"symbol": "NI0", "unit": "CNY/t"},
    )


# -- Westmetall -------------------------------------------------------------


def test_lme_rows_are_one_record_per_day_with_the_header_skipped(tmp_path) -> None:
    rows = list(WestmetallLmeExtractor().extract(landed_lme(tmp_path)))

    assert [r["columns"]["date"] for r in rows] == ["2026-09-25", "2026-09-24", "2026-01-02"]
    assert [r["row_number"] for r in rows] == [1, 2, 3]
    assert {r["dataset"] for r in rows} == {"lme-nickel"}


def test_lme_figures_keep_their_separators_for_silver_to_read(tmp_path) -> None:
    first = next(WestmetallLmeExtractor().extract(landed_lme(tmp_path)))["columns"]

    assert first["cash_settlement"] == "16,050.00"
    assert first["three_month"] == "16,230.00"
    assert first["stock"] == "284,946"


def test_lme_units_and_commodity_are_joined_on(tmp_path) -> None:
    """The page states neither; without them the figure is a bare number."""
    first = next(WestmetallLmeExtractor().extract(landed_lme(tmp_path)))["columns"]

    assert first["unit"] == "USD/t"
    assert first["stock_unit"] == "t"
    assert first["commodity"] == "nickel"


def test_a_dashed_lme_cell_stays_blank(tmp_path) -> None:
    rows = list(WestmetallLmeExtractor().extract(landed_lme(tmp_path)))

    assert rows[-1]["columns"]["cash_settlement"] == ""
    assert rows[-1]["columns"]["three_month"] == "16,915.00"


def test_a_page_with_no_dated_rows_is_an_error(tmp_path) -> None:
    empty = "<table><tr><th>date</th><th>a</th><th>b</th><th>c</th></tr></table>"
    with pytest.raises(ExtractionError):
        list(WestmetallLmeExtractor().extract(landed_lme(tmp_path, empty)))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("25. September 2026", "2026-09-25"),
        ("02. January 2008", "2008-01-02"),
        ("date", None),
        ("31. February 2026", None),
    ],
)
def test_westmetall_dates_are_restated_as_iso(text, expected) -> None:
    assert parse_date(text) == expected


def test_a_full_pull_reaches_back_to_2008_and_a_nightly_asks_for_one_year() -> None:
    today = date(2026, 9, 27)

    assert list(years_to_fetch(ScrapeContext(), today)) == list(range(FIRST_YEAR, 2027))
    assert list(years_to_fetch(ScrapeContext(since=date(2026, 9, 20)), today)) == [2026]
    # A week's window across New Year needs both pages.
    assert list(years_to_fetch(ScrapeContext(since=date(2025, 12, 28)), date(2026, 1, 3))) == [
        2025,
        2026,
    ]


def test_every_lme_metal_resolves_in_the_commodity_registry() -> None:
    from terusan_pipelines.normalize.reference import load_commodities

    known = {c.commodity_id for c in load_commodities()}
    assert {m.commodity for m in METALS} <= known


# -- Sina -------------------------------------------------------------------


def test_sina_keys_are_named_and_the_symbol_and_unit_carried(tmp_path) -> None:
    rows = list(SinaFuturesExtractor().extract(landed_sina(tmp_path)))

    assert len(rows) == 2
    last = rows[-1]["columns"]
    assert last["date"] == "2026-09-24"
    assert last["close"] == "125320.000"
    assert last["settlement"] == "125350.000"
    assert last["open_interest"] == "132548"
    assert last["symbol"] == "NI0"
    assert last["unit"] == "CNY/t"


def test_a_sina_body_that_is_not_a_list_is_an_error(tmp_path) -> None:
    with pytest.raises(ExtractionError):
        list(SinaFuturesExtractor().extract(landed_sina(tmp_path, body={"x": 1})))


# -- Routing and registration -------------------------------------------------


def _claimant(landed: Landed):
    return next(e for e in DEFAULT_EXTRACTORS if e.handles(landed))


def test_the_new_files_reach_their_own_readers_not_the_generic_ones(tmp_path) -> None:
    assert isinstance(_claimant(landed_lme(tmp_path)), WestmetallLmeExtractor)
    assert isinstance(_claimant(landed_sina(tmp_path)), SinaFuturesExtractor)


@pytest.mark.parametrize(
    ("slug", "symbol"),
    [
        ("yahoo-aluminium", "ALI=F"),
        ("yahoo-zinc", "ZNC=F"),
        ("yahoo-iron-ore", "TIO=F"),
        ("yahoo-silver", "SI=F"),
        ("yahoo-platinum", "PL=F"),
        ("yahoo-palladium", "PA=F"),
        ("yahoo-hot-rolled-coil", "HRC=F"),
        ("sina-shfe-nickel", "NI0"),
        ("sina-shfe-tin", "SN0"),
        ("sina-shfe-stainless", "SS0"),
        ("sina-dce-coking-coal", "JM0"),
    ],
)
def test_each_instrument_is_registered_as_a_daily_source(slug, symbol) -> None:
    registry.ensure_loaded()
    source = registry.get(slug)

    assert source.symbol == symbol
    assert source.meta.update_frequency == "daily"
    assert source.meta.active


def test_westmetall_is_registered_as_a_daily_source() -> None:
    registry.ensure_loaded()
    assert registry.get("westmetall-lme").meta.update_frequency == "daily"


# -- Trading Economics: Newcastle coal ---------------------------------------


def landed_market(tmp_path, payload) -> Landed:
    from terusan_pipelines.sources.trading_economics.charts import encode_payload

    path = tmp_path / "newcastle-coal-2026-08-01-2026-09-27.json"
    path.write_bytes(encode_payload(payload))
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="tradingeconomics-coal",
        dataset="newcastle-coal",
        extra={"kind": "market", "symbol": "xal1:com", "commodity": "thermal-coal"},
    )


MARKET_PAYLOAD = {
    "series": [
        {
            "symbol": "XAL1:COM",
            "unit": "USD/T",
            "data": [
                [1790208000, 143.5, -0.829, -1.2],
                [1790294400, 143.75, 0.174, 0.25],
                # A restated reading for the same day supersedes the first.
                [1790294400, 143.8, 0.2, 0.3],
            ],
        }
    ]
}


def test_market_points_put_the_timestamp_first_and_are_dated_by_utc(tmp_path) -> None:
    from terusan_pipelines.extract import TradingEconomicsMarketExtractor

    rows = list(TradingEconomicsMarketExtractor().extract(landed_market(tmp_path, MARKET_PAYLOAD)))

    assert [r["columns"]["date"] for r in rows] == ["2026-09-24", "2026-09-25"]
    assert [r["columns"]["close"] for r in rows] == ["143.5", "143.8"]
    assert {r["columns"]["unit"] for r in rows} == {"USD/t"}
    assert {r["columns"]["commodity"] for r in rows} == {"thermal-coal"}


def test_the_coal_chart_reaches_the_market_reader(tmp_path) -> None:
    from terusan_pipelines.extract import TradingEconomicsMarketExtractor

    landed = landed_market(tmp_path, MARKET_PAYLOAD)
    assert isinstance(_claimant(landed), TradingEconomicsMarketExtractor)


def test_windows_are_contiguous_five_year_spans_ending_today() -> None:
    from terusan_pipelines.sources.trading_economics import windows

    spans = windows(date(2008, 12, 1), date(2026, 9, 27))

    assert spans[0].start == date(2008, 12, 1)
    assert spans[-1].end == date(2026, 9, 27)
    assert len(spans) == 4
    for left, right in zip(spans, spans[1:], strict=False):
        assert right.start.toordinal() == left.end.toordinal() + 1
    # A nightly's week is one window.
    assert len(windows(date(2026, 9, 20), date(2026, 9, 27))) == 1
