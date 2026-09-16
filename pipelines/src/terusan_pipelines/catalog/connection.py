"""Connecting to the application catalog.

PostgreSQL holds operational state — the source registry, dataset metadata,
pipeline runs, audit (program.md §48). It does not hold the analytical
warehouse; that stays in Parquet and is read by DuckDB.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import structlog
from psycopg.rows import dict_row
from psycopg.types import TypeInfo

log = structlog.get_logger(__name__)

#: Enum types the schema defines. Without registering them psycopg returns an
#: array of one as the raw literal `{a,b}` rather than a list, so any caller
#: reading `change_kinds` or `scopes` would iterate over characters.
ENUM_TYPES = (
    "source_type",
    "collection_method",
    "update_frequency",
    "storage_layer",
    "access_level",
    "dataset_status",
    "change_kind",
    "principal_kind",
    "dataset_grant",
    "run_status",
)


class CatalogUnavailable(RuntimeError):
    """The catalog could not be reached.

    Raised rather than returning None so a caller has to decide what to do.
    Most pipeline work can proceed without the catalog — the lake is the
    system of record for data — but it must be a deliberate choice, not a
    silent one.
    """


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise CatalogUnavailable(
            "DATABASE_URL is not set; the catalog holds the source registry and "
            "run history (program.md §48)"
        )
    return url


@contextmanager
def connect(url: str | None = None, *, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """Open a catalog connection, committing on clean exit.

    Rows come back as dicts: these tables carry a dozen-plus columns and
    positional access to them is how a column added in the middle silently
    shifts every read after it.
    """
    try:
        connection = psycopg.connect(url or database_url(), row_factory=dict_row)
    except psycopg.OperationalError as exc:
        raise CatalogUnavailable(f"cannot reach the catalog: {exc}") from exc

    connection.autocommit = autocommit
    register_enums(connection)
    try:
        yield connection
        if not autocommit:
            connection.commit()
    except Exception:
        if not autocommit:
            connection.rollback()
        raise
    finally:
        connection.close()


def register_enums(connection: psycopg.Connection) -> None:
    """Teach psycopg about the schema's enum types.

    Public because any connection opened outside `connect` — a test fixture, a
    migration tool, a one-off script — needs the same treatment.

    Skips any that are absent so a connection to a partially migrated database
    still works — the migration itself has to connect before the types exist.
    """
    for name in ENUM_TYPES:
        info = TypeInfo.fetch(connection, name)
        if info is not None:
            info.register(connection)


def is_available(url: str | None = None) -> bool:
    """Whether the catalog can be reached right now."""
    try:
        with connect(url) as connection:
            connection.execute("SELECT 1")
    except CatalogUnavailable:
        return False
    return True
