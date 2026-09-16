"""Discovering sources.

Sources register themselves by subclassing `Source` inside
`terusan_pipelines.sources`. Discovery walks the package rather than reading a
hand-maintained list: with fifty-odd sources, a central list is a merge
conflict that silently drops a scraper when resolved badly.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator

from .base import Source, SourceMeta


class DuplicateSourceSlug(ValueError):
    """Two sources claim the same slug.

    Slugs key the `sources` table and the RAW path, so a duplicate would make
    two providers write into one directory.
    """


class UnknownSource(KeyError):
    """No registered source has the requested slug."""


class Registry:
    """The set of sources available to this process."""

    def __init__(self) -> None:
        self._sources: dict[str, type[Source]] = {}
        self._loaded = False

    def register(self, source: type[Source]) -> type[Source]:
        """Add a source. Usable as a decorator."""
        slug = source.meta.slug
        existing = self._sources.get(slug)
        if existing is not None and existing is not source:
            raise DuplicateSourceSlug(
                f"slug {slug!r} claimed by both {existing.__module__}.{existing.__name__} "
                f"and {source.__module__}.{source.__name__}"
            )
        self._sources[slug] = source
        return source

    def discover(self, package: str = "terusan_pipelines.sources") -> None:
        """Import every submodule so subclasses register themselves."""
        root = importlib.import_module(package)
        for info in pkgutil.walk_packages(root.__path__, prefix=f"{package}."):
            importlib.import_module(info.name)

        for subclass in _all_subclasses(Source):
            if subclass.abstract or getattr(subclass, "__abstractmethods__", None):
                continue
            self.register(subclass)
        self._loaded = True

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.discover()

    def get(self, slug: str) -> type[Source]:
        self.ensure_loaded()
        try:
            return self._sources[slug]
        except KeyError:
            raise UnknownSource(f"no source registered with slug {slug!r}") from None

    def all(self) -> list[type[Source]]:
        self.ensure_loaded()
        return sorted(self._sources.values(), key=lambda s: s.meta.slug)

    def metas(self) -> Iterator[SourceMeta]:
        """Every registry record, for syncing into the `sources` table."""
        for source in self.all():
            yield source.meta

    def scheduled(self) -> list[type[Source]]:
        """Sources that declare a schedule and are active."""
        return [s for s in self.all() if s.meta.schedule and s.meta.active]

    def __len__(self) -> int:
        self.ensure_loaded()
        return len(self._sources)


def _all_subclasses(cls: type) -> Iterator[type]:
    for subclass in cls.__subclasses__():
        yield subclass
        yield from _all_subclasses(subclass)


#: Process-wide registry. Tests build their own rather than mutating this.
registry = Registry()
