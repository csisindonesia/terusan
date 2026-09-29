"""SATUSEHAT's workforce planning dashboard: what a run fetches, and reading it."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from terusan_pipelines.extract import KemkesWorkforceExtractor, Landed
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.extract.kemkes_workforce import (
    FIGURES_DATASET,
    province,
    split_header,
    tables,
)
from terusan_pipelines.normalize.reference import geography_registry
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.kemkes import workforce
from terusan_pipelines.sources.kemkes.workforce import (
    DashboardChanged,
    WorkforcePlanning,
    options,
    sisdmk_cutoff,
)

REGISTER_PAGE = """<html><body><h5>Sebaran</h5><form action="user/sebaran" method="POST">
<select name="rumpun"><option value="Bidan">Bidan</option>
<option value="Dokter Gigi">Dokter Gigi</option></select></form>
<script>var note = '(Sumber : SISDMK 1 September 2026 )';</script></body></html>"""

REGISTER = {
    "recordsTotal": 3,
    "data": [
        ["RIAU", "KOTA PEKANBARU", "Bidan Klinis", "TNI AD", "2"],
        ["RIAU", "KAMPAR", "Bidan Desa", "PNS", "4"],
        ["YOGYAKARTA", "KOTA YOGYAKARTA", "Bidan Klinis", "PNS", "5"],
    ],
}

VACANCY_PAGE = """<table class="table table-bordered" id="dtone"><thead><tr>
<th>Propinsi</th><th>Dokter Spesialis Anak (Sp.A)</th>
<th>Dokter Spesialis Obstetri & Ginekologi (Sp.OG)</th><th>Total</th></tr></thead>
<tbody><tr class=""><td><a href="https://dreams.kemkes.go.id/user/kekosongan_dev/RS/prov/11">ACEH</a></td>
<td>0</td><td>2</td><td>2</td></tr>
<tr><td><a href="#">GORONTALO</a></td><td>1</td><td>-</td><td>1</td></tr></tbody>
<tfoot><tr><td>Total</td><td>50</td><td>61</td><td>505</td></tr></tfoot></table>"""

DEMAND_PAGE = """<div class="card">Indonesia <span>Tahun 2026</span>
<h6>Kebutuhan</h6><b>146.119</b><h6>Eksisting</h6><b>68.057</b>
<h6>Gap</h6><b>-78.062</b> Kurang Pemenuhan 46,58%</div>
<table class="table table-bordered" id="dtone"><thead><tr><th rowspan=2>Propinsi</th>
<th colspan=3>ATLM</th></tr><tr><th>Kebutuhan</th><th>Eksisting</th><th>Gap</th></tr>
</thead><tbody><tr class=""><td><a href="#">SUMATERA SELATAN</a></td>
<td class="bg-danger">4416</td><td>2012</td><td>-2404</td></tr></tbody></table>"""

WORKLOAD_PAGE = """<table id="dtone"><thead><tr><th rowspan=2>Propinsi</th>
<th rowspan=2>Jenjang</th><th colspan=3>Teknisi Transfusi Darah </th></tr>
<tr><th>Kebutuhan</th><th>Eksisting</th><th>Gap</th></tr></thead>
<tbody><tr><td>BANTEN</td><td>Terampil</td><td>3</td><td>0</td><td>-3</td></tr></tbody></table>"""

FORM = """<form action="user/demand/nakes" method="get">
<select name="nakes"><option value="">-- pilih --</option>
<option value="1" selected> ATLM </option><option value="60">Tenaga Kesehatan Tradisional</option>
</select><select name="tahun"><option value="2025">2025</option>
<option value="2026">2026</option></select></form>"""


# ---- the source --------------------------------------------------------------


class FakeDashboard:
    """dreams.kemkes.go.id, with a switch for each way it can go wrong."""

    def __init__(self) -> None:
        self.register_page = REGISTER_PAGE
        self.register: dict = REGISTER
        self.requested: list[str] = []
        self.chosen: dict[str, str] = {}
        #: Families the register answers nothing for.
        self.empty: set[str] = {"Dokter Gigi"}

    def post(self, url: str, **kwargs: object) -> httpx.Response:
        self.chosen = dict(kwargs.get("data") or {})
        return httpx.Response(200, text=self.register_page, request=httpx.Request("POST", url))

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        params = kwargs.get("params") or {}
        path = urlparse(url).path.lstrip("/")
        self.requested.append(path)
        request = httpx.Request("GET", url)
        if path == "user/sebaran/":
            return httpx.Response(200, text=self.register_page, request=request)
        if path == "empty-dataload/sebaran":
            if self.chosen.get("rumpun") in self.empty or self.chosen.get("sip") != "2":
                return httpx.Response(200, json={"recordsTotal": 0, "data": []}, request=request)
            return httpx.Response(200, json=self.register, request=request)
        if path.startswith("user/kekosongan_dev/"):
            return httpx.Response(200, text=VACANCY_PAGE, request=request)
        if path.startswith(("user/demand/", "user/abk/")):
            if not params:
                return httpx.Response(200, text=FORM, request=request)
            # A profession the form lists and has no figures for.
            if params.get("nakes") == "60":
                return httpx.Response(200, text="<p>no data</p>", request=request)
            page = WORKLOAD_PAGE if path.startswith("user/abk/") else DEMAND_PAGE
            return httpx.Response(200, text=page, request=request)
        return httpx.Response(200, text="<html></html>", request=request)

    def try_get(self, url: str, **kwargs: object) -> httpx.Response | None:
        return self.get(url, **kwargs)


@pytest.fixture
def dashboard(monkeypatch: pytest.MonkeyPatch) -> FakeDashboard:
    fake = FakeDashboard()

    @contextmanager
    def _fetcher(**_: object) -> Iterator[FakeDashboard]:
        yield fake

    monkeypatch.setattr(workforce, "fetcher", _fetcher)
    return fake


def test_the_snapshot_date_is_read_off_the_register_page() -> None:
    assert sisdmk_cutoff(REGISTER_PAGE) == date(2026, 9, 1)
    assert sisdmk_cutoff("<p>Sumber : SISDMK</p>") is None


def test_form_options_drop_the_placeholder() -> None:
    assert options(FORM, "nakes") == [("1", "ATLM"), ("60", "Tenaga Kesehatan Tradisional")]
    assert [v for v, _ in options(FORM, "tahun")] == ["2025", "2026"]


def test_a_run_lands_every_shape_and_dates_each(dashboard: FakeDashboard) -> None:
    artifacts = list(WorkforcePlanning().collect(ScrapeContext()))
    datasets = [a.dataset for a in artifacts]

    # One family per group answers, headcount; the other is empty and skipped.
    assert datasets.count(workforce.DISTRIBUTION) == len(workforce.REGISTER_GROUPS)
    assert datasets.count(workforce.VACANCIES) == len(workforce.VACANCY_PAGES)
    # Two years of one profession for the health-worker projection, one page
    # for the doctors', and the profession with no table is not landed.
    assert datasets.count(workforce.DEMAND) == 3
    assert datasets.count(workforce.WORKLOAD) == 2
    assert all(a.metadata["sisdmk_cutoff"] == "2026-09-01" for a in artifacts)
    assert all(a.partition == ("year=2026", "month=09") for a in artifacts)
    assert "demand_nakes-1-2026.html" in [a.filename for a in artifacts]
    assert "sebaran-nakes-bidan.json" in [a.filename for a in artifacts]
    register = next(a for a in artifacts if a.dataset == workforce.DISTRIBUTION)
    assert register.metadata["family"] == "Bidan"
    assert register.metadata["count"] == "headcount"


def test_an_undated_register_refuses_the_run(dashboard: FakeDashboard) -> None:
    dashboard.register_page = "<html>Sebaran</html>"
    with pytest.raises(DashboardChanged, match="SISDMK"):
        list(WorkforcePlanning().collect(ScrapeContext()))


def test_a_short_register_is_refused(dashboard: FakeDashboard) -> None:
    dashboard.register = {**REGISTER, "recordsTotal": 84502}
    with pytest.raises(DashboardChanged, match="recordsTotal"):
        list(WorkforcePlanning().collect(ScrapeContext()))


def test_a_register_that_answers_nothing_refuses_the_run(dashboard: FakeDashboard) -> None:
    dashboard.empty = {"Bidan", "Dokter Gigi"}
    with pytest.raises(DashboardChanged, match="no family"):
        list(WorkforcePlanning().collect(ScrapeContext()))


def test_the_limit_is_honoured(dashboard: FakeDashboard) -> None:
    assert len(list(WorkforcePlanning().collect(ScrapeContext(limit=2)))) == 2


# ---- the extractor -----------------------------------------------------------


def landed(tmp_path: Path, name: str, content: str, dataset: str, **extra: object) -> Landed:
    path = tmp_path / name
    path.write_text(content)
    return Landed(
        path=path,
        document_id="doc_1",
        content_hash="0" * 64,
        source_slug="kemkes-sdmk-planning",
        dataset=dataset,
        extra={"sisdmk_cutoff": "2026-09-01", **extra},
    )


def _figures(rows: list[dict]) -> list[dict]:
    return [r["columns"] for r in rows if r["dataset"] == FIGURES_DATASET]


def test_headers_spanning_two_rows_are_merged() -> None:
    header, rows = split_header(tables(WORKLOAD_PAGE)["dtone"].rows())
    assert header == [
        "Propinsi",
        "Jenjang",
        "Teknisi Transfusi Darah - Kebutuhan",
        "Teknisi Transfusi Darah - Eksisting",
        "Teknisi Transfusi Darah - Gap",
    ]
    assert rows == [["BANTEN", "Terampil", "3", "0", "-3"]]


def test_the_register_is_summed_per_province_and_for_the_country(tmp_path: Path) -> None:
    rows = list(
        KemkesWorkforceExtractor().extract(
            landed(
                tmp_path,
                "sebaran-nakes-bidan.json",
                json.dumps(REGISTER),
                "kemkes-sdmk-distribution",
                group="Nakes",
                family="Bidan",
            )
        )
    )

    facts = [r["columns"] for r in rows if r["dataset"] == "kemkes-sdmk-distribution"]
    assert facts[0]["kabupaten_kota"] == "KOTA PEKANBARU"
    assert facts[0]["jumlah"] == "2"
    assert facts[0]["family"] == "Bidan"

    figures = {(f["indicator"], f["geo"], f["category"]): f["value"] for f in _figures(rows)}
    assert figures[("kemkes_sdmk_workforce", "Indonesia", "Bidan — Bidan Klinis")] == "7"
    assert figures[("kemkes_sdmk_workforce", "Provinsi RIAU", "Bidan — Bidan Desa")] == "4"
    assert figures[("kemkes_sdmk_workforce_family", "Indonesia", "Bidan")] == "11"
    assert figures[("kemkes_sdmk_workforce_family", "Provinsi DI Yogyakarta", "Bidan")] == "5"
    # One family is not every family: there is no total of its own.
    assert not [key for key in figures if key[2] == ""]
    assert {f["period"] for f in _figures(rows)} == {"2026-09-01"}


def test_a_vacancy_table_is_a_figure_per_province_and_profession(tmp_path: Path) -> None:
    rows = list(
        KemkesWorkforceExtractor().extract(
            landed(tmp_path, "rs.html", VACANCY_PAGE, "kemkes-sdmk-vacancies", page="rs")
        )
    )

    records = [r["columns"] for r in rows if r["dataset"] == "kemkes-sdmk-vacancies"]
    assert records[0]["Dokter Spesialis Obstetri & Ginekologi (Sp.OG)"] == "2"

    figures = {(f["geo"], f["category"]): f["value"] for f in _figures(rows)}
    assert figures[("Provinsi ACEH", "Dokter Spesialis Anak (Sp.A)")] == "0"
    # The table's own total row is the country; its total column the total.
    assert figures[("Indonesia", "")] == "505"
    # A dash is a status, and passes through for the normalizer to record.
    assert figures[("Provinsi GORONTALO", "Dokter Spesialis Obstetri & Ginekologi (Sp.OG)")] == "-"
    assert {f["indicator"] for f in _figures(rows)} == {"kemkes_vacancy_hospital"}


def test_a_projection_is_dated_by_its_summary_and_keeps_its_national_figure(
    tmp_path: Path,
) -> None:
    rows = list(
        KemkesWorkforceExtractor().extract(
            landed(
                tmp_path,
                "demand_nakes-1-2026.html",
                DEMAND_PAGE,
                "kemkes-sdmk-demand",
                page="demand_nakes",
                profession="ATLM",
                year="2026",
            )
        )
    )

    figures = {(f["indicator"], f["geo"]): f for f in _figures(rows)}
    assert figures[("kemkes_sdmk_need", "Indonesia")]["value"] == "146119"
    assert figures[("kemkes_sdmk_gap", "Indonesia")]["value"] == "-78062"
    assert figures[("kemkes_sdmk_existing", "Provinsi SUMATERA SELATAN")]["value"] == "2012"
    assert {f["period"] for f in figures.values()} == {"2026"}
    assert {f["category"] for f in figures.values()} == {"ATLM"}

    record = next(r["columns"] for r in rows if r["dataset"] == "kemkes-sdmk-demand")
    assert record["profession"] == "ATLM"
    assert record["ATLM - Kebutuhan"] == "4416"


def test_the_workload_analysis_stays_in_bronze(tmp_path: Path) -> None:
    rows = list(
        KemkesWorkforceExtractor().extract(
            landed(tmp_path, "abk_kl-1.html", WORKLOAD_PAGE, "kemkes-sdmk-workload", page="abk_kl")
        )
    )
    assert rows and not _figures(rows)


def test_a_page_without_its_table_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ExtractionError, match="dtone"):
        list(
            KemkesWorkforceExtractor().extract(
                landed(tmp_path, "rs.html", "<p>maintenance</p>", "kemkes-sdmk-vacancies")
            )
        )


def test_every_province_the_dashboard_names_resolves() -> None:
    names = [
        "ACEH",
        "BALI",
        "BANGKA BELITUNG",
        "BANTEN",
        "BENGKULU",
        "GORONTALO",
        "JAKARTA",
        "JAMBI",
        "JAWA BARAT",
        "JAWA TENGAH",
        "JAWA TIMUR",
        "KALIMANTAN BARAT",
        "KALIMANTAN SELATAN",
        "KALIMANTAN TENGAH",
        "KALIMANTAN TIMUR",
        "KALIMANTAN UTARA",
        "KEPULAUAN RIAU",
        "LAMPUNG",
        "MALUKU",
        "MALUKU UTARA",
        "NUSA TENGGARA BARAT",
        "NUSA TENGGARA TIMUR",
        "PAPUA",
        "PAPUA BARAT",
        "PAPUA BARAT DAYA",
        "PAPUA PEGUNUNGAN",
        "PAPUA SELATAN",
        "PAPUA TENGAH",
        "RIAU",
        "SULAWESI BARAT",
        "SULAWESI SELATAN",
        "SULAWESI TENGAH",
        "SULAWESI TENGGARA",
        "SULAWESI UTARA",
        "SUMATERA BARAT",
        "SUMATERA SELATAN",
        "SUMATERA UTARA",
        "YOGYAKARTA",
    ]
    registry = geography_registry()
    resolved = {registry.resolve(province(name)).identifier for name in names}

    assert None not in resolved
    assert len(resolved) == 38
