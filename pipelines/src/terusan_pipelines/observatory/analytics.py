"""The questions the observatory answers, as queries that refuse the wrong ones.

Each function returns rows, not charts, and each carries the rules of §7 of the
spec in its SQL rather than in a caption:

- **Comparisons stay within a comparability group.** A request is not an item,
  TikTok's request is not Google's, and a half-year is not a calendar year.
  `compare_countries` refuses a filter that spans groups instead of quietly
  charting them on one axis.
- **Rates only where the definitions allow.** `restriction_rates` divides only
  figures from the same report, period, country and requester, on a pair of
  metrics declared compatible for that provider. Anything else is N/A, never a
  computed number with a footnote.
- **Government and platform figures side by side, never subtracted.**
  `side_by_side` lays Komdigi's counts beside the platforms' and adds no
  column that is one minus the other.
- **Every row links to its evidence.** Each carries the source report id, and
  the report row carries the RAW object and its hash.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..storage import Layer
from ..warehouse import Warehouse
from . import model as m
from . import taxonomy as tx


class IncomparableSelection(ValueError):
    """A comparison whose figures do not share one definition."""


@dataclass(frozen=True, slots=True)
class RatePair:
    """A numerator and denominator one provider defines compatibly."""

    numerator: tx.Metric
    numerator_scope: tx.Scope
    denominator: tx.Metric
    label: str
    #: The tables both sides are read from, where a provider has several. The
    #: rows of one table must partition what they count for a sum over them to
    #: mean anything — Google's pre-2019 totals give "all requesters" beside
    #: courts and the executive, which a sum would count twice.
    tables: tuple[str, ...] = ()


#: Declared, not discovered. TikTok's actioned and received items are counted
#: on the same request set in the same report; Google's removed and requested
#: items are counted on the same rows. Komdigi publishes no denominator.
RATE_PAIRS: dict[str, tuple[RatePair, ...]] = {
    tx.Provider.TIKTOK: (
        RatePair(
            tx.Metric.ITEMS_ACTIONED,
            tx.Scope.LOCAL_LAW,
            tx.Metric.ITEMS_SPECIFIED,
            "content actioned for local law / content specified",
        ),
        RatePair(
            tx.Metric.ACCOUNTS_ACTIONED,
            tx.Scope.LOCAL_LAW,
            tx.Metric.ACCOUNTS_SPECIFIED,
            "accounts actioned for local law / accounts specified",
        ),
    ),
    tx.Provider.GOOGLE: (
        RatePair(
            tx.Metric.ITEMS_REMOVED,
            tx.Scope.LOCAL_LAW,
            tx.Metric.ITEMS_SPECIFIED,
            "items removed for legal reasons / items requested",
            tables=("google-government-removal-requests.csv",),
        ),
    ),
}

#: The as-published headline figures per provider: the ones a provider states
#: for a country as a whole. Google's 2019-onward rows are breakdowns, and a
#: sum of them is not a figure Google published, so they are not headlines.
_HEADLINE = """
    is_current
    AND (
        (provider = 'tiktok' AND country_code IS NOT NULL)
        OR (provider = 'google'
            AND source_table = 'historical/google-government-removal-requests-historical.csv')
        OR (provider = 'komdigi')
    )
