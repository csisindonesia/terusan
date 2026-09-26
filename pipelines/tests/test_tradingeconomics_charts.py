"""Trading Economics charts: finding the request, decoding the answer, dating it."""

from __future__ import annotations

from pathlib import Path

import pytest

from terusan_pipelines.extract import Landed, TradingEconomicsChartExtractor
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.extract.runner import DEFAULT_EXTRACTORS
from terusan_pipelines.extract.trading_economics import chart_period
from terusan_pipelines.sources.trading_economics.charts import (
    chart_request,
    decode_payload,
    encode_payload,
)

PAGE = """
<script>
var TEChartsDatasource = 'https://d3ii0wo49og5mi.cloudfront.net'; var TESymbol = '';
var TEChartsToken = '20260324:loboantunes'; var TELastUpdate = '20260831000000';
TESymbol = 'IDNNYGDPPCAPPPCD'; TELastUpdate = '202608141600'; var TEFrequency = 'Yearly';
</script>
"""

PAYLOAD = [
    {
        "series": [
            {
                "serie": {
                    "s": "idnnygdppcapppcd:te",
                    "name": "ID GDP per Capita PPP",
                    "unit": "USD",
                    "frequency": "yearly",
                    "source": "World Bank",
                    "data": [
                        [4873.05, 631152000, None, "1990-01-01"],
                        [15091.01, 1735689600, None, "2025-01-01"],
                    ],
                    "forecast": [[15500.0, 1767225600, None, "2026-01-01"]],
                }
            },
            # A comparison series, which must not be read as this indicator.
            {"serie": {"s": "mysnygdppcapppcd:te", "data": [[1.0, 0, None, "2025-01-01"]]}},
        ]
    }
]


def test_the_last_assignment_of_each_global_wins() -> None:
    request = chart_request(PAGE)

    assert request is not None
    assert request.symbol == "IDNNYGDPPCAPPPCD"
    assert request.url == (
        "https://d3ii0wo49og5mi.cloudfront.net/economics/idnnygdppcapppcd?span=max&v=20260814160000"
    )
    assert request.headers == {"x-api-key": "20260324:loboantunes"}


def test_a_page_without_a_symbol_has_no_chart() -> None:
    assert chart_request("<html></html>") is None


def test_the_payload_round_trips() -> None:
    assert decode_payload(encode_payload(PAYLOAD)) == PAYLOAD


@pytest.mark.parametrize(
    ("day", "frequency", "period"),
    [
        ("2025-01-01", "yearly", "2025"),
        ("2000-09-01", "quarterly", "2000-Q3"),
        ("2000-12-01", "Quarterly", "2000-Q4"),
        ("2026-08-01", "monthly", "2026-08"),
        ("2026-09-24", "daily", "2026-09-24"),
    ],
)
def test_a_point_is_dated_at_its_series_frequency(day: str, frequency: str, period: str) -> None:
    assert chart_period(day, frequency) == period


def _landed(tmp_path: Path, body: bytes) -> Landed:
    path = tmp_path / "gdp-per-capita-ppp-chart-2026-09-25.json"
    path.write_bytes(body)
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="tradingeconomics-indonesia",
        dataset="gdp-per-capita-ppp",
        source_url="https://d3ii0wo49og5mi.cloudfront.net/economics/idnnygdppcapppcd",
        extra={
            "kind": "chart",
            "indicator": "gdp-per-capita-ppp",
            "symbol": "IDNNYGDPPCAPPPCD",
            "page_url": "https://id.tradingeconomics.com/indonesia/gdp-per-capita-ppp",
        },
    )


def test_the_history_is_read_without_forecasts_or_comparisons(tmp_path: Path) -> None:
    rows = list(
        TradingEconomicsChartExtractor().extract(_landed(tmp_path, encode_payload(PAYLOAD)))
    )

    columns = [row["columns"] for row in rows]
    assert [c["period"] for c in columns] == ["1990", "2025"]
    assert columns[-1]["value"] == "15091.01"
    assert columns[-1]["indicator_key"] == "te_gdp_per_capita_ppp"
    assert columns[-1]["country"] == "Indonesia"
    assert columns[-1]["publisher"] == "World Bank"
    assert {row["dataset"] for row in rows} == {"indonesia-indicators"}


def test_an_undecodable_payload_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ExtractionError, match="undecodable"):
        list(TradingEconomicsChartExtractor().extract(_landed(tmp_path, b'"not base64 at all"')))


def test_charts_are_read_before_the_generic_json_reader(tmp_path: Path) -> None:
    landed = _landed(tmp_path, encode_payload(PAYLOAD))

    first = next(e for e in DEFAULT_EXTRACTORS if e.handles(landed))

    assert isinstance(first, TradingEconomicsChartExtractor)


def test_a_period_with_two_points_keeps_the_last(tmp_path: Path) -> None:
    """A rate moved twice in one day is one closing figure for that day."""
    payload = [
        {
            "series": [
                {
                    "serie": {
                        "s": "idnnygdppcapppcd:te",
                        "frequency": "daily",
                        "data": [
                            [7.0, 1377993600, None, "2013-08-01"],
                            [6.5, 1377907200, None, "2013-08-01"],
                        ],
                    }
                }
            ]
        }
    ]

    rows = list(
        TradingEconomicsChartExtractor().extract(_landed(tmp_path, encode_payload(payload)))
    )

    assert [(r["columns"]["period"], r["columns"]["value"]) for r in rows] == [
        ("2013-08-01", "7.0")
    ]
