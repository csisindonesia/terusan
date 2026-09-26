"""Scoring the machine coder against the people it is imitating.

VEWS publishes a human-coded record of collective violence. This crawls the
same months the humans coded, codes what it finds, and asks three questions of
the result:

- **Recall.** Of the incidents a human coded, how many did the crawl find? This
  is the number that matters most and the one no amount of inspecting the
  output can tell you: a crawl cannot report what it never saw.
- **Precision.** Of the events the machine produced, how many match a human
  incident? A machine event with no human match is not automatically wrong —
  the human coders miss things too, which is part of why this exists — but a
  low figure means the gate is admitting crime reporting that is not collective
  violence.
- **Agreement.** Where both coded the same incident, how often did they choose
  the same category? Reported per field, because a coder is not equally good at
  every question and a single score would hide which one to fix.

**Reach is reported separately from recall**, and the distinction is the whole
point. An incident missed because no outlet in the list covered that district,
or because the outlet's archive no longer serves a month sixteen months back,
is a missed incident — but it is not a failure of the lexicon or the
classifier, and treating the two as one number makes the result unactionable.
So the report says how many articles each outlet yielded for the period, and a
province where the crawl found nothing at all is called out rather than
averaged in.

Matching a machine event to a human incident uses the same rule that clusters
reports into events: same district, within a day, same primary form. Actors are
not required, because the human record names actors far more specifically than
a headline does.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import structlog

from ..storage import Layer
from ..warehouse.query import warehouse
from .cluster import DATE_TOLERANCE_DAYS, _as_date, cluster, read_codings
from .codes import ENUM_MISSING, INCIDENTS_DATASET, canonical
from .profiles import profile as get_profile

log = structlog.get_logger(__name__)

#: Fields compared where machine and human coded the same incident.
COMPARED = (
    "province",
    "district_city",
    "violence_form1",
    "weapon_type1",
    "issue_type1",
    "actor1a_t",
    "actor2a_t",
    "intervene",
)


@dataclass(slots=True)
class Score:
    """What one validation run found."""

    period: str
    human_incidents: int = 0
    machine_events: int = 0
    matched: int = 0

    #: Field to (agreed, compared).
    agreement: dict[str, tuple[int, int]] = field(default_factory=dict)

    #: Outlet host to how many articles the crawl got from it in the period.
    reach: dict[str, int] = field(default_factory=dict)

    #: Provinces where a human coded incidents and the crawl found no article.
    unreached_provinces: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.matched / self.human_incidents if self.human_incidents else 0.0

    @property
    def precision(self) -> float:
        return self.matched / self.machine_events if self.machine_events else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "human_incidents": self.human_incidents,
            "machine_events": self.machine_events,
            "matched": self.matched,
            "recall": round(self.recall, 3),
            "precision": round(self.precision, 3),
            "agreement": {
                name: {"agreed": agreed, "compared": compared,
                       "rate": round(agreed / compared, 3) if compared else None}
                for name, (agreed, compared) in sorted(self.agreement.items())
            },
            "outlets_with_articles": len([n for n in self.reach.values() if n]),
            "articles_by_outlet": dict(sorted(self.reach.items(), key=lambda kv: -kv[1])),
            "unreached_provinces": self.unreached_provinces,
        }


def _human_incidents(period: str) -> list[dict[str, str]]:
    """The human-coded incidents for one month, `YYYY-MM`."""
    year, month = period.split("-")
    with warehouse() as house:
        expression = house.source(Layer.BRONZE, "records")
        rows = house.query(
            f"SELECT columns FROM {expression} WHERE dataset = '{INCIDENTS_DATASET}' "
            f"AND try_cast(replace(trim(columns['year']), '.0', '') AS INTEGER) = {int(year)} "
            f"AND try_cast(replace(trim(columns['month']), '.0', '') AS INTEGER) = {int(month)}"
        ).fetchall()
    return [dict(row[0]) for row in rows]


def _human_date(row: dict[str, str]) -> date | None:
    """A human incident's date, which the sheet writes as `DD/MM/YY`."""
    raw = str(row.get("date") or "").strip()
    for separator in ("/", "-"):
        parts = raw.split(separator)
        if len(parts) == 3:
            try:
                day, month, year = (int(part) for part in parts)
            except ValueError:
                continue
            year = year + 2000 if year < 100 else year
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return _as_date(raw)


def _district(row: dict[str, str]) -> str:
    """A comparable district name, however either side spelled it."""
    value = str(row.get("district_city") or "").upper()
    for prefix in ("KABUPATEN ", "KAB. ", "KAB ", "KOTA "):
        value = value.removeprefix(prefix)
    return value.strip()


def _matches(machine: dict[str, str], human: dict[str, str]) -> bool:
    if _district(machine) != _district(human) or not _district(machine):
        return False
    left, right = _as_date(machine.get("date")), _human_date(human)
    if left is None or right is None:
        return False
    if abs((left - right).days) > DATE_TOLERANCE_DAYS:
        return False
    return canonical(str(machine.get("violence_form1") or "")) == canonical(
        str(human.get("violence_form1") or "")
    )


def _reach(period: str) -> dict[str, int]:
    """How many articles the crawl has from each outlet for a period."""
    with warehouse() as house:
        expression = house.source(Layer.BRONZE, "records")
        rows = house.query(
            f"SELECT columns['outlet_host'] AS host, count(*) AS n FROM {expression} "
            f"WHERE dataset = 'news-articles' AND published_at IS NOT NULL "
            f"AND strftime(published_at, '%Y-%m') = '{period}' GROUP BY 1"
        ).fetchall()
    return {str(host): int(n) for host, n in rows if host}


def validate(period: str, issue_slug: str = "violence") -> Score:
    """Score a month of machine coding against the human record."""
    issue = get_profile(issue_slug)
    year, month = (int(part) for part in period.split("-"))
    first = date(year, month, 1)
    last = date(year + (month == 12), (month % 12) + 1, 1)

    humans = _human_incidents(period)
    events = cluster(read_codings(issue, since=first, until=last))
    score = Score(period=period, human_incidents=len(humans), machine_events=len(events))
    score.reach = _reach(period)

    agreement: dict[str, list[int]] = {name: [0, 0] for name in COMPARED}
    unmatched_humans = list(humans)
    for event in events:
        for human in unmatched_humans:
            if not _matches(event.row, human):
                continue
            score.matched += 1
            unmatched_humans.remove(human)
            for name in COMPARED:
                left = canonical(str(event.row.get(name) or "")).upper()
                right = canonical(str(human.get(name) or "")).upper()
                if not left or not right or ENUM_MISSING in (left, right):
                    continue
                agreement[name][1] += 1
                if left == right or left in right or right in left:
                    agreement[name][0] += 1
            break
    score.agreement = {name: (agreed, compared) for name, (agreed, compared) in agreement.items()}

    by_province: dict[str, int] = defaultdict(int)
    for human in humans:
        by_province[str(human.get("province") or "").upper()] += 1
    found = {str(event.row.get("province") or "").upper() for event in events}
    score.unreached_provinces = sorted(
        province for province in by_province if province and province not in found
    )

    log.info(
        "news.validate.done",
        period=period,
        recall=round(score.recall, 3),
        precision=round(score.precision, 3),
        human=score.human_incidents,
        machine=score.machine_events,
    )
    return score
