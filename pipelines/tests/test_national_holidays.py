"""The SKB 3 Menteri on national holidays: finding every decree behind the
JDIH's Livewire pages, and reading the scanned annex back into dates that fall
on the weekdays the decree prints beside them.
"""

from __future__ import annotations

import html
import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import httpx
import pytest

from terusan_pipelines.extract import DEFAULT_EXTRACTORS, Landed, MenpanHolidaysExtractor, menpan
from terusan_pipelines.extract.base import ExtractionError
from terusan_pipelines.extract.menpan import (
    COLLECTIVE,
    NATIONAL,
    parse_dates,
    weekdays_agree,
)
from terusan_pipelines.sources import registry
from terusan_pipelines.sources.base import ScrapeContext
from terusan_pipelines.sources.http import Fetcher
from terusan_pipelines.sources.menpan import (
    NationalHolidays,
    decrees_in,
    is_holiday_decree,
    target_year,
)
from terusan_pipelines.sources.menpan import holidays as source

# --- the portal ----------------------------------------------------------


def record(id: int, title: str, enacted: str) -> dict[str, Any]:
    return {
        "id": id,
        "slug": f"keputusan-bersama-menteri-{id}",
        "judul": title,
        "nomor_peraturan": "2",
        "tanggal_penetapan": enacted,
        "status": "Berlaku",
    }


# As the portal types them: the Cyrillic `а` in "Bersamа" is theirs.
HOLIDAYS_2027 = record(
    2131,
    "Keputusan Bersama Menteri Agama, Menteri Ketenagakerjaan, dan Menteri "
    "Pendayagunaan Aparatur Negara dan Reformasi Birokrasi Nomor 2 Tahun 2026 "
    "tentang Hari Libur Nasional dan Cuti Bersamа Tahun 2027",
    "2026-09-15",
)
AMENDED_2025 = record(
    2034,
    "Keputusan Bersama … Nomor 3 Tahun 2025 tentang Perubahan Atas Keputusan "
    "Bersama … tentang Hari Libur Nasional dan Cuti Bersamа Tahun 2025",
    "2025-08-07",
)
NEUTRALITY = record(
    1689,
    "Keputusan Bersama … tentang Pedoman Pembinaan dan Pengawasan Netralitas "
    "Pegawai Aparatur Sipil Negara Dalam Penyelenggaraan Pemilihan Umum",
    "2022-09-22",
)


def results_snapshot(records: list[dict[str, Any]], pages: int) -> dict[str, Any]:
    return {
        "data": {
            "data": [[[r, {"s": "arr"}] for r in records], {"s": "arr"}],
            "totalRows": len(records) * pages,
            "totalPage": pages,
            "currentPage": 1,
        },
        "memo": {"id": "abc", "name": "dokumen.dokumen-search-result"},
        "checksum": "0" * 64,
    }


def page_with(*snapshots: dict[str, Any]) -> str:
    divs = "".join(
        f'<div wire:snapshot="{html.escape(json.dumps(s), quote=True)}"></div>' for s in snapshots
    )
    cookie = {"data": [], "memo": {"name": "pengunjung-cookie"}}
    return (
        '<html><body><script data-csrf="token123" data-update-uri="/livewire/update"></script>'
        f'<div wire:snapshot="{html.escape(json.dumps(cookie), quote=True)}"></div>'
        f"{divs}</body></html>"
    )


def detail_page(pdf: str) -> str:
    return page_with(
        {
            "data": {
                "slug": "x",
                "data": [
                    {
                        "attachment": [
                            {"judul_lampiran": "lampiran", "dokumen_lampiran": pdf},
                            {"s": "arr"},
                        ]
                    },
                    {"s": "arr"},
                ],
            },
            "memo": {"name": "dokumen.dokumen-detail"},
        }
    )


PDF = b"%PDF-1.4\n%fake\n"


