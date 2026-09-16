"""Registering datasets and their versions (program.md §19, §40).

The catalog records where a dataset lives and what is in it; the Parquet is the
data. `storage_path` is therefore a logical address — layer plus segments —
never a physical one, so the catalog survives the lake moving between NAS and
object storage (program.md §45.4). The schema enforces this too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import psycopg
import structlog

from ..storage import Layer

log = structlog.get_logger(__name__)

_UPSERT_DATASET = """
INSERT INTO datasets (
    slug, title, description, layer, storage_path, access_level, status,
    source_id, partition_keys, tags, license, citation
)
VALUES (
    %(slug)s, %(title)s, %(description)s, %(layer)s, %(storage_path)s,
    %(access_level)s, %(status)s, %(source_id)s, %(partition_keys)s,
    %(tags)s, %(license)s, %(citation)s
)
ON CONFLICT (slug) DO UPDATE SET
    title          = EXCLUDED.title,
    description    = COALESCE(EXCLUDED.description, datasets.description),
    layer          = EXCLUDED.layer,
    storage_path   = EXCLUDED.storage_path,
    source_id      = COALESCE(EXCLUDED.source_id, datasets.source_id),
    partition_keys = EXCLUDED.partition_keys,
    tags           = EXCLUDED.tags,
    license        = COALESCE(EXCLUDED.license, datasets.license),
    updated_at     = now()
RETURNING id
"""

_RECORD_VERSION = """
INSERT INTO dataset_versions (
    dataset_id, version, change_kinds, changelog, content_hash, row_count, size_bytes
)
VALUES (
    %(dataset_id)s, %(version)s, %(change_kinds)s, %(changelog)s,
    %(content_hash)s, %(row_count)s, %(size_bytes)s
)
ON CONFLICT (dataset_id, version) DO UPDATE SET
    change_kinds = EXCLUDED.change_kinds,
    changelog    = EXCLUDED.changelog,
    content_hash = EXCLUDED.content_hash,
    row_count    = EXCLUDED.row_count,
    size_bytes   = EXCLUDED.size_bytes
RETURNING id
"""


@dataclass(slots=True)
class DatasetStats:
    """What a write produced, for the catalog's size and coverage columns."""

    row_count: int = 0
    size_bytes: int = 0
    temporal_start: date | None = None
    temporal_end: date | None = None


def upsert_dataset(
    connection: psycopg.Connection,
    *,
    slug: str,
    title: str,
    layer: Layer,
    storage_path: str,
    description: str | None = None,
    access_level: str = "internal",
    status: str = "active",
    source_slug: str | None = None,
    partition_keys: list[str] | None = None,
    tags: list[str] | None = None,
    license: str | None = None,
    citation: str | None = None,
) -> UUID:
    """Register a dataset, or update what is already registered.

    Access defaults to `internal`. A dataset that becomes public by default the
    moment a pipeline registers it is the wrong failure direction: widening
    access should be a decision, narrowing it a correction (program.md §36).
    """
    if storage_path.startswith(("/", "s3://")):
        raise ValueError(
            f"storage_path must be a logical address, not a physical one: {storage_path!r}"
        )

    source_id = None
    if source_slug:
        row = connection.execute(
            "SELECT id FROM sources WHERE slug = %s", (source_slug,)
        ).fetchone()
        source_id = row["id"] if row else None

    row = connection.execute(
        _UPSERT_DATASET,
        {
            "slug": slug,
            "title": title,
            "description": description,
            "layer": str(layer),
            "storage_path": storage_path,
            "access_level": access_level,
            "status": status,
            "source_id": source_id,
            "partition_keys": partition_keys or [],
            "tags": tags or [],
            "license": license,
            "citation": citation,
        },
    ).fetchone()
    assert row is not None
    return row["id"]


def record_version(
    connection: psycopg.Connection,
    dataset_id: UUID,
    *,
    version: str,
    change_kinds: list[str] | None = None,
    changelog: str | None = None,
    content_hash: str | None = None,
    stats: DatasetStats | None = None,
) -> UUID:
    """Record a dataset version (program.md §40)."""
    stats = stats or DatasetStats()
    row = connection.execute(
        _RECORD_VERSION,
        {
            "dataset_id": dataset_id,
            "version": version,
            "change_kinds": change_kinds or [],
            "changelog": changelog,
            "content_hash": content_hash,
            "row_count": stats.row_count,
            "size_bytes": stats.size_bytes,
        },
    ).fetchone()
    assert row is not None

    # Keep the dataset's own counters in step with its newest version, so the
    # portal can list sizes without joining to versions on every row.
    connection.execute(
        """
        UPDATE datasets SET
            row_count      = %(row_count)s,
            size_bytes     = %(size_bytes)s,
            temporal_start = COALESCE(%(temporal_start)s, temporal_start),
            temporal_end   = COALESCE(%(temporal_end)s, temporal_end),
            updated_at     = now()
        WHERE id = %(dataset_id)s
        """,
        {
            "dataset_id": dataset_id,
            "row_count": stats.row_count,
            "size_bytes": stats.size_bytes,
            "temporal_start": stats.temporal_start,
            "temporal_end": stats.temporal_end,
        },
    )
    return row["id"]


def next_version(connection: psycopg.Connection, dataset_id: UUID) -> str:
    """The next minor version for a dataset.

    Minor by default because that is what an ingestion run produces: more
    observations of the same thing. A schema or methodology change is a major
    bump and a deliberate decision, so it is not inferred here.
    """
    row = connection.execute(
        """
        SELECT version FROM dataset_versions
        WHERE dataset_id = %s ORDER BY released_at DESC LIMIT 1
        """,
        (dataset_id,),
    ).fetchone()
    if row is None:
        return "v1.0"

    try:
        major, minor = row["version"].removeprefix("v").split(".", 1)
        return f"v{int(major)}.{int(minor) + 1}"
    except ValueError:
        # A version nobody can parse should not block a release.
        return f"{row['version']}-next"


def list_datasets(
    connection: psycopg.Connection, *, layer: Layer | None = None
) -> list[dict[str, Any]]:
    sql = """
        SELECT d.slug, d.title, d.layer, d.storage_path, d.access_level, d.status,
               d.row_count, d.size_bytes, s.slug AS source_slug
        FROM datasets d
        LEFT JOIN sources s ON s.id = d.source_id
    """
    params: list[Any] = []
    if layer:
        sql += " WHERE d.layer = %s"
        params.append(str(layer))
    sql += " ORDER BY d.slug"
    return connection.execute(sql, params).fetchall()
