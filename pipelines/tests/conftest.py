"""Shared fixtures.

Catalog tests run against a real PostgreSQL, created and dropped per session.
Mocking a database tests the mock: these tables carry enum constraints, unique
indexes and check constraints that are the whole point of the schema, and a
fake would not have them.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"

#: Server to create the throwaway database on. Points at a local PostgreSQL by
#: default; CI overrides it.
ADMIN_URL = os.getenv("TEST_POSTGRES_URL", "postgres://localhost:5432/postgres")


def _psql(url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", url, *args],
        capture_output=True,
        text=True,
    )


def postgres_available() -> bool:
    return _psql(ADMIN_URL, "-c", "SELECT 1").returncode == 0


requires_postgres = pytest.mark.skipif(
    not postgres_available(), reason="no PostgreSQL at TEST_POSTGRES_URL"
)


@pytest.fixture(scope="session")
def catalog_url() -> Iterator[str]:
    """A throwaway database with every migration applied."""
    name = f"terusan_test_{uuid.uuid4().hex[:12]}"
    created = subprocess.run(["createdb", "-h", "localhost", name], capture_output=True, text=True)
    if created.returncode != 0:
        pytest.skip(f"could not create test database: {created.stderr.strip()}")

    url = f"postgres://localhost:5432/{name}"
    try:
        for migration in sorted(MIGRATIONS.glob("*.up.sql")):
            applied = _psql(url, "-f", str(migration))
            if applied.returncode != 0:
                pytest.fail(f"migration {migration.name} failed: {applied.stderr}")
        yield url
    finally:
        subprocess.run(["dropdb", "-h", "localhost", "--if-exists", name], capture_output=True)


@pytest.fixture
def catalog(catalog_url: str) -> Iterator:
    """A connection that rolls back, so tests cannot see each other's rows."""
    import psycopg
    from psycopg.rows import dict_row

    from terusan_pipelines.catalog import register_enums

    connection = psycopg.connect(catalog_url, row_factory=dict_row)
    register_enums(connection)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()
