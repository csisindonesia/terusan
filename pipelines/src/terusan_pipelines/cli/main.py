"""Command-line entry point for the ingestion pipelines."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from terusan_pipelines.storage import Layer, StorageConfig, StorageResolver, slugify

app = typer.Typer(help="Terusan research data warehouse pipelines.", no_args_is_help=True)
storage_app = typer.Typer(help="Inspect and prepare data-lake storage.", no_args_is_help=True)
app.add_typer(storage_app, name="storage")


def _resolver() -> StorageResolver:
    return StorageResolver(StorageConfig())


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


if __name__ == "__main__":
    app()
