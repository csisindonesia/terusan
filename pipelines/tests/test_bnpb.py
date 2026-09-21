"""BNPB's disaster tables: the catalogue, the challenge, and the fallback.

The portal answers its action API and challenges its downloads, so the tests
that matter are the ones about which route a resource takes — and that a
challenge page never reaches RAW named `.xlsx`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import httpx
import pytest

from terusan_pipelines.extract import BnpbDatastoreExtractor, Landed, bnpb_impact
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.sources import ScrapeContext
from terusan_pipelines.sources.bnpb import disaster
from terusan_pipelines.sources.bnpb.ckan import packages, search_url
from terusan_pipelines.sources.bnpb.disaster import DisasterData, PartialCatalogue

DOWNLOAD = "https://data.bnpb.go.id/dataset/pkg/resource/{}/download/table.xlsx"

CATALOGUE = {
    "success": True,
    "result": {
        "count": 2,
        "results": [
            {
                "name": "kompilasi-data-kejadian-dan-dampak-bencana-2025",
                "title": "Kompilasi Data Kejadian dan Dampak Bencana 2025",
                "notes": "Kompilasi data kejadian dan dampak bencana tahun 2025",
                "license_id": "odc-by",
                "metadata_modified": "2026-07-08T04:01:34.994747",
                "resources": [
                    {
                        "id": "aaaa1111",
                        "name": "Rekapitulasi Kejadian Bencana 2025.xlsx",
                        "description": "Rekapitulasi jumlah kejadian",
                        "format": "XLSX",
                        "datastore_active": True,
                        "last_modified": "2026-07-02T08:28:14.391164",
                        "size": 12646,
                        "url": DOWNLOAD.format("aaaa1111"),
                    },
                    {
                        "id": "bbbb2222",
                        "name": "Catatan metodologi.pdf",
                        "format": "PDF",
                        "datastore_active": False,
                        "url": DOWNLOAD.format("bbbb2222"),
                    },
                    {
                        "id": "cccc3333",
                        "name": "stat_by_wil_11_2025.xlsx",
                        "format": "XLSX",
                        # Uploaded but never parsed by CKAN: with the download
                        # challenged, there is no route to this one at all.
                        "datastore_active": False,
                        "last_modified": "2026-07-02T08:30:00.000000",
                        "url": DOWNLOAD.format("cccc3333"),
                    },
                ],
            },
            {
                "name": "documents-only",
                "title": "Laporan",
                "license_id": "cc-by",
                "resources": [
                    {"id": "dddd4444", "format": "PDF", "url": DOWNLOAD.format("dddd4444")}
                ],
            },
        ],
    },
}

DATASTORE = {
    "success": True,
    "result": {
        "resource_id": "aaaa1111",
        "fields": [
            {"id": "_id", "type": "int"},
            {"id": "Kode Bencana", "type": "numeric", "info": {"label": "Kode Jenis Bencana"}},
            {"id": "Jenis Bencana", "type": "text"},
            {"id": "Jumlah Kejadian", "type": "numeric"},
            {"id": "Meninggal", "type": "numeric"},
        ],
        "records": [
            {
                "_id": 1,
                "Kode Bencana": 101,
                "Jenis Bencana": "BANJIR",
                "Jumlah Kejadian": 2009,
                "Meninggal": 1353,
            },
            {
                "_id": 2,
                "Kode Bencana": 102,
                "Jenis Bencana": "TANAH LONGSOR",
                "Jumlah Kejadian": 330,
                "Meninggal": None,
            },
        ],
    },
}

#: What Cloudflare serves in place of a workbook.
CHALLENGE = (
    b"<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
    b"<body>Enable JavaScript and cookies to continue</body></html>"
)

WORKBOOK = b"PK\x03\x04" + b"\x00" * 64


# -- reading the catalogue --------------------------------------------------


def test_only_xlsx_resources_are_collected() -> None:
    """The Solr filter matches datasets, so a dataset arrives with its PDFs."""
    found = packages(CATALOGUE)
    assert [p.name for p in found] == ["kompilasi-data-kejadian-dan-dampak-bencana-2025"]
    assert [r.resource_id for r in found[0].resources] == ["aaaa1111", "cccc3333"]


def test_the_year_becomes_the_partition() -> None:
    assert packages(CATALOGUE)[0].partition == ("year=2025",)


def test_a_dataset_without_a_year_is_unpartitioned() -> None:
    body = {
        "success": True,
        "result": {
            "count": 1,
            "results": [
                {
                    "name": "data-bencana-indonesia",
                    "title": "Kompilasi Data Kejadian dan Dampak Bencana",
                    "resources": [{"id": "x", "format": "XLSX", "url": DOWNLOAD.format("x")}],
                }
            ],
        },
    }
    assert packages(body)[0].partition == ()


def test_a_refused_catalogue_is_not_read_as_an_empty_one() -> None:
    """CKAN answers `success: false` with a 200, which must not pass for
    "BNPB publishes nothing"."""
    with pytest.raises(ValueError, match="did not return a catalogue"):
        packages({"success": False, "error": {"message": "Bad search"}})


def test_the_search_url_carries_both_filters() -> None:
    url = search_url(start=50)
    assert "res_format%3AXLSX" in url
    assert "organization%3Apusdatinkom" in url
    assert "start=50" in url


# -- choosing a route -------------------------------------------------------


class FakePortal:
    """data.bnpb.go.id as it behaves today, unless a test says otherwise."""

    def __init__(self, *, downloads: bytes | int = 403) -> None:
        self.downloads = downloads
        self.requested: list[str] = []

    def get(self, url: str, **_: object) -> httpx.Response:
        self.requested.append(url)
        request = httpx.Request("GET", url)

        if "/download/" in url:
            if isinstance(self.downloads, int):
                response = httpx.Response(self.downloads, content=CHALLENGE, request=request)
                raise httpx.HTTPStatusError("challenged", request=request, response=response)
            return httpx.Response(200, content=self.downloads, request=request)

        if "package_search" in url:
            return httpx.Response(200, json=CATALOGUE, request=request)

        if "datastore_search" in url:
            return httpx.Response(200, json=DATASTORE, request=request)

        raise AssertionError(f"unexpected request: {url}")

    def try_get(self, url: str, **kwargs: object) -> httpx.Response | None:
        try:
            return self.get(url, **kwargs)
        except httpx.HTTPStatusError:
            return None


@pytest.fixture
def portal(monkeypatch: pytest.MonkeyPatch) -> FakePortal:
    fake = FakePortal()

    @contextmanager
    def _fetcher(**_: object) -> Iterator[FakePortal]:
        yield fake

    monkeypatch.setattr(disaster, "fetcher", _fetcher)
    return fake


def test_a_challenged_download_falls_back_to_the_datastore(portal: FakePortal) -> None:
    artifacts = list(DisasterData().collect(ScrapeContext()))

    assert [a.dataset for a in artifacts] == [
        "bnpb-catalogue",
        "kompilasi-data-kejadian-dan-dampak-bencana-2025",
    ]
    table = artifacts[-1]
    assert table.filename == "Rekapitulasi Kejadian Bencana 2025-00000.json"
    assert table.metadata["delivery"] == "datastore"
    assert table.metadata["license"] == "odc-by"
    assert table.partition == ("year=2025",)
    assert table.published_at == date(2026, 7, 2)


def test_the_challenge_is_only_met_once(portal: FakePortal) -> None:
    """It is site-wide, so asking two hundred times would be hammering someone
    else's server to learn what the first refusal already said."""
    list(DisasterData().collect(ScrapeContext()))
    assert len([url for url in portal.requested if "/download/" in url]) == 1


