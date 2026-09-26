"""SP2KP's national series: the half of the fact that is not in the bytes.

The response is a bare list of dates and prices. Which good it prices, and in
what quantity, were request parameters — so every test here is really about
whether the landing record survives the trip into Bronze.
"""

from __future__ import annotations

import json

import pytest

from terusan_pipelines.extract import Landed, Sp2kpNationalExtractor
from terusan_pipelines.extract.base import ExtractionError

SERIES = {
    "status": "success",
    "message": "ok",
    "data": [
        {"tanggal_data": "2024-02-01", "harga": 13924},
        {"tanggal_data": "2024-02-02", "harga": 13944},
        # A day the ministry published no figure.
        {"tanggal_data": "2024-02-05", "harga": None},
    ],
}

EXTRA = {
    "variant_id": 52,
    "variant_nama": "Beras Medium",
    "komoditas": "Beras",
    "satuan": "kg",
}


def landed_series(tmp_path, body=None, extra=None, name="hnt-52.json"):
    path = tmp_path / name
    path.write_bytes(json.dumps(SERIES if body is None else body).encode())
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="kemendag-sp2kp-national",
        dataset="sp2kp-national-prices",
        extra=EXTRA if extra is None else extra,
    )


def test_the_series_becomes_one_row_per_day(tmp_path) -> None:
    rows = list(Sp2kpNationalExtractor().extract(landed_series(tmp_path)))

    assert [r["columns"]["date"] for r in rows] == ["2024-02-01", "2024-02-02", "2024-02-05"]
    assert [r["row_number"] for r in rows] == [1, 2, 3]


def test_the_commodity_is_read_back_off_the_landing_record(tmp_path) -> None:
    """It is nowhere in the response. Without this, forty-two commodities are
    forty-two files of identical shape that normalize into one series."""
    rows = list(Sp2kpNationalExtractor().extract(landed_series(tmp_path)))

    assert {r["columns"]["variant"] for r in rows} == {"Beras Medium"}
    assert {r["columns"]["komoditas"] for r in rows} == {"Beras"}


def test_the_variant_and_not_the_category_is_what_tells_goods_apart(tmp_path) -> None:
    """`Beras` covers medium, premium and Bulog's subsidised SPHP. Filed under
    the category, the subsidised price averages into the premium one."""
    rows = list(Sp2kpNationalExtractor().extract(landed_series(tmp_path)))

    assert rows[0]["columns"]["variant"] != rows[0]["columns"]["komoditas"]


def test_the_currency_is_joined_to_the_quantity_the_api_states(tmp_path) -> None:
    """The API says `kg` and never says rupiah. A unit of `kg` on a price is
    wrong where it lands, beside `IDR/kg` from the same ministry's crosstab."""
    rows = list(Sp2kpNationalExtractor().extract(landed_series(tmp_path)))

    assert rows[0]["columns"]["unit"] == "IDR/kg"
    # And what the API actually said survives beside it.
    assert rows[0]["columns"]["satuan"] == "kg"


@pytest.mark.parametrize(
    ("satuan", "expected"),
    [("kg", "IDR/kg"), ("lt", "IDR/lt"), ("bks", "IDR/bks"), ("ekor", "IDR/ekor"), ("", "")],
)
def test_the_basket_is_not_all_kilograms(tmp_path, satuan: str, expected: str) -> None:
    """Cooking oil is by the litre, noodles by the packet, free-range chicken
    by the bird. Asserting kilograms would be wrong four ways."""
    landed = landed_series(tmp_path, extra={**EXTRA, "satuan": satuan})
    rows = list(Sp2kpNationalExtractor().extract(landed))

    assert rows[0]["columns"]["unit"] == expected


def test_a_day_with_no_figure_stays_blank(tmp_path) -> None:
    """Rather than becoming a zero, which would say the price was nothing."""
    rows = list(Sp2kpNationalExtractor().extract(landed_series(tmp_path)))

    assert rows[2]["columns"]["price"] == ""
    assert rows[0]["columns"]["price"] == "13924"


def test_a_series_that_cannot_name_its_commodity_is_refused(tmp_path) -> None:
    """Rather than landing anonymous rows that normalize into whichever series
    they sit beside."""
    landed = landed_series(tmp_path, extra={"variant_id": 52})

    with pytest.raises(ExtractionError, match="does not name its commodity"):
        list(Sp2kpNationalExtractor().extract(landed))


def test_the_commodity_master_is_left_to_the_generic_reader(tmp_path) -> None:
    """It is landed for replay, not for figures, and has no variant on its
    record — so this reader must not claim it and then fail for the lack."""
    path = tmp_path / "variants.json"
    path.write_bytes(b'{"data":[]}')
    catalogue = Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="kemendag-sp2kp-national",
        dataset="sp2kp-variants",
        extra={"role": "catalogue", "variants": 56},
    )

    assert not Sp2kpNationalExtractor().handles(catalogue)
    assert Sp2kpNationalExtractor().handles(landed_series(tmp_path))


def test_a_response_with_no_series_is_a_failure(tmp_path) -> None:
    landed = landed_series(tmp_path, body={"status": "error", "message": "nope"})

    with pytest.raises(ExtractionError, match="no series"):
        list(Sp2kpNationalExtractor().extract(landed))
