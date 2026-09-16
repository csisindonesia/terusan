"""Command-line entry point for the ingestion pipelines."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from terusan_pipelines.catalog import (
    CatalogUnavailable,
    connect,
    list_datasets,
    recent_runs,
    reporter,
    stale_runs,
    sync_sources,
)
from terusan_pipelines.extract import ExtractionRunner
from terusan_pipelines.sources import Registry, Runner, ScrapeContext, registry, summarize
from terusan_pipelines.storage import Layer, StorageConfig, StorageResolver, slugify
from terusan_pipelines.warehouse import Warehouse, compact_layer

app = typer.Typer(help="Terusan research data warehouse pipelines.", no_args_is_help=True)
storage_app = typer.Typer(help="Inspect and prepare data-lake storage.", no_args_is_help=True)
sources_app = typer.Typer(help="List and run data sources.", no_args_is_help=True)
warehouse_app = typer.Typer(help="Extract, compact and query the lake.", no_args_is_help=True)
catalog_app = typer.Typer(help="Inspect and sync the PostgreSQL catalog.", no_args_is_help=True)
app.add_typer(storage_app, name="storage")
app.add_typer(sources_app, name="sources")
app.add_typer(warehouse_app, name="warehouse")
app.add_typer(catalog_app, name="catalog")


def _resolver() -> StorageResolver:
    return StorageResolver(StorageConfig())


def _registry() -> Registry:
    registry.ensure_loaded()
    return registry


@storage_app.command("info")
def storage_info() -> None:
    """Show which physical backend STORAGE_ROOT currently points at."""
    config = _resolver().config
    typer.echo(
        json.dumps(
            {
                "profile": str(config.profile),
                "backend": str(config.backend),
                "root": config.root,
                "scratch_dir": str(config.scratch_dir),
                "writes_allowed": config.writes_allowed,
            },
            indent=2,
        )
    )


@storage_app.command("init")
def storage_init() -> None:
    """Create every layer directory under STORAGE_ROOT."""
    resolver = _resolver()
    created = resolver.ensure_layout()
    if not created:
        typer.echo("object storage: no directories to create")
        raise typer.Exit()
    for path in created:
        typer.echo(path)


@storage_app.command("resolve")
def storage_resolve(
    layer: Annotated[Layer, typer.Argument(help="Which storage layer to address.")],
    segments: Annotated[
        list[str] | None, typer.Argument(help="Path segments below the layer.")
    ] = None,
    glob: Annotated[
        bool, typer.Option("--glob", help="Emit a DuckDB read_parquet pattern.")
    ] = False,
) -> None:
    """Resolve a logical address to its physical path."""
    resolver = _resolver()
    parts = segments or []
    typer.echo(resolver.glob(layer, *parts) if glob else resolver.resolve(layer, *parts))


@storage_app.command("slug")
def storage_slug(value: Annotated[str, typer.Argument(help="Untrusted text.")]) -> None:
    """Reduce untrusted text to a safe path segment."""
    typer.echo(slugify(value))


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------


@sources_app.command("list")
def sources_list(
    scheduled_only: Annotated[
        bool, typer.Option("--scheduled", help="Only sources that declare a schedule.")
    ] = False,
) -> None:
    """List every registered source."""
    reg = _registry()
    sources = reg.scheduled() if scheduled_only else reg.all()
    if not sources:
        typer.echo("no sources registered")
        raise typer.Exit()
    for source in sources:
        meta = source.meta
        flags = [str(meta.category), str(meta.collection_method)]
        if meta.schedule:
            flags.append(meta.schedule)
        if not meta.active:
            flags.append("inactive")
        typer.echo(f"{meta.slug:<32} {meta.name}  [{', '.join(flags)}]")


@sources_app.command("show")
def sources_show(slug: Annotated[str, typer.Argument(help="Source slug.")]) -> None:
    """Show one source's registry record."""
    meta = _registry().get(slug).meta
    typer.echo(
        json.dumps(
            {
                "slug": meta.slug,
                "name": meta.name,
                "organization": meta.organization,
                "category": str(meta.category),
                "source_type": str(meta.source_type),
                "collection_method": str(meta.collection_method),
                "base_url": meta.base_url,
                "license": meta.license,
                "update_frequency": str(meta.update_frequency),
                "schedule": meta.schedule,
                "active": meta.active,
                "max_requests_per_second": meta.max_requests_per_second,
            },
            indent=2,
        )
    )


@sources_app.command("run")
def sources_run(
    slugs: Annotated[
        list[str] | None,
        typer.Argument(help="Source slugs to run. Omit to run every scheduled source."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Fetch but do not land in RAW.")
    ] = False,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Stop after this many artifacts per source.")
    ] = None,
    workers: Annotated[int, typer.Option("--workers", help="Concurrent sources.")] = 4,
    rate: Annotated[
        float, typer.Option("--rate", help="Default requests per second, per host.")
    ] = 1.0,
    trigger: Annotated[str, typer.Option("--trigger", help="How this run was started.")] = "manual",
    require_catalog: Annotated[
        bool,
        typer.Option("--require-catalog", help="Fail if run history cannot be recorded."),
    ] = False,
) -> None:
    """Run sources and land what they yield in RAW."""
    reg = _registry()
    selected = [reg.get(slug) for slug in slugs] if slugs else reg.scheduled()
    if not selected:
        typer.echo("no sources selected")
        raise typer.Exit(code=1)

    with reporter(required=require_catalog) as catalog:
        runner = Runner(
            _resolver(),
            max_workers=workers,
            default_rate=rate,
            reporter=catalog,
            trigger=trigger,
        )
        results = runner.run_many(
            [cls() for cls in selected], ScrapeContext(dry_run=dry_run, limit=limit)
        )
    summary = summarize(results)
    typer.echo(json.dumps(summary, indent=2))
    if summary["failed"]:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# warehouse