def test_a_challenge_page_never_lands_as_a_workbook(portal: FakePortal) -> None:
    """The portal also serves the challenge with a 200. Landing would refuse
    those bytes; this refuses them where the reason can be said."""
    portal.downloads = CHALLENGE

    artifacts = list(DisasterData().collect(ScrapeContext()))

    assert all(not a.filename.endswith(".xlsx") for a in artifacts)


def test_the_workbook_is_preferred_when_the_portal_serves_it(portal: FakePortal) -> None:
    """The datastore is a fallback, not the plan: BNPB's own bytes win the day
    the challenge is lifted."""
    portal.downloads = WORKBOOK

    artifacts = list(DisasterData().collect(ScrapeContext()))

    workbooks = [a for a in artifacts if a.filename.endswith(".xlsx")]
    assert [a.filename for a in workbooks] == [
        "Rekapitulasi Kejadian Bencana 2025.xlsx",
        "stat_by_wil_11_2025.xlsx",
    ]
    assert workbooks[0].metadata["delivery"] == "download"
    assert not any("datastore_search" in url for url in portal.requested)


def test_the_catalogue_lands_as_received(portal: FakePortal) -> None:
    """It is the only record of what BNPB offered on the day: a datastore page
    names neither its dataset nor its licence."""
    catalogue = next(iter(DisasterData().collect(ScrapeContext())))

    assert json.loads(catalogue.content)["result"]["count"] == 2
    assert catalogue.metadata["datasets"] == 1


