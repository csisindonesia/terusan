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
from terusan_pipelines.normalize import (
    ColumnMapping,
    NumberFormat,
    SilverRunner,
    commodity_registry,
    geography_registry,
    load_aggregates,
    load_commodities,
    load_countries,
    load_indonesia,
    parse_value,
)
from terusan_pipelines.sources import Registry, Runner, ScrapeContext, registry, summarize
from terusan_pipelines.storage import (
    Layer,
    StorageConfig,
    StorageResolver,
    project_root,
    resolve_path,
    slugify,
)
from terusan_pipelines.warehouse import Warehouse, compact_layer

app = typer.Typer(help="Terusan research data warehouse pipelines.", no_args_is_help=True)
storage_app = typer.Typer(help="Inspect and prepare data-lake storage.", no_args_is_help=True)
sources_app = typer.Typer(help="List and run data sources.", no_args_is_help=True)
warehouse_app = typer.Typer(help="Extract, compact and query the lake.", no_args_is_help=True)
catalog_app = typer.Typer(help="Inspect and sync the PostgreSQL catalog.", no_args_is_help=True)
silver_app = typer.Typer(help="Normalize Bronze into Silver.", no_args_is_help=True)
app.add_typer(storage_app, name="storage")
app.add_typer(sources_app, name="sources")
app.add_typer(warehouse_app, name="warehouse")
app.add_typer(catalog_app, name="catalog")
app.add_typer(silver_app, name="silver")


def _resolver() -> StorageResolver:
    return StorageResolver(StorageConfig())


def _registry() -> Registry:
    registry.ensure_loaded()
    return registry


