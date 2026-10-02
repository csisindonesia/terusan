"""OJK's SPI workbooks: the province series read out of an edition's sheets."""

from __future__ import annotations

import io
from pathlib import Path

from openpyxl import Workbook

from terusan_pipelines.extract import Landed, OjkSpiExtractor
from terusan_pipelines.extract.ojk_spi import FIGURES_DATASET, month_of, sheet_figures

DPK_TITLE = (
    "Tabel 1.33.a. \nKomposisi DPK Bank Umum Berdasarkan Lokasi Penghimpunan Dana {month}\n"
    "(Composition of Third Party Funds of Commercial Banks Based on Location)\n"
    "Miliar Rp (Billion Rp)"
)
DPK_HEADER = [
    "Lokasi", "", "Giro\n(Demand Deposits)", "", "Tabungan \n(Saving Deposits)", "",
    "Deposito\n(Time Deposits)", "", "Total DPK\n(Total Third Party Funds)", "", "",
    "Pangsa Thd Total DPK (%)",
]  # fmt: skip
DPK_SUB = [
    "",
    "",
    "Rupiah",
    "Valas",
    "Rupiah",
    "Valas",
    "Rupiah",
    "Valas",
    "Rupiah",
    "Valas",
    "Total",
    "",
]


def _dpk_block(month: str, scale: int = 1) -> list[list[str]]:
    """One month's composition table, as the older editions lay it out from column A."""
    return [
        [DPK_TITLE.format(month=month)] + [""] * 11,
        DPK_HEADER,
        DPK_SUB,
        ["1.", "Jawa Barat", *[str(v * scale) for v in (10, 1, 20, 2, 30, 3, 60, 6, 66)], "50"],
        ["2.", "Papua", *[str(v * scale) for v in (5, 0, 5, 0, 5, 0, 15, 0, 15)], "10"],
        ["3.", "Lainnya", *[str(v * scale) for v in (1, 0, 1, 0, 1, 0, 3, 0, 3)], "2"],
        ["Total", "", *[str(v * scale) for v in (16, 1, 26, 2, 36, 3, 78, 6, 84)], "100"],
    ]


def _side_by_side(*blocks: list[list[str]]) -> list[list[str]]:
    return [
        sum((block[row] + ["", "", ""] for block in blocks), []) for row in range(len(blocks[0]))
    ]


def _figures(title: str, grid: list[list[str]], edition: str) -> dict[tuple[str, str, str], str]:
    return {
        (row["indicator"], row["geo"], row["category"]): row["value"]
        for row in sheet_figures(title, grid, edition)
    }


def test_a_deposit_type_is_its_rupiah_and_its_foreign_exchange_added() -> None:
    got = _figures("Komp DPK per Lok_1.45.a.", _dpk_block("Juni 2015"), "2015-06")

    assert got[("ojk_spi_bu_dpk", "Provinsi Jawa Barat", "1. Giro")] == "11"
    assert got[("ojk_spi_bu_dpk", "Provinsi Jawa Barat", "")] == "66"
    assert got[("ojk_spi_bu_dpk_currency", "Provinsi Jawa Barat", "002. Valas")] == "6"


def test_the_country_is_the_printed_total_and_unplaced_rows_are_in_no_province() -> None:
    got = _figures("Komp DPK per Lok_1.45.a.", _dpk_block("Juni 2015"), "2015-06")

    # 84, not the 81 the two provinces add up to: `Lainnya` is in the total.
    assert got[("ojk_spi_bu_dpk", "Indonesia", "")] == "84"
    assert not any("Lainnya" in geo for _, geo, _ in got)


def test_papua_is_the_territory_before_the_split() -> None:
    got = _figures("Komp DPK per Lok_1.45.a.", _dpk_block("Juni 2015"), "2015-06")

    assert ("ojk_spi_bu_dpk", "Papua (wilayah sebelum pemekaran 2022)", "") in got


def test_only_the_block_for_the_editions_own_month_is_read() -> None:
    # An older sheet carries the months before the edition's beside it, and
    # an edition revises them; the month comes from the edition named after it.
    grid = _side_by_side(_dpk_block("Mei 2018", scale=1000), _dpk_block("Agustus 2018"))

    got = _figures("Komp DPK per Lok_1.45.a.", grid, "2018-08")

    assert got[("ojk_spi_bu_dpk", "Indonesia", "")] == "84"


def test_a_sheet_without_the_editions_month_gives_nothing() -> None:
    assert not _figures("Komp DPK per Lok_1.45.a.", _dpk_block("Mei 2018"), "2018-08")


