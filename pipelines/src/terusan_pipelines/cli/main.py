"""Command-line entry point for the ingestion pipelines."""

from __future__ import annotations

import json
from datetime import datetime
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
    SilverResult,
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
from terusan_pipelines.warehouse import (
    RunLog,
    RunRecord,
    Warehouse,
    compact_layer,
    read_runs,
)
from terusan_pipelines.warehouse.runlog import now as _now

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
    since: Annotated[
        datetime | None,
        typer.Option(
            "--since",
            formats=["%Y-%m-%d"],
            help=(
                "Only material published or updated on or after this date. What a "
                "backfill is driven by: a source that pages by date reads it as the "
                "far end of the span to walk, and without it takes its own default, "
                "which is generally the last few days."
            ),
        ),
    ] = None,
    param: Annotated[
        list[str] | None,
        typer.Option(
            "--param",
            help=(
                "key=value passed through to the source, repeatable. For the knobs "
                "that are one source's own — PIHPS reads `scope=national` to stay off "
                "the 34-province walk — and so do not deserve a flag of their own."
            ),
        ),
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
            [cls() for cls in selected],
            ScrapeContext(
                dry_run=dry_run,
                limit=limit,
                since=since.date() if since else None,
                params=_params(param),
            ),
        )
    summary = summarize(results)
    typer.echo(json.dumps(summary, indent=2))
    if summary["failed"]:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------


@app.command("runs")
def runs(
    pipeline: Annotated[
        str | None, typer.Option("--pipeline", help="Only this pipeline, e.g. ingest-bps.")
    ] = None,
    source: Annotated[str | None, typer.Option("--source", help="Only this source's runs.")] = None,
    failed: Annotated[bool, typer.Option("--failed", help="Only runs that failed.")] = False,
    limit: Annotated[int, typer.Option("--limit")] = 20,
) -> None:
    """Show recent runs from the lake's journal, newest first.

    The terminal's view of what the portal shows under Logs. Distinct from
    `catalog runs`, which reads PostgreSQL: this one answers on a machine with
    no database, which is where an unrecorded ingestion usually hides.
    """
    rows = read_runs(
        _resolver(),
        limit=limit,
        pipeline=pipeline,
        source_id=source,
        status="failed" if failed else None,
    )
    if not rows:
        typer.echo("no runs recorded in the journal")
        raise typer.Exit()

    for row in rows:
        # Shown in the reader's timezone, like the portal does. The journal
        # stores UTC, which is right for storage and wrong for a terminal
        # where "did it run this morning" is the question.
        started = row["started_at"].astimezone().strftime("%Y-%m-%d %H:%M")
        line = (
            f"{started}  {row['status']:<9} {row['pipeline']:<32} "
            f"in {row['records_in']:>7} out {row['records_out']:>7}  "
            f"{row['duration_seconds']:.1f}s"
        )
        if row["dry_run"]:
            line += "  [dry run]"
        typer.echo(line)
        if row["error_message"]:
            typer.echo(f"    {row['error_message']}")


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


def _params(specs: list[str] | None) -> dict[str, str]:
    """Read `key=value` source parameters off the command line."""
    params: dict[str, str] = {}
    for spec in specs or []:
        key, sep, value = spec.partition("=")
        if not sep or not key.strip():
            raise typer.BadParameter("--param needs key=value", param_hint="--param")
        params[key.strip()] = value.strip()
    return params


def _readable(key: str) -> str:
    """A series key as a name, for the common case where it already reads.

    `provincial_poverty_rate` becomes `Provincial poverty rate`. It is not a
    title somebody wrote, and it is far better than the identifier — which is
    what the portal shows otherwise.
    """
    return key.replace("_", " ").replace("-", " ").strip().capitalize()


def _where_clauses(flag: str, specs: list[str] | None) -> dict[str, tuple[str, ...]]:
    """Read `column=value,value` filters off the command line."""
    clauses: dict[str, tuple[str, ...]] = {}
    for spec in specs or []:
        column, _, values = spec.partition("=")
        wanted = tuple(v.strip() for v in values.split(",") if v.strip())
        if not column or not wanted:
            # An include naming no values matches nothing, which silently
            # empties the result rather than failing — worth refusing.
            raise typer.BadParameter(f"{flag} needs column=value[,value]", param_hint=flag)
        # Repeating a column would otherwise drop the earlier values silently,
        # leaving a narrower result than was asked for.
        clauses[column] = clauses.get(column, ()) + wanted
    return clauses


def _first(records: list[dict], column: str | None) -> str | None:
    """The first non-empty value of a column across a group of Bronze rows."""
    if not column:
        return None
    for record in records:
        value = (_column(record, column) or "").strip()
        if value:
            return value
    return None


def _about(describing: dict | None, rows: list[dict], column: str | None) -> str | None:
    """One descriptive field, from the describing row or from the figures."""
    if not column:
        return None
    if describing is not None and (value := (_column(describing, column) or "").strip()):
        return value
    return _first(rows, column)


def _sole_source(records: list[dict]) -> str | None:
    """The source every record came from, or None where they disagree."""
    sources = {record.get("source_id") for record in records}
    return next(iter(sources)) if len(sources) == 1 else None  # type: ignore[return-value]


def _column(record: dict, name: str) -> str | None:
    """One column off a Bronze record, whichever shape Arrow handed it back in."""
    columns = record.get("columns")
    if isinstance(columns, dict):
        value = columns.get(name)
    elif isinstance(columns, list):
        value = next((v for k, v in columns if k == name), None)
    else:
        return None
    return None if value is None else str(value)


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
    period_parts: Annotated[
        str | None,
        typer.Option(
            "--period-parts",
            help="Columns holding one period between them, e.g. 'period,year'.",
        ),
    ] = None,
    value_column: Annotated[str | None, typer.Option("--value-column")] = None,
    value_columns: Annotated[
        str | None,
        typer.Option("--value-columns", help="Comma-separated period columns (wide tables)."),
    ] = None,
    period_columns: Annotated[
        bool,
        typer.Option(
            "--period-columns",
            help="Wide table whose value columns are periods, discovered per row.",
        ),
    ] = False,
    geo_column: Annotated[str | None, typer.Option("--geo-column")] = None,
    geo: Annotated[
        str | None,
        typer.Option(
            "--geo",
            help=(
                "The place every row is about, where no column names it — a "
                "national series whose publisher had no reason to repeat the "
                "country. Resolved through reference/geography."
            ),
        ),
    ] = None,
    commodity_column: Annotated[str | None, typer.Option("--commodity-column")] = None,
    commodity: Annotated[
        str | None,
        typer.Option(
            "--commodity",
            help=(
                "The commodity every row is about, where no column names it — "
                "a price series for one instrument. Resolved through "
                "reference/commodities/commodities.csv."
            ),
        ),
    ] = None,
    unit: Annotated[str | None, typer.Option("--unit")] = None,
    unit_column: Annotated[
        str | None,
        typer.Option(
            "--unit-column",
            help=(
                "Column holding each row's unit, where the source states it. "
                "Yahoo quotes coffee in US cents and copper in dollars; asserting "
                "one unit for both would be wrong by a hundred."
            ),
        ),
    ] = None,
    number_format: Annotated[
        NumberFormat,
        typer.Option("--number-format", help="id | en | auto. Explicit removes ambiguity."),
    ] = NumberFormat.AUTO,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", help="column=value,value — drops totals and subtotals."),
    ] = None,
    include: Annotated[
        list[str] | None,
        typer.Option(
            "--include",
            help=(
                "column=value,value — keeps only these rows, for a file holding several "
                "series. Repeatable: a file can need narrowing on more than one column, "
                "as DJPK's APBD export does on both the account and the fiscal month."
            ),
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help=(
                "What to call this series in the catalogue. Without it the name "
                "is derived from the indicator key, which reads passably — "
                "`gold_price_high` becomes `Gold price high` — and badly for a "
                "key that was never meant to be read."
            ),
        ),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Normalize a Bronze dataset into Silver observations.

    The column mapping is declared rather than inferred: a column headed `2026`
    is a period in a wide table and a value in a long one, and nothing in the
    data settles which (program.md §7).

    The series is named in the catalogue as well as normalized. A figure whose
    name lives nowhere is a figure the portal lists by its identifier, which is
    eight characters of base32.
    """

    exclusions = _where_clauses("--exclude", exclude)
    inclusions = _where_clauses("--include", include)

    mapping = ColumnMapping(
        indicator_id=indicator,
        period_column=period_column,
        period_parts=tuple(c.strip() for c in period_parts.split(",")) if period_parts else (),
        value_column=value_column,
        value_columns=tuple(c.strip() for c in value_columns.split(",")) if value_columns else (),
        value_columns_are_periods=period_columns,
        geo_column=geo_column,
        geo=geo,
        commodity_column=commodity_column,
        commodity=commodity,
        unit=unit,
        unit_column=unit_column,
        number_format=number_format,
        exclude_where=exclusions,
        include_where=inclusions,
    )

    runner = SilverRunner(
        _resolver(),
        geography=geography_registry(),
        commodities=commodity_registry(),
    )

    # Recorded whichever way it ends. A mapping that raises is the failure a
    # reader most needs to see in the history — the series simply stops being
    # refreshed, and nothing about the data itself says so.
    started = _now()
    with reporter() as catalog:
        try:
            result = runner.normalize(mapping, dataset=dataset, source_id=source, dry_run=dry_run)
        except Exception as exc:
            catalog.normalize_run(
                SilverResult(indicator_id=indicator, started_at=started, finished_at=_now()),
                source_id=source,
                dataset=dataset,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        catalog.normalize_run(result, source_id=source, dataset=dataset)

    # Merged rather than replacing: a wide table is normalized one column at a
    # time, and replacing would leave only the last column named.
    if source and not dry_run and result.stats.observations:
        runner.write_indicators(
            [
                {
                    "indicator_id": result.indicator_id,
                    "slug": result.slug,
                    "name": name or _readable(result.slug or indicator),
                    "unit": unit,
                }
            ],
            source_id=source,
            dataset=dataset,
            merge=True,
        )

    stats = result.stats
    typer.echo(
        json.dumps(
            {
                "indicator": result.indicator_id,
                "slug": result.slug,
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


@silver_app.command("normalize-each")
def silver_normalize_each(
    by: Annotated[
        str,
        typer.Option("--by", help="Bronze column whose value names the indicator to produce."),
    ],
    dataset: Annotated[
        str | None, typer.Option("--dataset", help="Bronze dataset to read.")
    ] = None,
    source: Annotated[
        str | None, typer.Option("--source", help="Only this source's records.")
    ] = None,
    period_column: Annotated[str | None, typer.Option("--period-column")] = None,
    period_columns: Annotated[
        bool,
        typer.Option(
            "--period-columns",
            help=(
                "Wide tables: any column whose header reads as a period is a value "
                "column. For a table whose columns are the dates, which change with "
                "every release — listing them would mean editing the call each run."
            ),
        ),
    ] = False,
    value_column: Annotated[str | None, typer.Option("--value-column")] = None,
    geo_column: Annotated[str | None, typer.Option("--geo-column")] = None,
    geo: Annotated[
        str | None,
        typer.Option(
            "--geo",
            help=(
                "The place every row is about, where no column names it — a "
                "national series whose publisher had no reason to repeat the "
                "country. Resolved through reference/geography."
            ),
        ),
    ] = None,
    commodity_column: Annotated[str | None, typer.Option("--commodity-column")] = None,
    commodity: Annotated[
        str | None,
        typer.Option(
            "--commodity",
            help=(
                "The commodity every row is about, where no column names it — "
                "a price series for one instrument. Resolved through "
                "reference/commodities/commodities.csv."
            ),
        ),
    ] = None,
    unit: Annotated[str | None, typer.Option("--unit")] = None,
    unit_column: Annotated[str | None, typer.Option("--unit-column")] = None,
    name_column: Annotated[
        str | None,
        typer.Option(
            "--name-column",
            help=(
                "Column holding what the series is called. Published into the "
                "Silver indicators table, which is the only thing standing "
                "between a reader and a table of bare identifiers."
            ),
        ),
    ] = None,
    code_column: Annotated[
        str | None,
        typer.Option(
            "--code-column",
            help="Column holding the publisher's own identifier, e.g. a FRED series id.",
        ),
    ] = None,
    describe_dataset: Annotated[
        str | None,
        typer.Option(
            "--describe-dataset",
            help=(
                "A second Bronze dataset, one row per indicator, holding what "
                "describes it. Keeps a paragraph of notes off every observation."
            ),
        ),
    ] = None,
    description_column: Annotated[
        str | None,
        typer.Option("--description-column", help="Column holding what the series counts."),
    ] = None,
    publisher_column: Annotated[
        str | None,
        typer.Option(
            "--publisher-column",
            help=(
                "Column naming who produced the figures — distinct from the "
                "source we collected them from."
            ),
        ),
    ] = None,
    release_column: Annotated[
        str | None,
        typer.Option("--release-column", help="Column naming the release the figures arrive in."),
    ] = None,
    number_format: Annotated[
        NumberFormat,
        typer.Option("--number-format", help="id | en | auto. Explicit removes ambiguity."),
    ] = NumberFormat.AUTO,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", help="column=value,value — drops totals and subtotals."),
    ] = None,
    include: Annotated[
        list[str] | None,
        typer.Option("--include", help="column=value,value — keeps only these rows."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Stop after this many indicators, for a smoke run."),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Normalize one indicator per distinct value of a Bronze column.

    For a dataset that holds many series under one shape. FRED's Indonesian
    crawl is seven hundred of them, all read by the same mapping and differing
    only in which series a row belongs to — running `normalize` once per series
    would scan the whole of Bronze seven hundred times to say the same thing.
    Here Bronze is read once and each series' rows are normalized from memory.

    The column must already name the indicator. That is deliberate: composing
    an identifier is the extractor's business, where the source's own titles and
    codes are still at hand, and a flag that built one here would be a second
    place for it to be decided differently.

    With `--name-column`, the indicators table is published too, so the portal
    has something to print beside an identifier that is only a code.
    `--describe-dataset` says those descriptions live in a second Bronze
    dataset, keyed by the same column — which is where a paragraph of notes
    belongs, rather than copied onto every observation that shares it.
    """
    runner = SilverRunner(
        _resolver(),
        geography=geography_registry(),
        commodities=commodity_registry(),
    )
    started = _now()
    records = runner.read_bronze(dataset=dataset, source_id=source)
    if not records:
        # Recorded rather than only printed: a normalization that finds no
        # Bronze is how an extraction that quietly stopped becomes visible.
        RunLog(_resolver()).record(
            RunRecord(
                pipeline=f"normalize-each-{slugify(dataset or by)}",
                kind="normalize",
                status="failed",
                started_at=started,
                finished_at=_now(),
                source_id=source,
                dataset=dataset,
                error_message="no Bronze records match; nothing to normalize",
            )
        )
        typer.echo("no Bronze records match; nothing to normalize", err=True)
        raise typer.Exit(code=1)

    # One describing row per indicator, where a second dataset holds them.
    # First wins: a re-crawl lands a second copy of a series page, and the two
    # describe the same series.
    describing: dict[str, dict] = {}
    for record in (
        runner.read_bronze(dataset=describe_dataset, source_id=source) if describe_dataset else []
    ):
        key = (_column(record, by) or "").strip()
        if key:
            describing.setdefault(key, record)
    if describe_dataset and not describing:
        typer.echo(
            f"--describe-dataset {describe_dataset!r} holds no rows carrying {by!r}",
            err=True,
        )
        raise typer.Exit(code=1)

    exclusions = _where_clauses("--exclude", exclude)
    inclusions = _where_clauses("--include", include)

    groups: dict[str, list[dict]] = {}
    for record in records:
        indicator = (_column(record, by) or "").strip()
        if not indicator:
            # A row that does not say which indicator it belongs to cannot be
            # normalized into one, and guessing would file it under a
            # neighbour's series.
            continue
        groups.setdefault(indicator, []).append(record)

    if not groups:
        typer.echo(f"no Bronze record carries a {by!r} column; nothing to normalize", err=True)
        raise typer.Exit(code=1)

    observations = 0
    failures: dict[str, str] = {}
    normalized: list[str] = []
    described: list[dict] = []

    for indicator in sorted(groups)[: limit if limit is not None else None]:
        rows = groups[indicator]
        mapping = ColumnMapping(
            indicator_id=indicator,
            period_column=period_column,
            value_columns_are_periods=period_columns,
            value_column=value_column,
            geo_column=geo_column,
            geo=geo,
            commodity_column=commodity_column,
            commodity=commodity,
            unit=unit,
            unit_column=unit_column,
            number_format=number_format,
            exclude_where=exclusions,
            include_where=inclusions,
        )
        try:
            if name_column:
                # Two names under one identifier means two series were given
                # the same one — a derived code that collided, or a `--by`
                # column that does not identify a series. Normalizing anyway
                # would write one series' figures over the other's.
                names = {n for row in rows if (n := (_column(row, name_column) or "").strip())}
                if len(names) > 1:
                    raise ValueError(
                        f"identifier {indicator!r} is claimed by {len(names)} different "
                        f"series: {sorted(names)[:3]}"
                    )
            result = runner.normalize(mapping, records=rows, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - one bad series must not stop the rest
            failures[indicator] = f"{type(exc).__name__}: {exc}"
            continue
        observations += result.stats.observations
        normalized.append(result.indicator_id)

        if name_column and result.stats.observations:
            # What describes a series comes from its describing row where there
            # is one, and from the figures themselves otherwise.
            about = describing.get(indicator)
            described.append(
                {
                    "indicator_id": result.indicator_id,
                    "slug": result.slug,
                    "name": _about(about, rows, name_column) or indicator,
                    "code": _about(about, rows, code_column),
                    "description": _about(about, rows, description_column),
                    "publisher": _about(about, rows, publisher_column),
                    "release": _about(about, rows, release_column),
                    "unit": (_first(rows, unit_column) if unit_column else None) or unit,
                    # The resolution the figures actually landed at, not the one
                    # the publisher declares: a series FRED calls five-yearly is
                    # dated by the day it publishes, and the table should say
                    # what the observations say.
                    "frequency": result.resolution,
                }
            )

    indicators_written = 0
    if described and not dry_run:
        # One source at a time, and only where the caller named the source:
        # the table is replaced per source, and replacing it under a guessed
        # name would delete another source's indicators.
        source_id = source or _sole_source(records)
        if source_id is None:
            typer.echo(
                "--name-column needs --source: the indicators table is replaced "
                "one source at a time",
                err=True,
            )
            raise typer.Exit(code=1)
        indicators_written = runner.write_indicators(
            described, source_id=source_id, dataset=dataset
        )

    # One journal row for the command rather than one per series: FRED's crawl
    # is seven hundred indicators, and seven hundred rows saying the same thing
    # would bury every other run in the history. Which series failed is in the
    # row's detail.
    RunLog(_resolver()).record(
        RunRecord(
            pipeline=f"normalize-each-{slugify(dataset or by)}",
            kind="normalize",
            status="failed" if failures else "succeeded",
            started_at=started,
            finished_at=_now(),
            source_id=source,
            dataset=dataset,
            records_in=len(records),
            records_out=observations,
            dry_run=dry_run,
            error_message=(
                f"{len(failures)} of {len(groups)} series failed to normalize" if failures else None
            ),
            detail={
                "by": by,
                "series_seen": len(groups),
                "series_normalized": len(normalized),
                "indicators_named": indicators_written,
                "failures": dict(list(failures.items())[:20]),
            },
        )
    )

    typer.echo(
        json.dumps(
            {
                "by": by,
                "indicators": len(normalized),
                "observations": observations,
                "named": indicators_written,
                "failed": failures,
            },
            indent=2,
        )
    )
    if failures:
        raise typer.Exit(code=1)


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
                # The registry too: it is reference data about how the figures
                # are collected, and the serving layer has no other way to read
                # it — the catalog database that also holds it is optional.
                "sources": runner.write_sources(list(_registry().metas())),
                # And the dataset catalogue: what a collection is called, what
                # it holds and what a reader would search it by is nowhere in
                # the figures, so it is published rather than derived.
                "datasets": runner.write_datasets(
                    runner.collected_datasets(),
                    member_tags=runner.indicator_tags_by_dataset(),
                ),
                "scope": "all reference members" if everything else "in use, plus Indonesia",
            },
            indent=2,
        )
    )


