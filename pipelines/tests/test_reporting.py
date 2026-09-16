"""The bridge from pipeline results to catalog rows."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import requires_postgres

from terusan_pipelines.catalog import (
    CatalogReporter,
    CatalogUnavailable,
    NullReporter,
    connect,
    recent_runs,
    reporter,
    sync_sources,
)
from terusan_pipelines.extract import ExtractionRunner
from terusan_pipelines.sources import (
    Artifact,
    Category,
    CollectionMethod,
    Landing,
    Runner,
    ScrapeContext,
    Source,
    SourceMeta,
    SourceType,
)
from terusan_pipelines.storage import StorageConfig, StorageResolver


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


def meta(slug: str = "bps") -> SourceMeta:
    return SourceMeta(
        slug=slug,
        name="Badan Pusat Statistik",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
    )


class _Fake(Source):
    meta = meta("fake-reported")

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        for i in range(3):
            yield Artifact(
                content=f"month,value\n2026-0{i + 1},{i}\n".encode(),
                filename=f"cpi-{i}.csv",
                dataset="inflation",
            )


class _Broken(Source):
    meta = meta("broken-reported")

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        raise RuntimeError("portal changed layout")
        yield  # pragma: no cover


# ---- degrading without a catalog -----------------------------------------


def test_a_runner_with_no_reporter_still_works(resolver):
    """The lake is the system of record; the catalog records what happened."""
    result = Runner(resolver).run_one(_Fake())
    assert result.artifacts_landed == 3


def test_null_reporter_accepts_everything(resolver):
    result = Runner(resolver, reporter=NullReporter()).run_one(_Fake())
    assert result.artifacts_landed == 3


def test_reporter_falls_back_when_there_is_no_catalog(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with reporter() as r:
        assert isinstance(r, NullReporter)


def test_required_reporter_fails_loudly(monkeypatch):
    """For scheduled runs, losing history silently is worse than failing."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(CatalogUnavailable), reporter(required=True):
        pass


def test_a_broken_catalog_does_not_break_ingestion(resolver):
    """The data is already landed; history is not worth losing it over."""

    class Exploding:
        def source_run(self, meta, result, trigger="manual"):
            raise RuntimeError("catalog is on fire")

        def extraction_run(self, result, trigger="manual"):
            raise RuntimeError("catalog is on fire")

    result = Runner(resolver, reporter=Exploding()).run_one(_Fake())
    assert result.succeeded
    assert result.artifacts_landed == 3


# ---- recording real runs --------------------------------------------------


pytestmark_db = requires_postgres


@requires_postgres
def test_a_source_run_becomes_a_catalog_row(resolver, catalog_url):
    with connect(catalog_url) as conn:
        sync_sources(conn, [_Fake.meta])

    result = Runner(resolver, reporter=CatalogReporter(catalog_url)).run_one(_Fake())
    assert result.artifacts_landed == 3

    with connect(catalog_url) as conn:
        runs = recent_runs(conn, slug="ingest-fake-reported", limit=1)
    assert runs[0]["status"] == "succeeded"
    assert runs[0]["records_out"] == 3
    assert runs[0]["bytes_written"] > 0
    assert runs[0]["finished_at"] is not None


@requires_postgres
def test_a_failed_source_is_recorded_as_failed(resolver, catalog_url):
    """The runner caught this; the catalog still has to show it."""
    with connect(catalog_url) as conn:
        sync_sources(conn, [_Broken.meta])

    result = Runner(resolver, reporter=CatalogReporter(catalog_url)).run_one(_Broken())
    assert not result.succeeded

    with connect(catalog_url) as conn:
        runs = recent_runs(conn, slug="ingest-broken-reported", limit=1)
    assert runs[0]["status"] == "failed"
    assert "portal changed layout" in runs[0]["error_message"]


@requires_postgres
def test_extraction_records_a_run_and_versions_bronze(resolver, catalog_url):
    landing = Landing(resolver)
    for i in range(2):
        landing.land(
            meta(),
            Artifact(content=f"a\n{i}\n".encode(), filename=f"f{i}.csv", dataset="d"),
        )

    ExtractionRunner(resolver, reporter=CatalogReporter(catalog_url)).run()

    with connect(catalog_url) as conn:
        runs = recent_runs(conn, slug="extract-bronze", limit=1)
        datasets = conn.execute(
            "SELECT slug, layer, storage_path, row_count FROM datasets "
            "WHERE slug LIKE 'bronze-%' ORDER BY slug"
        ).fetchall()
        versions = conn.execute(
            "SELECT version, changelog FROM dataset_versions ORDER BY released_at"
        ).fetchall()

    assert runs[0]["status"] == "succeeded"
    assert runs[0]["records_out"] == 2
    assert [d["slug"] for d in datasets] == ["bronze-documents", "bronze-records"]
    assert all(d["layer"] == "bronze" for d in datasets)
    assert all(not d["storage_path"].startswith(("/", "s3://")) for d in datasets)
    assert versions and versions[0]["version"] == "v1.0"


@requires_postgres
def test_an_idempotent_extraction_does_not_manufacture_a_version(resolver, catalog_url):
    """A run that wrote nothing has nothing to version (program.md §40)."""
    Landing(resolver).land(meta(), Artifact(content=b"a\n1\n", filename="x.csv", dataset="d"))
    reporting = CatalogReporter(catalog_url)
    ExtractionRunner(resolver, reporter=reporting).run()

    with connect(catalog_url) as conn:
        before = conn.execute("SELECT count(*) AS n FROM dataset_versions").fetchone()["n"]

    ExtractionRunner(resolver, reporter=reporting).run()

    with connect(catalog_url) as conn:
        after = conn.execute("SELECT count(*) AS n FROM dataset_versions").fetchone()["n"]
        runs = recent_runs(conn, slug="extract-bronze", limit=2)

    assert after == before
    # The no-op run is still recorded: knowing a refresh happened and changed
    # nothing is the point of run history.
    assert len(runs) == 2
    assert runs[0]["records_out"] == 0


@requires_postgres
def test_extraction_failures_show_in_the_run_row(resolver, catalog_url):
    landing = Landing(resolver)
    landing.land(meta(), Artifact(content=b"a\n1\n", filename="good.csv", dataset="d"))
    landing.land(meta(), Artifact(content=b"{not json", filename="bad.json", dataset="d"))

    ExtractionRunner(resolver, reporter=CatalogReporter(catalog_url)).run()

    with connect(catalog_url) as conn:
        runs = recent_runs(conn, slug="extract-bronze", limit=1)
    assert runs[0]["status"] == "failed"
    assert "1 of 2" in runs[0]["error_message"]


@requires_postgres
def test_the_trigger_is_recorded(resolver, catalog_url):
    """Distinguishing a scheduled run from a manual one matters when debugging."""
    with connect(catalog_url) as conn:
        sync_sources(conn, [_Fake.meta])

    Runner(resolver, reporter=CatalogReporter(catalog_url), trigger="schedule").run_one(_Fake())

    with connect(catalog_url) as conn:
        runs = recent_runs(conn, slug="ingest-fake-reported", limit=1)
    assert runs[0]["trigger"] == "schedule"
