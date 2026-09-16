"""Dimension resolution, the observation mapping, and Bronze to Silver."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from terusan_pipelines.extract import ExtractionRunner
from terusan_pipelines.normalize import (
    ColumnMapping,
    Commodity,
    CommodityRegistry,
    Geography,
    GeographyRegistry,
    GeoType,
    MappingError,
    NormalizationResult,
    NumberFormat,
    ObservationNormalizer,
    SilverRunner,
    normalize_name,
    observation_id,
)
from terusan_pipelines.sources import (
    Artifact,
    Category,
    CollectionMethod,
    Landing,
    SourceMeta,
    SourceType,
)
from terusan_pipelines.storage import Layer, StorageConfig, StorageResolver
from terusan_pipelines.warehouse import Warehouse


@pytest.fixture
def resolver(tmp_path: Path) -> StorageResolver:
    return StorageResolver(
        StorageConfig(
            STORAGE_PROFILE="local",
            STORAGE_BACKEND="local",
            STORAGE_ROOT=str(tmp_path / "data"),
            SCRATCH_DIR=str(tmp_path / "cache"),
        )
    )


@pytest.fixture
def geography() -> GeographyRegistry:
    registry = GeographyRegistry()
    registry.add(Geography("ID", "Indonesia", GeoType.COUNTRY, iso_code="IDN"))
    registry.add(
        Geography(
            "ID-JB",
            "Jawa Barat",
            GeoType.PROVINCE,
            parent_geo_id="ID",
            bps_code="32",
            aliases=("Jabar", "West Java"),
        )
    )
    registry.add(
        Geography(
            "ID-SG",
            "Sulawesi Tenggara",
            GeoType.PROVINCE,
            parent_geo_id="ID",
            bps_code="74",
            aliases=("Sultra",),
        )
    )
    return registry


@pytest.fixture
def commodities() -> CommodityRegistry:
    registry = CommodityRegistry()
    registry.add(
        Commodity(
            "NICKEL_ORE",
            "Nickel Ore",
            category="minerals",
            hs_code="2604",
            aliases=("nikel", "bijih nikel", "nickel"),
        )
    )
    registry.add(Commodity("FERRONICKEL", "Ferronickel", category="minerals"))
    return registry


# ---- name normalization ---------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Jawa Barat", "jawa barat"),
        ("JAWA BARAT", "jawa barat"),
        ("  Jawa   Barat  ", "jawa barat"),
        ("D.I. Yogyakarta", "d i yogyakarta"),
        ("Aceh", "aceh"),
    ],
)
def test_normalize_name_folds_case_and_punctuation(raw, expected):
    assert normalize_name(raw) == expected


# ---- geography ------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["Jawa Barat", "JAWA BARAT", "jawa barat", "Jabar", "West Java", "32"]
)
def test_a_province_resolves_however_it_is_written(geography, name):
    assert geography.resolve(name).identifier == "ID-JB"


def test_administrative_prefixes_are_ignored(geography):
    assert geography.resolve("Prov. Jawa Barat").identifier == "ID-JB"
    assert geography.resolve("Provinsi Jawa Barat").identifier == "ID-JB"


def test_an_unknown_place_resolves_to_nothing(geography):
    """Filed under nothing is visible; filed under the wrong province is not."""
    result = geography.resolve("Atlantis")
    assert not result.resolved
    assert result.matched_on == "Atlantis"


def test_a_name_claimed_by_two_areas_resolves_to_neither():
    """Picking one silently would misfile every figure using that name."""
    registry = GeographyRegistry()
    registry.add(Geography("ID-A", "Bandung", GeoType.REGENCY, aliases=("Bandung",)))
    registry.add(Geography("ID-B", "Bandung", GeoType.DISTRICT, aliases=("Bandung",)))
    assert not registry.resolve("Bandung").resolved


def test_validity_dates_keep_historical_boundaries_apart():
    """A code reused for a new area must not absorb the old one's history."""
    registry = GeographyRegistry()
    registry.add(
        Geography("ID-NEW", "Papua Selatan", GeoType.PROVINCE, valid_from=date(2022, 7, 1))
    )
    assert registry.resolve("Papua Selatan", on=date(2023, 1, 1)).resolved
    assert not registry.resolve("Papua Selatan", on=date(2010, 1, 1)).resolved


# ---- commodities ----------------------------------------------------------


@pytest.mark.parametrize("name", ["Nickel Ore", "nikel", "bijih nikel", "2604"])
def test_a_commodity_resolves_by_alias_and_hs_code(commodities, name):
    assert commodities.resolve(name).identifier == "NICKEL_ORE"


def test_distinct_nickel_products_stay_distinct(commodities):
    """Program.md §12 is explicit: these must not collapse into one."""
    assert commodities.resolve("Ferronickel").identifier == "FERRONICKEL"
    assert commodities.resolve("Nickel Ore").identifier == "NICKEL_ORE"


# ---- the mapping ----------------------------------------------------------


def test_a_mapping_must_say_where_values_come_from():
    with pytest.raises(MappingError, match="neither"):
        ColumnMapping(indicator_id="X")


def test_observation_ids_are_derived_not_assigned():
    """Re-running normalization must produce the same ids."""
    first = observation_id("CPI", "2026-01", "ID-JB", None)
    second = observation_id("CPI", "2026-01", "ID-JB", None)
    assert first == second
    assert first != observation_id("CPI", "2026-02", "ID-JB", None)
    assert first != observation_id("CPI", "2026-01", "ID-SG", None)


def _record(**columns) -> dict:
    return {"source_id": "bps", "document_id": "doc_1", "columns": columns}


def test_long_form_one_row_one_observation(geography):
    mapping = ColumnMapping(
        indicator_id="CPI_INFLATION_YOY",
        period_column="bulan",
        value_column="nilai",
        geo_column="provinsi",
    )
    rows = list(
        ObservationNormalizer(mapping, geography=geography).normalize(
            [_record(bulan="Januari 2026", nilai="1.234,56", provinsi="Jabar")]
        )
    )
    assert len(rows) == 1
    assert rows[0]["period"] == "2026-01"
    assert rows[0]["period_start"] == date(2026, 1, 1)
    assert rows[0]["value"] == Decimal("1234.56")
    assert rows[0]["geo_id"] == "ID-JB"
    assert rows[0]["geo_name_raw"] == "Jabar"


def test_wide_form_one_row_many_observations(geography):
    """The commonest shape in published tables: each column header is a period."""
    mapping = ColumnMapping(
        indicator_id="NICKEL_PRODUCTION",
        value_columns=("2024", "2025", "2026"),
        geo_column="wilayah",
    )
    rows = list(
        ObservationNormalizer(mapping, geography=geography).normalize(
            [_record(wilayah="Sultra", **{"2024": "1.000,5", "2025": "-", "2026": "2.000,5"})]
        )
    )
    assert [r["period"] for r in rows] == ["2024", "2025", "2026"]
    assert rows[0]["value"] == Decimal("1000.5")
    assert rows[1]["value"] is None
    assert rows[1]["status"] == "missing"
    assert all(r["geo_id"] == "ID-SG" for r in rows)


def test_total_rows_are_excluded_to_avoid_double_counting():
    mapping = ColumnMapping(
        indicator_id="X",
        period_column="tahun",
        value_column="nilai",
        geo_column="wilayah",
        exclude_where={"wilayah": ("Jumlah", "Total", "Indonesia")},
    )
    stats = NormalizationResult()
    rows = list(
        ObservationNormalizer(mapping).normalize(
            [
                _record(tahun="2026", nilai="1", wilayah="Jawa Barat"),
                _record(tahun="2026", nilai="99", wilayah="Jumlah"),
            ],
            stats,
        )
    )
    assert len(rows) == 1
    assert stats.skipped_excluded == 1


