"""Moving a lake off slug identifiers and onto derived codes.

Silver used to publish a series under whatever key its mapping was declared
with — `retail_sales_index`, `te_balance_of_trade` — and a collection under
the name extraction gave it. Those are good names and poor identifiers: they
are the publisher's wording, they change when the publisher rewords, and a
rename orphans every partition, link and bookmark made under the old one. The
catalogue now identifies both by a derived code and keeps the readable key
beside it as `slug` (program.md §10).

This rewrites a lake written under the old rule. It is a migration, not a
pipeline stage: normalization already produces codes, so a lake built from
scratch never needs it, and running it twice changes nothing the second time.

Three things move together, because a half-moved lake is worse than either
state: the observations (their `indicator_id`, their `dataset_id`, and the
`observation_id` derived from the first of those), the indicators table, and
the dataset catalogue. The figures themselves are untouched — this renames
what points at them.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from .. import datasets as dataset_registry
from ..identifiers import dataset_code, indicator_code, is_code
from ..storage import Layer
from ..warehouse import Warehouse, table_from_rows
from ..warehouse.observations import ObservationStore
from .observations import observation_id
from .runner import SilverRunner
from .schema import SILVER_OBSERVATIONS

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class RecodeResult:
    """What a migration moved."""

    indicators_recoded: int = 0
    indicators_already_coded: int = 0
    observations_rewritten: int = 0
    indicator_rows: int = 0
    dataset_rows: int = 0
    #: `old id → new id`, for the record. Small enough to print: a lake holds
    #: hundreds of series, not hundreds of thousands.
    mapping: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Series:
    """One series as the observations describe it."""

    old_id: str
    new_id: str
    slug: str | None
    source_id: str | None
    dataset_slug: str | None
    #: The identifier the observations carry, once coded. Kept beside the slug
    #: because a dataset can be referenced by a code nothing names — figures
    #: normalized against a Bronze dataset that has since been renamed — and
    #: dropping the reference would orphan the series rather than the name.
    dataset_id: str | None
    unit: str | None
    frequency: str | None
    observations: int


def recode(runner: SilverRunner, *, dry_run: bool = False) -> RecodeResult:
    """Rewrite a Silver lake under derived identifiers."""
    result = RecodeResult()
    resolver = runner._resolver  # noqa: SLF001 - the migration is part of this package

    root = Path(resolver.resolve(Layer.SILVER, "observations"))
    if not any(root.rglob("*.parquet")):
        log.info("recode.empty", reason="no observations published")
        return result

    series = _survey(runner)
    result.mapping = {s.old_id: s.new_id for s in series if s.old_id != s.new_id}
    result.indicators_recoded = len(result.mapping)
    result.indicators_already_coded = len(series) - len(result.mapping)

    described = _describe(runner, series)
    if dry_run:
        result.indicator_rows = len(described)
        result.dataset_rows = len({s.dataset_slug for s in series if s.dataset_slug})
        return result

    slug_like = {meta.slug for meta in dataset_registry.DATASETS}
    slug_like.update(slug for slug, _ in runner.collected_datasets())
    result.observations_rewritten = _rewrite_observations(runner, series, slug_like)
    result.indicator_rows = _rewrite_indicators(runner, described)
    collected = runner.collected_datasets() or [
        (entry.dataset_slug, entry.source_id) for entry in series if entry.dataset_slug
    ]
    result.dataset_rows = runner.write_datasets(
        collected, member_tags=runner.indicator_tags_by_dataset()
    )
    return result


def _survey(runner: SilverRunner) -> list[_Series]:
    """What every series in the lake is, and what it should be identified by."""
    resolver = runner._resolver  # noqa: SLF001
    pattern = resolver.glob(Layer.SILVER, "observations")
    with Warehouse(resolver) as warehouse:
        rows = warehouse.query(
            """
            SELECT indicator_id,
                   any_value(source_id),
                   any_value(dataset_id),
                   mode(unit),
                   mode(temporal_resolution),
                   count(*)
            FROM read_parquet(?, union_by_name=true, hive_partitioning=false)
            GROUP BY indicator_id ORDER BY indicator_id
            """,
            [pattern],
        ).fetchall()

    known = _published_indicators(runner)
    # A lake this has already run over carries dataset codes, not slugs. The
    # catalogue is what turns one back into the other, so a second pass keeps
    # the dataset a series belongs to instead of dropping it.
    slugs = _dataset_slugs(runner)
    slug_like = {meta.slug for meta in dataset_registry.DATASETS}
    slug_like.update(slug for slug, _ in runner.collected_datasets())
    series = []
    for indicator_id, source_id, dataset_id, unit, resolution, count in rows:
        old_id = str(indicator_id)
        source = str(source_id) if source_id else None
        published = known.get(old_id, {})
        if is_code(old_id):
            # Already a code, from an extractor that had the publisher's own
            # series id in hand. Recoding it would move partitions for nothing.
            new_id, slug = old_id, _slugify(published.get("code")) or published.get("slug")
        else:
            new_id, slug = indicator_code(source or "indicator", old_id), old_id
        series.append(
            _Series(
                old_id=old_id,
                new_id=new_id,
                slug=slug,
                source_id=source,
                # A dataset already carrying a code has been through this
                # before; its slug is in the dataset catalogue, not here.
                dataset_slug=_dataset_slug(dataset_id, slugs, slug_like),
                dataset_id=_dataset_id(dataset_id, slug_like),
                unit=str(unit) if unit else None,
                frequency=str(resolution) if resolution else None,
                observations=int(count),
            )
        )
    return series


def _published_indicators(runner: SilverRunner) -> dict[str, dict]:
    """The indicators table as it stands, keyed by the identifier it uses."""
    resolver = runner._resolver  # noqa: SLF001
    root = Path(resolver.resolve(Layer.SILVER, "indicators"))
    if not any(root.rglob("*.parquet")):
        return {}

    pattern = resolver.glob(Layer.SILVER, "indicators")
    with Warehouse(resolver) as warehouse:
        table = (
            warehouse.query(
                "SELECT * FROM read_parquet(?, union_by_name=true, hive_partitioning=true)",
                [pattern],
            )
            .arrow()
            .read_all()
        )
    return {str(row["indicator_id"]): row for row in table.to_pylist()}


def _describe(runner: SilverRunner, series: list[_Series]) -> list[dict]:
    """One indicators row per series, from what is published plus what is not.

    A series with no row in the indicators table is not an anomaly: only the
    bulk sources publish one, so most of the lake reaches the portal as a bare
    identifier. Rebuilding the table is the moment to give the rest a name —
    read off the key they were declared with, which is what a maintainer chose
    to call them in the first place.
    """
    known = _published_indicators(runner)
    described = []
    for entry in series:
        published = known.get(entry.old_id, {})
        name = published.get("name") or _titleize(entry.slug or entry.old_id)
        described.append(
            {
                "indicator_id": entry.new_id,
                "slug": entry.slug,
                "name": name,
                "code": published.get("code"),
                "description": published.get("description"),
                "publisher": published.get("publisher"),
                "release": published.get("release"),
                "unit": published.get("unit") or entry.unit,
                "frequency": published.get("frequency") or entry.frequency,
                "source_id": entry.source_id,
                "dataset": entry.dataset_slug,
                "dataset_id": entry.dataset_id,
            }
        )
    return described


def _rewrite_observations(runner: SilverRunner, series: list[_Series], slug_like: set[str]) -> int:
    """Rewrite the fact table under the new identifiers.

    Read whole, then written whole. The dataset is partitioned by indicator,
    so a row moving between partitions cannot be rewritten in place — and a
    partial rewrite would leave the same figures published twice, once under
    each identifier.
    """
    resolver = runner._resolver  # noqa: SLF001
    identifiers = {entry.old_id: entry.new_id for entry in series}

    pattern = resolver.glob(Layer.SILVER, "observations")
    with Warehouse(resolver) as warehouse:
        table = (
            warehouse.query(
                "SELECT * FROM read_parquet(?, union_by_name=true, hive_partitioning=false)",
                [pattern],
            )
            .arrow()
            .read_all()
        )

    rows = table.to_pylist()
    for row in rows:
        old = str(row["indicator_id"])
        new = identifiers.get(old, old)
        row["indicator_id"] = new
        dataset = row.get("dataset_id")
        if dataset:
            row["dataset_id"] = _dataset_id(dataset, slug_like)
        # The observation id is derived from the indicator, so it moves with
        # it. Recomputed rather than rewritten by hand: the same function the
        # normalizer uses, so a re-normalization lands on the ids this wrote
        # and revises the figures instead of duplicating them.
        row["observation_id"] = observation_id(
            new,
            str(row["period"]),
            row.get("geo_id") or row.get("geo_name_raw"),
            row.get("commodity_id") or row.get("commodity_name_raw"),
        )

    rewritten = table_from_rows(rows, SILVER_OBSERVATIONS)
    # Every series moves, so every bucket is rewritten, each renamed into
    # place rather than the table cleared first and written again.
    written = ObservationStore(resolver).rewrite_all(rewritten)
    log.info("recode.observations", rows=written.rows, files=written.files)
    return written.rows


def _rewrite_indicators(runner: SilverRunner, described: list[dict]) -> int:
    """Republish the indicators table, one source at a time."""
    resolver = runner._resolver  # noqa: SLF001
    _clear(Path(resolver.resolve(Layer.SILVER, "indicators")))

    by_source: dict[str, list[dict]] = {}
    for row in described:
        by_source.setdefault(row.get("source_id") or "unknown", []).append(row)

    written = 0
    for source_id, rows in sorted(by_source.items()):
        written += runner.write_indicators(rows, source_id=source_id)
    return written


def _clear(target: Path) -> None:
    shutil.rmtree(target, ignore_errors=True)


def _is_slug(value: str, slug_like: set[str]) -> bool:
    """Whether a dataset identifier is a name rather than a code.

    Decided by what the lake and the registry actually call their datasets,
    not by the string's shape: a dataset called `handbook` is eight lowercase
    letters and would pass for a code, and treating it as one leaves its
    figures pointing at a dataset the catalogue does not list.
    """
    return value in slug_like or not is_code(value)


def _dataset_id(value: object, slug_like: set[str]) -> str | None:
    """The identifier a dataset reference should carry after the migration."""
    if value is None:
        return None
    text = str(value)
    return dataset_code(text) if _is_slug(text, slug_like) else text


def _dataset_slug(value: object, published: dict[str, str], slug_like: set[str]) -> str | None:
    """The readable name of the dataset an observation points at.

    The value is a slug on a lake that has not been migrated and a code on one
    that has. Both resolve to the slug, so rerunning the migration is a no-op
    rather than a quiet loss of what each series belongs to. A code nothing
    names resolves to nothing, and the reference is kept as the code.
    """
    if value is None:
        return None
    text = str(value)
    if _is_slug(text, slug_like):
        return text
    declared = dataset_registry.by_code(text)
    return declared.slug if declared is not None else published.get(text)


def _dataset_slugs(runner: SilverRunner) -> dict[str, str]:
    """`code → slug`, as the published dataset catalogue has it."""
    resolver = runner._resolver  # noqa: SLF001
    root = Path(resolver.resolve(Layer.SILVER, "datasets"))
    if not any(root.rglob("*.parquet")):
        return {}

    pattern = resolver.glob(Layer.SILVER, "datasets")
    with Warehouse(resolver) as warehouse:
        rows = warehouse.query(
            "SELECT dataset_id, slug FROM "
            "read_parquet(?, union_by_name=true, hive_partitioning=true)",
            [pattern],
        ).fetchall()
    return {str(code): str(slug) for code, slug in rows if code and slug}


def _slugify(value: str | None) -> str | None:
    if not value:
        return None
    from ..storage import slugify

    try:
        return slugify(str(value), allow_partition=False)
    except Exception:  # noqa: BLE001 - a code that sanitises to nothing has no slug
        return None


def _titleize(key: str) -> str:
    """A readable name from a declared key.

    `retail_sales_index` reads as "Retail sales index", which is what whoever
    declared the mapping meant by it. Better than the alternative, which is a
    portal printing the identifier twice.
    """
    words = key.replace("_", " ").replace("-", " ").split()
    if not words:
        return key
    return " ".join(words).capitalize()
