"""Source acquisition: landing, provenance, deduplication, the legacy adapter."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from terusan_pipelines.sources import (
    Artifact,
    Category,
    CollectionMethod,
    DuplicateSourceSlug,
    Landing,
    Registry,
    Runner,
    RunStatus,
    ScrapeContext,
    Source,
    SourceMeta,
    SourceType,
    legacy_source,
    summarize,
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


def meta_for(slug: str = "bps-inflation", **kw) -> SourceMeta:
    defaults = {
        "slug": slug,
        "name": "BPS — Consumer Price Index",
        "category": Category.STATISTICS,
        "source_type": SourceType.GOVERNMENT_API,
        "collection_method": CollectionMethod.API,
        "organization": "Badan Pusat Statistik",
        "license": "CC-BY-4.0",
    }
    defaults.update(kw)
    return SourceMeta(**defaults)


def artifact(content: bytes = b"cpi,2026\n1.2", **kw) -> Artifact:
    defaults = {"content": content, "filename": "cpi.csv", "dataset": "inflation"}
    defaults.update(kw)
    return Artifact(**defaults)


# ---- RAW layout and provenance -------------------------------------------


def test_raw_path_follows_the_documented_layout(resolver):
    """category / source / dataset / partition / doc_id — program.md §5."""
    landing = Landing(resolver)
    path = landing.path_for(meta_for(), artifact(partition=("year=2026",)))
    tail = path.split("/raw/")[1]
    parts = tail.split("/")
    assert parts[:4] == ["statistics", "bps-inflation", "inflation", "year=2026"]
    assert parts[4].startswith("doc_")


def test_document_id_is_derived_from_content(resolver):
    """Same bytes must land at the same path, so re-runs are idempotent."""
    landing = Landing(resolver)
    a, b = artifact(), artifact()
    assert landing.path_for(meta_for(), a) == landing.path_for(meta_for(), b)


def test_different_content_lands_separately(resolver):
    landing = Landing(resolver)
    first = landing.path_for(meta_for(), artifact(b"one"))
    second = landing.path_for(meta_for(), artifact(b"two"))
    assert first != second


def test_landing_writes_content_and_provenance(resolver):
    landed = Landing(resolver).land(
        meta_for(),
        artifact(source_url="https://webapi.bps.go.id/cpi", published_at=date(2026, 3, 1)),
    )
    written = Path(landed.path)
    assert written.read_bytes() == b"cpi,2026\n1.2"

    sidecar = json.loads((written.parent / "metadata.json").read_text())
    assert sidecar["content_hash"] == landed.content_hash
    assert sidecar["source"]["slug"] == "bps-inflation"
    assert sidecar["source"]["license"] == "CC-BY-4.0"
    assert sidecar["source_url"] == "https://webapi.bps.go.id/cpi"
    assert sidecar["published_at"] == "2026-03-01"
    assert sidecar["retrieved_at"]


def test_relanding_identical_content_is_a_noop(resolver):
    landing = Landing(resolver)
    first = landing.land(meta_for(), artifact())
    second = landing.land(meta_for(), artifact())

    assert not first.deduplicated
    assert second.deduplicated
    assert second.bytes_written == 0
    assert first.path == second.path


def test_untrusted_titles_are_slugified_into_the_path(resolver):
    """Scraped text carries slashes and em dashes; that is input, not a bug."""
    landed = Landing(resolver).land(
        meta_for(),
        # Real PDF bytes: landing verifies content against the extension, so a
        # CSV named .PDF would be refused (see the sniffing tests).
        artifact(
            content=b"%PDF-1.4\n1 0 obj\n",
            dataset="Ekspor Nikel — Q1/2026",
            filename="Laporan Akhir.PDF",
        ),
    )
    assert "/ekspor-nikel-q1-2026/" in landed.path
    assert landed.path.endswith("/laporan-akhir.pdf")


# ---- running sources ------------------------------------------------------


class _Fake(Source):
    meta = meta_for("fake-source")

    def __init__(self, count: int = 3) -> None:
        self._count = count

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        for i in range(self._count):
            yield artifact(content=f"row-{i}".encode(), filename=f"part-{i}.csv")


class _Broken(Source):
    meta = meta_for("broken-source")

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        yield artifact(content=b"partial")
        raise RuntimeError("portal changed layout")


def test_runner_lands_everything_a_source_yields(resolver):
    result = Runner(resolver, keep_paths=True).run_one(_Fake(3))
    assert result.status is RunStatus.SUCCEEDED
    assert result.artifacts_seen == 3
    assert result.artifacts_landed == 3
    assert result.bytes_written > 0
    assert all(Path(p).exists() for p in result.landed_paths)


def test_second_run_deduplicates(resolver):
    runner = Runner(resolver)
    runner.run_one(_Fake(3))
    second = runner.run_one(_Fake(3))
    assert second.artifacts_deduplicated == 3
    assert second.artifacts_landed == 0
    assert second.bytes_written == 0


def test_a_raising_source_becomes_a_failed_result(resolver):
    """One broken scraper must not abort a batch of fifty."""
    result = Runner(resolver).run_one(_Broken())
    assert result.status is RunStatus.FAILED
    assert "portal changed layout" in (result.error_message or "")
    assert result.error_traceback
    assert result.artifacts_landed == 1  # what it managed before failing


def test_dry_run_fetches_but_does_not_land(resolver):
    result = Runner(resolver, keep_paths=True).run_one(_Fake(3), ScrapeContext(dry_run=True))
    assert result.artifacts_seen == 3
    assert result.artifacts_landed == 0
    assert result.landed_paths == []


def test_limit_stops_early(resolver):
    result = Runner(resolver).run_one(_Fake(10), ScrapeContext(limit=2))
    assert result.artifacts_seen == 2


def test_run_many_keeps_going_past_a_failure(resolver):
    results = Runner(resolver, max_workers=2).run_many([_Fake(2), _Broken()])
    summary = summarize(results)
    assert summary["sources"] == 2
    assert summary["succeeded"] == 1
    assert summary["failed"] == 1
    assert "broken-source" in summary["failures"]


# ---- the legacy adapter ---------------------------------------------------


def test_legacy_script_runs_unmodified_with_relative_paths(resolver, monkeypatch):
    """The whole point: an ad-hoc script keeps writing to `out/` and works."""
    calls: list[str] = []

    def ad_hoc_scraper() -> None:  # no arguments, writes relative — as found
        calls.append("ran")
        out = Path("out")
        out.mkdir(exist_ok=True)
        (out / "inflation.csv").write_text("month,value\n2026-01,1.2\n")
        (out / "trade.csv").write_text("month,value\n2026-01,9.9\n")

    Wrapped = legacy_source(
        ad_hoc_scraper,
        meta=meta_for("legacy-bps", collection_method=CollectionMethod.SCRAPE),
        dataset="bulk",
        output_subdir="out",
    )
    result = Runner(resolver, keep_paths=True).run_one(Wrapped())

    assert calls == ["ran"]
    assert result.status is RunStatus.SUCCEEDED
    assert result.artifacts_landed == 2
    assert any("inflation.csv" in p for p in result.landed_paths)


def test_legacy_script_accepting_an_output_dir(resolver):
    def ad_hoc_scraper(output_dir: Path) -> None:
        (output_dir / "report.json").write_text('{"ok": true}')

    Wrapped = legacy_source(ad_hoc_scraper, meta=meta_for("legacy-dir"), dataset="reports")
    result = Runner(resolver, keep_paths=True).run_one(Wrapped())
    assert result.artifacts_landed == 1
    assert result.landed_paths[0].endswith("report.json")


def test_legacy_adapter_restores_the_working_directory(resolver):
    def ad_hoc_scraper() -> None:
        Path("something.txt").write_text("x")

    before = Path.cwd()
    Wrapped = legacy_source(ad_hoc_scraper, meta=meta_for("legacy-cwd"), dataset="d")
    Runner(resolver).run_one(Wrapped())
    assert Path.cwd() == before


def test_legacy_adapter_restores_cwd_even_when_the_script_raises(resolver):
    def ad_hoc_scraper() -> None:
        raise RuntimeError("boom")

    before = Path.cwd()
    result = Runner(resolver).run_one(
        legacy_source(ad_hoc_scraper, meta=meta_for("legacy-boom"), dataset="d")()
    )
    assert result.status is RunStatus.FAILED
    assert Path.cwd() == before


def test_legacy_output_does_not_leak_between_runs(resolver):
    """Stale files would be re-landed as though freshly fetched."""
    state = {"n": 0}

    def ad_hoc_scraper() -> None:
        state["n"] += 1
        Path(f"run-{state['n']}.txt").write_text(str(state["n"]))

    Wrapped = legacy_source(ad_hoc_scraper, meta=meta_for("legacy-clean"), dataset="d")
    runner = Runner(resolver, keep_paths=True)
    runner.run_one(Wrapped())
    second = runner.run_one(Wrapped())

    assert second.artifacts_seen == 1
    assert second.landed_paths[0].endswith("run-2.txt")


def test_legacy_sources_are_not_run_concurrently(resolver):
    """They move the process working directory, so the runner serialises them."""
    from terusan_pipelines.sources.legacy import LegacySource

    assert LegacySource.concurrency_safe is False
    assert getattr(_Fake, "concurrency_safe", True) is True


# ---- registry -------------------------------------------------------------


def test_registry_rejects_duplicate_slugs():
    """Slugs key the sources table and the RAW path."""
    registry = Registry()

    class A(Source):
        meta = meta_for("collide")

        def collect(self, ctx):
            yield from ()

    class B(Source):
        meta = meta_for("collide")

        def collect(self, ctx):
            yield from ()

    registry.register(A)
    with pytest.raises(DuplicateSourceSlug, match="collide"):
        registry.register(B)


def test_concrete_source_without_meta_fails_at_import_time():
    with pytest.raises(TypeError, match="must define `meta`"):

        class Forgetful(Source):
            def collect(self, ctx):
                yield from ()


def test_abstract_bases_need_no_meta():
    class Intermediate(Source, abstract=True):
        def collect(self, ctx):
            yield from ()

    assert Intermediate.abstract


def test_registry_lists_scheduled_sources():
    registry = Registry()

    class Scheduled(Source):
        meta = meta_for("scheduled-one", schedule="0 3 * * *")

        def collect(self, ctx):
            yield from ()

    class Manual(Source):
        meta = meta_for("manual-one")

        def collect(self, ctx):
            yield from ()

    registry.register(Scheduled)
    registry.register(Manual)
    registry._loaded = True
    assert [s.meta.slug for s in registry.scheduled()] == ["scheduled-one"]