def test_an_unparseable_period_skips_the_cell_not_the_row():
    mapping = ColumnMapping(indicator_id="X", value_columns=("2026", "Jumlah"))
    stats = NormalizationResult()
    rows = list(
        ObservationNormalizer(mapping).normalize([_record(**{"2026": "5", "Jumlah": "5"})], stats)
    )
    assert len(rows) == 1
    assert stats.skipped_no_period == 1


def test_unresolved_dimensions_are_counted_and_kept(geography):
    mapping = ColumnMapping(
        indicator_id="X", period_column="tahun", value_column="nilai", geo_column="wilayah"
    )
    stats = NormalizationResult()
    rows = list(
        ObservationNormalizer(mapping, geography=geography).normalize(
            [_record(tahun="2026", nilai="1", wilayah="Atlantis")], stats
        )
    )
    assert stats.unresolved_geo == 1
    assert rows[0]["geo_id"] is None
    assert rows[0]["geo_name_raw"] == "Atlantis"


def test_ambiguous_values_are_counted():
    mapping = ColumnMapping(indicator_id="X", period_column="t", value_column="v")
    stats = NormalizationResult()
    list(ObservationNormalizer(mapping).normalize([_record(t="2026", v="1.234")], stats))
    assert stats.ambiguous_values == 1


def test_an_explicit_number_format_removes_the_ambiguity():
    mapping = ColumnMapping(
        indicator_id="X",
        period_column="t",
        value_column="v",
        number_format=NumberFormat.INDONESIAN,
    )
    stats = NormalizationResult()
    rows = list(ObservationNormalizer(mapping).normalize([_record(t="2026", v="1.234")], stats))
    assert rows[0]["value"] == Decimal("1234")
    assert rows[0]["value_unambiguous"] is True
    assert stats.ambiguous_values == 0


def test_the_unit_comes_from_the_cell_then_the_column_then_the_mapping():
    base = {"indicator_id": "X", "period_column": "t", "value_column": "v"}
    from_cell = list(
        ObservationNormalizer(ColumnMapping(**base, unit="ton")).normalize(
            [_record(t="2026", v="5%")]
        )
    )
    assert from_cell[0]["unit"] == "%"

    from_mapping = list(
        ObservationNormalizer(ColumnMapping(**base, unit="ton")).normalize(
            [_record(t="2026", v="5")]
        )
    )
    assert from_mapping[0]["unit"] == "ton"


def test_provenance_is_carried_into_the_observation():
    """A Silver row still answers "where did this come from" without a join."""
    mapping = ColumnMapping(indicator_id="X", period_column="t", value_column="v")
    rows = list(
        ObservationNormalizer(mapping).normalize(
            [
                {
                    "source_id": "bps",
                    "document_id": "doc_abc",
                    "content_hash": "deadbeef",
                    "raw_path": "statistics/bps/x/doc_abc/f.csv",
                    "columns": {"t": "2026", "v": "1"},
                }
            ]
        )
    )
    assert rows[0]["source_id"] == "bps"
    assert rows[0]["document_id"] == "doc_abc"
    assert rows[0]["content_hash"] == "deadbeef"
    assert rows[0]["raw_path"].startswith("statistics/bps/")


# ---- Bronze to Silver end to end -----------------------------------------


def _land_and_extract(resolver, csv: bytes, filename: str = "cpi.csv") -> None:
    meta = SourceMeta(
        slug="bps",
        name="Badan Pusat Statistik",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
    )
    Landing(resolver).land(meta, Artifact(content=csv, filename=filename, dataset="inflation"))
    ExtractionRunner(resolver).run()


def test_bronze_to_silver_produces_queryable_observations(resolver, geography):
    # Semicolon-delimited, because the values use comma decimals — which is
    # exactly why Indonesian sources publish semicolon CSVs.
    _land_and_extract(resolver, b"bulan;provinsi;nilai\nJanuari 2026;Jawa Barat;1.234,56\n")
    mapping = ColumnMapping(
        indicator_id="CPI_INFLATION_YOY",
        period_column="bulan",
        value_column="nilai",
        geo_column="provinsi",
    )
    result = SilverRunner(resolver, geography=geography).normalize(mapping, dataset="inflation")
    assert result.observations == 1

    with Warehouse(resolver) as warehouse:
        warehouse.view("observations", Layer.SILVER, "observations")
        row = warehouse.query(
            "SELECT period, period_start, value, geo_id, source_id FROM observations"
        ).fetchone()
    assert row[0] == "2026-01"
    assert row[1] == date(2026, 1, 1)
    assert row[2] == Decimal("1234.56")
    assert row[3] == "ID-JB"
    assert row[4] == "bps"


def test_silver_is_partitioned_for_pruning(resolver, geography):
    _land_and_extract(resolver, b"bulan;nilai\n2026-01;5\n2026-02;6\n")
    mapping = ColumnMapping(
        indicator_id="CPI_INFLATION_YOY", period_column="bulan", value_column="nilai"
    )
    SilverRunner(resolver, geography=geography).normalize(mapping, dataset="inflation")

    partitions = list(Path(resolver.resolve(Layer.SILVER, "observations")).glob("indicator_id=*"))
    assert [p.name for p in partitions] == ["indicator_id=cpi_inflation_yoy"]


def test_renormalizing_replaces_rather_than_appends(resolver, geography):
    """A corrected mapping must not leave its wrong rows beside the new ones."""
    _land_and_extract(resolver, b"bulan;nilai\n2026-01;5\n2026-02;6\n")
    mapping = ColumnMapping(
        indicator_id="CPI_INFLATION_YOY", period_column="bulan", value_column="nilai"
    )
    runner = SilverRunner(resolver, geography=geography)
    runner.normalize(mapping, dataset="inflation")
    runner.normalize(mapping, dataset="inflation")

    with Warehouse(resolver) as warehouse:
        warehouse.view("observations", Layer.SILVER, "observations")
        assert warehouse.query("SELECT count(*) FROM observations").fetchone()[0] == 2


def test_values_survive_as_decimals_through_parquet(resolver, geography):
    """Float would drift; the whole point of decimal128 is that it does not."""
    _land_and_extract(resolver, b"bulan;nilai\n2026-01;1.234,56\n")
    mapping = ColumnMapping(indicator_id="X", period_column="bulan", value_column="nilai")
    SilverRunner(resolver, geography=geography).normalize(mapping, dataset="inflation")

    with Warehouse(resolver) as warehouse:
        warehouse.view("observations", Layer.SILVER, "observations")
        kind = warehouse.query("SELECT typeof(value) FROM observations LIMIT 1").fetchone()[0]
    assert kind.startswith("DECIMAL")


def test_dimensions_are_published_as_silver_tables(resolver, geography, commodities):
    runner = SilverRunner(resolver, geography=geography, commodities=commodities)
    geo_rows = runner.write_geography([geography.get(i) for i in ("ID", "ID-JB", "ID-SG")])
    commodity_rows = runner.write_commodities(
        [commodities.get(i) for i in ("NICKEL_ORE", "FERRONICKEL")]
    )
    assert geo_rows == 3
    assert commodity_rows == 2

    with Warehouse(resolver) as warehouse:
        warehouse.view("geography", Layer.SILVER, "geography")
        row = warehouse.query(
            "SELECT name, parent_geo_id, aliases FROM geography WHERE geo_id='ID-JB'"
        ).fetchone()
    assert row[0] == "Jawa Barat"
    assert row[1] == "ID"
    assert "Jabar" in row[2]


def test_normalizing_an_empty_bronze_is_harmless(resolver):
    mapping = ColumnMapping(indicator_id="X", period_column="t", value_column="v")
    result = SilverRunner(resolver).normalize(mapping)
    assert result.observations == 0
    assert result.files_written == 0


def test_dry_run_reports_without_writing(resolver, geography):
    _land_and_extract(resolver, b"bulan;nilai\n2026-01;5\n")
    mapping = ColumnMapping(indicator_id="X", period_column="bulan", value_column="nilai")
    result = SilverRunner(resolver, geography=geography).normalize(
        mapping, dataset="inflation", dry_run=True
    )
    assert result.observations == 1
    assert result.files_written == 0
    assert not Path(resolver.resolve(Layer.SILVER, "observations")).exists()