def test_the_newest_block_titled_a_year_early_is_the_editions() -> None:
    # January to March 2025 kept 2024 in their titles; their totals are 2025's.
    got = _figures("Komp DPK per Lok_1.33.a.", _dpk_block("Januari 2024"), "2025-01")

    assert got[("ojk_spi_bu_dpk", "Indonesia", "")] == "84"


def test_an_older_block_titled_a_year_early_is_not_taken() -> None:
    grid = _side_by_side(_dpk_block("Januari 2024"), _dpk_block("Desember 2024"))

    assert not _figures("Komp DPK per Lok_1.33.a.", grid, "2025-01")


CREDIT = [
    ["", "Tabel 3.12.a. \nKredit ... per Lokasi Dati I - Juni 2025\n(Credit ... for June 2025)"],
    ["", "Lokasi / Location", "", "Modal Kerja\n(Working Capital)", "Investasi\n(Investment)",
     "Konsumsi\n(Consumption)", "Orientasi Penggunaan  (User Orientation)", "", ""],
    ["", "", "", "", "", "", "Ekspor", "Impor", "Lainnya"],
    ["", "1.", "Jawa Barat", "100", "50", "25", "5", "1", "169"],
    ["", "", "   NPL/NPF", "4", "2", "1", "0", "0", "7"],
    ["", "Total", "", "100", "50", "25", "5", "1", "169"],
    ["", "", "   NPL/NPF", "4", "2", "1", "0", "0", "7"],
]  # fmt: skip


def test_non_performing_credit_is_the_row_beneath_each_province() -> None:
    got = _figures("Kredit JP-OP per Lok._3.12.a.", CREDIT, "2025-06")

    assert got[("ojk_spi_bu_credit", "Provinsi Jawa Barat", "002. Investasi")] == "50"
    assert got[("ojk_spi_bu_credit", "Provinsi Jawa Barat", "")] == "175"
    assert got[("ojk_spi_bu_credit_orientation", "Provinsi Jawa Barat", "003. Lainnya")] == "169"
    assert got[("ojk_spi_bu_npl_purpose", "Indonesia", "001. Modal Kerja")] == "4"
    assert got[("ojk_spi_bu_npl_purpose", "Indonesia", "")] == "7"


BRANCHES = [
    ["Tabel 1.43.a. \nPerkembangan Jumlah Kantor Cabang Bank Berdasarkan Lokasi Bank"],
    ["Lokasi / Location", "", "2023", "2024", "", "2025"],
    ["", "", "", "Des r)", "Jan", "Feb"],
    ["1.", "Jawa Barat", "380", "373", "372", "371"],
    ["2.", "Irian Jaya Barat", "26", "29", "29", "30"],
    ["Total", "", "3420", "3402", "3401", "3400"],
    ["r) Angka-angka diperbaiki"],
]


def test_a_time_series_table_gives_its_rightmost_month() -> None:
    got = _figures("Juml KC per Lok_1.43.a.", BRANCHES, "2025-02")

    assert got == {
        ("ojk_spi_bu_branch_offices", "Provinsi Jawa Barat", ""): "371",
        ("ojk_spi_bu_branch_offices", "Papua Barat (wilayah sebelum pemekaran 2022)", ""): "30",
        ("ojk_spi_bu_branch_offices", "Indonesia", ""): "3400",
    }


def test_a_time_series_table_not_brought_up_to_date_is_skipped() -> None:
    # Its last column is February; filed under March it would be a month old.
    assert not _figures("Juml KC per Lok_1.43.a.", BRANCHES, "2025-03")


MSME = [
    ["Tabel 3.22.a.\nKredit/Pembiayaan UMKM dan MKM Menurut Lokasi Proyek"],
    ["Lokasi / Location", "", "2024", "2025"],
    ["", "", "Des", "Jan"],
    ["Usaha Mikro Kecil dan Menengah (UMKM) / Micro, Small and Medium Enterprises"],
    ["1.", "Jawa Barat", "190", "191"],
    ["35.", "Tidak Teridentifikasi", "3", "4"],
    ["", "Jumlah / Total", "1000", "1001"],
    ["MKM 1)"],
    ["1.", "Jawa Barat", "", ""],
]


def test_the_msme_table_stops_at_its_first_section() -> None:
    got = _figures("UMKM Lok. Dati I_3.22.a.", MSME, "2025-01")

    assert got == {
        ("ojk_spi_bu_credit_msme", "Provinsi Jawa Barat", ""): "191",
        ("ojk_spi_bu_credit_msme", "Indonesia", ""): "1001",
    }


RURAL_OFFICES = [
    ["", "Tabel 2.14\nPerkembangan Jumlah BPR dan Kantor BPR Berdasarkan Lokasi -- Juni 2025\n"
         "(Growth of Total Rural Bank and Rural Banks Offices Based on Location - June 2025)"],
    ["", "Lokasi / Location", "", "BPR (Rural Bank)", "", "", "Total", "", ""],
    ["", "", "", "KP", "KC", "KPK", "", "", ""],
    ["", "1.", "Jawa Barat", "216", "374", "286", "876", "", ""],
    ["", "32.", "Papua", "6", "12", "3", "21", "", ""],
    ["", "35.", "Papua Barat Daya", "2", "3", "0", "5", "", ""],
    ["", "39.", "Lainnya / Others", "0", "0", "0", "0", "", ""],
    ["", "             Total", "", "1339", "1964", "2609", "5912", "", ""],
]  # fmt: skip


def test_the_rural_bank_office_table_gives_each_kind_of_office() -> None:
    got = _figures("Juml BPR per Lok_2.14", RURAL_OFFICES, "2025-06")

    # `KP` is read as itself, not as the beginning of `KPK` beside it; and the
    # blank columns after `Total` are not more of it.
    assert got[("ojk_spi_bpr_offices", "Provinsi Jawa Barat", "1. Kantor Pusat")] == "216"
    assert got[("ojk_spi_bpr_offices", "Provinsi Jawa Barat", "3. Kantor Pelayanan Kas")] == "286"
    assert got[("ojk_spi_bpr_offices", "Indonesia", "")] == "5912"


def test_a_table_listing_the_new_provinces_keeps_papua_as_todays() -> None:
    got = _figures("Juml BPR per Lok_2.14", RURAL_OFFICES, "2025-06")

    assert got[("ojk_spi_bpr_offices", "Provinsi Papua", "")] == "21"
    assert got[("ojk_spi_bpr_offices", "Provinsi Papua Barat Daya", "")] == "5"
    assert not any("pemekaran" in geo for _, geo, _ in got)


RURAL_CREDIT = [
    ["", "Tabel 3.18.a.\nKredit Bank Perekonomian Rakyat Berdasarkan Lokasi Bank Penyalur\n"
         "(Credit of Rural Banks Based on Banks Location)\nMiliar Rp (Billion Rp)"],
    ["", "Lokasi / Location", "", "2024", "", "2025"],
    ["", "", "", "Des", "Jan", "Feb"],
    ["", "1.", "Jawa Barat", "17713.4", "17618.7", "17981.2"],
    ["", "39", "Lainnya / Others", "0", "0", "0"],
    ["", " Total", "", "150000", "151000", "152000"],
]  # fmt: skip


def test_rural_bank_credit_is_a_time_series_table() -> None:
    got = _figures("Kredit BPR per Lok_3.18.a.", RURAL_CREDIT, "2025-02")

    assert got == {
        ("ojk_spi_bpr_credit", "Provinsi Jawa Barat", ""): "17981.2",
        ("ojk_spi_bpr_credit", "Indonesia", ""): "152000",
    }


def test_an_editions_month_is_read_from_its_title() -> None:
    assert month_of("Statistik Perbankan Indonesia - Agustus 2018") == "2018-08"
    assert month_of("spi statistik perbankan indonesia desember 2020.zip") == "2020-12"


def test_an_edition_lands_its_cells_and_its_figures(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Komp DPK per Lok_1.33.a."
    for row in _dpk_block("Juni 2025"):
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    path = tmp_path / "spi-statistik-perbankan-indonesia-juni-2025.xlsx"
    path.write_bytes(buffer.getvalue())

    landed = Landed(
        path=path,
        document_id="doc_spi",
        content_hash="0" * 64,
        source_slug="ojk-banking-spi",
        dataset="banking-statistics",
        original_filename=path.name,
        extra={"edition": "Statistik Perbankan Indonesia - Juni 2025"},
    )
    extractor = OjkSpiExtractor()
    assert extractor.handles(landed)
    rows = list(extractor.extract(landed))

    cells = [row for row in rows if row["dataset"] == "banking-statistics"]
    figures = [row["columns"] for row in rows if row["dataset"] == FIGURES_DATASET]
    assert len(cells) == 7
    assert {figure["period"] for figure in figures} == {"2025-06"}
    assert {figure["unit"] for figure in figures} == {"IDR billion"}