# ---------------------------------------------------------------------------


@warehouse_app.command("extract")
def warehouse_extract(
    segments: Annotated[
        list[str] | None,
        typer.Argument(help="RAW subtree to extract, e.g. statistics bps. Omit for all."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Extract but do not write Bronze.")
    ] = False,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Stop after this many documents.")
    ] = None,
    reprocess: Annotated[
        bool,
        typer.Option("--reprocess", help="Re-extract documents already in Bronze."),
    ] = False,
) -> None:
    """Extract RAW into Bronze.

    Idempotent: documents already extracted at the current parser version are
    left alone. Use --reprocess after improving a parser.
    """
    with reporter() as catalog:
        result = ExtractionRunner(_resolver(), reporter=catalog).run(
            *(segments or []), dry_run=dry_run, limit=limit, reprocess=reprocess
        )
    typer.echo(
        json.dumps(
            {
                "seen": result.documents_seen,
                "extracted": result.documents_extracted,
                "unchanged": result.documents_unchanged,
                "skipped": result.documents_skipped,
                "failed": result.documents_failed,
                "rows": result.rows_written,
                "files": result.files_written,
                "unhandled": result.unhandled,
                "failures": result.failures,
            },
            indent=2,
        )
    )
    if result.documents_failed:
        raise typer.Exit(code=1)


@warehouse_app.command("compact")
def warehouse_compact(
    layer: Annotated[Layer, typer.Argument(help="Layer to compact.")],
    dataset: Annotated[
        str | None, typer.Argument(help="Dataset within the layer. Omit for all.")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report without rewriting.")] = False,
) -> None:
    """Merge small Parquet files (program.md §47)."""
    results = compact_layer(_resolver(), layer, dataset, dry_run=dry_run)
    if not results:
        typer.echo("nothing to compact")
        raise typer.Exit()
    typer.echo(
        json.dumps(
            {
                "partitions": len(results),
                "files_removed": sum(r.files_removed for r in results),
                "rows": sum(r.rows for r in results),
                "bytes_before": sum(r.bytes_before for r in results),
                "bytes_after": sum(r.bytes_after for r in results),
            },
            indent=2,
        )
    )


@warehouse_app.command("query")
def warehouse_query(
    sql: Annotated[str, typer.Argument(help="SQL to run against the lake.")],
) -> None:
    """Run a DuckDB query against the lake.

    Layers are exposed as `read_parquet` over the configured backend, so the
    same query works against local disk, NAS or a bucket.
    """
    with Warehouse(_resolver()) as wh:
        wh.query(sql).show()


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


def _catalog_or_exit():
    try:
        return connect()
    except CatalogUnavailable as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@catalog_app.command("sync")
def catalog_sync() -> None:
    """Push the source registry into the `sources` table.

    The code is the authority: a source's registry record lives beside the
    scraper that uses it (program.md §16).
    """
    with _catalog_or_exit() as connection:
        result = sync_sources(connection, list(_registry().metas()))
    typer.echo(
        json.dumps(
            {
                "inserted": result.inserted,
                "updated": result.updated,
                "orphaned": result.orphaned,
            },
            indent=2,
        )
    )
    if result.orphaned:
        typer.echo(
            f"\n{len(result.orphaned)} source(s) in the catalog have no code. "
            "Left in place: datasets and runs reference them.",
            err=True,
        )


@catalog_app.command("runs")
def catalog_runs(
    slug: Annotated[
        str | None, typer.Option("--pipeline", help="Only this pipeline's runs.")
    ] = None,
    limit: Annotated[int, typer.Option("--limit")] = 20,
) -> None:
    """Show recent pipeline runs, newest first."""
    with _catalog_or_exit() as connection:
        runs = recent_runs(connection, limit=limit, slug=slug)
    if not runs:
        typer.echo("no runs recorded")
        raise typer.Exit()
    for run in runs:
        started = run["started_at"].isoformat(timespec="seconds") if run["started_at"] else "-"
        line = f"{started}  {run['status']:<10} {run['slug']:<32} out={run['records_out'] or 0}"
        if run["error_message"]:
            line += f"  {run['error_message'][:60]}"
        typer.echo(line)


@catalog_app.command("stale")
def catalog_stale(
    hours: Annotated[int, typer.Option("--hours", help="Age past which a run is stale.")] = 6,
) -> None:
    """Show runs still marked running long past any plausible duration.

    These are processes that died without closing their row. The alternative to
    noticing is an ingestion that stopped weeks ago.
    """
    with _catalog_or_exit() as connection:
        runs = stale_runs(connection, hours=hours)
    if not runs:
        typer.echo("no stale runs")
        raise typer.Exit()
    for run in runs:
        typer.echo(f"{run['started_at'].isoformat(timespec='seconds')}  {run['slug']}")
    raise typer.Exit(code=1)


@catalog_app.command("datasets")
def catalog_datasets(
    layer: Annotated[Layer | None, typer.Option("--layer")] = None,
) -> None:
    """List registered datasets."""
    with _catalog_or_exit() as connection:
        rows = list_datasets(connection, layer=layer)
    if not rows:
        typer.echo("no datasets registered")
        raise typer.Exit()
    for row in rows:
        typer.echo(
            f"{row['slug']:<28} {row['layer']:<8} {row['access_level']:<10} "
            f"rows={row['row_count'] or 0:<10} {row['storage_path']}"
        )


if __name__ == "__main__":
    app()
