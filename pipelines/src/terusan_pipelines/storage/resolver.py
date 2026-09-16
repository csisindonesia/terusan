"""The single authority for physical storage paths.

program.md §45.4 forbids building physical paths anywhere else. The point is
that a dataset keeps the same logical address — layer plus segments — whether
it lives in `./.data`, on a NAS mount, or in a bucket, so moving between them
is a configuration change rather than a migration.

    >>> r = StorageResolver(StorageConfig(STORAGE_ROOT="./.data"))
    >>> r.resolve(Layer.SILVER, "observations", "year=2026")
    './.data/silver/observations/year=2026'
"""

from __future__ import annotations

from pathlib import Path

from .config import Backend, StorageConfig
from .layers import DISPOSABLE_LAYERS, IMMUTABLE_LAYERS, Layer
from .paths import check_segment


class StorageWriteRefused(RuntimeError):
    """Raised when a write is attempted that the active profile forbids."""


class StorageResolver:
    """Turns logical addresses into physical paths or URIs."""

    def __init__(self, config: StorageConfig | None = None) -> None:
        self._config = config if config is not None else StorageConfig()

    @property
    def config(self) -> StorageConfig:
        return self._config

    @property
    def root(self) -> str:
        """The lake root, absolute for a filesystem and a URI for a bucket.

        Everything below resolves from here, so `./.data` cannot mean two
        different directories depending on where a command was run.
        """
        if self._config.is_object_storage:
            return self._config.root
        return str(self._config.root_path)

    # ---- reading -------------------------------------------------------

    def resolve(self, layer: Layer, *segments: str) -> str:
        """Return the physical location of a logical address.

        Segments are validated rather than rewritten: a path that silently
        differs from what the catalog recorded is worse than a loud failure.
        Callers holding untrusted text (a document title, a source name)
        should pass it through `paths.slugify` first.
        """
        checked = [check_segment(s) for s in segments]
        return self._join(self.root, str(layer), *checked)

    def glob(self, layer: Layer, *segments: str, pattern: str = "**/*.parquet") -> str:
        """Return a recursive glob suitable for DuckDB `read_parquet`."""
        return self._join(self.resolve(layer, *segments), pattern)

    def scratch(self, *segments: str) -> Path:
        """Return a local-disk scratch path, creating the directory.

        Never resolves against `STORAGE_ROOT`: spill and staging stay on local
        SSD regardless of where the lake lives.
        """
        from .root import resolve_path

        base = resolve_path(self._config.scratch_dir)
        path = base.joinpath(*(check_segment(s) for s in segments))
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- writing -------------------------------------------------------

    def resolve_for_write(
        self,
        layer: Layer,
        *segments: str,
        overwrite_immutable: bool = False,
    ) -> str:
        """Like `resolve`, but refuses writes the active profile disallows.

        Two separate guards, because they fail for different reasons:
        a shared profile is a wrong-machine mistake, while writing to RAW is a
        wrong-layer mistake that would destroy the one non-reproducible copy
        of a source document (program.md §45.8).
        """
        if not self._config.writes_allowed:
            raise StorageWriteRefused(
                f"profile {self._config.profile!s} is read-only for this process; "
                "set STORAGE_ALLOW_SHARED_WRITES=true to override"
            )
        if layer in IMMUTABLE_LAYERS and not overwrite_immutable:
            raise StorageWriteRefused(
                f"layer {layer!s} is immutable; pass overwrite_immutable=True only when "
                "landing a newly retrieved source document"
            )
        target = self.resolve(layer, *segments)
        if self._config.backend is not Backend.S3:
            Path(target).mkdir(parents=True, exist_ok=True)
        return target

    def ensure_layout(self) -> list[str]:
        """Create every layer directory. No-op for object storage, which has
        no directories to create."""
        created: list[str] = []
        if self._config.is_object_storage:
            return created
        for layer in Layer:
            path = Path(self._join(self.root, str(layer)))
            path.mkdir(parents=True, exist_ok=True)
            created.append(str(path))
        return created

    def is_disposable(self, layer: Layer) -> bool:
        return layer in DISPOSABLE_LAYERS

    # ---- internals -----------------------------------------------------

    @staticmethod
    def _join(base: str, *parts: str) -> str:
        """Join path parts without letting `pathlib` mangle the `s3://` scheme."""
        stem = base.rstrip("/")
        for part in parts:
            stem = f"{stem}/{part.strip('/')}"
        return stem
