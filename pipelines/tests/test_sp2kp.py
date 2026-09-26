"""Kemendag's SP2KP crosstab: reading it, and the two ways it could be misread.

The export is UTF-16, tab-separated, and dates its prices in a column header.
The fixture below is the real thing cut to four rows — the same encoding, the
same padded headers (`Komoditas `, `HET/HA `), the same thousands separators,
and one good with no ceiling.
"""

from __future__ import annotations

import pytest

from terusan_pipelines.extract import Landed, Sp2kpPricesExtractor
from terusan_pipelines.extract.base import ExtractionError

HEADER = "No\tKode Wilayah\tProvinsi\tKabupaten Kota\tKomoditas \tHET/HA \t23/09/2026"
ROWS = [
    "1\t1101\tAceh\tKab. Aceh Selatan\tBawang Merah\t41.500\t31.667",
    "2\t1101\tAceh\tKab. Aceh Selatan\tBeras Medium\t14.000\t14.500",
    # Bulk cooking oil has no ceiling, and the cell is empty rather than zero.
    "3\t1101\tAceh\tKab. Aceh Selatan\tMinyak Goreng Sawit Curah\t\t19.000",
    # A 2022 Papua code, which the geography registry answers to by alias.
    "4\t9401\tPapua Tengah\tKab. Nabire\tDaging Sapi Paha Belakang\t140.000\t150.000",
]


def crosstab(tmp_path, header: str = HEADER, rows: list[str] | None = None):
    path = tmp_path / "prices-2026-09-23.csv"
    body = "\n".join([header, *(ROWS if rows is None else rows)]) + "\n"
    # UTF-16 with a BOM, which is what Tableau writes.
    path.write_bytes(body.encode("utf-16"))
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="kemendag-sp2kp-prices",
        dataset="sp2kp-food-prices",
    )


def test_the_reader_claims_this_source_and_not_another(tmp_path) -> None:
    landed = crosstab(tmp_path)
    assert Sp2kpPricesExtractor().handles(landed)

    other = crosstab(tmp_path)
    object.__setattr__(other, "source_slug", "bi-pihps-food-prices")
    assert not Sp2kpPricesExtractor().handles(other)


def test_the_date_moves_out_of_the_header_and_onto_every_row(tmp_path) -> None:
    """The price column's header *is* the date, because the file is one day's
    crosstab. Left there, each day's landing would make a column named after
    that day and nothing would tie a row to a period."""
    rows = list(Sp2kpPricesExtractor().extract(crosstab(tmp_path)))

    assert len(rows) == 4
    assert {r["columns"]["date"] for r in rows} == {"2026-09-23"}
    assert [r["row_number"] for r in rows] == [1, 2, 3, 4]


def test_the_thousands_separator_is_left_for_silver_to_read(tmp_path) -> None:
    """`41.500` is forty-one thousand five hundred rupiah. Read as an English
    decimal it is 41.5 — a thousandfold error that would look plausible in a
    chart. Bronze keeps the ministry's own spelling and the mapping declares
    `--number-format id`."""
    rows = list(Sp2kpPricesExtractor().extract(crosstab(tmp_path)))

    assert rows[0]["columns"]["price"] == "31.667"
    assert rows[0]["columns"]["ceiling"] == "41.500"


def test_a_good_with_no_ceiling_keeps_an_empty_one(tmp_path) -> None:
    """Bulk cooking oil has no HET. A zero would say the ceiling is nothing."""
    rows = list(Sp2kpPricesExtractor().extract(crosstab(tmp_path)))

    assert rows[2]["columns"]["komoditas"] == "Minyak Goreng Sawit Curah"
    assert rows[2]["columns"]["ceiling"] == ""
    assert rows[2]["columns"]["price"] == "19.000"


def test_the_padded_headers_are_matched_after_stripping(tmp_path) -> None:
    """The export writes `Komoditas ` and `HET/HA ` with a trailing space, and
    a reader matching them literally would treat both as date columns."""
    rows = list(Sp2kpPricesExtractor().extract(crosstab(tmp_path)))

    assert rows[1]["columns"]["komoditas"] == "Beras Medium"
    assert rows[1]["columns"]["ceiling"] == "14.000"


