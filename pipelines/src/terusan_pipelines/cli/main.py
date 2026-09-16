"""Command-line entry point for the ingestion pipelines."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from terusan_pipelines.sources import Registry, Runner, ScrapeContext, registry, summarize
from terusan_pipelines.storage import Layer, StorageConfig, StorageResolver, slugify

app = typer.Typer(help="Terusan research data warehouse pipelines.", no_args_is_help=True)
storage_app = typer.Typer(help="Inspect and prepare data-lake storage.", no_args_is_help=True)
sources_app = typer.Typer(help="List and run data sources.", no_args_is_help=True)
app.add_typer(storage_app, name="storage")
app.add_typer(sources_app, name="sources")


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
) -> None:
    """Run sources and land what they yield in RAW."""
    reg = _registry()
    selected = [reg.get(slug) for slug in slugs] if slugs else reg.scheduled()
    if not selected:
        typer.echo("no sources selected")
        raise typer.Exit(code=1)

    runner = Runner(_resolver(), max_workers=workers, default_rate=rate)
    results = runner.run_many(
        [cls() for cls in selected], ScrapeContext(dry_run=dry_run, limit=limit)
    )
    summary = summarize(results)
    typer.echo(json.dumps(summary, indent=2))
    if summary["failed"]:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
