"""Writing artifacts into RAW, with provenance.

Every scraper routes through here, so the RAW layout and the provenance record
are defined once. The alternative — each scraper writing its own directories —
is how a lake becomes unqueryable (program.md §17).

Layout follows program.md §5:

    raw/<category>/<source>/<dataset>/<partition...>/<doc_id>/
        original.pdf      the bytes as received
        metadata.json     provenance
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..storage import Layer, StorageResolver, slugify
from .base import Artifact, SourceMeta
from .sniff import verify

#: Provenance sidecar written next to every landed artifact.
METADATA_FILENAME = "metadata.json"


@dataclass(frozen=True, slots=True)
class Landed:
    """Result of landing one artifact."""

    path: str
    content_hash: str
    document_id: str
    bytes_written: int

    #: True when the artifact was already present with identical content, so
    #: nothing was rewritten. Re-running a scraper over a stable archive
    #: should be almost entirely skips.
    deduplicated: bool


class Landing:
    """Lands artifacts in RAW and records where they came from."""

    def __init__(self, resolver: StorageResolver) -> None:
        self._resolver = resolver

    def segments_for(self, meta: SourceMeta, artifact: Artifact) -> list[str]:
        """Return the RAW path segments an artifact belongs under.

        Segments are slugified here rather than validated, because they come
        from scraped pages: a document title carrying a slash or an em dash is
        ordinary input, not a bug to reject.
        """
        return [
            slugify(meta.category),
            slugify(meta.slug),
            slugify(artifact.dataset),
            *(slugify(p) for p in artifact.partition),
            artifact.document_id,
        ]

    def path_for(self, meta: SourceMeta, artifact: Artifact) -> str:
        """Return the RAW directory an artifact belongs in."""
        return self._resolver.resolve(Layer.RAW, *self.segments_for(meta, artifact))

    def land(self, meta: SourceMeta, artifact: Artifact) -> Landed:
        """Write an artifact and its provenance into RAW.

        RAW is immutable (program.md §5), so an existing directory with the
        same content hash is left alone. The path is content-addressed, so a
        collision means the same bytes, not a conflict.

        Raises `ContentMismatch` if the bytes are not the format the filename
        claims. That check belongs here rather than downstream: RAW is
        permanent, so an HTML error page landed as `.xls` is a mistake nothing
        later can undo.
        """
        verify(artifact.content, artifact.filename)

        segments = self.segments_for(meta, artifact)
        directory = self._resolver.resolve(Layer.RAW, *segments)
        target = Path(directory) / _safe_filename(artifact.filename)

        if target.exists() and _hash_matches(target, artifact.content_hash):
            return Landed(
                path=str(target),
                content_hash=artifact.content_hash,
                document_id=artifact.document_id,
                bytes_written=0,
                deduplicated=True,
            )

        # Go through resolve_for_write so the profile and immutability guards
        # apply: landing is the one legitimate reason to write to RAW.
        self._resolver.resolve_for_write(Layer.RAW, *segments, overwrite_immutable=True)

        target.write_bytes(artifact.content)
        (Path(directory) / METADATA_FILENAME).write_text(
            json.dumps(self._provenance(meta, artifact, target), indent=2, sort_keys=True)
        )

        return Landed(
            path=str(target),
            content_hash=artifact.content_hash,
            document_id=artifact.document_id,
            bytes_written=len(artifact.content),
            deduplicated=False,
        )

    @staticmethod
    def _provenance(meta: SourceMeta, artifact: Artifact, target: Path) -> dict:
        """Build the sidecar.

        This is what makes "where did this number come from?" answerable four
        layers downstream, so it records the retrieval, not just the file.
        """
        retrieved = artifact.retrieved_at or datetime.now(UTC)
        return {
            "document_id": artifact.document_id,
            "content_hash": artifact.content_hash,
            "content_hash_algorithm": "sha256",
            "original_filename": target.name,
            "media_type": artifact.media_type,
            "size_bytes": len(artifact.content),
            "source": {
                "slug": meta.slug,
                "name": meta.name,
                "organization": meta.organization,
                "source_type": str(meta.source_type),
                "collection_method": str(meta.collection_method),
                "license": meta.license,
            },
            "source_url": artifact.source_url,
            "dataset": artifact.dataset,
            "partition": list(artifact.partition),
            "published_at": artifact.published_at.isoformat() if artifact.published_at else None,
            "retrieved_at": retrieved.isoformat(),
            "extra": artifact.metadata,
        }


def _safe_filename(filename: str) -> str:
    """Slugify a filename while preserving its extension."""
    stem, _, suffix = filename.rpartition(".")
    if not stem:
        return slugify(filename)
    return f"{slugify(stem)}.{slugify(suffix)}"


def _hash_matches(path: Path, expected: str) -> bool:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected
