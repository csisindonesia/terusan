"""Dimension resolution, the observation mapping, and Bronze to Silver."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from terusan_pipelines.extract import ExtractionRunner
from terusan_pipelines.identifiers import indicator_code
from terusan_pipelines.normalize import (
    CollapsedDimension,
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
    SilverResult,
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
    result = SilverRunner(resolver, geography=geography).normalize(
        mapping, dataset="inflation", source_id="bps"
    )

    # Partitioned by the identifier the series is published under, which is a
    # derived code and not the key the mapping declared (program.md §10).
    partitions = list(Path(resolver.resolve(Layer.SILVER, "observations")).glob("indicator_id=*"))
    expected = indicator_code("bps", "CPI_INFLATION_YOY")
    assert [p.name for p in partitions] == [f"indicator_id={expected}"]
    assert result.slug == "CPI_INFLATION_YOY"


def test_the_identifier_is_namespaced_by_source():
    """The same key from two publishers is two series, and neither moves."""
    assert indicator_code("bps", "gdp") == indicator_code("bps", "gdp")
    assert indicator_code("bps", "gdp") != indicator_code("worldbank", "gdp")


def test_a_key_that_is_already_a_code_is_left_alone(resolver, geography):
    """An extractor that composed its own identifier keeps it.

    FRED's series carry codes derived from the publisher's series id, and
    recoding them here would move every partition written under them.
    """
    _land_and_extract(resolver, b"bulan;nilai\n2026-01;5\n")
    result = SilverRunner(resolver, geography=geography).normalize(
        ColumnMapping(indicator_id="wa47qsi1", period_column="bulan", value_column="nilai"),
        dataset="inflation",
        source_id="fred-indonesia",
    )
    assert result.indicator_id == "wa47qsi1"
    assert result.slug is None


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


def next_parser_version() -> str:
    """A version the corpus has not been extracted under.

    Derived rather than written down: a literal here silently stops testing
    anything the day the real `PARSER_VERSION` catches up with it, which is what
    happened when the FRED reader took version 9.
    """
    import terusan_pipelines.extract.base as base

    return f"{base.PARSER_VERSION}-next"


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
    bumped_to = next_parser_version()

    with parser_version(bumped_to):
        # A second extraction, as a changed extractor would produce.
        ExtractionRunner(resolver).run()

        with Warehouse(resolver) as warehouse:
            warehouse.view("records", Layer.BRONZE, "records")
            versions = warehouse.query(
                "SELECT DISTINCT parser_version FROM records ORDER BY 1"
            ).fetchall()
        # Both are kept: a partition stays traceable to the code that made it.
        assert sorted(v[0] for v in versions) == sorted([original, bumped_to])

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

    with parser_version(next_parser_version()):
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


# ---- data with no geography ----------------------------------------------


def test_an_unresolved_dimension_still_separates_observations():
    """A commodity absent from the registry is still a commodity.

    Bank Indonesia's food price table is forty commodities by date and names no
    place at all. Keying identity on the resolved id alone would give all forty
    of a day's prices one id — the warehouse would assert that rice and chilli
    are the same observation.
    """
    rice = observation_id("food_price_retail", "2026-09-10", None, "Beras")
    chilli = observation_id("food_price_retail", "2026-09-10", None, "Cabai Merah")

    assert rice != chilli
    assert rice == observation_id("food_price_retail", "2026-09-10", None, "Beras")


def test_a_series_with_no_geography_normalizes(resolver):
    """Geography is a dimension, not a requirement."""
    mapping = ColumnMapping(
        indicator_id="food_price_retail",
        value_columns_are_periods=True,
        commodity_column="name",
        unit="IDR",
        number_format=NumberFormat.ANGLO,
    )
    rows = list(
        ObservationNormalizer(mapping).normalize(
            [
                _record(name="Beras", **{"10/09/2026": "14,900", "11/09/2026": "14,800"}),
                _record(name="Cabai Merah", **{"10/09/2026": "52,000", "11/09/2026": "51,500"}),
            ]
        )
    )

    assert len(rows) == 4
    assert all(row["geo_id"] is None for row in rows)
    assert len({row["observation_id"] for row in rows}) == 4
    assert {row["commodity_name_raw"] for row in rows} == {"Beras", "Cabai Merah"}


def test_a_series_whose_commodity_is_its_identity_carries_it(commodities):
    """A price series for one instrument names no commodity in any column.

    Yahoo publishes one file per contract, so the commodity is the series
    rather than a column of it. Without the mapping declaring it, the figures
    land outside the commodity dimension entirely and the only commodities the
    warehouse admits to are the ones that happen to share a table.
    """
    mapping = ColumnMapping(
        indicator_id="nickel_price_close",
        period_column="date",
        value_column="close",
        commodity="nickel",
        unit="USD",
        number_format=NumberFormat.ANGLO,
    )
    rows = list(
        ObservationNormalizer(mapping, commodities=commodities).normalize(
            [
                _record(date="2026-09-10", close="15,300"),
                _record(date="2026-09-11", close="15,420"),
            ]
        )
    )

    assert len(rows) == 2
    assert {row["commodity_id"] for row in rows} == {"NICKEL_ORE"}
    assert {row["commodity_name_raw"] for row in rows} == {"nickel"}
    # Two sessions of one commodity are two observations, not one restated.
    assert len({row["observation_id"] for row in rows}) == 2


def test_a_commodity_column_beats_the_series_wide_constant(commodities):
    """A table that names a commodity per row is saying what the constant cannot.

    Overriding it would collapse every row of a price table into one
    commodity, which is the dimension loss the mapping exists to prevent.
    """
    mapping = ColumnMapping(
        indicator_id="ore_price",
        period_column="date",
        value_column="price",
        commodity_column="name",
        commodity="ferronickel",
        number_format=NumberFormat.ANGLO,
    )
    rows = list(
        ObservationNormalizer(mapping, commodities=commodities).normalize(
            [
                _record(date="2026-09-10", name="nikel", price="15,300"),
                _record(date="2026-09-10", name="Ferronickel", price="18,100"),
            ]
        )
    )

    assert {row["commodity_id"] for row in rows} == {"NICKEL_ORE", "FERRONICKEL"}


def test_a_mapping_that_drops_a_dimension_is_refused(resolver):
    """The failure this guards is silent: the rows are well-formed, the counts
    are plausible, and a chart plots whichever row was read last."""
    _land_and_extract(
        resolver,
        b"name;10/09/2026\nBeras;14900\nCabai Merah;52000\n",
    )
    mapping = ColumnMapping(
        indicator_id="food_price_retail",
        value_columns_are_periods=True,
        # `name` is what the rows differ along, and it is not named here.
        unit="IDR",
        number_format=NumberFormat.ANGLO,
    )

    with pytest.raises(CollapsedDimension) as raised:
        SilverRunner(resolver).normalize(mapping, dataset="inflation")

    assert "--commodity-column" in str(raised.value)


def test_naming_the_dimension_makes_those_same_rows_normalize(resolver):
    """The same file, mapped correctly, is two series rather than a collision."""
    _land_and_extract(
        resolver,
        b"name;10/09/2026;11/09/2026\nBeras;14900;14800\nCabai Merah;52000;51500\n",
    )
    mapping = ColumnMapping(
        indicator_id="food_price_retail",
        value_columns_are_periods=True,
        commodity_column="name",
        unit="IDR",
        number_format=NumberFormat.ANGLO,
    )

    result = SilverRunner(resolver).normalize(mapping, dataset="inflation")

    assert result.observations == 4
    with Warehouse(resolver) as warehouse:
        warehouse.view("observations", Layer.SILVER, "observations")
        distinct, total = warehouse.query(
            "SELECT count(DISTINCT observation_id), count(*) FROM observations"
        ).fetchone()
    assert distinct == total == 4


def test_a_row_restated_identically_is_deduplicated(resolver):
    """A source that repeats a line is not a source that lost a dimension."""
    _land_and_extract(
        resolver,
        b"name;10/09/2026\nBeras;14900\nBeras;14900\n",
    )
    mapping = ColumnMapping(
        indicator_id="food_price_retail",
        value_columns_are_periods=True,
        commodity_column="name",
        unit="IDR",
        number_format=NumberFormat.ANGLO,
    )

    result = SilverRunner(resolver).normalize(mapping, dataset="inflation")

    assert result.stats.duplicate_rows == 1
    with Warehouse(resolver) as warehouse:
        warehouse.view("observations", Layer.SILVER, "observations")
        assert warehouse.query("SELECT count(*) FROM observations").fetchone()[0] == 1


# ---- the source registry ---------------------------------------------------


def test_the_registry_is_published_into_the_lake(resolver):
    """The settings a series is collected under live in code, and the serving
    layer has no other way to read them: the catalog database that also holds
    them is optional, and a reader asking "when does this refresh" should not
    depend on it being up."""
    from terusan_pipelines.sources import Registry

    registry = Registry()
    registry.discover()
    metas = list(registry.metas())

    written = SilverRunner(resolver).write_sources(metas)
    assert written == len(metas)

    with Warehouse(resolver) as warehouse:
        warehouse.view("sources", Layer.SILVER, "sources")
        rows = warehouse.query(
            "SELECT source_id, schedule, active, max_requests_per_second "
            "FROM sources ORDER BY source_id"
        ).fetchall()

    assert [row[0] for row in rows] == sorted(meta.slug for meta in metas)
    # The schedule is the whole point of publishing this, so a run that dropped
    # it would defeat the exercise while still counting the right rows.
    scheduled = {row[0]: row[1] for row in rows if row[1]}
    assert scheduled
    assert all(len(cron.split()) == 5 for cron in scheduled.values())


def test_republishing_the_registry_replaces_rather_than_appends(resolver):
    """A source removed from the code must not linger in the lake."""
    from terusan_pipelines.sources import Registry

    registry = Registry()
    registry.discover()
    metas = list(registry.metas())
    runner = SilverRunner(resolver)

    runner.write_sources(metas)
    runner.write_sources(metas[:2])

    with Warehouse(resolver) as warehouse:
        warehouse.view("sources", Layer.SILVER, "sources")
        assert warehouse.query("SELECT count(*) FROM sources").fetchone()[0] == 2


# ---- indicator names -------------------------------------------------------


def test_indicator_names_are_published_beside_their_identifiers(resolver):
    """An identifier is not a name.

    FRED's are eight-character codes, because its titles are too long for a URL
    and too alike to shorten — so without this table the portal has nothing to
    print but the code.
    """
    written = SilverRunner(resolver).write_indicators(
        [
            {
                "indicator_id": "w9vl0dq8",
                "name": "Nasdaq Indonesia Basic Materials Large Mid Cap Net Total Return Index",
                "code": "NASDAQNQID55LMN",
                "unit": "Index",
                "frequency": "daily",
            },
            {
                "indicator_id": "jj6i3jt3",
                "name": "World Uncertainty Index for Indonesia",
                "code": "WUIIDN",
                "unit": "Index",
                "frequency": "quarterly",
            },
        ],
        source_id="fred-indonesia",
    )
    assert written == 2

    with Warehouse(resolver) as warehouse:
        warehouse.view("indicators", Layer.SILVER, "indicators")
        rows = warehouse.query(
            "SELECT indicator_id, name, code, unit, frequency, source_id "
            "FROM indicators ORDER BY indicator_id"
        ).fetchall()

    assert rows[0][0] == "jj6i3jt3"
    assert rows[0][1] == "World Uncertainty Index for Indonesia"
    # The publisher's own identifier, which is what a reader takes back to FRED.
    assert rows[0][2] == "WUIIDN"
    assert rows[1][4] == "daily"
    assert {row[5] for row in rows} == {"fred-indonesia"}


def test_publishing_one_source_leaves_the_others_alone(resolver):
    """The table is rebuilt per source, so FRED must not delete anyone else."""
    runner = SilverRunner(resolver)
    runner.write_indicators(
        [{"indicator_id": "te_inflation_cpi", "name": "Tingkat Inflasi"}],
        source_id="tradingeconomics-indonesia",
    )
    runner.write_indicators(
        [{"indicator_id": "jj6i3jt3", "name": "World Uncertainty Index"}],
        source_id="fred-indonesia",
    )
    # And re-published, a source replaces only its own rows.
    runner.write_indicators(
        [
            {"indicator_id": "jj6i3jt3", "name": "World Uncertainty Index"},
            {"indicator_id": "nicmjq31", "name": "Currency Conversions"},
        ],
        source_id="fred-indonesia",
    )

    with Warehouse(resolver) as warehouse:
        warehouse.view("indicators", Layer.SILVER, "indicators")
        rows = warehouse.query(
            "SELECT source_id, count(*) FROM indicators GROUP BY 1 ORDER BY 1"
        ).fetchall()

    assert rows == [("fred-indonesia", 2), ("tradingeconomics-indonesia", 1)]


# ---- revisions -------------------------------------------------------------


def _row(observation_id: str, value: str, retrieved_at, unit: str = "USD") -> dict:
    return {
        "observation_id": observation_id,
        "period": "2026-09-17",
        "value": Decimal(value),
        "unit": unit,
        "status": "ok",
        "retrieved_at": retrieved_at,
    }


def test_a_later_retrieval_supersedes_an_earlier_one():
    """A daily API pulled twice returns the same window twice, and the last
    session is still moving: gold closed at 4330.00 in one pull and 4328.40 in
    the other. That is a revision, which is what re-pulling is for."""
    from datetime import datetime

    from terusan_pipelines.normalize.runner import _reject_collapsed

    morning = datetime(2026, 9, 17, 9, 0)
    evening = datetime(2026, 9, 17, 18, 0)
    result = SilverResult(indicator_id="gold_price_close")

    kept = _reject_collapsed(
        [_row("obs_1", "4328.399902344", evening), _row("obs_1", "4330.0", morning)],
        ColumnMapping(indicator_id="gold_price_close", period_column="d", value_column="v"),
        result,
    )

    assert len(kept) == 1
    # The evening figure, whichever order the rows arrived in.
    assert kept[0]["value"] == Decimal("4328.399902344")
    assert result.stats.revised_rows == 1


def test_one_snapshot_holding_two_figures_is_still_refused():
    """Retrieved at the same moment there is nothing to supersede: one snapshot
    cannot hold two figures for one observation, and that is a dropped
    dimension rather than a revision."""
    from datetime import datetime

    from terusan_pipelines.normalize.runner import _reject_collapsed

    same = datetime(2026, 9, 17, 9, 0)

    with pytest.raises(CollapsedDimension) as raised:
        _reject_collapsed(
            [_row("obs_1", "1.0", same), _row("obs_1", "2.0", same)],
            ColumnMapping(indicator_id="x", period_column="d", value_column="v"),
            SilverResult(indicator_id="x"),
        )

    assert "same retrieval" in str(raised.value)


# ---- the document catalogue ----------------------------------------------


def _land(resolver: StorageResolver, content: bytes, filename: str, media_type: str) -> None:
    Landing(resolver).land(
        SourceMeta(
            slug="esdm-publications",
            name="ESDM — Publikasi statistik",
            category=Category.STATISTICS,
            source_type=SourceType.OFFICIAL_PORTAL,
            collection_method=CollectionMethod.BULK_DOWNLOAD,
            organization="Kementerian ESDM",
        ),
        Artifact(
            content=content,
            filename=filename,
            dataset="handbook",
            media_type=media_type,
            metadata={"title": filename},
        ),
    )


def test_the_catalogue_holds_documents_and_not_the_machinery(resolver):
    """A table of spreadsheets under a heading that says Documents describes
    nothing anyone was looking for. RAW keeps them; this does not."""
    _land(resolver, b"%PDF-1.4\n1 0 obj\n", "handbook.pdf", "application/pdf")
    _land(resolver, b"year,value\n2026,1.2\n", "series.csv", "text/csv")
    _land(resolver, b"<html><body>a listing</body></html>", "listing.html", "text/html")

    written = SilverRunner(resolver).write_documents()
    assert written == 1

    pattern = resolver.glob(Layer.SILVER, "documents")
    with Warehouse(resolver) as warehouse:
        rows = warehouse.query(
            f"SELECT title, document_type FROM read_parquet('{pattern}', union_by_name=true)"
        ).fetchall()

    assert [r[1] for r in rows] == ["publication"]
    assert rows[0][0] == "handbook.pdf"


def test_a_lake_of_only_data_files_catalogues_no_documents(resolver):
    """Not an error, and not an empty table by accident: most sources here
    publish spreadsheets and have nothing anyone would sit down and read."""
    _land(resolver, b"year,value\n2026,1.2\n", "series.csv", "text/csv")
    assert SilverRunner(resolver).write_documents() == 0


def test_a_constant_geo_fills_in_a_country_no_column_names(geography):
    """UN Comtrade's public endpoint returns `reporterISO` empty on every row
    of a table that is entirely about Indonesia. Without a constant, those
    figures reach Silver with no geography at all."""
    mapping = ColumnMapping(
        indicator_id="COMTRADE_EXPORTS_TOTAL",
        period_column="period",
        value_column="value",
        geo="IDN",
    )

    rows = list(
        ObservationNormalizer(mapping, geography=geography).normalize(
            [_record(period="2024", value="264000000000")]
        )
    )

    assert [row["geo_id"] for row in rows] == ["ID"]


def test_a_geo_column_wins_over_the_constant(geography):
    """A table naming a place per row says something the series-wide constant
    cannot, and overriding it would collapse the provinces into one country."""
    mapping = ColumnMapping(
        indicator_id="PROVINCIAL_POPULATION",
        period_column="tahun",
        value_column="nilai",
        geo_column="provinsi",
        geo="IDN",
    )

    rows = list(
        ObservationNormalizer(mapping, geography=geography).normalize(
            [_record(tahun="2025", nilai="1", provinsi="Jabar")]
        )
    )

    assert [row["geo_id"] for row in rows] == ["ID-JB"]
