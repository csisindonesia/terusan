"""PIHPS food prices: the paging the source does, and what the extractor keeps.

None of this reaches Bank Indonesia. What is worth testing here is the two
places the pipeline can lose a fact silently — a window boundary that shifts
between runs, and a dimension that lived in the request rather than the
response — because both fail by producing plausible data rather than an error.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from terusan_pipelines.extract.base import ExtractionError, Landed
from terusan_pipelines.extract.pihps import PihpsPricesExtractor
from terusan_pipelines.sources.bank_indonesia.legacy import pihps_prices

GRID = [
    {"no": "I", "name": "Beras", "level": 1, "01/09/2026": "16,350", "02/09/2026": "-"},
    {
        "no": 1,
        "name": "Beras Kualitas Medium I",
        "level": 2,
        "01/09/2026": "16,600",
        "02/09/2026": "16,600",
    },
]


def landed(tmp_path, extra: dict) -> Landed:
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(GRID), encoding="utf-8")
    return Landed(
        path=path,
        document_id="doc_test",
        content_hash="0" * 64,
        source_slug="bi-pihps-food-prices",
        media_type="application/json",
        dataset="food-prices",
        retrieved_at=datetime(2026, 9, 21),
        extra=extra,
    )


#: The 34 provinces `GetRefProvince` returns, as it spells them. Recorded so the
#: geography registry is checked against the names that actually arrive, not
#: against the ones we would have chosen.
PORTAL_PROVINCES = (
    "Aceh",
    "Bali",
    "Banten",
    "Bengkulu",
    "DI Yogyakarta",
    "DKI Jakarta",
    "Gorontalo",
    "Jambi",
    "Jawa Barat",
    "Jawa Tengah",
    "Jawa Timur",
    "Kalimantan Barat",
    "Kalimantan Selatan",
    "Kalimantan Tengah",
    "Kalimantan Timur",
    "Kalimantan Utara",
    "Kepulauan Bangka Belitung",
    "Kepulauan Riau",
    "Lampung",
    "Maluku",
    "Maluku Utara",
    "Nusa Tenggara Barat",
    "Nusa Tenggara Timur",
    "Papua",
    "Papua Barat",
    "Riau",
    "Sulawesi Barat",
    "Sulawesi Selatan",
    "Sulawesi Tengah",
    "Sulawesi Tenggara",
    "Sulawesi Utara",
    "Sumatera Barat",
    "Sumatera Selatan",
    "Sumatera Utara",
)


FULL = {
    "geo_name": "Provinsi Jawa Barat",
    "geo_level": "province",
    "price_type": "producer",
    "price_type_name": "Produsen",
}


# ---------------------------------------------------------------------------
# paging
# ---------------------------------------------------------------------------


def test_windows_are_calendar_months_clipped_to_the_span():
    assert list(pihps_prices.month_windows(date(2026, 1, 15), date(2026, 3, 10))) == [
        (date(2026, 1, 15), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 10)),
    ]


def test_a_span_inside_one_month_is_one_window():
    assert list(pihps_prices.month_windows(date(2026, 5, 4), date(2026, 5, 6))) == [
        (date(2026, 5, 4), date(2026, 5, 6))
    ]


def test_windows_cover_a_leap_february():
    windows = dict(pihps_prices.month_windows(date(2024, 2, 1), date(2024, 3, 1)))
    assert windows[date(2024, 2, 1)] == date(2024, 2, 29)


def test_an_inverted_span_yields_nothing():
    # Rather than looping forever or yielding a backwards window, both of which
    # a backfill driven by a mistyped date could otherwise produce.
    assert list(pihps_prices.month_windows(date(2026, 5, 1), date(2026, 4, 1))) == []


def test_interior_windows_do_not_move_with_the_span():
    # The reason the paging unit is the calendar month rather than a rolling
    # thirty days: a re-run asking for a wider span must land the same bytes for
    # the months it shares, or content-addressed landing stores each month twice.
    january = list(pihps_prices.month_windows(date(2026, 1, 1), date(2026, 6, 30)))
    again = list(pihps_prices.month_windows(date(2026, 1, 1), date(2026, 12, 31)))
    assert january[1:] == again[1:6]


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


def columns_of(tmp_path, extra=None):
    rows = list(PihpsPricesExtractor().extract(landed(tmp_path, extra or FULL)))
    return [r["columns"] for r in rows]


def test_the_query_dimensions_reach_every_row(tmp_path):
    # Neither the province nor the market is anywhere in the JSON. If the
    # extractor does not put them back, every province's rice is one series.
    columns = columns_of(tmp_path)

    assert [c["geo"] for c in columns] == ["Provinsi Jawa Barat"] * 2
    assert [c["price_type"] for c in columns] == ["producer"] * 2


def test_rows_carry_the_bronze_envelope(tmp_path):
    # `dataset` is the extractor's to supply, not the runner's to infer, and a
    # row without it fails the Bronze schema rather than landing unlabelled.
    rows = list(PihpsPricesExtractor().extract(landed(tmp_path, FULL)))

    assert [r["dataset"] for r in rows] == ["food-prices"] * 2
    assert [r["row_number"] for r in rows] == [1, 2]


def test_categories_and_varieties_are_told_apart(tmp_path):
    columns = columns_of(tmp_path)

    assert columns[0]["commodity"] == "Beras"
    assert columns[0]["level_name"] == "category"
    assert columns[1]["commodity"] == "Beras Kualitas Medium I"
    assert columns[1]["level_name"] == "variety"


def test_the_dates_stay_columns(tmp_path):
    # Bronze defers the unpivot: the mapping in Silver reads a wide table whose
    # headers are periods, and an extractor that unpivoted here would be making
    # the judgement Bronze exists to postpone.
    columns = columns_of(tmp_path)

    assert columns[0]["01/09/2026"] == "16,350"
    # A price not yet posted is preserved as the portal wrote it, for Silver to
    # read as missing — not dropped here, and not turned into a zero.
    assert columns[0]["02/09/2026"] == "-"


def test_a_landing_without_its_dimensions_is_refused(tmp_path):
    # These rows would be indistinguishable from national traditional-market
    # prices. Filed under the wrong province a figure is not wrong-looking, it
    # is invisible — so this fails rather than guesses.
    with pytest.raises(ExtractionError, match="geo_name/price_type"):
        list(PihpsPricesExtractor().extract(landed(tmp_path, {"price_type": "modern"})))


def test_it_claims_the_source_ahead_of_the_generic_json_reader(tmp_path):
    extractor = PihpsPricesExtractor()
    assert extractor.handles(landed(tmp_path, FULL))

    from terusan_pipelines.extract.runner import DEFAULT_EXTRACTORS

    kinds = [type(e).__name__ for e in DEFAULT_EXTRACTORS]
    assert kinds.index("PihpsPricesExtractor") < kinds.index("JsonExtractor")


# ---------------------------------------------------------------------------
# the dimensions the rows are resolved against
# ---------------------------------------------------------------------------


def test_every_commodity_the_portal_prices_is_registered():
    from terusan_pipelines.normalize.reference import commodity_registry

    registry = commodity_registry()
    for name in ("Beras", "Beras Kualitas Medium I", "Cabai Rawit Merah", "Gula Pasir Lokal"):
        assert registry.resolve(name).identifier, name


def test_provinces_are_qualified_so_gorontalo_is_not_ambiguous():
    # `Gorontalo` names both a province and a regency inside it, and the
    # geography registry refuses a name that could be either. The source
    # qualifies every province rather than special-casing the one.
    from terusan_pipelines.normalize.reference import geography_registry

    registry = geography_registry()
    assert registry.resolve("Gorontalo").identifier is None
    assert registry.resolve("Provinsi Gorontalo").identifier == "ID-75"
    assert registry.resolve("Provinsi Jawa Barat").identifier == "ID-32"


def test_a_province_whose_capital_shares_its_name_still_resolves_qualified():
    # `Kota Jambi` normalizes to the same string as the province `Jambi`, so
    # the qualified name had no unambiguous normalized match and resolved to
    # nothing — while the bare name resolved fine by exact match. Naming a
    # province the careful way got the worse answer, and 148,000 prices landed
    # with no place on them rather than failing anywhere visible.
    from terusan_pipelines.normalize.reference import geography_registry

    registry = geography_registry()
    assert registry.resolve("Provinsi Jambi").identifier == "ID-15"
    assert registry.resolve("Provinsi Bengkulu").identifier == "ID-17"


def test_every_province_the_portal_names_resolves_when_qualified():
    # The walk is over the portal's own province list, so this is the set that
    # actually has to resolve — a gap anywhere in it is figures with no place.
    from terusan_pipelines.normalize.reference import geography_registry

    registry = geography_registry()
    for name in PORTAL_PROVINCES:
        assert registry.resolve(f"Provinsi {name}").identifier, name
