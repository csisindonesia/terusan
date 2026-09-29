"""OJK's SJK portal: which months a run takes, and reading what lands."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from terusan_pipelines.extract import Landed, OjkSjkExtractor
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.extract.ojk_sjk import FIGURES_DATASET, figures, province
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.ojk import sjk
from terusan_pipelines.sources.ojk.sjk import (
    CUBES,
    PortalRefused,
    SjkBankingStatistics,
    month_before,
)

ROW = {
    "CalendarID": 6026,
    "Bulan": "2026-07",
    "KelompokBank": "2. BPD",
    "DATI1": "Sulawesi Selatan",
    "Kategori": None,
    "Nilai": 37104828289.0,
}


class FakePortal:
    """The grid export, with each cube's months and row counts set per test."""

    def __init__(self, months: dict[str, dict[str, int]]) -> None:
        self.months = months
        self.requested: list[dict[str, str]] = []
        self.html = False

    def get(self, url: str, **_: object) -> httpx.Response:
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.requested.append(query)
        request = httpx.Request("GET", url)
        if self.html:
            return httpx.Response(
                200,
                text="<html>Request Rejected</html>",
                request=request,
                headers={"content-type": "text/html"},
            )
        total = self.months.get(query["namaTable"], {}).get(query["_data"], 0)
        take = int(query["take"])
        rows = [ROW] * max(0, min(take, total - int(query["skip"])))
        return httpx.Response(200, json={"data": rows, "totalCount": total}, request=request)


@pytest.fixture
def portal(monkeypatch: pytest.MonkeyPatch) -> FakePortal:
    fake = FakePortal({"BPR_DanaPihakKetiga": {"2026-07": 3, "2026-06": 3, "2026-05": 3}})

    @contextmanager
    def _fetcher(**_: object) -> Iterator[FakePortal]:
        yield fake

    monkeypatch.setattr(sjk, "fetcher", _fetcher)
    monkeypatch.setattr(sjk, "_this_month", lambda: "2026-09")
    return fake


def _pages(artifacts: list) -> list[str]:
    return [a.filename for a in artifacts]


def test_months_roll_over_the_year() -> None:
    assert month_before("2026-01") == "2025-12"
    assert month_before("2026-10") == "2026-09"


def test_a_routine_run_takes_the_newest_three_months(portal: FakePortal) -> None:
    portal.months["BPR_DanaPihakKetiga"]["2026-04"] = 3

    artifacts = list(SjkBankingStatistics().collect(ScrapeContext()))

    assert _pages(artifacts) == [
        "BPR_DanaPihakKetiga-2026-07.json",
        "BPR_DanaPihakKetiga-2026-06.json",
        "BPR_DanaPihakKetiga-2026-05.json",
    ]
    assert artifacts[0].dataset == "ojk-rural-bank-deposits"
    assert artifacts[0].partition == ("year=2026", "month=07")


def test_a_big_month_lands_whole_in_one_response(portal: FakePortal) -> None:
    """A month split into pages is a month no extractor can add up."""
    portal.months = {"BU_KreditdanPembiayaanEntitas": {"2026-07": 59_437}}

    artifacts = list(SjkBankingStatistics().collect(ScrapeContext(limit=1)))

    assert _pages(artifacts) == ["BU_KreditdanPembiayaanEntitas-2026-07.json"]
    assert len(json.loads(artifacts[0].content)["data"]) == 59_437
    assert artifacts[0].metadata["rows_in_month"] == 59_437


def test_a_month_shorter_than_its_count_fails_the_run(portal: FakePortal) -> None:
    """Its sums would be short, and nothing downstream could tell."""
    real = portal.get

    def short(url: str, **kwargs: object) -> httpx.Response:
        response = real(url, **kwargs)
        if "requireTotalCount" in url:
            return response
        body = response.json()
        body["data"] = body["data"][:-1]
        return httpx.Response(200, json=body, request=response.request)

    portal.get = short  # type: ignore[method-assign]

    with pytest.raises(PortalRefused):
        list(SjkBankingStatistics().collect(ScrapeContext()))


def test_a_full_pull_walks_back_past_a_gap_to_where_history_starts(
    portal: FakePortal,
) -> None:
    """One empty month is a hole in the cube, not its beginning."""
    portal.months["BPR_DanaPihakKetiga"] = {"2026-07": 1, "2026-05": 1, "2026-01": 1}

    artifacts = list(SjkBankingStatistics().collect(ScrapeContext(params={"full": "true"})))

    months = [a.metadata["month"] for a in artifacts]
    assert months == ["2026-07", "2026-05"]


