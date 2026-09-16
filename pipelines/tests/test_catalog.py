"""The PostgreSQL catalog: source registry, run history, dataset versions."""

from __future__ import annotations

import pytest
from conftest import requires_postgres

from terusan_pipelines.catalog import (
    CatalogUnavailable,
    DatasetStats,
    connect,
    definition_for_source,
    fail_run,
    list_datasets,
    next_version,
    recent_runs,
    record_run,
    record_version,
    source_id,
    stale_runs,
    sync_sources,
    upsert_dataset,
    upsert_definition,
)
from terusan_pipelines.sources import (
    Category,
    CollectionMethod,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)
from terusan_pipelines.storage import Layer

pytestmark = requires_postgres


def meta(slug: str = "bps", **kw) -> SourceMeta:
    defaults = {
        "slug": slug,
        "name": "Badan Pusat Statistik",
        "category": Category.STATISTICS,
        "source_type": SourceType.GOVERNMENT_API,
        "collection_method": CollectionMethod.API,
        "organization": "BPS",
        "license": "CC-BY-4.0",
        "update_frequency": UpdateFrequency.MONTHLY,
    }
    defaults.update(kw)
    return SourceMeta(**defaults)


# ---- connecting -----------------------------------------------------------


def test_missing_database_url_is_an_explicit_failure(monkeypatch):
    """Silently skipping the catalog would hide a broken deployment."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(CatalogUnavailable, match="DATABASE_URL"), connect():
        pass


def test_unreachable_catalog_reports_rather_than_hangs():
    unreachable = "postgres://localhost:1/nope?connect_timeout=1"
    with pytest.raises(CatalogUnavailable, match="cannot reach"), connect(unreachable):
        pass


def test_connect_commits_on_clean_exit(catalog_url):
    with connect(catalog_url) as conn:
        sync_sources(conn, [meta("commit-check")])
    with connect(catalog_url) as conn:
        assert source_id(conn, "commit-check") is not None
        conn.execute("DELETE FROM sources WHERE slug = 'commit-check'")


def test_connect_rolls_back_on_failure(catalog_url):
    with pytest.raises(RuntimeError), connect(catalog_url) as conn:
        sync_sources(conn, [meta("rollback-check")])
        raise RuntimeError("something went wrong")

    with connect(catalog_url) as conn:
        assert source_id(conn, "rollback-check") is None


# ---- source registry ------------------------------------------------------


def test_sync_inserts_new_sources(catalog):
    result = sync_sources(catalog, [meta("bps"), meta("bi", name="Bank Indonesia")])
    assert sorted(result.inserted) == ["bi", "bps"]
    assert result.updated == []
    assert result.total == 2


def test_sync_is_idempotent(catalog):
    sync_sources(catalog, [meta()])
    second = sync_sources(catalog, [meta()])
    assert second.inserted == []
    assert second.updated == ["bps"]


def test_sync_updates_a_changed_record(catalog):
    sync_sources(catalog, [meta()])
    sync_sources(catalog, [meta(name="BPS — Statistics Indonesia", license="ODC-BY")])

    row = catalog.execute("SELECT name, license FROM sources WHERE slug='bps'").fetchone()
    assert row["name"] == "BPS — Statistics Indonesia"
    assert row["license"] == "ODC-BY"


def test_sync_carries_every_registry_field(catalog):
    sync_sources(catalog, [meta(base_url="https://webapi.bps.go.id", notes="via API")])
    row = catalog.execute("SELECT * FROM sources WHERE slug='bps'").fetchone()

    assert row["organization"] == "BPS"
    assert row["source_type"] == "government_api"
    assert row["collection_method"] == "api"
    assert row["update_frequency"] == "monthly"
    assert row["base_url"] == "https://webapi.bps.go.id"
    assert row["country"] == "ID"
    assert row["active"] is True


def test_sync_reports_orphans_without_deleting_them(catalog):
    """A scraper removed in a refactor must not take its history with it."""
    sync_sources(catalog, [meta("retired-source")])
    result = sync_sources(catalog, [meta("still-here")])

    assert "retired-source" in result.orphaned
    assert source_id(catalog, "retired-source") is not None


def test_inactive_sources_round_trip(catalog):
    sync_sources(catalog, [meta(active=False)])
    row = catalog.execute("SELECT active FROM sources WHERE slug='bps'").fetchone()
    assert row["active"] is False


# ---- pipeline definitions and runs (program.md §39) -----------------------


def test_every_source_gets_a_pipeline_definition(catalog):
    sync_sources(catalog, [meta()])
    definition_id = definition_for_source(catalog, meta(schedule="0 3 2 * *"))

    row = catalog.execute(
        "SELECT slug, target_layer, schedule, source_id FROM pipeline_definitions WHERE id=%s",
        (definition_id,),
    ).fetchone()
    assert row["slug"] == "ingest-bps"
    assert row["target_layer"] == "raw"
    assert row["schedule"] == "0 3 2 * *"
    assert row["source_id"] is not None


def test_definition_upsert_is_idempotent(catalog):
    sync_sources(catalog, [meta()])
    first = definition_for_source(catalog, meta())
    second = definition_for_source(catalog, meta())
    assert first == second


def test_run_is_recorded_as_running_before_the_work_starts(catalog_url):
    """A process killed mid-run must still leave a trace."""
    with connect(catalog_url) as conn:
        sync_sources(conn, [meta("run-visibility")])
        definition_id = definition_for_source(conn, meta("run-visibility"))

    with connect(catalog_url) as conn, record_run(conn, definition_id) as handle:
        # A separate connection sees the open row while work is in flight.
        with connect(catalog_url) as observer:
            row = observer.execute(
                "SELECT status FROM pipeline_runs WHERE id=%s", (handle.run_id,)
            ).fetchone()
            assert row["status"] == "running"
        handle.records_out = 5

    with connect(catalog_url) as conn:
        row = conn.execute(
            "SELECT status, records_out, finished_at FROM pipeline_runs WHERE id=%s",
            (handle.run_id,),
        ).fetchone()
    assert row["status"] == "succeeded"
    assert row["records_out"] == 5
    assert row["finished_at"] is not None


def test_a_raising_block_records_a_failed_run(catalog_url):
    with connect(catalog_url) as conn:
        sync_sources(conn, [meta("run-failure")])
        definition_id = definition_for_source(conn, meta("run-failure"))

    with (
        connect(catalog_url) as conn,
        pytest.raises(ValueError, match="portal changed"),
        record_run(conn, definition_id) as handle,
    ):
        raise ValueError("portal changed layout")

    with connect(catalog_url) as conn:
        row = conn.execute(
            "SELECT status, error_message FROM pipeline_runs WHERE id=%s", (handle.run_id,)
        ).fetchone()
    assert row["status"] == "failed"
    assert "portal changed layout" in row["error_message"]


def test_fail_run_closes_a_run_without_raising(catalog):
    """The source runner reports a broken scraper as a result, not an exception."""
    sync_sources(catalog, [meta()])
    definition_id = definition_for_source(catalog, meta())
    row = catalog.execute(
        "INSERT INTO pipeline_runs (pipeline_definition_id, status) "
        "VALUES (%s, 'running') RETURNING id",
        (definition_id,),
    ).fetchone()

    fail_run(catalog, row["id"], "HTTP 503")
    closed = catalog.execute(
        "SELECT status, error_message FROM pipeline_runs WHERE id=%s", (row["id"],)
    ).fetchone()
    assert closed["status"] == "failed"
    assert closed["error_message"] == "HTTP 503"


def test_recent_runs_are_newest_first(catalog):
    sync_sources(catalog, [meta()])
    definition_id = definition_for_source(catalog, meta())
    for _ in range(3):
        catalog.execute(
            "INSERT INTO pipeline_runs (pipeline_definition_id, status) VALUES (%s, 'succeeded')",
            (definition_id,),
        )

    runs = recent_runs(catalog, limit=2)
    assert len(runs) == 2
    assert all(r["slug"] == "ingest-bps" for r in runs)


def test_stale_runs_surface_processes_that_died(catalog):
    """An ingestion that stopped weeks ago is what this table exists to catch."""
    sync_sources(catalog, [meta()])
    definition_id = definition_for_source(catalog, meta())
    catalog.execute(
        "INSERT INTO pipeline_runs (pipeline_definition_id, status, started_at) "
        "VALUES (%s, 'running', now() - interval '2 days')",
        (definition_id,),
    )
    catalog.execute(
        "INSERT INTO pipeline_runs (pipeline_definition_id, status, started_at) "
        "VALUES (%s, 'running', now())",
        (definition_id,),
    )

    stale = stale_runs(catalog, hours=6)
    assert len(stale) == 1


# ---- datasets and versions (program.md §19, §40) --------------------------


def test_dataset_registration_round_trips(catalog):
    sync_sources(catalog, [meta()])
    upsert_dataset(
        catalog,
        slug="bronze-records",
        title="Bronze — extracted records",
        layer=Layer.BRONZE,
        storage_path="records",
        source_slug="bps",
        partition_keys=["source_id"],
        tags=["bronze", "raw-extract"],
    )
    rows = list_datasets(catalog, layer=Layer.BRONZE)
    assert len(rows) == 1
    assert rows[0]["slug"] == "bronze-records"
    assert rows[0]["source_slug"] == "bps"


def test_dataset_defaults_to_internal_access(catalog):
    """Widening access should be a decision; narrowing it, a correction."""
    upsert_dataset(catalog, slug="d", title="D", layer=Layer.SILVER, storage_path="d")
    row = catalog.execute("SELECT access_level FROM datasets WHERE slug='d'").fetchone()
    assert row["access_level"] == "internal"


@pytest.mark.parametrize(
    "path", ["/Volumes/research/terusan/silver/x", "s3://terusan-warehouse/silver/x"]
)
def test_physical_storage_paths_are_refused(catalog, path):
    """A physical path in the catalog would pin it to one backend."""
    with pytest.raises(ValueError, match="logical address"):
        upsert_dataset(catalog, slug="bad", title="Bad", layer=Layer.SILVER, storage_path=path)


def test_the_schema_refuses_them_too(catalog):
    """Belt and braces: the check constraint holds even if the code changes."""
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        catalog.execute(
            "INSERT INTO datasets (slug, title, layer, storage_path) "
            "VALUES ('x', 'X', 'silver', 's3://bucket/x')"
        )


def test_versions_accumulate(catalog):
    dataset_id = upsert_dataset(
        catalog, slug="obs", title="Observations", layer=Layer.SILVER, storage_path="obs"
    )
    record_version(catalog, dataset_id, version="v1.0", stats=DatasetStats(100, 2048))
    record_version(catalog, dataset_id, version="v1.1", stats=DatasetStats(150, 3072))

    rows = catalog.execute(
        "SELECT version FROM dataset_versions WHERE dataset_id=%s ORDER BY version",
        (dataset_id,),
    ).fetchall()
    assert [r["version"] for r in rows] == ["v1.0", "v1.1"]


def test_recording_a_version_updates_the_dataset_counters(catalog):
    dataset_id = upsert_dataset(
        catalog, slug="obs", title="Observations", layer=Layer.SILVER, storage_path="obs"
    )
    record_version(catalog, dataset_id, version="v1.0", stats=DatasetStats(500, 9000))

    row = catalog.execute(
        "SELECT row_count, size_bytes FROM datasets WHERE id=%s", (dataset_id,)
    ).fetchone()
    assert row["row_count"] == 500
    assert row["size_bytes"] == 9000


def test_next_version_starts_at_one_zero(catalog):
    dataset_id = upsert_dataset(
        catalog, slug="new", title="New", layer=Layer.GOLD, storage_path="new"
    )
    assert next_version(catalog, dataset_id) == "v1.0"


def test_next_version_bumps_the_minor(catalog):
    """An ingestion run produces more of the same thing, not a new methodology."""
    dataset_id = upsert_dataset(
        catalog, slug="obs", title="Obs", layer=Layer.GOLD, storage_path="obs"
    )
    record_version(catalog, dataset_id, version="v1.4")
    assert next_version(catalog, dataset_id) == "v1.5"


def test_an_unparseable_version_does_not_block_a_release(catalog):
    dataset_id = upsert_dataset(
        catalog, slug="odd", title="Odd", layer=Layer.GOLD, storage_path="odd"
    )
    record_version(catalog, dataset_id, version="2026-Q1")
    assert next_version(catalog, dataset_id) == "2026-Q1-next"


def test_recording_the_same_version_twice_updates_it(catalog):
    dataset_id = upsert_dataset(
        catalog, slug="obs", title="Obs", layer=Layer.SILVER, storage_path="obs"
    )
    record_version(catalog, dataset_id, version="v1.0", stats=DatasetStats(10, 100))
    record_version(catalog, dataset_id, version="v1.0", stats=DatasetStats(20, 200))

    rows = catalog.execute(
        "SELECT row_count FROM dataset_versions WHERE dataset_id=%s", (dataset_id,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["row_count"] == 20


def test_change_kinds_are_recorded(catalog):
    """Program.md §40 asks what changed, not just that something did."""
    dataset_id = upsert_dataset(
        catalog, slug="obs", title="Obs", layer=Layer.SILVER, storage_path="obs"
    )
    record_version(
        catalog,
        dataset_id,
        version="v1.1",
        change_kinds=["new_observations", "corrections"],
        changelog="Revised January figures",
    )
    row = catalog.execute(
        "SELECT change_kinds, changelog FROM dataset_versions WHERE dataset_id=%s",
        (dataset_id,),
    ).fetchone()
    assert set(row["change_kinds"]) == {"new_observations", "corrections"}
    assert row["changelog"] == "Revised January figures"


def test_upsert_definition_records_the_body_it_ran_with(catalog):
    """A run must be readable against the definition that produced it (§38)."""
    definition_id = upsert_definition(
        catalog,
        slug="extract-bronze",
        name="Extract RAW into Bronze",
        target_layer=Layer.BRONZE,
        definition={"kind": "extract", "parsers": ["csv", "html"]},
    )
    row = catalog.execute(
        "SELECT definition FROM pipeline_definitions WHERE id=%s", (definition_id,)
    ).fetchone()
    assert row["definition"]["parsers"] == ["csv", "html"]