# ---- parser versions ------------------------------------------------------


@contextmanager
def parser_version(value: str):
    """Pretend the extractor's output changed.

    The constant is bound by name at import in each module that uses it, so
    every binding has to move together — patching only the definition would
    leave the runners on the old value.
    """
    import terusan_pipelines.extract.base as base
    import terusan_pipelines.extract.runner as extract_runner
    import terusan_pipelines.normalize.runner as silver_runner

    modules = (base, extract_runner, silver_runner)
    previous = [m.PARSER_VERSION for m in modules]
    for module in modules:
        module.PARSER_VERSION = value
    try:
        yield
    finally:
        for module, original in zip(modules, previous, strict=True):
            module.PARSER_VERSION = original


def test_silver_reads_only_the_current_parser_version(resolver, geography):
    """Re-extraction appends rather than replaces, so a read must say which
    version it wants. Without this, improving a parser doubles every figure."""
    import terusan_pipelines.extract.base as base

    _land_and_extract(resolver, b"bulan;nilai\n2026-01;5\n2026-02;6\n")
    original = base.PARSER_VERSION

    with parser_version("9"):
        # A second extraction, as a changed extractor would produce.
        ExtractionRunner(resolver).run()

        with Warehouse(resolver) as warehouse:
            warehouse.view("records", Layer.BRONZE, "records")
            versions = warehouse.query(
                "SELECT DISTINCT parser_version FROM records ORDER BY 1"
            ).fetchall()
        # Both are kept: a partition stays traceable to the code that made it.
        assert sorted(v[0] for v in versions) == sorted([original, "9"])

        mapping = ColumnMapping(indicator_id="X", period_column="bulan", value_column="nilai")
        result = SilverRunner(resolver, geography=geography).normalize(mapping, dataset="inflation")
        # Two documents, not four.
        assert result.stats.rows_in == 2
        assert result.observations == 2


def test_bumping_the_parser_version_forces_re_extraction(resolver):
    """The mechanism that makes an improved parser actually run again."""
    _land_and_extract(resolver, b"bulan;nilai\n2026-01;5\n")

    unchanged = ExtractionRunner(resolver).run()
    assert unchanged.documents_extracted == 0
    assert unchanged.documents_unchanged == 1

    with parser_version("9"):
        bumped = ExtractionRunner(resolver).run()
        assert bumped.documents_extracted == 1
        assert bumped.documents_unchanged == 0


# ---- dimension scope ------------------------------------------------------


def test_referenced_geo_ids_reports_what_the_data_uses(resolver, geography):
    """A dimension exists to make its facts interpretable, so publishing
    members nothing refers to is noise."""
    _land_and_extract(resolver, b"bulan;wilayah;nilai\n2026-01;Jawa Barat;5\n")
    mapping = ColumnMapping(
        indicator_id="X",
        period_column="bulan",
        value_column="nilai",
        geo_column="wilayah",
    )
    runner = SilverRunner(resolver, geography=geography)
    runner.normalize(mapping, dataset="inflation")

    assert runner.referenced_geo_ids() == {"ID-JB"}


def test_referenced_geo_ids_is_empty_before_anything_is_normalized(resolver):
    assert SilverRunner(resolver).referenced_geo_ids() == set()


def test_unresolved_places_do_not_appear_as_referenced(resolver, geography):
    """A null geo_id is a gap in the data, not a member of the dimension."""
    _land_and_extract(resolver, b"bulan;wilayah;nilai\n2026-01;Atlantis;5\n")
    mapping = ColumnMapping(
        indicator_id="X",
        period_column="bulan",
        value_column="nilai",
        geo_column="wilayah",
    )
    runner = SilverRunner(resolver, geography=geography)
    result = runner.normalize(mapping, dataset="inflation")

    assert result.stats.unresolved_geo == 1
    assert runner.referenced_geo_ids() == set()