"""


def _observations(w: Warehouse) -> str:
    return w.source(Layer.SILVER, m.OBSERVATIONS_TABLE)


def _rows(w: Warehouse, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    relation = w.query(sql, list(params) if params else None)
    names = relation.columns
    return [dict(zip(names, row, strict=True)) for row in relation.fetchall()]


def coverage(w: Warehouse, country: str = "ID") -> list[dict[str, Any]]:
    """Which provider reports which metric for which period, and how.

    Gaps are the point: a period with no row for a metric a provider reports
    elsewhere is a missing period, and a metric with only `not_available`
    rows is an unreported one.
    """
    return _rows(
        w,
        f"""
        SELECT provider, metric, period_start, period_end, temporal_resolution,
               count(*) FILTER (WHERE value_status = 'reported') AS reported,
               count(*) FILTER (WHERE value_status <> 'reported') AS withheld_or_unavailable,
               count(DISTINCT source_report_id) AS reports
        FROM {_observations(w)}
        WHERE is_current AND (country_code = ? OR country_code IS NULL)
        GROUP BY ALL
        ORDER BY provider, metric, period_start
        """,
        [country],
    )


def headline_series(
    w: Warehouse, country: str = "ID", metric: str = tx.Metric.REQUESTS_RECEIVED
) -> list[dict[str, Any]]:
    """A metric over time, per provider, as each provider published it."""
    return _rows(
        w,
        f"""
        SELECT provider, platform, requester_type, scope, period, period_start, period_end,
               CAST(metric_value AS VARCHAR) AS value, value_status, raw_value, unit,
               comparability_group, source_report_id, quality_flags
        FROM {_observations(w)}
        WHERE {_HEADLINE} AND country_code = ? AND metric = ?
        ORDER BY provider, comparability_group, period_start
        """,
        [country, metric],
    )


def compare_countries(
    w: Warehouse,
    *,
    provider: str,
    metric: str,
    countries: Sequence[str] = ("ID", *tx.COMPARATORS),
    scope: str | None = None,
    requester_type: str | None = None,
) -> list[dict[str, Any]]:
    """Indonesia beside its neighbours on one definition.

    Raises `IncomparableSelection` when the figures selected span more than one
    comparability group — two scopes, two requester types, two resolutions —
    and says which, so the caller narrows the selection rather than the chart
    mixing them.
    """
    filters = ["provider = ?", "metric = ?", f"country_code IN ({', '.join('?' * len(countries))})"]
    params: list[Any] = [provider, metric, *countries]
    if scope:
        filters.append("scope = ?")
        params.append(scope)
    if requester_type:
        filters.append("requester_type = ?")
        params.append(requester_type)
    where = f"{_HEADLINE} AND " + " AND ".join(filters)

    groups = _rows(
        w, f"SELECT DISTINCT comparability_group FROM {_observations(w)} WHERE {where}", params
    )
    if len(groups) > 1:
        raise IncomparableSelection(
            "the selection spans "
            + ", ".join(sorted(g["comparability_group"] for g in groups))
            + "; narrow it with a scope or requester type"
        )
    return _rows(
        w,
        f"""
        SELECT country_code, period, period_start, period_end,
               CAST(metric_value AS VARCHAR) AS value, value_status, raw_value, unit,
               comparability_group, source_report_id
        FROM {_observations(w)}
        WHERE {where}
        ORDER BY period_start, country_code
        """,
        params,
    )


def restriction_rates(w: Warehouse, country: str = "ID") -> list[dict[str, Any]]:
    """Declared rate pairs, computed only where both sides share a footing.

    Both sides must come from the same report, period, country and requester,
    and be reported figures. The ratio is of sums over the same rows, so
    Google's row breakdown divides cleanly; a pair with any side withheld is
    N/A rather than a ratio over what happens to be present.
    """
    out: list[dict[str, Any]] = []
    for provider, pairs in RATE_PAIRS.items():
        for pair in pairs:
            out.extend(
                _rows(
                    w,
                    f"""
                    WITH obs AS (
                        SELECT * FROM {_observations(w)}
                        WHERE is_current AND provider = ? AND country_code = ?
                          AND (len(?::VARCHAR[]) = 0 OR list_contains(?::VARCHAR[], source_table))
                    ),
                    num AS (
                        SELECT source_report_id, source_table, period_start, period_end,
                               requester_type, coalesce(product, '') AS product,
                               coalesce(reason_category_raw, '') AS reason,
                               metric_value AS n, value_status AS ns, source_row_locator AS loc
                        FROM obs WHERE metric = ? AND scope = ?
                    ),
                    den AS (
                        SELECT source_report_id, source_table, period_start, period_end,
                               requester_type, coalesce(product, '') AS product,
                               coalesce(reason_category_raw, '') AS reason,
                               metric_value AS d, value_status AS ds, source_row_locator AS loc
                        FROM obs WHERE metric = ?
                    ),
                    paired AS (
                        SELECT den.source_report_id, den.source_table, den.period_start,
                               den.period_end, num.n, num.ns, den.d, den.ds
                        FROM den LEFT JOIN num
                          ON num.source_report_id = den.source_report_id
                         AND num.source_table = den.source_table
                         AND num.period_start = den.period_start
                         AND num.period_end = den.period_end
                         AND num.requester_type = den.requester_type
                         AND num.product = den.product
                         AND num.reason = den.reason
                         AND (num.loc = den.loc OR ? = 'tiktok')
                    )
                    SELECT ? AS provider, ? AS rate, source_report_id, source_table,
                           period_start, period_end,
                           CAST(sum(n) AS VARCHAR) AS numerator,
                           CAST(sum(d) AS VARCHAR) AS denominator,
                           CASE
                             WHEN bool_or(n IS NULL OR ns <> 'reported' OR ds <> 'reported')
                               THEN 'N/A'
                             WHEN sum(d) = 0 THEN 'N/A'
                             ELSE CAST(round(sum(n) / sum(d), 4) AS VARCHAR)
                           END AS value
                    FROM paired
                    GROUP BY source_report_id, source_table, period_start, period_end
                    ORDER BY period_start, source_table
                    """,
                    [
                        provider,
                        country,
                        list(pair.tables),
                        list(pair.tables),
                        pair.numerator,
                        pair.numerator_scope,
                        pair.denominator,
                        provider,
                        provider,
                        pair.label,
                    ],
                )
            )
    return out


def side_by_side(w: Warehouse, country: str = "ID") -> list[dict[str, Any]]:
    """Komdigi's figures beside the platforms', aligned by calendar year.

    Platforms report half-years; Komdigi reports years. A platform's year is
    shown only where both its half-years are reported, and as the two halves
    added — the one alignment the definitions allow. No difference is
    computed: the counts measure different things (methodology.md).
    """
    return _rows(
        w,
        f"""
        WITH platform_years AS (
            SELECT provider, platform, metric, scope, year(period_start) AS year,
                   CASE WHEN count(*) = 2 AND bool_and(value_status = 'reported')
                        THEN CAST(sum(metric_value) AS VARCHAR) END AS value,
                   count(*) AS halves, list(source_report_id) AS source_report_ids
            FROM {_observations(w)}
            WHERE {_HEADLINE} AND provider <> 'komdigi' AND country_code = ?
              AND temporal_resolution = 'semiannual' AND unit = 'count'
              AND requester_type IN ('government', 'all')
            GROUP BY ALL
        ),
        komdigi_years AS (
            SELECT provider, platform, metric, scope, year(period_start) AS year,
                   CAST(metric_value AS VARCHAR) AS value, 1 AS halves,
                   [source_report_id] AS source_report_ids
            FROM {_observations(w)}
            WHERE is_current AND provider = 'komdigi' AND temporal_resolution = 'annual'
        )
        SELECT *, 'not comparable: different units and definitions' AS comparability
        FROM (SELECT * FROM platform_years UNION ALL SELECT * FROM komdigi_years)
        ORDER BY year, provider, platform, metric
        """,
        [country],
    )