def test_limit_stops_early(portal: FakePortal) -> None:
    assert len(list(DisasterData().collect(ScrapeContext(limit=1)))) == 1


def test_since_skips_resources_older_than_the_run(portal: FakePortal) -> None:
    artifacts = list(DisasterData().collect(ScrapeContext(since=date(2026, 8, 1))))

    assert [a.dataset for a in artifacts] == ["bnpb-catalogue"]


def test_a_dead_datastore_fails_the_run(portal: FakePortal) -> None:
    """One resource erroring is noise; every reachable one erroring is an
    outage, and a catalogue with no figures in it must not pass for a run."""

    def refuse(url: str, **_: object) -> httpx.Response | None:
        return None if "datastore_search" in url else portal.get(url)

    portal.try_get = refuse  # type: ignore[method-assign]

    with pytest.raises(PartialCatalogue):
        list(DisasterData().collect(ScrapeContext()))


# -- reading a datastore page -----------------------------------------------


def landed(
    tmp_path: Path, body: dict, *, dataset: str = "kejadian-2025", **extra: object
) -> Landed:
    path = tmp_path / "table-00000.json"
    path.write_text(json.dumps(body))
    return Landed(
        path=path,
        document_id="doc_1",
        content_hash="0" * 64,
        source_slug="bnpb-disaster",
        dataset=dataset,
        extra={"title": "Rekapitulasi Kejadian Bencana 2025.xlsx", **extra},
    )


def test_each_datastore_row_becomes_a_record(tmp_path: Path) -> None:
    rows = list(BnpbDatastoreExtractor().extract(landed(tmp_path, DATASTORE)))

    assert len(rows) == 2
    assert rows[0]["columns"]["Jenis Bencana"] == "BANJIR"
    assert rows[0]["columns"]["Jumlah Kejadian"] == "2009"
    # A null is an empty cell, not the string "None".
    assert rows[1]["columns"]["Meninggal"] == ""


def test_ckans_row_key_is_not_a_figure(tmp_path: Path) -> None:
    rows = list(BnpbDatastoreExtractor().extract(landed(tmp_path, DATASTORE)))

    assert "_id" not in rows[0]["columns"]


def test_the_uploaders_column_labels_travel_with_the_rows(tmp_path: Path) -> None:
    rows = list(BnpbDatastoreExtractor().extract(landed(tmp_path, DATASTORE)))

    assert rows[0]["columns"]["label:Kode Bencana"] == "Kode Jenis Bencana"


def test_row_numbers_continue_across_pages(tmp_path: Path) -> None:
    rows = list(BnpbDatastoreExtractor().extract(landed(tmp_path, DATASTORE, row_offset=10_000)))

    assert [row["row_number"] for row in rows] == [10_001, 10_002]


def test_the_catalogue_is_provenance_rather_than_records(tmp_path: Path) -> None:
    rows = list(
        BnpbDatastoreExtractor().extract(landed(tmp_path, CATALOGUE, dataset="bnpb-catalogue"))
    )

    assert rows == []


def test_the_catalogue_is_claimed_so_the_generic_reader_leaves_it_alone(
    tmp_path: Path,
) -> None:
    assert BnpbDatastoreExtractor().handles(landed(tmp_path, CATALOGUE, dataset="bnpb-catalogue"))


def test_an_answer_without_records_is_an_extraction_error(tmp_path: Path) -> None:
    with pytest.raises(ExtractionError):
        list(BnpbDatastoreExtractor().extract(landed(tmp_path, {"success": True, "result": {}})))


# -- the impact tables ------------------------------------------------------


PROVINCE_ROW = {
    "No.": "1",
    "Kode Wilayah Provinsi": "11",
    "Provinsi": "ACEH",
    "Latitude": "4.6951",
    "Longitude": "96.9103",
    "BANJIR": "564",
    "CUACA EKSTREM": "0",
    "ERUPSI GUNUNG API": "0",
    "GELOMBANG PASANG DAN ABRASI": "0",
    "GEMPABUMI": "0",
    "KEBAKARAN HUTAN DAN LAHAN": "0",
    "KEKERINGAN": "0.0",
    "TANAH LONGSOR": "2",
    "TSUNAMI": "0.0",
    "resource": "Jumlah Korban Meninggal Akibat Bencana Menurut Provinsi 2025.xlsx",
}

