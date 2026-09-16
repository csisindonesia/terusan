"""Keeping the `sources` table in step with the registry.

The code is the authority: a source's registry record lives beside the scraper
that uses it, so the two cannot drift. This module pushes those records into
PostgreSQL, where the portal and the API read them (program.md §16).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import psycopg
import structlog

from ..sources import SourceMeta

log = structlog.get_logger(__name__)

_UPSERT = """
INSERT INTO sources (
    slug, name, organization, source_type, base_url, country,
    license, collection_method, update_frequency, active, notes
)
VALUES (
    %(slug)s, %(name)s, %(organization)s, %(source_type)s, %(base_url)s, %(country)s,
    %(license)s, %(collection_method)s, %(update_frequency)s, %(active)s, %(notes)s
)
ON CONFLICT (slug) DO UPDATE SET
    name              = EXCLUDED.name,
    organization      = EXCLUDED.organization,
    source_type       = EXCLUDED.source_type,
    base_url          = EXCLUDED.base_url,
    country           = EXCLUDED.country,
    license           = EXCLUDED.license,
    collection_method = EXCLUDED.collection_method,
    update_frequency  = EXCLUDED.update_frequency,
    active            = EXCLUDED.active,
    notes             = EXCLUDED.notes,
    updated_at        = now()
RETURNING id, (xmax = 0) AS inserted
"""


@dataclass(slots=True)
class SyncResult:
    inserted: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    #: In the table but no longer in the code. Reported, never deleted: a
    #: source row is referenced by datasets and runs, and a scraper removed in
    #: a refactor should not take its history with it.
    orphaned: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.inserted) + len(self.updated)


def sync_sources(connection: psycopg.Connection, metas: list[SourceMeta]) -> SyncResult:
    """Upsert registry records into the `sources` table."""
    result = SyncResult()

    for meta in metas:
        row = connection.execute(
            _UPSERT,
            {
                "slug": meta.slug,
                "name": meta.name,
                "organization": meta.organization,
                "source_type": str(meta.source_type),
                "base_url": meta.base_url,
                "country": meta.country,
                "license": meta.license,
                "collection_method": str(meta.collection_method),
                "update_frequency": str(meta.update_frequency),
                "active": meta.active,
                "notes": meta.notes,
            },
        ).fetchone()
        assert row is not None
        (result.inserted if row["inserted"] else result.updated).append(meta.slug)

    known = {meta.slug for meta in metas}
    rows = connection.execute("SELECT slug FROM sources").fetchall()
    result.orphaned = sorted({r["slug"] for r in rows} - known)

    log.info(
        "catalog.sources_synced",
        inserted=len(result.inserted),
        updated=len(result.updated),
        orphaned=len(result.orphaned),
    )
    return result


def source_id(connection: psycopg.Connection, slug: str) -> UUID | None:
    """Look up a source's primary key by slug."""
    row = connection.execute("SELECT id FROM sources WHERE slug = %s", (slug,)).fetchone()
    return row["id"] if row else None
