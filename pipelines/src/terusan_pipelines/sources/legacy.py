"""Running existing standalone scrapers without rewriting them.

Porting fifty ad-hoc scripts before any of them can run is not a migration,
it is a rewrite with the lake switched off in the meantime. So a legacy script
keeps its own fetching and its own output files; this module supplies the part
it never had — a RAW landing with provenance — and sweeps whatever it wrote.

    from terusan_pipelines.sources.legacy import legacy_source
    from .scrape_inflation import main

    InflationSource = legacy_source(
        main,
        meta=SourceMeta(slug="bps-inflation", ...),
        dataset="inflation",
    )

The script is unchanged. It runs against a scratch directory, and every file
it leaves behind is landed, hashed and recorded.

This is a staging post, not a destination. A legacy source cannot honour
`since` or `limit`, so it re-fetches everything every run; content-addressed
landing keeps that cheap on disk but not on the source's bandwidth. Convert
the ones that run often to a native `Source` first.
"""

from __future__ import annotations

import inspect
import mimetypes
import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .base import Artifact, ScrapeContext, Source, SourceMeta

#: A legacy entry point: either it accepts the output directory, or it writes
#: to the working directory and we run it inside one.
LegacyCallable = Callable[..., None]


def directory_artifacts(
    directory: Path,
    *,
    dataset: str,
    partition: tuple[str, ...] = (),
    retrieved_at: datetime | None = None,
) -> Iterator[Artifact]:
    """Turn every file under `directory` into an Artifact.

    Walks recursively and preserves the relative path in the metadata, so a
    script that organises its output into subdirectories does not lose that
    structure on the way into RAW.
    """
    retrieved = retrieved_at or datetime.now(UTC)
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        relative = path.relative_to(directory)
        media_type, _ = mimetypes.guess_type(path.name)
        yield Artifact(
            content=path.read_bytes(),
            filename=path.name,
            dataset=dataset,
            media_type=media_type,
            retrieved_at=retrieved,
            partition=partition,
            metadata={
                "legacy_relative_path": str(relative),
                "legacy_mtime": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
            },
        )


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    """Run a block with the process working directory moved.

    Ad-hoc scripts routinely write to relative paths like `out/` or
    `./data`. Moving the working directory is what lets them do that without
    scattering files across the repository.

    Process-global, so the runner serialises legacy sources rather than
    running them concurrently with each other.
    """
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class LegacySource(Source, abstract=True):
    """A `Source` backed by a standalone script."""

    #: Legacy scripts mutate the process working directory, so they cannot run
    #: alongside each other. The runner reads this.
    concurrency_safe = False

    def __init__(
        self,
        entry_point: LegacyCallable,
        *,
        dataset: str,
        partition: tuple[str, ...] = (),
        output_subdir: str | None = None,
    ) -> None:
        self._entry_point = entry_point
        self._dataset = dataset
        self._partition = partition
        self._output_subdir = output_subdir

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        from ..storage import StorageConfig, StorageResolver

        resolver = StorageResolver(StorageConfig())
        workspace = resolver.scratch("legacy", self.meta.slug)

        # Start clean: leftovers from a previous run would be landed again as
        # though freshly fetched, with a misleading retrieved_at.
        shutil.rmtree(workspace, ignore_errors=True)
        workspace.mkdir(parents=True, exist_ok=True)

        try:
            self._invoke(workspace)
            harvest = workspace / self._output_subdir if self._output_subdir else workspace
            if not harvest.is_dir():
                return

            for emitted, artifact in enumerate(
                directory_artifacts(harvest, dataset=self._dataset, partition=self._partition),
                start=1,
            ):
                yield artifact
                if ctx.limit is not None and emitted >= ctx.limit:
                    return
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _invoke(self, workspace: Path) -> None:
        """Call the legacy entry point however it expects to be called."""
        if _accepts_an_argument(self._entry_point):
            self._entry_point(workspace)
        else:
            with _working_directory(workspace):
                self._entry_point()


def _accepts_an_argument(fn: LegacyCallable) -> bool:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    return any(
        p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty
        for p in signature.parameters.values()
    )


def legacy_source(
    entry_point: LegacyCallable,
    *,
    meta: SourceMeta,
    dataset: str,
    partition: tuple[str, ...] = (),
    output_subdir: str | None = None,
) -> type[LegacySource]:
    """Build a registrable `Source` class around a legacy script."""

    # Created abstract so the `meta` check does not fire before `meta` can be
    # assigned, then made concrete.
    class _Wrapped(LegacySource, abstract=True):
        pass

    _Wrapped.meta = meta
    _Wrapped.abstract = False
    _Wrapped.__name__ = f"Legacy_{meta.slug.replace('-', '_')}"
    _Wrapped.__qualname__ = _Wrapped.__name__
    _Wrapped.__doc__ = f"Legacy scraper for {meta.name}, adapted without modification."

    def __init__(self: LegacySource) -> None:
        LegacySource.__init__(
            self,
            entry_point,
            dataset=dataset,
            partition=partition,
            output_subdir=output_subdir,
        )

    _Wrapped.__init__ = __init__  # type: ignore[method-assign]
    return _Wrapped
