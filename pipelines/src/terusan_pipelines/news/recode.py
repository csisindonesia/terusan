"""Coding an article again from what Bronze already holds.

Two different things make a coding wrong after it was written, and they cost
different amounts to put right.

A **label** can be wrong — the form of violence, the weapon, who the sides
were. Those were decided by a reader weighing declared alternatives, and the
only way to decide them again is to ask again, which costs a model call per
article.

A **literal field** can be wrong — the district, the date, the casualty
figures. Those were never the model's: `news.places` and `news.figures` read
them out of the text. When one of those parsers is corrected, every coding
already written is wrong in the same way, and putting them right needs neither
the outlet nor the model — the article's text is in Bronze beside the coding,
which is the reason the corpus keeps the body and not merely a pointer to it.

That second case is the common one and is what this module does by default:
re-read the literal fields for codings already made, keep the labels, write the
row again. The first case is served by `--ask-model`, which walks the queue the
extractor leaves behind — codings landed with `engine = "unavailable"` because
the classifier could not be reached — and asks for them now.

**Appended, never overwritten.** A recode writes a new coding row rather than
editing the old one, under a stamp of its own so runs do not overwrite each
other. Bronze is a record of what was read and when; a coding that silently
replaced its predecessor would leave no way to see that a figure changed or
why. Everything downstream — the API's corpus query, the clustering — takes the
newest coding per URL at the current parser version, so the correction wins
without the history being thrown away.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import structlog

from ..extract.base import PARSER_VERSION
from ..storage import Layer, StorageConfig, StorageResolver
from ..warehouse.query import warehouse
from ..warehouse.schema import BRONZE_RECORDS, table_from_rows
from ..warehouse.writer import ParquetWriter
from .profiles import Profile
from .profiles import profile as get_profile

log = structlog.get_logger(__name__)

#: The corpus collection, which holds the text a recode reads.
ARTICLES_DATASET = "news-articles"

#: This step's own version, written as the *pipeline* version on the rows it
#: produces. The parser version stays extraction's, for the same reason
#: clustering keeps it: everything downstream reads Bronze at the current
#: parser version, and a row stamped with a version of its own would be
#: invisible to all of it.
RECODE_VERSION = "1"


def _newest(records: str, dataset: str) -> str:
    """The newest row per URL in one collection, at the current parser version.

    The same rule the API applies, and it is load-bearing here twice over: a
    recode must read the correction it wrote last time rather than the original
    beside it, and it must not write a second correction of a row it already
    corrected.
    """
    return (
        "(SELECT * FROM (SELECT *, row_number() OVER ("
        "PARTITION BY columns['url'] ORDER BY processed_at DESC) AS pick FROM "
        f"{records} WHERE dataset = '{dataset}' AND parser_version = "
        f"'{PARSER_VERSION}') WHERE pick = 1)"
    )


def _as_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _as_moment(value: Any) -> datetime | None:
    """A time-zoned Bronze timestamp, read back from its text form."""
    try:
        return datetime.fromisoformat(str(value)).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def read_pairs(
    issue: Profile,
    *,
    since: date | None = None,
    until: date | None = None,
    url: str | None = None,
) -> list[tuple[dict[str, Any], dict[str, str], dict[str, str]]]:
    """Every coding worth recoding, with the article it was made from.

    Returns the coding's provenance, its stored columns and the article's, so a
    rebuilt row can be stamped as coming from the same document as the coding it
    corrects — which it does: one article, read again.
    """
    with warehouse() as house:
        records = house.source(Layer.BRONZE, "records")
        rows = house.query(
            "SELECT c.document_id, c.source_id, c.source_type, c.source_url, "
            "c.content_hash, c.raw_path, c.original_filename, c.media_type, "
            # Both timestamps as text. DuckDB hands a time-zoned value back
            # through a library this project does not depend on, and the only
            # thing wanted from them here is to put them back unchanged.
            "CAST(c.published_at AS VARCHAR), CAST(c.retrieved_at AS VARCHAR), "
            "c.row_number, c.columns, a.columns "
            f"FROM {_newest(records, issue.codings_dataset)} c "
            f"JOIN {_newest(records, ARTICLES_DATASET)} a "
            "ON a.columns['url'] = c.columns['url']"
        ).fetchall()

    pairs = []
    for row in rows:
        coding, article = dict(row[11]), dict(row[12])
        if url is not None and coding.get("url") != url:
            continue
        when = _as_date(coding.get("date")) or _as_date(row[8])
        if since is not None and (when is None or when < since):
            continue
        if until is not None and (when is None or when > until):
            continue
        provenance = {
            "document_id": row[0],
            "source_id": row[1],
            "source_type": row[2],
            "source_url": row[3],
            "content_hash": row[4],
            "raw_path": row[5],
            "original_filename": row[6],
            "media_type": row[7],
            "published_at": _as_date(row[8]),
            "retrieved_at": _as_moment(row[9]),
            "row_number": row[10],
        }
        pairs.append((provenance, coding, article))
    return pairs


def _subject(coding: dict[str, str], article: dict[str, str], published: Any) -> dict[str, Any]:
    """The article as a profile expects to be handed it.

    Built from Bronze rather than from RAW: the corpus row holds the title, the
    lead and the body, so the bytes the outlet served are not needed to read a
    figure out of them again — and an outlet that has since edited, paywalled or
    deleted the page cannot change what a re-read finds.
    """
    return {
        "title": article.get("title") or "",
        "lead": article.get("lead") or "",
        "body": article.get("body") or "",
        "url": coding.get("url") or article.get("url") or "",
        "published_at": _as_date(published) or _as_date(article.get("published_at")),
        "outlet_geo_id": coding.get("outlet_geo_id") or article.get("outlet_geo_id"),
    }


def _text(subject: dict[str, Any]) -> str:
    """What a classifier is shown, spelled as extraction spells it."""
    return f"{subject['title']}\n{subject['body'] or subject['lead']}".strip()


def _columns(values: dict[str, Any]) -> dict[str, str]:
    """Bronze holds values as text, and deciding types is Silver's job."""
    return {key: ("" if value is None else str(value)) for key, value in values.items()}