def test_full_false_from_the_command_line_is_a_routine_run(portal: FakePortal) -> None:
    portal.months["BPR_DanaPihakKetiga"]["2026-04"] = 3

    artifacts = list(SjkBankingStatistics().collect(ScrapeContext(params={"full": "false"})))

    assert len(artifacts) == 3


def test_since_starts_at_that_month(portal: FakePortal) -> None:
    artifacts = list(SjkBankingStatistics().collect(ScrapeContext(since=date(2026, 6, 14))))

    assert [a.metadata["month"] for a in artifacts] == ["2026-07", "2026-06"]


def test_the_filter_is_spelt_the_way_the_grid_parses_it(portal: FakePortal) -> None:
    list(SjkBankingStatistics().collect(ScrapeContext(limit=1)))

    page = next(q for q in portal.requested if "requireTotalCount" not in q)
    assert page["_filter"] == "Bulan\\=\\2026-07"
    assert page["_field"] == "Bulan"
    assert page["metricID"] == "10"


def test_every_cube_empty_fails_the_run(portal: FakePortal) -> None:
    """Nothing published anywhere is the portal failing, not OJK resting."""
    portal.months = {}

    with pytest.raises(PortalRefused):
        list(SjkBankingStatistics().collect(ScrapeContext()))


def test_a_waf_page_never_lands_as_json(portal: FakePortal) -> None:
    portal.html = True

    with pytest.raises(PortalRefused):
        list(SjkBankingStatistics().collect(ScrapeContext()))


def test_every_cube_lands_under_its_own_dataset() -> None:
    datasets = [cube.dataset for cube in CUBES]
    assert len(set(datasets)) == len(datasets)


# -- reading a month ----------------------------------------------------------


def landed(tmp_path: Path, body: object, **extra: object) -> Landed:
    path = tmp_path / "BPR_DanaPihakKetiga-2026-07.json"
    path.write_text(json.dumps(body))
    return Landed(
        path=path,
        document_id="doc_1",
        content_hash="0" * 64,
        source_slug="ojk-banking-sjk",
        dataset="ojk-rural-bank-deposits",
        extra={"table": "BPR_DanaPihakKetiga", "month": "2026-07", **extra},
    )


def _facts(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["dataset"] != FIGURES_DATASET]


def _figures(rows: list[dict]) -> dict[tuple[str, str, str], str]:
    return {
        (r["columns"]["indicator"], r["columns"]["geo"], r["columns"]["category"]): r["columns"][
            "value"
        ]
        for r in rows
        if r["dataset"] == FIGURES_DATASET
    }


def test_each_fact_row_becomes_a_record(tmp_path: Path) -> None:
    rows = _facts(list(OjkSjkExtractor().extract(landed(tmp_path, {"data": [ROW, ROW]}))))

    assert [r["row_number"] for r in rows] == [1, 2]
    assert rows[0]["dataset"] == "ojk-rural-bank-deposits"
    assert rows[0]["columns"]["table"] == "BPR_DanaPihakKetiga"
    assert rows[0]["columns"]["KelompokBank"] == "2. BPD"
    # A null is an empty cell, not the string "None".
    assert rows[0]["columns"]["Kategori"] == ""


def test_facts_keep_the_digits_the_portal_sent(tmp_path: Path) -> None:
    page = landed(tmp_path, {})
    page.path.write_text('{"data": [{"Nilai": 1234567890123.45}]}')

    rows = _facts(list(OjkSjkExtractor().extract(page)))

    assert rows[0]["columns"]["Nilai"] == "1234567890123.45"


def test_a_page_of_a_month_is_skipped(tmp_path: Path) -> None:
    """The first run landed pages; their rows are in the whole month beside them."""
    page = landed(tmp_path, {"data": [ROW]}, row_offset=10_000, rows_in_month=10_001)

    assert list(OjkSjkExtractor().extract(page)) == []


def test_the_first_page_of_a_month_is_skipped_too(tmp_path: Path) -> None:
    page = landed(tmp_path, {"data": [ROW]}, row_offset=0, rows_in_month=10_001)

    assert list(OjkSjkExtractor().extract(page)) == []


