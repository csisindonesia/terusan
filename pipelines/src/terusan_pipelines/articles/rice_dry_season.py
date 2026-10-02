"""Rice After the Dry Season: the 2023 El Niño and the price of rice, by province.

Two Silver series, joined on the province:

- ERA5-Land monthly precipitation, summed over the June–October dry season and
  compared with the same sum's 1991–2020 mean;
- PIHPS daily prices for medium-grade rice in traditional markets, averaged to
  a month per province.

The question is whether the provinces that dried out most saw rice rise most,
and whether the dry season explains how far rice rose nationally. It answers
the first yes and the second no, because 2019 was drier than 2023 and rice
barely moved. The article is written around that, so `compute` returns the
2019 comparison beside the 2023 figures.

The reads are kept apart from the arithmetic: `build` queries the lake and
`compute` is a pure function of the rows, which is what the tests exercise.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from ..storage import Layer
from ..warehouse.query import warehouse

SLUG = "rice-after-the-dry-season"

RAIN_INDICATOR = "j8pe1oe1"  # Monthly precipitation (ERA5-Land), mm
PRICE_INDICATOR = "6yugbox8"  # Food price, traditional market, IDR/kg
RICE = "rice-medium-grade-1"  # Beras Kualitas Medium I
CHILI = "red-chili-curly"  # Cabai Merah Keriting
DATASETS = ("uv7fqbgj", "nbrxezkf")

SEASON = (6, 10)  # June to October, inclusive
BASELINE = (1991, 2020)
PRICES_FROM = "2018-01"

#: Java, Bali and Nusa Tenggara: the provinces where the 2023 deficit was
#: deepest, and the rice bowl. Named so the article can test whether the
#: pattern survives without them.
CORE = frozenset({"ID-31", "ID-32", "ID-33", "ID-34", "ID-35", "ID-36", "ID-51", "ID-52", "ID-53"})

#: Provinces drier than this, against normal, are "the dry half" of the split.
DRY_THRESHOLD = -30.0


def _excluded(geo_id: str) -> bool:
    """The national row, and Papua and the provinces formed from it in 2022.

    The price data still reports Papua as the old provinces while the rainfall
    series uses the new boundaries, so the two cannot be matched there.
    """
    return geo_id == "IDN" or geo_id.startswith("ID-9")


def _change(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or before == 0:
        return None
    return (after / before - 1) * 100


def _ranks(values: list[float]) -> list[float]:
    """Ranks with ties given their mean rank, as Spearman's rho needs."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for position in range(start, end + 1):
            ranks[order[position]] = (start + end) / 2
        start = end + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    # Undefined where either side does not vary: there is nothing to rank.
    if len(xs) < 3 or len(xs) != len(ys) or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    return statistics.correlation(_ranks(xs), _ranks(ys))


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def compute(
    rain: list[tuple[str, int, int, float]],
    prices: list[tuple[str, str, str, float]],
    names: dict[str, str],
    missing_share: float,
    latest_day: str,
) -> dict[str, Any]:
    """The article's figures from the rows.

    `rain` is (geo_id, year, month, mm); `prices` is (commodity, geo_id,
    "YYYY-MM", mean price that month).
    """
    # Dry-season totals, only for years where all five months are present: a
    # season missing a month would read as a drought.
    months: dict[tuple[str, int], list[float]] = defaultdict(list)
    for geo_id, year, month, mm in rain:
        if SEASON[0] <= month <= SEASON[1]:
            months[(geo_id, year)].append(mm)
    season = {key: sum(v) for key, v in months.items() if len(v) == SEASON[1] - SEASON[0] + 1}
    normal: dict[str, float] = {}
    for geo_id in {g for g, _ in season}:
        base = [
            mm for (g, y), mm in season.items() if g == geo_id and BASELINE[0] <= y <= BASELINE[1]
        ]
        if base:
            normal[geo_id] = statistics.fmean(base)

    def anomaly(geo_id: str, year: int) -> float | None:
        if geo_id not in normal or (geo_id, year) not in season:
            return None
        return (season[(geo_id, year)] / normal[geo_id] - 1) * 100

    price: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for commodity, geo_id, period, value in prices:
        price[commodity][geo_id][period] = value

    rice = price[RICE]

    # National line: every province counts once, however many markets it has.
    national = []
    periods = sorted({p for geo_id, series in rice.items() if geo_id != "IDN" for p in series})
    for period in periods:
        if period < PRICES_FROM:
            continue
        values = [s[period] for g, s in rice.items() if g != "IDN" and period in s]
        national.append(
            {
                "period": period,
                "mean": round(statistics.fmean(values)),
                "low": round(min(values)),
                "high": round(max(values)),
                "provinces": len(values),
            }
        )
    by_period = {row["period"]: row["mean"] for row in national}

    windows = []
    for year in range(int(PRICES_FROM[:4]), 2100):
        start, end = f"{year}-07", f"{year + 1}-03"
        if start not in by_period or end not in by_period:
            if start not in by_period:
                break
            continue
        windows.append({"year": year, "change": _round(_change(by_period[start], by_period[end]))})

    def cross_section(year: int) -> list[dict[str, Any]]:
        rows = []
        for geo_id, series in sorted(rice.items()):
            if _excluded(geo_id):
                continue
            rain_change = anomaly(geo_id, year)
            rice_change = _change(series.get(f"{year}-07"), series.get(f"{year + 1}-03"))
            if rain_change is None or rice_change is None:
                continue
            chili = price[CHILI].get(geo_id, {})
            rows.append(
                {
                    "geo_id": geo_id,
                    "name": names.get(geo_id, geo_id),
                    "core": geo_id in CORE,
                    "rain_normal": round(normal[geo_id]),
                    "rain": round(season[(geo_id, year)]),
                    "rain_change": rain_change,
                    "rice_before": round(series[f"{year}-07"]),
                    "rice_after": round(series[f"{year + 1}-03"]),
                    "rice_change": rice_change,
                    "chili_change": _change(chili.get(f"{year}-07"), chili.get(f"{year}-10")),
                }
            )
        return rows

    def summary(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not rows:
            return None
        rain_changes = [r["rain_change"] for r in rows]
        rice_changes = [r["rice_change"] for r in rows]
        outside = [r for r in rows if not r["core"]]
        chili = [r for r in rows if r["chili_change"] is not None]
        dry = [r["rice_change"] for r in rows if r["rain_change"] < DRY_THRESHOLD]
        rest = [r["rice_change"] for r in rows if r["rain_change"] >= DRY_THRESHOLD]
        return {
            "provinces": len(rows),
            "rain_median": _round(statistics.median(rain_changes)),
            "drier": sum(1 for v in rain_changes if v < 0),
            "rice_median": _round(statistics.median(rice_changes)),
            "rho": _round(spearman(rain_changes, rice_changes), 2),
            "rho_outside_core": _round(
                spearman([r["rain_change"] for r in outside], [r["rice_change"] for r in outside]),
                2,
            ),
            "outside_core": len(outside),
            "rho_chili": _round(
                spearman([r["rain_change"] for r in chili], [r["chili_change"] for r in chili]), 2
            ),
            "dry_count": len(dry),
            "dry_rice_median": _round(statistics.median(dry)) if dry else None,
            "rest_count": len(rest),
            "rest_rice_median": _round(statistics.median(rest)) if rest else None,
        }

    y2023, y2019 = cross_section(2023), cross_section(2019)

    # How the core region's season ranks among every year of the series.
    core_totals: dict[int, float] = defaultdict(float)
    core_counts: dict[int, int] = defaultdict(int)
    for (geo_id, year), mm in season.items():
        if geo_id in CORE:
            core_totals[year] += mm
            core_counts[year] += 1
    complete = sorted(y for y, n in core_counts.items() if n == len(CORE))
    driest = sorted(complete, key=lambda y: core_totals[y])

    latest = national[-1]
    return {
        "slug": SLUG,
        "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "datasets": list(DATASETS),
        "latest_day": latest_day,
        "missing_share": round(missing_share * 100, 1),
        "national": national,
        "windows": windows,
        "provinces": [
            {
                **r,
                "rain_change": _round(r["rain_change"]),
                "rice_change": _round(r["rice_change"]),
                "chili_change": _round(r["chili_change"]),
            }
            for r in y2023
        ],
        "y2023": summary(y2023),
        "y2019": summary(y2019),
        "core_rank_2023": driest.index(2023) + 1 if 2023 in driest else None,
        "core_rank_2019": driest.index(2019) + 1 if 2019 in driest else None,
        "core_years": len(driest),
        "core_first_year": complete[0] if complete else None,
        "driest_year": driest[0] if driest else None,
        "rice_jul_2023": by_period.get("2023-07"),
        "rice_mar_2024": by_period.get("2024-03"),
        "rice_change": _round(_change(by_period.get("2023-07"), by_period.get("2024-03"))),
        "latest_period": latest["period"],
        "latest_mean": latest["mean"],
        "latest_change": _round(_change(by_period.get("2023-07"), latest["mean"]), 0),
    }


def build() -> dict[str, Any]:
    """Read the two series from the lake and compute the article's figures."""
    with warehouse() as house:
        obs = house.source(Layer.SILVER, "observations")
        geo = house.source(Layer.SILVER, "geography")
        names = dict(house.query(f"SELECT geo_id, name FROM {geo}").fetchall())
        rain = house.query(
            f"SELECT geo_id, year(period_start), month(period_start), CAST(value AS DOUBLE) "
            f"FROM {obs} WHERE indicator_id = ? AND value IS NOT NULL",
            [RAIN_INDICATOR],
        ).fetchall()
        # Missing reports are left out rather than read as zero; the share
        # left out is reported in the article.
        prices = house.query(
            f"SELECT commodity_id, geo_id, strftime(period_start, '%Y-%m'), "
            f"avg(CAST(value AS DOUBLE)) FROM {obs} "
            f"WHERE indicator_id = ? AND commodity_id IN (?, ?) AND value IS NOT NULL "
            f"GROUP BY ALL",
            [PRICE_INDICATOR, RICE, CHILI],
        ).fetchall()
        missing, total, latest = house.query(
            f"SELECT count(*) FILTER (WHERE value IS NULL), count(*), "
            f"CAST(max(period_start) AS VARCHAR) FROM {obs} "
            f"WHERE indicator_id = ? AND commodity_id = ?",
            [PRICE_INDICATOR, RICE],
        ).fetchone()
    return compute(rain, prices, names, missing / total if total else 0.0, latest)
