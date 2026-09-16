"""Committed reference data, and what it resolves.

These read the real files rather than fixtures. The point of the tests is that
the committed data is correct and stays correct — a fixture would only prove the
loader parses CSV.
"""

from __future__ import annotations

from datetime import date

import pytest

from terusan_pipelines.normalize import (
    GeoType,
    ReferenceDataError,
    geography_registry,
    load_aggregates,
    load_countries,
    load_indonesia,
)
from terusan_pipelines.normalize.reference import GEOGRAPHY_DIR, _rows


@pytest.fixture(scope="module")
def registry():
    return geography_registry()


# ---- the files load -------------------------------------------------------


def test_every_reference_file_is_where_the_loader_expects():
    assert GEOGRAPHY_DIR.is_dir()
    for name in ("countries.csv", "indonesia-provinces.csv", "worldbank-aggregates.csv"):
        assert (GEOGRAPHY_DIR / name).exists(), name


def test_a_missing_file_fails_loudly():
    """A dimension that silently loads nothing resolves nothing, invisibly."""
    with pytest.raises(ReferenceDataError, match="not found"):
        list(_rows(GEOGRAPHY_DIR / "nope.csv"))


def test_comment_lines_carry_provenance_and_are_skipped():
    """Where a code set came from belongs next to it, not in a separate doc."""
    text = (GEOGRAPHY_DIR / "indonesia-provinces.csv").read_text()
    assert text.startswith("#")
    assert "VERIFY BEFORE PRODUCTION USE" in text
    rows = list(_rows(GEOGRAPHY_DIR / "indonesia-provinces.csv"))
    assert all(not row["geo_id"].startswith("#") for row in rows)


# ---- countries ------------------------------------------------------------


def test_countries_load_with_iso3_identifiers():
    countries = load_countries()
    assert len(countries) > 200
    assert all(len(c.geo_id) == 3 for c in countries)
    assert all(c.geo_type is GeoType.COUNTRY for c in countries)


def test_country_identifiers_are_unique():
    identifiers = [c.geo_id for c in load_countries()]
    assert len(set(identifiers)) == len(identifiers)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("IDN", "IDN"), ("ID", "IDN"), ("Indonesia", "IDN"), ("MYS", "MYS"), ("Malaysia", "MYS")],
)
def test_countries_resolve_by_code_and_by_name(registry, name, expected):
    assert registry.resolve(name).identifier == expected


def test_indonesia_is_defined_exactly_once(registry):
    """It was defined in two files, and the duplicate guard cost it both.

    The country list is authoritative; the province file hangs off it.
    """
    assert registry.resolve("Indonesia").resolved
    assert {area.parent_geo_id for area in load_indonesia()} == {"IDN"}


# ---- Indonesian provinces -------------------------------------------------


def test_all_thirty_eight_provinces_are_present():
    provinces = [a for a in load_indonesia() if a.geo_type is GeoType.PROVINCE]
    assert len(provinces) == 38


def test_bps_codes_are_unique():
    codes = [a.bps_code for a in load_indonesia() if a.bps_code]
    assert len(set(codes)) == len(codes)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Jawa Barat", "ID-32"),
        ("JAWA BARAT", "ID-32"),
        ("Jabar", "ID-32"),
        ("West Java", "ID-32"),
        ("32", "ID-32"),
        ("Prov. Jawa Barat", "ID-32"),
        ("Sultra", "ID-74"),
        ("Sulawesi Tenggara", "ID-74"),
        ("DKI", "ID-31"),
        ("DKI Jakarta", "ID-31"),
        ("Jakarta", "ID-31"),
        ("DIY", "ID-34"),
        ("Jogja", "ID-34"),
        ("NTB", "ID-52"),
        ("Kaltara", "ID-65"),
        ("Malut", "ID-82"),
        ("NAD", "ID-11"),
    ],
)
def test_provinces_resolve_however_a_source_writes_them(registry, name, expected):
    assert registry.resolve(name).identifier == expected


def test_papua_resolves_to_the_province_not_a_region(registry):
    """Nothing is aliased to a bare "Papua" — the guard would cost the real one."""
    assert registry.resolve("Papua").identifier == "ID-94"
    assert registry.resolve("Papua Barat").identifier == "ID-91"


def test_provinces_created_in_2022_carry_their_dates():
    """Resolving a 2015 figure against a 2022 province would file it under an
    area that did not exist when the figure was collected (program.md §11)."""
    by_id = {a.geo_id: a for a in load_indonesia()}
    assert by_id["ID-95"].valid_from == date(2022, 7, 25)  # Papua Selatan
    assert by_id["ID-92"].valid_from == date(2022, 12, 8)  # Papua Barat Daya


def test_a_new_province_does_not_resolve_before_it_existed(registry):
    assert registry.resolve("Papua Tengah", on=date(2023, 6, 1)).resolved
    assert not registry.resolve("Papua Tengah", on=date(2015, 6, 1)).resolved


def test_long_established_provinces_resolve_at_any_date(registry):
    assert registry.resolve("Jawa Barat", on=date(1985, 1, 1)).resolved


# ---- World Bank aggregates ------------------------------------------------


def test_aggregates_load_as_regions():
    aggregates = load_aggregates()
    assert len(aggregates) > 50
    assert all(a.geo_type is GeoType.REGION for a in aggregates)


def test_aggregates_resolve(registry):
    """They were the entire remaining unresolved set on the World Bank data."""
    for code in ("WLD", "ARB", "EAS", "ECS", "LCN"):
        assert registry.resolve(code).identifier == code


def test_an_aggregate_is_distinguishable_from_a_country(registry):
    """This is what keeps a total from counting everything several times over.

    Real 2023 world GDP is about 106 trillion USD. The aggregates alone sum to
    roughly 671 trillion, because every country sits inside several overlapping
    groupings — so a sum over the whole dataset is about seven times the truth.
    Recording the distinction lets a consumer filter; deleting the rows would
    not, since nobody can filter what is absent.
    """
    assert registry.get("WLD").geo_type is GeoType.REGION
    assert registry.get("IDN").geo_type is GeoType.COUNTRY


def test_no_aggregate_code_collides_with_a_country_code():
    countries = {c.geo_id for c in load_countries()}
    aggregates = {a.geo_id for a in load_aggregates()}
    assert not (countries & aggregates)


# ---- the registry as a whole ---------------------------------------------


def test_the_registry_holds_every_set(registry):
    assert len(registry) == len(load_countries()) + len(load_aggregates()) + len(load_indonesia())


def test_sets_can_be_loaded_selectively():
    only_indonesia = geography_registry(countries=False, aggregates=False)
    assert only_indonesia.resolve("Jabar").resolved
    assert not only_indonesia.resolve("MYS").resolved


def test_an_unknown_name_still_resolves_to_nothing(registry):
    """A full registry must not start guessing."""
    assert not registry.resolve("Atlantis").resolved
    assert not registry.resolve("Wakanda").resolved
