"""Collapsing several papers' reports into the incidents they describe.

Five titles reporting one brawl produce five codings. Counted as they stand
they are five incidents, and a province with two attentive newspapers looks
twice as violent as its neighbour — which is a fact about the press, not about
the violence.

So the codings are clustered. Two of them describe the same incident when they
agree on the district, fall within a day of each other, name the same primary
form of violence, and share an actor. That is the rule the human coders use
when they cross-reference, and it is applied here rather than adapted, because
the point of this dataset is to be comparable with theirs.

**Recomputed, never merged.** There is no crawl state to merge into: clustering
reads the whole window out of Bronze and writes the events fresh every time.
That makes it a pure function of the codings — a re-run after a corrected
coding corrects the events, and running it twice changes nothing. An event's id
is the hash of what defines the cluster, so it survives a recompute unless the
cluster's own membership changes, which is the one case where it should.

**Counting.** The events are then counted per province and month into the same
shape VEWS's own figures land in, so the machine series and the human series
are read by one normalizer and charted on one axis.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import structlog

from ..extract.base import PARSER_VERSION
from ..storage import Layer, StorageConfig, StorageResolver
from ..warehouse.query import warehouse
from ..warehouse.schema import BRONZE_RECORDS, table_from_rows
from ..warehouse.writer import ParquetWriter
from .codes import ENUM_MISSING
from .profiles import Profile
from .profiles import profile as get_profile
from .profiles.violence import ESCALATION_ORDER

log = structlog.get_logger(__name__)

#: Reports of one incident do not carry the same date: a paper filing the
#: morning after writes tomorrow's date on yesterday's brawl.
DATE_TOLERANCE_DAYS = 1

#: A missing casualty figure, as VEWS writes it.
NUM_MISSING = -99

#: Written on every part file this step produces, so a recompute overwrites the
#: previous one instead of appending a second copy of the same events.
RUN_STAMP = "clustered"

#: The indicators the counts publish, and what each one sums.
SERIES: tuple[tuple[str, str, str, str], ...] = (
    (
        "news_violence_incidents",
        "Collective violence incidents reported in the press",
        "incidents",
        "count",
    ),
    (
        "news_violence_deaths",
        "Deaths in press-reported collective violence",
        "people",
        "num_death",
    ),
    (
        "news_violence_injured",
        "Injuries in press-reported collective violence",
        "people",
        "num_injured",
    ),
    (
        "news_violence_fem_death",
        "Female deaths in press-reported collective violence",
        "people",
        "fem_death",
    ),
    (
        "news_violence_child_death",
        "Child deaths in press-reported collective violence",
        "people",
        "child_death",
    ),
    (
        "news_violence_infra_damage",
        "Structures damaged in press-reported collective violence",
        "structures",
        "infra_damage",
    ),
    (
        "news_violence_intervened",
        "Press-reported incidents where someone intervened",
        "incidents",
        "intervene",
    ),
)


@dataclass(slots=True)
class Event:
    """One incident, and every report of it."""

    event_id: str
    row: dict[str, str]
    reports: list[dict[str, str]] = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        return [report.get("url", "") for report in self.reports if report.get("url")]

    @property
    def outlets(self) -> list[str]:
        return sorted({report.get("outlet_host", "") for report in self.reports} - {""})


def _as_int(value: Any) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return NUM_MISSING


def _as_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _actors(row: dict[str, str]) -> set[str]:
    """The named sides of an incident, ignoring the ones nobody could name."""
    keys = ("actor1a", "actor1b", "actor2a", "actor2b")
    return {
        str(row.get(key, "")).upper()
        for key in keys
        if row.get(key) and str(row[key]).upper() != ENUM_MISSING
    }


def same_incident(left: dict[str, str], right: dict[str, str]) -> bool:
    """Whether two codings describe one incident."""
    district = left.get("district_city_id") or left.get("district_city")
    if not district or district != (right.get("district_city_id") or right.get("district_city")):
        return False
    first, second = _as_date(left.get("date")), _as_date(right.get("date"))
    if first is None or second is None:
        return False
    if abs((first - second).days) > DATE_TOLERANCE_DAYS:
        return False
    if left.get("violence_form1") != right.get("violence_form1"):
        return False
    shared = _actors(left) & _actors(right)
    # An incident where neither report could name a side still clusters on
    # district, date and form: the alternative is one event per paper, which is
    # the miscount this whole step exists to prevent.
    return bool(shared) or not (_actors(left) and _actors(right))


def _event_id(row: dict[str, str]) -> str:
    """A stable name for a cluster, derived from what makes it one.

    Deterministic so a recompute produces the same ids. It changes when the
    cluster's defining facts change, which is correct: that is a different
    incident, not the same one renamed.
    """
    key = "|".join(
        [
            str(row.get("district_city_id") or row.get("district_city") or ""),
            str(row.get("date") or ""),
            str(row.get("violence_form1") or ""),
            ",".join(sorted(_actors(row))),
        ]
    )
    return "nve_" + hashlib.sha256(key.encode()).hexdigest()[:16]


#: Fields whose values are ordered, and the order. A report of one incident
#: filed while a crowd was gathering and another filed after the market burned
#: are both true; the event is the worse of them.
ORDINAL: dict[str, tuple[str, ...]] = {"escalation": ESCALATION_ORDER}


def _rank(value: str, order: tuple[str, ...]) -> int:
    """Where a label sits in its order; unknown labels sit at the bottom."""
    try:
        return order.index(value.upper())
    except ValueError:
        return -1


def _merge(reports: list[dict[str, str]]) -> dict[str, str]:
    """One event row from several reports of it.

    Where reports disagree the fuller answer wins: a figure beats a missing
    marker, a named category beats `TIDAK JELAS`. Papers report an incident at
    different stages and with different detail, and taking the first report
    would systematically undercount a casualty that was confirmed later.
    """
    merged: dict[str, str] = {}
    keys = {key for report in reports for key in report}
    numeric = {
        "num_death",
        "num_injured",
        "death_injured",
        "fem_death",
        "fem_injured",
        "child_death",
        "child_injured",
        "infra_damage",
        "infra_destroyed",
        "actor1_tot",
        "actor2_tot",
    }
    for key in keys:
        values = [report.get(key, "") for report in reports if report.get(key, "") != ""]
        if not values:
            merged[key] = ""
            continue
        if key in numeric:
            numbers = [n for n in (_as_int(v) for v in values) if n != NUM_MISSING]
            merged[key] = str(max(numbers) if numbers else NUM_MISSING)
            continue
        if key in ORDINAL:
            # Ranked rather than named: the fullest answer for an ordered
            # field is the furthest along it, not the first one written down.
            ranks = ORDINAL[key]
            merged[key] = str(max(values, key=lambda v: _rank(str(v), ranks)))
            continue
        named = [v for v in values if str(v).upper() != ENUM_MISSING]
        merged[key] = str(named[0] if named else values[0])
    return merged


def cluster(codings: Iterable[dict[str, str]]) -> list[Event]:
    """Group codings into events.

    Grouped within a district first, so the pairwise comparison is over the
    handful of reports from one place rather than over the whole window.
    """
    buckets: dict[str, list[dict[str, str]]] = defaultdict(list)
    for coding in codings:
        district = coding.get("district_city_id") or coding.get("district_city") or ""
        buckets[str(district)].append(coding)

    events: list[Event] = []
    for reports in buckets.values():
        open_clusters: list[list[dict[str, str]]] = []
        for report in sorted(reports, key=lambda r: str(r.get("date") or "")):
            for members in open_clusters:
                if same_incident(members[0], report):
                    members.append(report)
                    break
            else:
                open_clusters.append([report])
        for members in open_clusters:
            row = _merge(members)
            events.append(Event(event_id=_event_id(row), row=row, reports=members))
    return events


def read_codings(
    issue: Profile, since: date | None = None, until: date | None = None
) -> list[dict[str, str]]:
    """Every accepted coding in the window, from Bronze.

    At the current parser version only, and the newest one per article within
    it. That qualifier is the whole correctness of this step. Nothing here is
    ever edited in place: re-extracting under a new parser appends rows, and so
    does `news.recode` when a corrected parser re-reads a figure. It is what
    makes a Bronze partition traceable to the code that produced it — and it
    means that without this filter an article coded three times contributes
    three codings, and the oldest, most wrong ones outvote the correction in
    the merge. The same rule the API's corpus query applies, and the Silver
    normalizer, for the same reason.
    """
    with warehouse() as house:
        expression = house.source(Layer.BRONZE, "records")
        rows = house.query(
            "SELECT columns FROM (SELECT columns, row_number() OVER ("
            "PARTITION BY columns['url'] ORDER BY processed_at DESC) AS pick "
            f"FROM {expression} "
            f"WHERE dataset = '{issue.codings_dataset}' AND columns['accepted'] = 'true' "
            f"AND parser_version = '{PARSER_VERSION}') WHERE pick = 1"
        ).fetchall()
    codings = [dict(row[0]) for row in rows]
    if since or until:
        codings = [
            coding
            for coding in codings
            if (when := _as_date(coding.get("date"))) is not None
            and (since is None or when >= since)
            and (until is None or when <= until)
        ]
    return codings


#: This step's own version, on every row it writes as the *pipeline* version.
#: Bumped when the match rule or the merge changes, because both change what an
#: event is.
#:
#: The parser version on these rows is extraction's, not this one's, and that
#: is deliberate: everything downstream reads Bronze at the current parser
#: version — it is how re-extracting under a new parser avoids doubling every
#: observation — so a derived collection that stamped a version of its own
#: would be invisible to the normalizer that turns its counts into figures.
CLUSTER_VERSION = "1"


def _provenance(document_id: str, content_hash: str) -> dict[str, Any]:
    """Provenance for a row that no single document produced.

    An event is a claim about several articles, so there is no one `raw_path`
    to point at and saying otherwise would be a lie in the column that exists
    to be trusted. The document id is the event's own, the hash is of what
    defines it, and the articles behind it are listed on the row itself.
    """
    now = datetime.now(UTC)
    return {
        "document_id": document_id,
        "source_id": "news-monitoring",
        "source_type": "news",
        "source_url": None,
        "content_hash": content_hash,
        "raw_path": "",
        "original_filename": None,
        "media_type": None,
        "published_at": None,
        "retrieved_at": None,
        "processed_at": now,
        "parser_version": PARSER_VERSION,
        "pipeline_version": f"news-cluster-{CLUSTER_VERSION}",
    }


def _event_rows(events: list[Event], issue: Profile) -> list[dict[str, Any]]:
    rows = []
    for index, event in enumerate(events, start=1):
        columns = dict(event.row)
        columns.update(
            {
                "event_id": event.event_id,
                "profile": issue.slug,
                "report_count": str(len(event.reports)),
                "outlets": json.dumps(event.outlets),
                "sources": json.dumps(event.sources),
                "machine_coded": "1",
            }
        )
        rows.append(
            _provenance(event.event_id, event.event_id.removeprefix("nve_"))
            | {
                "dataset": issue.dataset,
                "row_number": index,
                "columns": {k: ("" if v is None else str(v)) for k, v in columns.items()},
            }
        )
    return rows


def _count_rows(events: list[Event], issue: Profile) -> list[dict[str, Any]]:
    """The events counted per province and month, plus a national total.

    Blank contributes zero to a sum and `-99` contributes nothing, exactly as
    the human record is counted. Getting that backwards would either invent
    casualties or make a quiet province indistinguishable from an unreported
    one.
    """
    totals: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for event in events:
        when = _as_date(event.row.get("date"))
        if when is None:
            continue
        period = when.strftime("%Y-%m")
        province = str(event.row.get("province") or "").strip()
        scopes = (("national", "Indonesia", "country"), ("province", province, "province"))
        for scope, name, level in scopes:
            if scope == "province" and not name:
                continue
            bucket = totals[(period, name, level)]
            bucket["count"] += 1
            if str(event.row.get("intervene", "")).upper() == "IYA":
                bucket["intervene"] += 1
            for column in ("num_death", "num_injured", "fem_death", "child_death", "infra_damage"):
                value = _as_int(event.row.get(column))
                if value != NUM_MISSING:
                    bucket[column] += value

    rows = []
    index = 0
    for (period, name, level), bucket in sorted(totals.items()):
        for indicator, series_name, unit, measure in SERIES:
            index += 1
            key = hashlib.sha256(f"{period}|{name}|{indicator}".encode()).hexdigest()[:16]
            rows.append(
                _provenance(f"nvc_{key}", key)
                | {
                    "dataset": f"{issue.dataset[: -len('-events')]}-counts",
                    "row_number": index,
                    "columns": {
                        "indicator": indicator,
                        "series_name": series_name,
                        "geo": name if level == "country" else f"Provinsi {name}",
                        "geo_level": level,
                        "province": "" if level == "country" else name,
                        "period": period,
                        "value": str(bucket.get(measure, 0)),
                        "unit": unit,
                        "measure": measure,
                        "publisher": "Terusan news monitoring",
                    },
                }
            )
    return rows


#: What the crawl read, as series. Kept apart from the incident series above
#: because their geography means a different thing, and a chart that put them
#: on one axis without saying so would be lying quietly.
#:
#: An incident is filed where it happened, read out of the article. A tally is
#: filed where the *newspaper* is — that is the only place it can be filed,
#: because the articles it counts were never stored and most of them were about
#: nothing in particular. So `news_articles_read` for Jakarta means "articles
#: read from Jakarta's two papers", not "articles about Jakarta".
READING_SERIES: tuple[tuple[str, str, str, str], ...] = (
    (
        "news_articles_read",
        "Articles read from the press, by the province of the newspaper",
        "articles",
        "scanned",
    ),
    (
        "news_articles_recorded",
        "Articles kept as collective violence, by the province of the newspaper",
        "articles",
        "recorded",
    ),
)

#: The Bronze collection holding what each outlet published, by day.
TALLIES_DATASET = "news-daily-tallies"


def read_tallies(since: date | None = None, until: date | None = None) -> list[dict[str, str]]:
    """Every daily tally in the window, from Bronze."""
    with warehouse() as house:
        expression = house.source(Layer.BRONZE, "records")
        try:
            rows = house.query(
                f"SELECT columns FROM {expression} WHERE dataset = '{TALLIES_DATASET}' "
                f"AND parser_version = '{PARSER_VERSION}'"
            ).fetchall()
        except Exception as error:  # noqa: BLE001 - an absent collection is a normal state
            log.warning("news.tallies.unreadable", error=str(error)[:200])
            return []
    tallies = [dict(row[0]) for row in rows]
    if since or until:
        tallies = [
            tally
            for tally in tallies
            if (when := _as_date(tally.get("date"))) is not None
            and (since is None or when >= since)
            and (until is None or when <= until)
        ]
    return tallies


def _reading_rows(tallies: list[dict[str, str]], issue: Profile) -> list[dict[str, Any]]:
    """The tallies summed per province and month, plus a national total.

    Reduced to one tally per outlet and day first, and the reduction is a
    maximum rather than the most recent. Both halves matter.

    A tally is landed per run, and an outlet read twice in a day — two shards,
    or a manual run beside the scheduled one — lands two of them. Summed as
    they stand, a day somebody re-ran would report the paper publishing twice
    as much.

    Taking the most recent instead would undercount, because the two runs do
    not see the same thing. An article that was kept is not read again: its
    landing directory exists, so the next run skips it before fetching. An
    article that was only counted has no directory, so it is read again. The
    second run's tally therefore omits exactly the articles that mattered. The
    maximum is the right reduction because the count for a past day can only
    grow as more of that day is discovered — it never legitimately falls.
    """
    best: dict[tuple[str, str], dict[str, str]] = {}
    for tally in tallies:
        key = (str(tally.get("outlet_host") or ""), str(tally.get("date") or ""))
        previous = best.get(key)
        if previous is None or _as_int(tally.get("scanned") or 0) > _as_int(
            previous.get("scanned") or 0
        ):
            best[key] = tally

    totals: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for tally in best.values():
        when = _as_date(tally.get("date"))
        if when is None:
            continue
        period = when.strftime("%Y-%m")
        province = str(tally.get("outlet_province") or "").strip()
        # A national paper has no province, and saying it has one called
        # `NASIONAL` is worse than saying nothing: the geography registry
        # resolves that name to Indonesia, so the province row and the country
        # row become one observation holding two different figures, and the
        # whole series refuses to normalize. Decided on the outlet's geography
        # code rather than on the word, because the word is whatever the source
        # sheet happened to type.
        national_only = str(tally.get("outlet_geo_id") or "").upper() in ("", "IDN")
        scopes = (("national", "Indonesia", "country"), ("province", province, "province"))
        for scope, name, level in scopes:
            if scope == "province" and (not name or national_only):
                continue
            bucket = totals[(period, name, level)]
            for column in ("scanned", "matched", "recorded"):
                bucket[column] += _as_int(tally.get(column)) if tally.get(column) else 0

    rows = []
    index = 0
    for (period, name, level), bucket in sorted(totals.items()):
        for indicator, series_name, unit, measure in READING_SERIES:
            index += 1
            digest = hashlib.sha256(f"{period}|{name}|{indicator}".encode()).hexdigest()[:16]
            rows.append(
                _provenance(f"nvr_{digest}", digest)
                | {
                    "dataset": f"{issue.dataset[: -len('-events')]}-counts",
                    "row_number": index,
                    "columns": {
                        "indicator": indicator,
                        "series_name": series_name,
                        "geo": name if level == "country" else f"Provinsi {name}",
                        "geo_level": level,
                        "province": "" if level == "country" else name,
                        "period": period,
                        "value": str(max(bucket.get(measure, 0), 0)),
                        "unit": unit,
                        "measure": measure,
                        "publisher": "Terusan news monitoring",
                    },
                }
            )
    return rows


def _clear(resolver: StorageResolver) -> None:
    """Remove this step's previous output.

    Only the parts this step writes, matched by the stamp on their name. The
    events are recomputed whole, so a leftover part from a run that produced
    more files would otherwise be read alongside the new ones and publish
    events twice. Everything else under `records` — the corpus, the codings,
    every other source — is written by extraction and is not this step's to
    touch.
    """
    directory = Path(resolver.resolve(Layer.BRONZE, "records"))
    if not directory.is_dir():
        return
    for path in directory.rglob(f"part-{RUN_STAMP}-*.parquet"):
        path.unlink()


def recluster(
    issue_slug: str = "violence",
    *,
    since: date | None = None,
    until: date | None = None,
    resolver: StorageResolver | None = None,
) -> dict[str, int]:
    """Recompute this issue's events and counts from Bronze."""
    issue = get_profile(issue_slug)
    resolver = resolver or StorageResolver(StorageConfig())
    writer = ParquetWriter(resolver)

    codings = read_codings(issue, since, until)
    events = cluster(codings)
    log.info(
        "news.cluster.done",
        profile=issue.slug,
        codings=len(codings),
        events=len(events),
        collapsed=len(codings) - len(events),
    )

    # Both collections go into `records` beside every other tabular source,
    # partitioned by source as extraction partitions it. Somewhere of their own
    # would be simpler to overwrite and would put them outside every query that
    # reads Bronze — including the normalizer that turns the counts into
    # observations, which looks for a source and a dataset and nothing else.
    #
    # Replacing rather than appending is what the stamped filename buys: the
    # parts are written under a fixed name, so a recompute overwrites the run
    # before it instead of publishing every event twice.
    tallies = read_tallies(since, until)
    _clear(resolver)
    rows = [
        *_event_rows(events, issue),
        *_count_rows(events, issue),
        *_reading_rows(tallies, issue),
    ]
    # Nothing to write is not an empty file to write. Partitioned by a column
    # with no values, the writer puts one part at the root of `records`, with
    # no `source_id=` directory above it — and DuckDB then refuses every Bronze
    # read that asks for hive partitions, which is every normalization.
    if not rows:
        return {"codings": len(codings), "events": 0, "tallies": len(tallies)}
    writer.write(
        Layer.BRONZE,
        "records",
        table_from_rows(rows, BRONZE_RECORDS),
        partition_by=["source_id"],
        run_id=RUN_STAMP,
    )

    return {"codings": len(codings), "events": len(events), "tallies": len(tallies)}