def test_a_month_is_summed_by_type_by_province_and_for_the_country() -> None:
    facts = [
        {"DATI1": "PROVINSI JAWA BARAT", "DPK": "Tabungan", "TotalDana": "100.5"},
        {"DATI1": "PROVINSI JAWA BARAT", "DPK": "Deposito", "TotalDana": "200"},
        {"DATI1": "PROVINSI BALI", "DPK": "Tabungan", "TotalDana": "50"},
        {"DATI1": "PROVINSI BALI", "DPK": "Deposito", "TotalDana": ""},
    ]

    rows = list(figures("BPR_DanaPihakKetiga", "2026-07", facts))
    sums = {(r["columns"]["geo"], r["columns"]["category"]): r["columns"]["value"] for r in rows}

    assert sums[("Indonesia", "")] == "350.5"
    assert sums[("Indonesia", "Tabungan")] == "150.5"
    assert sums[("PROVINSI JAWA BARAT", "")] == "300.5"
    # A blank figure contributes nothing, and is not a zero.
    assert ("PROVINSI BALI", "Deposito") not in sums
    assert {r["columns"]["period"] for r in rows} == {"2026-07"}
    assert {r["columns"]["unit"] for r in rows} == {"IDR"}


def test_unplaced_rows_count_toward_the_country_only() -> None:
    facts = [
        {"DATI1": "Lainnya", "JenisDPK": "1. Giro", "MataUang": "001. Rupiah", "Nilai": "7"},
        {"DATI1": "Bali", "JenisDPK": "1. Giro", "MataUang": "001. Rupiah", "Nilai": "3"},
    ]

    rows = list(figures("BU_DanaPihakKetiga", "2026-07", facts))
    places = {r["columns"]["geo"] for r in rows}

    assert places == {"Indonesia", "Provinsi Bali"}
    national = next(
        r
        for r in rows
        if r["columns"]["indicator"] == "ojk_bu_dpk"
        and r["columns"]["geo"] == "Indonesia"
        and not r["columns"]["category"]
    )
    assert national["columns"]["value"] == "10"


def test_npl_counts_only_the_three_bad_grades() -> None:
    base = {"DATI1": "Bali", "JenisPenggunaan": "001.Modal Kerja", "KategoriUsaha": "004. x"}
    facts = [
        base | {"KualitasKredit": "001. Lancar", "Nilai": "100"},
        base | {"KualitasKredit": "002. Dalam Perhatian Khusus", "Nilai": "10"},
        base | {"KualitasKredit": "003. Kurang Lancar", "Nilai": "1"},
        base | {"KualitasKredit": "005. Macet", "Nilai": "2"},
    ]

    rows = list(figures("BU_KreditdanPembiayaanEntitas", "2026-07", facts))
    npl = {
        r["columns"]["category"]: r["columns"]["value"]
        for r in rows
        if r["columns"]["indicator"] == "ojk_bu_npl" and r["columns"]["geo"] == "Indonesia"
    }
    credit = [
        r["columns"]["value"]
        for r in rows
        if r["columns"]["indicator"] == "ojk_bu_credit"
        and r["columns"]["geo"] == "Indonesia"
        and not r["columns"]["category"]
    ]

    assert npl == {"": "3", "003. Kurang Lancar": "1", "005. Macet": "2"}
    assert credit == ["113"]
    # Non-MSME lending is not MSME lending.
    assert not any(r["columns"]["indicator"] == "ojk_bu_credit_msme" for r in rows)


def test_commercial_banks_papua_is_the_territory_before_the_split() -> None:
    """Their cubes have no Papua Tengah: their Papua holds it."""
    assert province("BU_DanaPihakKetiga", "Papua") == "Papua (wilayah sebelum pemekaran 2022)"
    assert province("BPR_DanaPihakKetiga", "PROVINSI PAPUA") == "PROVINSI PAPUA"


def test_a_province_is_qualified_so_gorontalo_is_not_the_regency() -> None:
    assert province("BU_DanaPihakKetiga", "Gorontalo") == "Provinsi Gorontalo"
    assert province("BU_DanaPihakKetiga", "Aceh ") == "Provinsi Aceh"
    assert province("BU_DanaPihakKetiga", "") is None


def test_a_month_without_rows_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ExtractionError):
        list(OjkSjkExtractor().extract(landed(tmp_path, {"message": "error"})))


def test_only_this_sources_json_is_claimed(tmp_path: Path) -> None:
    page = landed(tmp_path, {"data": []})
    other = Landed(
        path=page.path, document_id="d", content_hash="0" * 64, source_slug="bnpb-disaster"
    )
    assert OjkSjkExtractor().handles(page)
    assert not OjkSjkExtractor().handles(other)