@silver_app.command("documents")
def silver_documents() -> None:
    """Publish the document catalogue into Silver (program.md §13).

    One row per artifact landed in RAW: what it is, who published it, where it
    came from, and how many indicators and observations rest on it. Built from
    the provenance sidecar written beside every landed file, so it describes
    everything collected rather than only what a parser has since read.

    Re-runnable, and cheap enough to run after every scrape: the table is
    rebuilt from the sidecars each time, so a document landed this morning
    appears without anyone remembering to add it.

    Run it after `silver dimensions`, not before — the observation counts come
    from the figures, and a lake normalized after this ran reports zeroes until
    it runs again.
    """
    runner = SilverRunner(_resolver())
    written = runner.write_documents()
    typer.echo(json.dumps({"documents": written}, indent=2))


@silver_app.command("recode")
def silver_recode(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would move, and write nothing."),
    ] = False,
) -> None:
    """Move a lake off slug identifiers and onto derived codes.

    A migration for lakes written before series and datasets were identified
    by a code (program.md §10). Normalization produces codes now, so a lake
    built from scratch never needs this, and running it twice is a no-op.

    It rewrites the observations — their indicator, their dataset, and the
    observation ids derived from the first — and republishes the indicators
    table and the dataset catalogue beside them. The figures do not change.
    """
    from terusan_pipelines.normalize.recode import recode

    runner = SilverRunner(_resolver())
    result = recode(runner, dry_run=dry_run)
    typer.echo(
        json.dumps(
            {
                "recoded": result.indicators_recoded,
                "already_coded": result.indicators_already_coded,
                "observations": result.observations_rewritten,
                "indicators": result.indicator_rows,
                "datasets": result.dataset_rows,
                "dry_run": dry_run,
                "mapping": result.mapping,
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
