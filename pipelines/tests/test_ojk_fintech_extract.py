"""OJK's fintech lending workbooks: the province series read out of their tables."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from terusan_pipelines.extract import Landed, OjkFintechExtractor
from terusan_pipelines.extract.ojk_fintech import FIGURES_DATASET, sheet_figures, table_of

OUTSTANDING = [
    ["Tabel 9 Outstanding Pinjaman dan TWP90 Berdasarkan Lokasi \n"
     "Table 9 Outstanding Loan and TWP90  by Location "],
    ["Lokasi / Locations", "", "2025-11-30 00:00:00", "", "", "2025-12-01 00:00:00", "", ""],
    ["", "", "Jumlah Rekening Penerima Pinjaman Aktif (entitas)",
     "Outstanding Pinjaman (miliar Rp)", "TWP 90",
     "Jumlah Rekening Penerima Pinjaman Aktif (entitas)",
     "Outstanding Pinjaman (miliar Rp)", "TWP 90"],
    ["    a. Jawa", "", "15487172", "55135.28", "0.029", "16102482", "55888.34", "0.027"],
    ["", "1. Banten", "1552932", "5892.22", "0.021", "1622198", "5967.30", "0.020"],
    ["    b. Luar Jawa", "", "6833852", "21888.95", "0.018", "7065501", "22616.24", "0.019"],
    ["", "1. Nangroe Aceh Darussalam", "86340", "182.31", "0.010", "89711", "185.13", "1e-05"],
    ["    c. Luar Negeri", "", "100", "1.00", "0.001", "110", "1.10", "0.001"],
    ["", "JUMLAH", "22321024", "77024.24", "0.026", "23167983", "78504.58", "0.025"],
    ["Catatan : data yang disajikan termasuk Syariah"],
]  # fmt: skip

ACCUMULATED = [
    ["Tabel 15 Akumlasi Jumlah Transaksi Pemberi Pinjaman (satuan akun)\n"
     "Table 15 Accumulated Number of Lender Lending Transaction (unit of account)"],
    ["Lokasi / Locations", "", "2024-12-01 00:00:00", "2025-01-01 00:00:00"],
    ["", "", "Akumulasi Sejak Perusahaan Didirikan s.d Akhir Posisi Bulan",
     "Akumulasi Sejak Perusahaan Didirikan s.d Akhir Posisi Bulan"],
    ["    a. Jawa", "", "400", "410"],
    ["", "2. DKI Jakarta", "226671", "226907"],
    ["", "JUMLAH", "559682539", "570828582"],
]  # fmt: skip


def _figures(grid: list[list[str]]) -> dict[tuple[str, str, str], str]:
    return {
        (row["indicator"], row["geo"], row["period"]): row["value"]
        for row in sheet_figures("9", grid)
    }


def test_each_month_spans_its_columns_and_each_label_is_one_series() -> None:
    got = _figures(OUTSTANDING)

    assert got[("ojk_p2p_outstanding", "Provinsi Banten", "2025-12")] == "5967.3"
    assert got[("ojk_p2p_active_borrower_accounts", "Provinsi Banten", "2025-11")] == "1552932"
    assert got[("ojk_p2p_twp90", "Indonesia", "2025-12")] == "0.025"


def test_a_month_dated_its_last_day_is_that_month() -> None:
    periods = {period for _, _, period in _figures(OUTSTANDING)}

    assert periods == {"2025-11", "2025-12"}


def test_the_country_is_the_printed_total_and_island_groups_are_not_places() -> None:
    got = _figures(OUTSTANDING)
    places = {geo for _, geo, _ in got}

    # 77024.24 is more than the provinces add up to: lending from abroad is
    # in the total and in no province.
    assert got[("ojk_p2p_outstanding", "Indonesia", "2025-11")] == "77024.24"
    assert places == {"Indonesia", "Provinsi Banten", "Provinsi Nangroe Aceh Darussalam"}


def test_a_small_float_is_written_out_in_digits() -> None:
    got = _figures(OUTSTANDING)

    assert got[("ojk_p2p_twp90", "Provinsi Nangroe Aceh Darussalam", "2025-12")] == "0.00001"


def test_an_accumulated_table_is_one_series_a_column_a_month() -> None:
    got = {
        (row["indicator"], row["geo"], row["period"]): (row["value"], row["unit"])
        for row in sheet_figures("15", ACCUMULATED)
    }

    assert got == {
        ("ojk_p2p_lender_transactions_accumulated", "Provinsi DKI Jakarta", "2024-12"): (
            "226671",
            "accounts",
        ),
        ("ojk_p2p_lender_transactions_accumulated", "Provinsi DKI Jakarta", "2025-01"): (
            "226907",
            "accounts",
        ),
        ("ojk_p2p_lender_transactions_accumulated", "Indonesia", "2024-12"): (
            "559682539",
            "accounts",
        ),
        ("ojk_p2p_lender_transactions_accumulated", "Indonesia", "2025-01"): (
            "570828582",
            "accounts",
        ),
    }


def test_a_table_is_found_by_its_title_and_not_its_sheet() -> None:
    funds = table_of("Tabel 5 Dana yang Diberikan oleh Pemberi Pinjaman berdasarkan Lokasi")
    accumulated = table_of(
        "Tabel 17 Akumulasi Dana yang Diberikan oleh Pemberi Pinjaman (Miliar Rp)"
    )

    assert funds is not None and funds[1].indicator == "ojk_p2p_lender_funds"
    assert accumulated is not None
    assert accumulated[0].indicator == "ojk_p2p_lender_funds_accumulated"
    assert table_of("Tabel 7 Penyaluran Pinjaman pada Sektor Produktif (Miliar Rp)") is None


def test_a_workbook_lands_its_cells_and_its_figures(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "15"
    for row in ACCUMULATED:
        sheet.append(
            [datetime.fromisoformat(cell) if cell.startswith("20") else cell for cell in row]
        )
    buffer = io.BytesIO()
    workbook.save(buffer)
    path = tmp_path / "fintech-lending.xlsx"
    path.write_bytes(buffer.getvalue())

    landed = Landed(
        path=path,
        document_id="doc_p2p",
        content_hash="0" * 64,
        source_slug="ojk-fintech-p2p",
        dataset="p2p-lending",
        original_filename=path.name,
    )
    extractor = OjkFintechExtractor()
    assert extractor.handles(landed)
    rows = list(extractor.extract(landed))

    cells = [row for row in rows if row["dataset"] == "p2p-lending"]
    figures = [row["columns"] for row in rows if row["dataset"] == FIGURES_DATASET]
    assert len(cells) == 6
    assert {figure["period"] for figure in figures} == {"2024-12", "2025-01"}
    assert len(figures) == 4