#: The same shape, except the columns are years. BNPB publishes one of these,
#: and reading it as hazards turned 2014 into a series.
YEARS_ROW = {
    "No.": "1",
    "Kode Wilayah Provinsi": "11",
    "Provinsi": "ACEH",
    "2010": "97",
    "2011": "84",
    "2012": "76",
    "2013": "61",
    "2014": "55",
    "resource": "Jumlah Kejadian Bencana Menurut Provinsi Tahun 2010-2024",
}


def impact(row: dict[str, str], *, period: str = "2025") -> list[dict]:
    return list(bnpb_impact.records(row, resource=row["resource"], period=period, row_number=1))


def test_a_province_row_becomes_one_record_per_hazard() -> None:
    rows = impact(PROVINCE_ROW)

    assert len(rows) == 9
    assert rows[0]["columns"]["indicator"] == "BNPB_DEATHS_FLOOD"
    assert rows[0]["columns"]["name"] == "Deaths from disasters: floods"
    assert rows[0]["columns"]["value"] == "564"
    assert rows[0]["columns"]["unit"] == "people"
    assert rows[0]["columns"]["period"] == "2025"
    assert rows[0]["dataset"] == bnpb_impact.IMPACT_DATASET


def test_each_hazard_gets_its_own_row_number() -> None:
    """Nine hazards sharing one row number would be one figure nine times."""
    rows = impact(PROVINCE_ROW)

    assert len({row["row_number"] for row in rows}) == 9


def test_a_rate_table_is_not_read_as_the_count_it_derives_from() -> None:
    row = dict(PROVINCE_ROW)
    row["resource"] = (
        "Jumlah Korban Meninggal dan Hilang per 100.000 Orang "
        "Akibat Bencana Menurut Provinsi 2025.xlsx"
    )

    assert impact(row)[0]["columns"]["indicator"] == "BNPB_DEATHS_MISSING_RATE_FLOOD"
    assert impact(row)[0]["columns"]["unit"] == "per 100,000"


def test_the_national_row_is_dropped() -> None:
    """It is the sum of the provinces, and keeping it would double every
    national total taken over the series."""
    row = dict(PROVINCE_ROW) | {"Kode Wilayah Provinsi": "", "Provinsi": "INDONESIA"}

    assert impact(row) == []


def test_a_table_of_years_is_not_a_table_of_hazards() -> None:
    assert not bnpb_impact.is_province_table(YEARS_ROW)
    assert impact(YEARS_ROW) == []


def test_the_hazard_table_is_recognised() -> None:
    assert bnpb_impact.is_province_table(PROVINCE_ROW)


def test_a_column_that_is_not_a_hazard_is_reported_rather_than_counted() -> None:
    """The rate tables carry the population as the denominator. Read as a
    hazard it would be a series of thirty million deaths."""
    row = dict(PROVINCE_ROW) | {"Jumlah Penduduk": "5470000"}

    assert bnpb_impact.unrecognised(row) == ["Jumlah Penduduk"]
    assert all(r["columns"]["hazard"] != "JUMLAH_PENDUDUK" for r in impact(row))


def test_an_empty_cell_is_not_a_zero() -> None:
    row = dict(PROVINCE_ROW) | {"BANJIR": ""}

    assert all(r["columns"]["hazard"] != "FLOOD" for r in impact(row))


def test_bnpbs_papua_codes_are_corrected_to_bps() -> None:
    """Every one of the six disagrees, so a code join does not fail — it files
    each province's casualties under its neighbour."""
    row = dict(PROVINCE_ROW) | {"Kode Wilayah Provinsi": "94", "Provinsi": "PAPUA TENGAH"}

    assert impact(row)[0]["columns"]["province_code"] == "96"
    assert impact(row)[0]["columns"]["province_code_published"] == "94"


def test_a_spelled_out_province_name_still_maps() -> None:
    """One table spells it `P A P U A`."""
    row = dict(PROVINCE_ROW) | {"Kode Wilayah Provinsi": "91", "Provinsi": "P A P U A"}

    assert impact(row)[0]["columns"]["province_code"] == "94"
    assert impact(row)[0]["columns"]["province"] == "P A P U A"


def test_a_province_outside_papua_keeps_the_code_bnpb_printed() -> None:
    assert impact(PROVINCE_ROW)[0]["columns"]["province_code"] == "11"


def test_the_year_comes_from_the_landing_partition() -> None:
    """It is the CKAN dataset's year. The table's own title need not carry
    one, and where it carries a range it is not the year of the figures."""
    assert bnpb_impact.period_for(("year=2024",), "data-bencana-indonesia") == "2024"
    assert bnpb_impact.period_for((), "kompilasi-data-kejadian-dan-dampak-bencana-2025") == "2025"
    assert bnpb_impact.period_for((), "data-bencana-indonesia") is None