def _differences(before: dict[str, str], after: dict[str, str]) -> dict[str, tuple[str, str]]:
    """What the rebuild changed, field by field.

    Reported rather than counted because a recode that moved one casualty
    figure and a recode that moved every district are different events, and a
    number alone cannot tell them apart.
    """
    return {
        key: (before.get(key, ""), after[key]) for key in after if before.get(key, "") != after[key]
    }


def _ask(issue: Profile, subject: dict[str, Any], client: Any) -> dict[str, Any] | None:
    """Put an uncoded article to the readers. Returns None if nobody answered."""
    from .deep import DeepUnavailable
    from .jev import JevUnavailable

    try:
        answers = client.ask(_text(subject)[:12_000], issue.questions())
    except JevUnavailable as error:
        log.warning("news.recode.unavailable", url=subject["url"], error=str(error)[:200])
        return None

    threshold = client.settings.gate_threshold
    coding = issue.code(answers, subject, threshold)
    if issue.deepen is not None:
        try:
            coding = issue.deepen(coding, subject, threshold)
        except DeepUnavailable as error:
            log.warning("news.recode.deep_unavailable", url=subject["url"], error=str(error)[:200])

    return {
        "engine": coding.engine,
        "accepted": "true" if coding.accepted else "false",
        "gate_probability": coding.gate_probability,
        "confidence": json.dumps(coding.confidence),
        "escalation_reason": coding.escalation_reason,
        "deepened": json.dumps(list(coding.deepened)),
    } | coding.row


def recode(
    issue_slug: str = "violence",
    *,
    since: date | None = None,
    until: date | None = None,
    url: str | None = None,
    ask_model: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
    resolver: StorageResolver | None = None,
) -> dict[str, Any]:
    """Write every coding again that a re-read changes.

    Unchanged codings are not rewritten. A recode over a corpus where nothing
    moved must cost nothing downstream, or the collection doubles in size every
    time somebody checks whether it needed correcting.
    """
    issue = get_profile(issue_slug)
    if issue.recode is None and not ask_model:
        raise ValueError(
            f"profile {issue.slug!r} cannot rebuild a coding in place; "
            "re-ask the classifier with --ask-model"
        )

    resolver = resolver or StorageResolver(StorageConfig())
    client = None
    if ask_model:
        from .jev import shared

        client = shared()

    pairs = read_pairs(issue, since=since, until=until, url=url)
    stamp = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    changed: dict[str, int] = {}
    examined = asked = 0

    for provenance, stored, article in pairs:
        subject = _subject(stored, article, provenance["published_at"])
        uncoded = stored.get("engine", "") in ("", "unavailable")

        if uncoded:
            # Never coded: there is no row to rebuild, only a question nobody
            # answered. Left alone unless the caller asked for the model.
            if not ask_model or client is None or not client.available():
                continue
            asked += 1
            fresh = _ask(issue, subject, client)
            if fresh is None:
                continue
            after = _columns(stored | fresh)
        else:
            examined += 1
            if issue.recode is None or stored.get("accepted") != "true":
                # A rejected coding has no event row to re-read: the article was
                # judged not to be about the issue, and that judgement is the
                # model's, not the parser's.
                continue
            # Merged onto the stored coding rather than assembled fresh: what
            # a reader decided stays exactly as it was written, including a
            # field it left blank, and only what the parsers read is replaced.
            after = _columns(stored | issue.recode(stored, subject))

        moved = _differences(stored, after)
        if not moved:
            continue
        for field in moved:
            changed[field] = changed.get(field, 0) + 1
        rows.append(
            provenance
            | {
                "processed_at": stamp,
                "parser_version": PARSER_VERSION,
                "pipeline_version": f"news-recode-{RECODE_VERSION}",
                "dataset": issue.codings_dataset,
                "columns": after,
            }
        )
        if limit is not None and len(rows) >= limit:
            break

    if rows and not dry_run:
        ParquetWriter(resolver).write(
            Layer.BRONZE,
            "records",
            table_from_rows(rows, BRONZE_RECORDS),
            partition_by=["source_id"],
            # Stamped per run rather than under one fixed name: two recodes
            # correct different things, and a fixed name would make the second
            # one silently undo the first for every article it did not touch.
            run_id=f"recode-{stamp.strftime('%Y%m%dT%H%M%S')}",
        )

    result = {
        "profile": issue.slug,
        "codings": len(pairs),
        "examined": examined,
        "asked": asked,
        "rewritten": len(rows),
        "dry_run": dry_run,
        "changed": dict(sorted(changed.items(), key=lambda item: (-item[1], item[0]))),
    }
    log.info("news.recode.done", **{k: v for k, v in result.items() if k != "changed"})
    return result
