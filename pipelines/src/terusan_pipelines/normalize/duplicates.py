"""The same figures, collected from more than one source.

Several sources in this lake redistribute one another. ADB's Key Indicators
are compiled from BPS; FRED republishes the IMF's copy of Bank Indonesia's
money supply; Trading Economics carries BPS's GDP; SEKI and BPS both publish
the national accounts. Kept side by side, a reader searching for "GDP" finds
five series that are one series, and cannot tell which to cite.

So Silver keeps one. The one kept is the most complete — the most
observations, then the latest period, then the earliest — and where two are
equally complete, the one that is easiest to collect again: an open API
before a file download, a download before a scrape, anything before a source
that needs a key someone has to request.

**Duplicates are found by their figures, not by their titles.** Titles are no
use across publishers: BPS says "Uang Beredar — Jumlah (M2)", Trading
Economics says "Money Supply M3", and they are the same numbers. Two series
are the same where, over the place-and-period cells both report, at least
four in five agree to three significant figures — at one consistent power of
ten, because one publishes rupiah and the other billions of rupiah. Series
that barely vary are left out: a tax rate of 10% for a decade agrees with
every other constant 10% in the lake, and at least five distinct values have
to match before agreement means anything.

**Nothing is lost by the deletion.** A series is deleted only where the one
kept also holds at least nine in ten of its own observations. Two series that
agree where they overlap but each extend past the other — a provincial table
and a national one, a long annual history and a recent one — are complements,
and both stay.

**Deletion is not transitive.** A series is deleted only for a direct match
with the one kept. "GDP growth" agreeing with two BPS tables does not make the
two BPS tables the same series, and chaining matches would delete one of them
on the strength of a resemblance to a third.

Bronze and RAW are untouched: a deleted series can always be normalized
again. What keeps it from coming back on the next scheduled run is the table
this writes, `indicator_duplicates`, which normalization consults — and which
it overrides where the kept series no longer covers the deleted one, so a
source that starts publishing periods the other lacks is let back in rather
than silently held out.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa
import structlog

from ..sources.base import CollectionMethod
from ..storage import Layer, StorageResolver
from ..warehouse import Warehouse

log = structlog.get_logger(__name__)

TABLE = "indicator_duplicates"


@dataclass(frozen=True, slots=True)
class Thresholds:
    """How alike two series must be before one is deleted."""

    #: Cells that must agree. Five periods of agreement is a pattern; two is
    #: a coincidence.
    min_matches: int = 5
    #: Distinct values among the agreeing cells. Keeps constant and
    #: near-constant series out.
    min_distinct: int = 5
    #: Share of the overlapping cells that agree. Not 1.0: publishers revise
    #: at different times, and a copy taken last year disagrees on the last
    #: few periods of a series that is otherwise the same.
    min_match_share: float = 0.8
    #: Share of the deleted series' observations the kept one must also hold.
    min_coverage: float = 0.9


#: How hard a source is to collect again. Lower is easier.
_METHOD_EFFORT = {
    CollectionMethod.API: 0,
    CollectionMethod.BULK_DOWNLOAD: 1,
    CollectionMethod.FEED: 1,
    CollectionMethod.SCRAPE: 2,
    CollectionMethod.MANUAL_UPLOAD: 3,
}

#: Sources that need a credential someone has to request. Named here rather
#: than read off the code, because what needs a key is a fact about the
#: publisher; see sources/credentials.py for the keys themselves.
KEYED_SOURCE_PREFIXES = ("bps-", "gee-", "kemendag-satudata", "badanpangan-")


def effort(source_id: str) -> int:
    """How much it takes to collect this source again."""
    penalty = 2 if source_id.startswith(KEYED_SOURCE_PREFIXES) else 0
    try:
        from ..sources import registry

        method = registry.get(source_id).meta.collection_method
    except Exception:  # noqa: BLE001 - an unregistered source is simply the hardest
        return 4 + penalty
    return _METHOD_EFFORT.get(method, 3) + penalty


@dataclass(frozen=True, slots=True)
class _Series:
    indicator_id: str
    source_id: str
    resolution: str | None
    observations: int
    first: date | None
    last: date | None

    def rank(self) -> tuple:
        """Sorts the one to keep first."""
        return (
            -self.observations,
            -(self.last.toordinal() if self.last else 0),
            self.first.toordinal() if self.first else 10**7,
            effort(self.source_id),
            self.indicator_id,
        )


@dataclass(frozen=True, slots=True)
class _Pair:
    matches: int
    overlap: int
    distinct: int
    scale: int

    @property
    def share(self) -> float:
        return self.matches / self.overlap if self.overlap else 0.0


def _compare(resolver: StorageResolver, thresholds: Thresholds):
    """Every series' size, and every cross-source pair that agrees."""
    pattern = resolver.glob(Layer.SILVER, "observations")
    with Warehouse(resolver) as warehouse:
        warehouse.query("SET preserve_insertion_order=false")
        # One row per cell. The mantissa to three significant figures is what
        # makes the comparison scale-free: 1389.77 trillion and 1,389,769.85
        # billion both read 1.39.
        warehouse.query(
            """
            CREATE TEMP TABLE cells AS
            SELECT indicator_id, source_id, geo_id,
                   coalesce(commodity_id, '') AS commodity_id,
                   coalesce(category, '') AS category,
                   temporal_resolution AS resolution, period_start,
                   value::DOUBLE AS v,
                   CASE WHEN value <> 0 THEN round(
                       abs(value::DOUBLE) / pow(10, floor(log10(abs(value::DOUBLE)))), 2
                   ) END AS m
            FROM read_parquet(?, union_by_name=true, hive_partitioning=false)
            WHERE value IS NOT NULL AND indicator_id IS NOT NULL
            """,
            [pattern],
        )
        sizes = warehouse.query(
            """
            SELECT indicator_id, any_value(source_id), any_value(resolution),
                   count(*), min(period_start), max(period_start)
            FROM cells GROUP BY indicator_id
            """
        ).fetchall()
        pairs = warehouse.query(
            f"""
            WITH agreeing AS (
                SELECT a.indicator_id AS a, b.indicator_id AS b,
                       round(log10(a.v / b.v))::INT AS scale,
                       count(*) AS matches, count(DISTINCT a.m) AS distinct_values
                FROM cells a JOIN cells b
                  USING (geo_id, commodity_id, category, resolution, period_start, m)
                WHERE a.source_id <> b.source_id AND sign(a.v) = sign(b.v)
                GROUP BY ALL
                HAVING count(*) >= {int(thresholds.min_matches)}
                   AND count(DISTINCT a.m) >= {int(thresholds.min_distinct)}
            ),
            best AS (
                SELECT * FROM agreeing
                QUALIFY row_number() OVER (PARTITION BY a, b ORDER BY matches DESC) = 1
            ),
            overlapping AS (
                SELECT p.a, p.b, count(*) AS overlap
                FROM best p
                JOIN cells x ON x.indicator_id = p.a
                JOIN cells y ON y.indicator_id = p.b
                 AND x.geo_id = y.geo_id AND x.commodity_id = y.commodity_id
                 AND x.category = y.category
                 AND x.resolution = y.resolution AND x.period_start = y.period_start
                GROUP BY ALL
            )
            SELECT a, b, matches, overlap, distinct_values, scale
            FROM best JOIN overlapping USING (a, b)
            """
        ).fetchall()

    series = {
        row[0]: _Series(
            indicator_id=row[0],
            source_id=row[1],
            resolution=row[2],
            observations=int(row[3]),
            first=row[4],
            last=row[5],
        )
        for row in sizes
    }
    agreeing = {
        (a, b): _Pair(matches=int(m), overlap=int(o), distinct=int(d), scale=int(k))
        for a, b, m, o, d, k in pairs
        if o and m / o >= thresholds.min_match_share
    }
    return series, agreeing