@storage_app.command("info")
def storage_info() -> None:
    """Show which physical backend STORAGE_ROOT currently points at."""
    resolver = _resolver()
    config = resolver.config
    typer.echo(
        json.dumps(
            {
                "profile": str(config.profile),
                "backend": str(config.backend),
                "project_root": str(project_root()),
                "configured_root": config.root,
                # What the configured value actually resolves to. A relative
                # root anchors to the project, not the working directory.
                "resolved_root": resolver.root,
                "scratch_dir": str(resolve_path(config.scratch_dir)),
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
    limit: Annotated[
        int, typer.Option("--limit", help="Rows to display. 0 shows everything.")
    ] = 40,
) -> None:
    """Run a DuckDB query against the lake.

    Every dataset is registered as a view named `<layer>_<dataset>`, so a query
    reads `silver_observations` rather than a read_parquet call with a path in
    it. `terusan warehouse tables` lists them.

    The same query works against local disk, NAS or a bucket.
    """
    with Warehouse(_resolver()) as wh:
        wh.register_all()
        relation = wh.query(sql)
        relation.limit(limit).show() if limit else relation.show()


@warehouse_app.command("tables")
def warehouse_tables() -> None:
    """List the datasets in the lake, and how many rows each holds."""
    with Warehouse(_resolver()) as wh:
        datasets = wh.datasets()
        if not datasets:
            typer.echo("the lake is empty; run `terusan sources run` first")
            raise typer.Exit()

        wh.register_all()
        for name in datasets:
            rows = wh.query(f"SELECT count(*) FROM {name}").fetchone()[0]
            typer.echo(f"{name:<28} {rows:>12,} rows")


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


# ---------------------------------------------------------------------------
# silver
# ---------------------------------------------------------------------------


@silver_app.command("normalize")
def silver_normalize(
    indicator: Annotated[str, typer.Argument(help="Indicator id to produce.")],
    dataset: Annotated[
        str | None, typer.Option("--dataset", help="Bronze dataset to read.")
    ] = None,
    source: Annotated[
        str | None, typer.Option("--source", help="Only this source's records.")
    ] = None,
    period_column: Annotated[str | None, typer.Option("--period-column")] = None,
    value_column: Annotated[str | None, typer.Option("--value-column")] = None,
    value_columns: Annotated[
        str | None,
        typer.Option("--value-columns", help="Comma-separated period columns (wide tables)."),
    ] = None,
    geo_column: Annotated[str | None, typer.Option("--geo-column")] = None,
    commodity_column: Annotated[str | None, typer.Option("--commodity-column")] = None,
    unit: Annotated[str | None, typer.Option("--unit")] = None,
    number_format: Annotated[
        NumberFormat,
        typer.Option("--number-format", help="id | en | auto. Explicit removes ambiguity."),
    ] = NumberFormat.AUTO,
    exclude: Annotated[
        str | None,
        typer.Option("--exclude", help="column=value,value — drops totals and subtotals."),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Normalize a Bronze dataset into Silver observations.

    The column mapping is declared rather than inferred: a column headed `2026`
    is a period in a wide table and a value in a long one, and nothing in the
    data settles which (program.md §7).
    """
    exclusions: dict[str, tuple[str, ...]] = {}
    if exclude:
        column, _, values = exclude.partition("=")
        exclusions[column] = tuple(v.strip() for v in values.split(",") if v.strip())

    mapping = ColumnMapping(
        indicator_id=indicator,
        period_column=period_column,
        value_column=value_column,
        value_columns=tuple(c.strip() for c in value_columns.split(",")) if value_columns else (),
        geo_column=geo_column,
        commodity_column=commodity_column,
        unit=unit,
        number_format=number_format,
        exclude_where=exclusions,
    )

    runner = SilverRunner(
        _resolver(),
        geography=geography_registry(),
        commodities=commodity_registry(),
    )
    result = runner.normalize(mapping, dataset=dataset, source_id=source, dry_run=dry_run)
    stats = result.stats
    typer.echo(
        json.dumps(
            {
                "indicator": result.indicator_id,
                "rows_in": stats.rows_in,
                "observations": stats.observations,
                "skipped_excluded": stats.skipped_excluded,
                "skipped_no_period": stats.skipped_no_period,
                "unresolved_geo": stats.unresolved_geo,
                "unresolved_commodity": stats.unresolved_commodity,
                "ambiguous_values": stats.ambiguous_values,
                "unparseable_values": stats.unparseable_values,
                "files": result.files_written,
            },
            indent=2,
        )
    )
    if stats.ambiguous_values:
        typer.echo(
            f"\n{stats.ambiguous_values} value(s) read under an assumption that could "
            "have gone the other way. Pass --number-format id or en to settle them.",
            err=True,
        )


@silver_app.command("check")
def silver_check(
    text: Annotated[str, typer.Argument(help="A period label or a value, as published.")],
    number_format: Annotated[NumberFormat, typer.Option("--number-format")] = NumberFormat.AUTO,
) -> None:
    """Show how one published cell would be read.

    For working out a mapping against a real table before running it.
    """
    from terusan_pipelines.normalize import try_parse_period

    period = try_parse_period(text)
    value = parse_value(text, number_format)
    typer.echo(
        json.dumps(
            {
                "as_period": (
                    {
                        "label": period.label,
                        "start": period.start.isoformat(),
                        "end": period.end.isoformat(),
                        "resolution": str(period.resolution),
                    }
                    if period
                    else None
                ),
                "as_value": {
                    "value": str(value.value) if value.value is not None else None,
                    "unit": value.unit,
                    "status": str(value.status),
                    "unambiguous": value.unambiguous,
                },
            },
            indent=2,
        )
    )


@silver_app.command("dimensions")
def silver_dimensions(
    everything: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Publish every reference member, not only the ones in use.",
        ),
    ] = False,
) -> None:
    """Publish the geography and commodity dimensions into Silver.

    Reference data under `reference/` is the authority; this copies it into the
    lake so a query can join on it (program.md §11, §12). Nothing writes back
    the other way.

    By default only the members the observations actually refer to are
    published, together with Indonesia and its provinces. A dimension exists to
    make its facts interpretable, and two hundred countries beside a warehouse
    of Indonesian figures interpret nothing. `--all` publishes the lot, for
    when a comparator series is about to arrive.
    """
    runner = SilverRunner(_resolver())
    everything_geo = [*load_countries(), *load_aggregates(), *load_indonesia()]

    if everything:
        geographies = everything_geo
    else:
        used = runner.referenced_geo_ids()
        # Indonesia and its administrative hierarchy are kept whether or not a
        # figure names them yet: they are what this warehouse is about, and a
        # province with no observations is a gap worth seeing.
        always = {g.geo_id for g in load_indonesia()} | {"IDN"}
        geographies = [g for g in everything_geo if g.geo_id in used | always]

    typer.echo(
        json.dumps(
            {
                "geography": runner.write_geography(geographies),
                "commodities": runner.write_commodities(load_commodities()),
                "scope": "all reference members" if everything else "in use, plus Indonesia",
            },
            indent=2,
        )
    )


@silver_app.command("resolve")
def silver_resolve(
    name: Annotated[str, typer.Argument(help="A place name, as a source writes it.")],
) -> None:
    """Show what a place name resolves to, and how.

    For working out whether a source's naming needs an alias adding to the
    reference data before a normalization run files everything under nothing.
    """
    result = geography_registry().resolve(name)
    if not result.resolved:
        typer.echo(f"{name!r} resolves to nothing")
        raise typer.Exit(code=1)

    geography = geography_registry().get(result.identifier or "")
    typer.echo(
        json.dumps(
            {
                "geo_id": result.identifier,
                "matched_by": result.method,
                "name": geography.name if geography else None,
                "geo_type": str(geography.geo_type) if geography else None,
                "parent": geography.parent_geo_id if geography else None,
                "bps_code": geography.bps_code if geography else None,
                "valid_from": (
                    geography.valid_from.isoformat() if geography and geography.valid_from else None
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