def serve(requests: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if request.method == "POST" and url.endswith("/livewire/update"):
            body = json.loads(request.content)
            assert body["_token"] == "token123"
            call = body["components"][0]["calls"][0]
            assert call["method"] == "searchDokumen" and call["params"] == [2]
            snapshot = results_snapshot([NEUTRALITY], pages=2)
            return httpx.Response(
                200, json={"components": [{"snapshot": json.dumps(snapshot), "effects": {}}]}
            )
        if "hasil-pencarian" in url:
            return httpx.Response(
                200, text=page_with(results_snapshot([HOLIDAYS_2027, AMENDED_2025], pages=2))
            )
        if url.endswith("-2131"):
            return httpx.Response(
                200, text=detail_page("https://data-jdih.menpan.go.id/dokumen/2026skb002.pdf")
            )
        if url.endswith("-2034"):
            return httpx.Response(
                200, text=detail_page("https://data-jdih.menpan.go.id/dokumen/2025skb003.pdf")
            )
        if url.endswith(".pdf"):
            return httpx.Response(200, content=PDF)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def portal(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    @contextmanager
    def fake_fetcher(**_: object) -> Iterator[Fetcher]:
        with httpx.Client(transport=serve(requests), follow_redirects=True) as client:
            yield Fetcher(client, attempts=1)

    monkeypatch.setattr(source, "fetcher", fake_fetcher)
    return requests


def test_titles_are_read_through_the_cyrillic_letters() -> None:
    assert is_holiday_decree(HOLIDAYS_2027["judul"])
    assert not is_holiday_decree(NEUTRALITY["judul"])
    # The year the calendar is for, not the year the decree was signed.
    assert target_year(HOLIDAYS_2027["judul"]) == 2027
    assert target_year(AMENDED_2025["judul"]) == 2025


def test_the_search_page_carries_its_first_results_and_page_count() -> None:
    decrees, pages = decrees_in(page_with(results_snapshot([HOLIDAYS_2027], pages=5)))
    assert pages == 5
    assert decrees[0].id == 2131
    assert decrees[0].enacted == date(2026, 9, 15)
    assert "Bersama Tahun 2027" in decrees[0].title


def test_collect_pages_through_livewire_and_lands_holiday_decrees_only(
    portal: list[httpx.Request],
) -> None:
    artifacts = list(NationalHolidays().collect(ScrapeContext()))

    assert any(r.method == "POST" for r in portal), "page 2 is only served to a Livewire call"
    assert [a.filename for a in artifacts] == [
        "skb-2131.html",
        "2026skb002.pdf",
        "skb-2034.html",
        "2025skb003.pdf",
    ]
    pdf = artifacts[1]
    assert pdf.dataset == "skb-hari-libur"
    assert pdf.partition == ("year=2027",)
    assert pdf.metadata["target_year"] == 2027
    assert pdf.metadata["amends"] is False
    assert artifacts[3].metadata["amends"] is True
    # The neutrality decree on page 2 was listed and left.
    assert not any("1689" in a.filename for a in artifacts)


def test_since_skips_decrees_enacted_before_it(portal: list[httpx.Request]) -> None:
    artifacts = list(NationalHolidays().collect(ScrapeContext(since=date(2026, 1, 1))))
    assert [a.filename for a in artifacts] == ["skb-2131.html", "2026skb002.pdf"]


def test_the_source_is_registered_and_scheduled() -> None:
    source_class = registry.get("menpan-hari-libur")
    assert source_class is NationalHolidays
    assert source_class.meta.schedule


# --- reading the annex ---------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("1 Januari", ["2027-01-01"]),
        ("10-11 Maret", ["2027-03-10", "2027-03-11"]),
        ("31 Maret-1 April", ["2027-03-31", "2027-04-01"]),
        ("9,12, dan 15 Maret", ["2027-03-09", "2027-03-12", "2027-03-15"]),
        ("28 dan 30 Oktober", ["2027-10-28", "2027-10-30"]),
        (
            "29 April, 4 Mei, 5 Mei, dan 6 Mei",
            ["2027-04-29", "2027-05-04", "2027-05-05", "2027-05-06"],
        ),
        # OCR slips in the month, and the row number let in by a broken rule.
        ("17 Agustns", ["2027-08-17"]),
        ("10. | 29 Mei", ["2027-05-29"]),
        # A year with no cuti bersama prints a dash.
        ("-", []),
        ("31 Februari", []),
    ],
)
def test_date_cells(cell: str, expected: list[str]) -> None:
    assert [d.isoformat() for d in parse_dates(cell, 2027)] == expected


def test_a_row_is_kept_only_when_its_dates_fall_on_its_weekdays() -> None:
    assert weekdays_agree(parse_dates("10-11 Maret", 2027), "Rabu-Kamis")
    assert weekdays_agree(parse_dates("9,12, dan 15 Maret", 2027), "Selasa, Jumat, dan Senin")
    assert weekdays_agree(parse_dates("22 Maret", 2020), "Minssu")  # Minggu, blurred
    # "25 Maret" misread as "5 Maret": a Thursday is not a Friday.
    assert not weekdays_agree(parse_dates("5 Maret", 2027), "Kamis")
    assert not weekdays_agree([], "Kamis")