def _components(pairs: Iterable[tuple[str, str]]) -> list[set[str]]:
    parent: dict[str, str] = {}

    def root(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        parent[root(a)] = root(b)
    groups: dict[str, set[str]] = defaultdict(set)
    for x in list(parent):
        groups[root(x)].add(x)
    return list(groups.values())


def find(
    resolver: StorageResolver,
    *,
    thresholds: Thresholds | None = None,
    names: dict[str, str] | None = None,
) -> list[dict]:
    """The series to delete, each naming the one kept in its place."""
    thresholds = thresholds or Thresholds()
    names = names or {}
    root = Path(resolver.resolve(Layer.SILVER, "observations"))
    if not any(root.rglob("*.parquet")):
        return []

    series, agreeing = _compare(resolver, thresholds)
    processed_at = datetime.now(UTC)
    found: list[dict] = []

    for component in _components(agreeing):
        remaining = sorted((series[x] for x in component if x in series), key=_Series.rank)
        while remaining:
            kept, *rest = remaining
            deleted = []
            for other in rest:
                pair = agreeing.get((kept.indicator_id, other.indicator_id))
                if pair is None:
                    continue
                coverage = pair.overlap / other.observations if other.observations else 0.0
                if coverage < thresholds.min_coverage:
                    continue
                deleted.append(other)
                found.append(
                    {
                        "indicator_id": other.indicator_id,
                        "source_id": other.source_id,
                        "name": names.get(other.indicator_id),
                        "superseded_by": kept.indicator_id,
                        "superseded_by_source_id": kept.source_id,
                        "superseded_by_name": names.get(kept.indicator_id),
                        "temporal_resolution": other.resolution,
                        "overlap": pair.overlap,
                        "matches": pair.matches,
                        "match_share": round(pair.share, 4),
                        # kept value = deleted value x 10^scale.
                        "scale": pair.scale,
                        "coverage": round(coverage, 4),
                        "observations": other.observations,
                        "superseded_by_observations": kept.observations,
                        "processed_at": processed_at,
                    }
                )
            gone = {kept.indicator_id, *(d.indicator_id for d in deleted)}
            remaining = [s for s in remaining if s.indicator_id not in gone]

    log.info("duplicates.found", series=len(found), compared=len(series), pairs=len(agreeing))
    return found


def carried_forward(previous: list[dict], found: list[dict], present: set[str]) -> list[dict]:
    """Earlier deletions still standing, merged with this sweep's.

    A series deleted by an earlier sweep is no longer in Silver, so this sweep
    cannot compare it — and dropping it from the table would let the next
    normalization write it straight back. It is kept while the series it gave
    way to is still present. Where that series has since been deleted in turn,
    the entry is pointed at whatever replaced it, so no row names a series
    that is gone.
    """
    fresh = {row["indicator_id"]: row for row in found}
    for row in previous:
        if row["indicator_id"] in fresh or row["indicator_id"] in present:
            continue
        fresh[row["indicator_id"]] = dict(row)

    for row in fresh.values():
        seen = {row["indicator_id"]}
        while (target := fresh.get(row["superseded_by"])) and target["indicator_id"] not in seen:
            seen.add(target["indicator_id"])
            row["superseded_by"] = target["superseded_by"]
            row["superseded_by_source_id"] = target["superseded_by_source_id"]
            row["superseded_by_name"] = target["superseded_by_name"]

    return [
        row
        for row in fresh.values()
        if row["superseded_by"] in present and row["superseded_by"] != row["indicator_id"]
    ]


def covered(winner: pa.Table | None, rows: list[dict], min_coverage: float) -> bool:
    """Whether the kept series still holds nearly every cell of these rows."""
    if winner is None or not rows:
        return False

    def cell(geo, commodity, category, resolution, start) -> tuple:
        return (geo, commodity or "", category or "", resolution, start)

    held = {
        cell(*values)
        for values in zip(
            winner.column("geo_id").to_pylist(),
            winner.column("commodity_id").to_pylist(),
            _column_or_nulls(winner, "category"),
            winner.column("temporal_resolution").to_pylist(),
            winner.column("period_start").to_pylist(),
            strict=True,
        )
    }
    cells = {
        cell(
            row.get("geo_id"),
            row.get("commodity_id"),
            row.get("category"),
            row.get("temporal_resolution"),
            row.get("period_start"),
        )
        for row in rows
        if row.get("value") is not None
    }
    if not cells:
        return False
    return len(cells & held) / len(cells) >= min_coverage


def _column_or_nulls(table: pa.Table, name: str) -> list:
    """A column's values, or nulls where the table predates the column."""
    if name in table.column_names:
        return table.column(name).to_pylist()
    return [None] * table.num_rows
