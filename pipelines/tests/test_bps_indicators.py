"""BPS figures: three years a request, and a cube read back into rows.

The table shape is BPS's own, trimmed to two regions: `datacontent` keyed by
region, variable, breakdown, year and sub-period run together.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from terusan_pipelines.extract import BpsDataExtractor, Landed
from terusan_pipelines.extract.bps import geo_key, period_of, region_code
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.bps import indicators, webapi
from terusan_pipelines.sources.bps.indicators import Indicators
from terusan_pipelines.sources.bps.webapi import BpsError

KEY = "0123456789abcdef0123456789abcdef"


# --- the source -------------------------------------------------------------


class FakeHttp:
    """A catalogue of three variables over two pages; seven years each, listed
    over two pages. Variable 13 always errors."""

    YEARS = {126 - i: 2026 - i for i in range(7)}
    CATALOGUE = [[11, 12], [13]]
    BROKEN = 13

    def __init__(self) -> None:
        self.requested: list[str] = []

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        self.requested.append(url)
        page = int(url.split("/page/")[1].split("/")[0]) if "/page/" in url else 1
        if "/list/model/var/" in url:
            body: dict[str, Any] = {
                "status": "OK",
                "data-availability": "available",
                "data": [
                    {"page": page, "pages": len(self.CATALOGUE)},
                    [{"var_id": v, "title": f"var {v}"} for v in self.CATALOGUE[page - 1]],
                ],
            }
        elif f"/var/{self.BROKEN}/" in url:
            body = {"status": "Error", "message": "broken table"}
        elif "/list/model/th/" in url:
            ids = sorted(self.YEARS, reverse=True)
            chunk = ids[(page - 1) * 5 : page * 5]
            body = {
                "status": "OK",
                "data-availability": "available",
                "data": [
                    {"page": page, "pages": 2},
                    [{"th_id": t, "th": str(self.YEARS[t])} for t in chunk],
                ],
            }
        else:
            body = {"status": "OK", "data-availability": "available", "datacontent": {}}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))


class FakeCredentials:
    bps_api_key = SecretStr(KEY)


@pytest.fixture
def bps(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    http = FakeHttp()

    @contextmanager
    def fake_fetcher(**kwargs: object) -> Iterator[FakeHttp]:
        yield http

    monkeypatch.setattr(indicators, "fetcher", fake_fetcher)
    monkeypatch.setattr(webapi, "credentials", FakeCredentials)
    # One failing variable in three is past the ceiling; the tests that are
    # not about failure raise it out of the way.
    monkeypatch.setattr(indicators, "MAX_FAILURE_RATIO", 0.5)
    return http


def data_requests(http: FakeHttp) -> list[str]:
    return [url for url in http.requested if "/list/model/data/" in url]


def test_every_catalogue_variable_is_collected(bps: FakeHttp) -> None:
    artifacts = list(Indicators().collect(ScrapeContext()))

    assert {a.metadata["var_id"] for a in artifacts} == {11, 12}


def test_the_default_run_is_the_last_two_years_in_one_request(bps: FakeHttp) -> None:
    """Year ids are the year less 1900, so no year list is needed."""
    this_year = date.today().year
    ids = f"{this_year - 1 - 1900};{this_year - 1900}"

    list(Indicators().collect(ScrapeContext(params={"vars": "11"})))

    assert not any("/list/model/th/" in url for url in bps.requested)
    assert [url.split("/th/")[1].split("/")[0] for url in data_requests(bps)] == [ids]


def test_a_full_run_walks_the_year_list_three_at_a_time(bps: FakeHttp) -> None:
    """BPS refuses a `th` of more than three years."""
    artifacts = list(Indicators().collect(ScrapeContext(params={"vars": "11", "full": "true"})))

    requested = data_requests(bps)
    assert len(requested) == 3  # seven years: 3 + 3 + 1
    assert all(len(url.split("/th/")[1].split("/")[0].split(";")) <= 3 for url in requested)
    assert artifacts[0].filename == "var-11-2020-2022.json"


def test_one_broken_variable_does_not_stop_the_rest(bps: FakeHttp) -> None:
    artifacts = list(Indicators().collect(ScrapeContext(params={"vars": "13,11"})))

    assert [a.metadata["var_id"] for a in artifacts] == [11]


def test_many_broken_variables_fail_the_run(bps: FakeHttp, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(indicators, "MAX_FAILURE_RATIO", 0.10)

    with pytest.raises(BpsError, match="1 of 3 variables failed"):
        list(Indicators().collect(ScrapeContext()))


def test_the_key_is_not_in_what_lands(bps: FakeHttp) -> None:
    for artifact in Indicators().collect(ScrapeContext()):
        assert KEY not in (artifact.source_url or "")


# --- the extractor ----------------------------------------------------------


def table() -> dict[str, Any]:
    return {
        "status": "OK",
        "var": [{"val": 192, "label": "Persentase Penduduk Miskin (P0)", "unit": "Persen"}],
        "labelvervar": "38 Provinsi",
        "vervar": [{"val": 1100, "label": "ACEH"}, {"val": 9999, "label": "INDONESIA"}],
        "turvar": [{"val": 432, "label": "Perkotaan"}, {"val": 434, "label": "Jumlah"}],
        "tahun": [{"val": 126, "label": "2026"}],
        "turtahun": [
            {"val": 61, "label": "Semester 1 (Maret)"},
            {"val": 63, "label": "Tahunan"},
        ],
        "datacontent": {
            "110019243412661": 12.33,
            "999919243412661": 8.07,
            "999919243212661": 6.5,
        },
    }


def landed(tmp_path: Path, body: dict[str, Any]) -> Landed:
    path = tmp_path / "var-192.json"
    path.write_text(json.dumps(body))
    return Landed(
        path=path,
        document_id="doc",
        content_hash="hash",
        source_slug="bps-indicators",
        dataset="bps-indicators",
    )


def test_each_published_cell_becomes_one_row(tmp_path: Path) -> None:
    """Rebuilt from the dimension lists; a combination not in the table makes
    no row, so three cells are three records."""
    rows = [r["columns"] for r in BpsDataExtractor().extract(landed(tmp_path, table()))]

    assert [(r["geo"], r["breakdown"], r["period"], r["value"]) for r in rows] == [
        ("11", "Jumlah", "2026-03", "12.33"),
        ("IDN", "Perkotaan", "2026-03", "6.5"),
        ("IDN", "Jumlah", "2026-03", "8.07"),
    ]
    assert rows[0]["unit"] == "Persen"
    assert rows[0]["period_label"] == "Semester 1 (Maret)"


def test_a_series_is_its_variable_and_breakdown(tmp_path: Path) -> None:
    """Provinces are places within one series, not series of their own; the
    urban figure is a different series from the total."""
    rows = [r["columns"] for r in BpsDataExtractor().extract(landed(tmp_path, table()))]

    by_series = {(r["geo"], r["breakdown"]): r for r in rows}
    total, urban = by_series[("IDN", "Jumlah")], by_series[("IDN", "Perkotaan")]
    assert by_series[("11", "Jumlah")]["indicator"] == total["indicator"]
    assert urban["indicator"] != total["indicator"]
    assert total["series_code"] == "192.434"
    assert urban["series_name"] == "Persentase Penduduk Miskin (P0) — Perkotaan"


def test_rows_that_are_not_places_are_series_of_the_country(tmp_path: Path) -> None:
    """`Wilayah` is BPS's word for town and village, not for a region."""
    body = {
        "var": [{"val": 183, "label": "Jumlah Penduduk Miskin", "unit": "Tidak Ada Satuan"}],
        "labelvervar": "Wilayah",
        "vervar": [{"val": 1, "label": "<b>Kota</b>"}, {"val": 2, "label": "  Desa"}],
        "turvar": [{"val": 0, "label": "Tidak ada"}],
        "tahun": [{"val": 125, "label": "2025"}],
        "turtahun": [{"val": 1, "label": "Maret"}, {"val": 3, "label": "Tahunan"}],
        "datacontent": {"118301251": 9.5, "218301251": 13.8, "118301253": 9.4},
    }
    rows = [r["columns"] for r in BpsDataExtractor().extract(landed(tmp_path, body))]

    assert {r["geo"] for r in rows} == {"IDN"}
    assert [r["series_name"] for r in rows] == [
        "Jumlah Penduduk Miskin — Kota",
        "Jumlah Penduduk Miskin — Kota",
        "Jumlah Penduduk Miskin — Desa",
    ]
    assert rows[0]["unit"] == ""
    # The annual figure beside a monthly one is marked, not dropped.
    assert [r["period_kind"] for r in rows] == ["", "summary", ""]


def test_region_codes_as_the_registry_keys_them() -> None:
    assert region_code("1100") == "11"
    assert region_code("1106") == "11.06"
    assert region_code("9999") == "IDN"
    # The 150-city table numbers its cities rather than coding them.
    assert region_code("1") == ""


def test_renumbered_papua_regencies_resolve_on_the_name() -> None:
    """BPS 95.01 is Merauke; Kemendagri's 95.01, which the registry answers
    to, is Jayawijaya."""
    assert geo_key("9501", "MERAUKE") == "MERAUKE"
    assert geo_key("9500", "PAPUA SELATAN") == "95"
    assert geo_key("1106", "KAB ACEH TENGAH") == "11.06"
    assert geo_key("1", "KAB ACEH TENGAH") == "KAB ACEH TENGAH"


@pytest.mark.parametrize(
    ("label", "period"),
    [
        ("Februari", "2026-02"),
        ("Semester 2 (September)", "2026-09"),
        ("Triwulan III", "2026Q3"),
        ("Tahun", "2026"),
        ("Tahunan", "2026"),
    ],
)
def test_periods(label: str, period: str) -> None:
    assert period_of("2026", label) == period