def test_the_annex_title_names_both_sections_and_is_not_a_heading() -> None:
    title = (
        "TENTANG\nHARI LIBUR NASIONAL DAN CUTI BERSAMA TAHUN 2027\n"
        "A. HARI LIBUR NASIONAL TAHUN 2027"
    )
    assert menpan._section_named(title) == NATIONAL
    assert menpan._section_named("B. CUTI BERSAMA TAHUN 2027") == COLLECTIVE
    assert menpan._section_named("HARI LIBUR NASIONAL DAN CUTI BERSAMA TAHUN 2027") is None


def ruled_page(rows: int, columns: list[int], row_height: int = 70) -> Any:
    """A page with one ruled table and nothing in it."""
    from PIL import Image, ImageDraw

    page = Image.new("L", (2480, 1600), 255)
    draw = ImageDraw.Draw(page)
    top = 300
    bottom = top + rows * row_height
    for n in range(rows + 1):
        y = top + n * row_height
        draw.line([(columns[0], y), (columns[-1], y)], fill=0, width=3)
    for x in columns:
        draw.line([(x, top), (x, bottom)], fill=0, width=3)
    # Left-aligned first digits, stacked row on row: not a rule.
    for n in range(rows):
        y = top + n * row_height + 15
        draw.rectangle([(columns[1] + 20, y), (columns[1] + 26, y + 40)], fill=0)
    return page


def test_the_grid_is_found_from_its_rules() -> None:
    pytest.importorskip("numpy")
    columns = [300, 450, 900, 1300, 2200]
    tables = list(menpan._tables(ruled_page(rows=5, columns=columns)))
    assert len(tables) == 1
    assert len(tables[0].rows) == 5
    # Four cells a row, and the stacked digits did not split one.
    assert {len(row) for row in tables[0].rows} == {4}


def test_rows_are_read_from_their_cells_and_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("numpy")
    table = [
        ["NO.", "TANGGAL", "HARI", "KETERANGAN"],
        ["1.", "1 Januari", "Jumat", "Tahun Baru 2027 Masehi"],
        ["5.", "10-11 Maret", "Rabu-Kamis", "Idul Fitri 1448 Hijriah"],
        # Misread: 26 Maret is a Friday, 25 a Thursday.
        ["6.", "25 Maret", "Jumat", "Wafat Yesus Kristus"],
    ]
    cells = iter(cell for row in table for cell in row)
    monkeypatch.setattr(menpan, "_straighten", lambda page: page)
    holidays, refused = menpan.read_annex(
        [ruled_page(rows=4, columns=[300, 450, 900, 1300, 2200])],
        2027,
        readers=(lambda image: next(cells),),
        heading=lambda image: "A. HARI LIBUR NASIONAL TAHUN 2027",
    )
    assert [(h.date.isoformat(), h.section) for h in holidays] == [
        ("2027-01-01", NATIONAL),
        ("2027-03-10", NATIONAL),
        ("2027-03-11", NATIONAL),
    ]
    assert holidays[1].name == "Idul Fitri 1448 Hijriah"
    assert refused == 1


def test_a_decree_read_short_is_refused_whole(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "2026skb002.pdf"
    path.write_bytes(PDF)
    landed = Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="menpan-hari-libur",
        dataset="skb-hari-libur",
        extra={"target_year": 2027, "decree_id": 2131},
    )
    monkeypatch.setattr(menpan.shutil, "which", lambda _: "/usr/bin/tesseract")
    monkeypatch.setattr(menpan, "render", lambda _: [])
    with pytest.raises(ExtractionError, match="national holidays"):
        list(MenpanHolidaysExtractor().extract(landed))


def test_the_extractor_claims_the_decrees_before_the_generic_pdf_reader(tmp_path: Any) -> None:
    path = tmp_path / "2026skb002.pdf"
    path.write_bytes(PDF)
    landed = Landed(
        path=path, document_id="d", content_hash="0" * 64, source_slug="menpan-hari-libur"
    )
    claimant = next(e for e in DEFAULT_EXTRACTORS if e.handles(landed))
    assert isinstance(claimant, MenpanHolidaysExtractor)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs tesseract")
def test_tesseract_reads_a_printed_row() -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("L", (700, 90), 255)
    ImageDraw.Draw(image).text((10, 15), "10-11 Maret", fill=0, font=ImageFont.load_default(48))
    try:
        text = menpan.read_block(image)
    except FileNotFoundError:
        pytest.skip("tesseract has no Indonesian model")
    assert [d.isoformat() for d in parse_dates(text, 2027)] == ["2027-03-10", "2027-03-11"]
