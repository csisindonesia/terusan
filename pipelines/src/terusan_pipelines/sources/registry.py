"""Discovering sources.

Sources register themselves by subclassing `Source` inside
`terusan_pipelines.sources`. Discovery walks the package rather than reading a
hand-maintained list: with fifty-odd sources, a central list is a merge
conflict that silently drops a scraper when resolved badly.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta

from .base import Source, SourceMeta
from .schedule import DEFAULT_WINDOW, due, last_firing


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
            # Only what this package defines. `Source.__subclasses__()` reaches
            # every subclass alive anywhere in the process — a throwaway defined
            # in a test, a notebook cell, a downstream consumer's own module —
            # and registering those would let an unrelated import decide what
            # this registry holds, or collide on a slug and fail the discovery
            # of the real sources. It also makes the argument mean something:
            # discovering one agency's package registers that agency alone.
            if not _defined_in(subclass, package):
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

    def due(
        self,
        now: datetime,
        window: timedelta = DEFAULT_WINDOW,
    ) -> list[type[Source]]:
        """Scheduled sources whose cron fired in the window ending at `now`.

        What an agent runs. A schedule that cannot be read raises rather than
        being skipped: a source going uncollected in silence is the failure
        reading these expressions was meant to end.
        """
        return [
            source for source in self.scheduled() if due(str(source.meta.schedule), now, window)
        ]

    def owed(
        self,
        now: datetime,
        window: timedelta,
        catch_up: timedelta,
        last_succeeded: Mapping[str, datetime],
    ) -> list[type[Source]]:
        """What is due, plus every firing in `catch_up` that no success followed.

        `due` alone gives each firing one chance. A daily source whose 07:00
        run met a dead network, or a machine asleep or restarting at 07:00,
        then waits a whole day, and the day's figure is never collected. This
        keeps such a firing owed for `catch_up` hours: each hour it is asked
        again until a run succeeds, and a source that has not fired within the
        catch-up is left alone however long ago it last succeeded.

        `last_succeeded` maps a slug to the start of its latest successful
        ingestion, as the run journal records it. A run that started after
        the firing settles it; one that started before does not. A naive
        firing time is local wall clock, which is how schedules are read.
        """
        owed = self.due(now, window)
        seen = {source.meta.slug for source in owed}
        for source in self.scheduled():
            slug = source.meta.slug
            if slug in seen:
                continue
            fired = last_firing(str(source.meta.schedule), now, catch_up)
            if fired is None:
                continue
            succeeded = last_succeeded.get(slug)
            if succeeded is None or succeeded < fired.astimezone():
                owed.append(source)
        return owed

    def __len__(self) -> int:
        self.ensure_loaded()
        return len(self._sources)


def _defined_in(subclass: type, package: str) -> bool:
    module = subclass.__module__
    return module == package or module.startswith(f"{package}.")


def _all_subclasses(cls: type) -> Iterator[type]:
    for subclass in cls.__subclasses__():
        yield subclass
        yield from _all_subclasses(subclass)


#: Process-wide registry. Tests build their own rather than mutating this.
registry = Registry()