def test_the_regency_code_is_carried_for_the_mapping_to_resolve(tmp_path) -> None:
    """The code and not the name: two regencies are called Banjar, and `Kab.`
    against `Kota` is not something a normalized name keeps."""
    rows = list(Sp2kpPricesExtractor().extract(crosstab(tmp_path)))

    assert rows[3]["columns"]["kode_wilayah"] == "9401"
    assert rows[3]["columns"]["kabupaten_kota"] == "Kab. Nabire"


def test_a_crosstab_with_no_priced_day_is_a_failure(tmp_path) -> None:
    """The view answered and priced nothing. A run that lands it quietly would
    report success and add no figures."""
    header = "No\tKode Wilayah\tProvinsi\tKabupaten Kota\tKomoditas \tHET/HA "
    row = "1\t1101\tAceh\tKab. Aceh Selatan\tBeras Medium\t14.000"
    landed = crosstab(tmp_path, header=header, rows=[row])

    with pytest.raises(ExtractionError, match="no priced day"):
        list(Sp2kpPricesExtractor().extract(landed))


def test_a_column_that_is_neither_dimension_nor_date_is_refused(tmp_path) -> None:
    """Rather than silently dropped. A new column is Kemendag changing the
    export, and guessing what it means is how a figure lands under the wrong
    series."""
    header = HEADER + "\tSesuatu Yang Baru"
    landed = crosstab(tmp_path, header=header, rows=[ROWS[0] + "\t1.000"])

    with pytest.raises(ExtractionError, match="neither a known dimension nor a date"):
        list(Sp2kpPricesExtractor().extract(landed))


# -- what the mapping depends on --------------------------------------------


def test_every_commodity_sp2kp_prints_resolves() -> None:
    """The seventeen Indonesian labels the view returns, against the commodity
    registry. An unresolved one lands with no commodity and disappears from the
    dimension the dataset is worth having."""
    from terusan_pipelines.normalize import commodity_registry

    registry = commodity_registry()
    labels = [
        "Bawang Merah",
        "Bawang Putih Honan",
        "Beras Medium",
        "Beras Premium",
        "Cabai Merah Besar",
        "Cabai Merah Keriting",
        "Cabai Rawit Merah",
        "Daging Ayam Ras",
        "Daging Sapi Paha Belakang",
        "Garam Halus",
        "Gula Pasir Curah",
        "Ikan Kembung",
        "Minyak Goreng Sawit Curah",
        "Minyak Goreng Sawit Kemasan Premium",
        "Minyakita",
        "Telur Ayam Ras",
        "Tepung Terigu",
    ]

    unresolved = [
        label for label in labels if getattr(registry.resolve(label), "identifier", None) is None
    ]

    assert unresolved == []


def test_bank_indonesias_own_labels_still_resolve_where_they_did() -> None:
    """The SP2KP aliases were added beside PIHPS's, not over them. `Daging Ayam
    Ras` and `Daging Ayam Ras Segar` are one good under two spellings; `Beras
    Medium` and `Beras Kualitas Medium I` are two goods under two taxonomies."""
    from terusan_pipelines.normalize import commodity_registry

    registry = commodity_registry()
    expected = {
        "Daging Ayam Ras Segar": "chicken-meat-broiler-fresh",
        "Daging Ayam Ras": "chicken-meat-broiler-fresh",
        "Beras Kualitas Medium I": "rice-medium-grade-1",
        "Beras Medium": "rice-medium",
        "Minyak Goreng Curah": "cooking-oil-bulk",
        "Minyak Goreng Sawit Curah": "cooking-oil-bulk",
    }

    resolved = {label: getattr(registry.resolve(label), "identifier", None) for label in expected}

    assert resolved == expected


def test_the_2022_papua_codes_reach_the_regencies_they_name() -> None:
    """Papua was split into four provinces in 2022 and BPS renumbered the
    regencies that moved. No boundary changed, so each answers to both codes —
    without which a sixth of SP2KP's Papua rows land with no place at all."""
    from terusan_pipelines.normalize import geography_registry

    registry = geography_registry()
    expected = {
        "9301": "ID-91.01",  # Merauke, now Papua Selatan
        "9401": "ID-91.04",  # Nabire, now Papua Tengah
        "9501": "ID-91.02",  # Jayawijaya, now Papua Pegunungan
        "9601": "ID-92.01",  # Sorong, now Papua Barat Daya
        "9671": "ID-92.71",  # Kota Sorong
        "91.01": "ID-91.01",  # and the old code still answers
    }

    resolved = {code: getattr(registry.resolve(code), "identifier", None) for code in expected}

    assert resolved == expected
