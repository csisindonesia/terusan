"""The run journal in the lake — the history that survives having no database."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from terusan_pipelines.catalog import Fanout, JournalReporter
from terusan_pipelines.normalize import SilverResult
from terusan_pipelines.sources import (
    Artifact,
    Category,
    CollectionMethod,
    Runner,
    ScrapeContext,
    Source,
    SourceMeta,
    SourceType,
)
from terusan_pipelines.storage import StorageConfig, StorageResolver
from terusan_pipelines.warehouse import read_runs


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


def meta(slug: str) -> SourceMeta:
    return SourceMeta(
        slug=slug,
        name="Badan Pusat Statistik",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
    )


class _Works(Source):
    meta = meta("journal-works")

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        yield Artifact(
            content=b"period,value\n2026-01,1\n",
            filename="cpi.csv",
            dataset="inflation",
        )


class _Broken(Source):
    meta = meta("journal-broken")

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        raise RuntimeError("portal changed layout")
        yield  # pragma: no cover


def test_a_successful_ingestion_is_journalled(resolver):
    Runner(resolver, reporter=JournalReporter(resolver)).run_one(_Works())

    runs = read_runs(resolver)
    assert len(runs) == 1
    run = runs[0]
    assert run["pipeline"] == "ingest-journal-works"
    assert run["kind"] == "ingest"
    assert run["status"] == "succeeded"
    assert run["source_id"] == "journal-works"
    assert run["records_out"] == 1
    assert run["error_message"] is None
    assert run["duration_seconds"] >= 0


def test_a_broken_scraper_is_journalled_as_failed(resolver):
    Runner(resolver, reporter=JournalReporter(resolver)).run_one(_Broken())

    run = read_runs(resolver)[0]
    assert run["status"] == "failed"
    assert "portal changed layout" in run["error_message"]
    # The traceback is what turns "it broke" into somewhere to look.
    assert "RuntimeError" in json.loads(run["detail"])["traceback"]


def test_a_dry_run_is_not_a_failed_ingestion(resolver):
    Runner(resolver, reporter=JournalReporter(resolver)).run_one(
        _Works(), ScrapeContext(dry_run=True)
    )

    run = read_runs(resolver)[0]
    assert run["status"] == "succeeded"
    assert run["dry_run"] is True
    assert run["records_out"] == 0


def test_runs_can_be_narrowed_to_one_source_or_to_failures(resolver):
    journal = JournalReporter(resolver)
    Runner(resolver, reporter=journal).run_one(_Works())
    Runner(resolver, reporter=journal).run_one(_Broken())

    assert len(read_runs(resolver)) == 2
    assert [r["source_id"] for r in read_runs(resolver, source_id="journal-broken")] == [
        "journal-broken"
    ]
    assert [r["status"] for r in read_runs(resolver, status="failed")] == ["failed"]


def test_a_journal_that_cannot_be_written_does_not_break_the_run(resolver, monkeypatch):
    from terusan_pipelines.warehouse import runlog

    def refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(runlog.ParquetWriter, "write", refuse)

    result = Runner(resolver, reporter=JournalReporter(resolver)).run_one(_Works())

    assert result.succeeded
    assert result.artifacts_landed == 1


def test_a_fanout_keeps_reporting_after_one_reporter_raises(resolver):
    class _Angry:
        def source_run(self, *args, **kwargs):
            raise RuntimeError("catalog is mid-migration")

    Fanout(_Angry(), JournalReporter(resolver)).source_run(
        _Works().meta, Runner(resolver).run_one(_Works())
    )

    assert read_runs(resolver)[0]["status"] == "succeeded"


def test_a_normalization_that_produces_nothing_is_a_failure(resolver):
    """A mapping can return cleanly and still have stopped working."""
    result = SilverResult(indicator_id="nothing-lands", slug="nothing-lands")
    result.stats.rows_in = 300
    result.stats.skipped_no_period = 300

    JournalReporter(resolver).normalize_run(result, source_id="journal-works")

    run = read_runs(resolver)[0]
    assert run["kind"] == "normalize"
    assert run["status"] == "failed"
    assert "produced no observations" in run["error_message"]
    assert run["indicator_id"] == "nothing-lands"


def test_a_normalization_that_lands_observations_succeeds(resolver):
    result = SilverResult(indicator_id="lands", slug="lands")
    result.stats.rows_in = 12
    result.stats.observations = 12

    JournalReporter(resolver).normalize_run(result, source_id="journal-works")

    run = read_runs(resolver)[0]
    assert run["status"] == "succeeded"
    assert run["records_out"] == 12
