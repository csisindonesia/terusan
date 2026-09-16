"""DuckDB sessions configured for whichever backend holds the lake.

DuckDB reads a local path and an `s3://` URI with the same `read_parquet`, but
only once the S3 credentials are attached. Doing that here means a query does
not have to know which backend it is running against (program.md §45.4).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from ..storage import Backend, Layer, StorageConfig, StorageResolver


def configure(connection: duckdb.DuckDBPyConnection, config: StorageConfig) -> None:
    """Attach credentials and resource limits to a connection."""
    connection.execute(f"SET memory_limit='{config.duckdb_memory_limit}'")
    connection.execute(f"SET threads={config.duckdb_threads}")

    # Spill belongs on local disk however the lake is stored: a NAS-backed
    # temp directory turns a large join into a network-bound crawl
    # (program.md §45.6).
    connection.execute(f"SET temp_directory='{config.scratch_dir}'")

    if config.backend is not Backend.S3:
        return

    connection.execute("INSTALL httpfs")
    connection.execute("LOAD httpfs")
    parts = [
        "TYPE S3",
        f"KEY_ID '{config.s3_access_key_id}'",
        f"SECRET '{config.s3_secret_access_key}'",
        f"REGION '{config.s3_region}'",
        f"USE_SSL {str(config.s3_use_ssl).lower()}",
    ]
    if config.s3_endpoint:
        endpoint = config.s3_endpoint.removeprefix("https://").removeprefix("http://")
        parts.append(f"ENDPOINT '{endpoint}'")
        parts.append("URL_STYLE 'path'")
    connection.execute(f"CREATE OR REPLACE SECRET lake ({', '.join(parts)})")


class Warehouse:
    """A DuckDB session that knows where the layers are."""

    def __init__(
        self,
        resolver: StorageResolver | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> None:
        self._resolver = resolver or StorageResolver(StorageConfig())
        self._connection = connection or duckdb.connect()
        configure(self._connection, self._resolver.config)

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        return self._connection

    def source(self, layer: Layer, dataset: str, *partition: str) -> str:
        """A `read_parquet` expression for one dataset.

        Returned as SQL text rather than a registered view so callers can join
        several layers in one statement without naming intermediate views.
        """
        pattern = self._resolver.glob(layer, dataset, *partition)
        return f"read_parquet('{pattern}', union_by_name=true, hive_partitioning=true)"

    def view(self, name: str, layer: Layer, dataset: str, *partition: str) -> None:
        """Register a dataset as a named view on this connection."""
        expression = self.source(layer, dataset, *partition)
        self._connection.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM {expression}")

    def datasets(self) -> dict[str, tuple[Layer, str]]:
        """Every dataset present in the lake, by view name.

        Found by looking rather than by asking the catalog, so the lake can be
        explored on a machine that has no database — which is the common case
        when someone is trying to see what is in it.
        """
        found: dict[str, tuple[Layer, str]] = {}
        if self._resolver.config.is_object_storage:
            # Listing a bucket needs a round trip per prefix; callers wanting
            # views there should register them by name.
            return found

        for layer in Layer:
            root = Path(self._resolver.resolve(layer))
            if not root.is_dir():
                continue
            for directory in sorted(root.iterdir()):
                if directory.is_dir() and any(directory.rglob("*.parquet")):
                    found[f"{layer}_{directory.name}".replace("-", "_")] = (
                        layer,
                        directory.name,
                    )
        return found

    def register_all(self) -> list[str]:
        """Register a view for every dataset in the lake.

        Turns exploration into `SELECT * FROM silver_observations` rather than a
        read_parquet call with a path in it.
        """
        registered = []
        for name, (layer, dataset) in self.datasets().items():
            self.view(name, layer, dataset)
            registered.append(name)
        return registered

    def query(self, sql: str, params: list[Any] | None = None) -> duckdb.DuckDBPyRelation:
        return self._connection.sql(sql, params=params) if params else self._connection.sql(sql)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@contextmanager
def warehouse(resolver: StorageResolver | None = None) -> Iterator[Warehouse]:
    """Open a warehouse session and close it afterwards."""
    session = Warehouse(resolver)
    try:
        yield session
    finally:
        session.close()
