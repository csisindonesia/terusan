"""Landed news pages into Bronze: the corpus, and what a coder made of each.

Two collections come out of one article, for the same reason VEWS produces two:

- **`news-articles`** — one row per page the crawl kept, whether or not it was
  about anything. The outlet, the date, the title, the lead, the body, and
  which lexicon terms matched. This is the corpus, and it is issue-agnostic: an
  article carries a tag per profile that claimed it, and may carry none.
- **`news-violence-codings`** — one row per article that a profile accepted,
  in the VEWS column names. One row per *article*, not per incident: five
  papers reporting one brawl produce five codings here, and collapsing them
  into one event is a separate step that reads this collection whole.

Coding happens at extraction rather than at collection because extraction is
idempotent on `(document_id, parser_version)`, which is exactly the property a
coding wants. An article is classified once. A better question set, a widened
lexicon or a revised vocabulary is a parser-version bump, and every article
already in RAW is re-read from bytes that have not changed — without going back
to an outlet that may have edited, paywalled or deleted the page.

**Two readers, in that order.** JEV codes the article. Where it was unsure —
a gate probability on the threshold, a field left `TIDAK JELAS`, a category
chosen by guessing — the coding is put to a large model with the same
vocabularies and the doubtful fields are filled from what it returns. The
escalation is recorded on the row: `escalation_reason` says why it was sent,
`deepened` names the fields the second reader supplied, and `engine` names
both. A coding that was never ambiguous is never escalated, which is what keeps
the second reader a fraction of the spend rather than all of it.

When the classifier cannot be reached the corpus row is still written, with
`engine = "unavailable"` and no coding. Those articles are the recode queue:
they are found by asking Bronze for them, which is what having no separate
crawl state means in practice. A coding carrying an `escalation_reason` with no
`deepened` fields beside it is the same kind of queue, for the second reader.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import structlog

from ..news.deep import DeepUnavailable
from ..news.dictionary import Dictionary, Match
from ..news.jev import JevUnavailable, shared
from ..news.profiles import Profile, profiles
from .article_text import read_article
from .base import Extractor, Landed

log = structlog.get_logger(__name__)

#: The corpus, and one collection per issue that codes into it.
ARTICLES_DATASET = "news-articles"

#: One row per article photographed, pointing at the image in RAW.
SCREENSHOTS_DATASET = "news-screenshots"

#: One row per outlet per day: how much was read, how much was kept.
TALLIES_DATASET = "news-daily-tallies"

#: How much of an article the classifier is shown. Beyond this the tail of a
#: news page is related-article rails and the questions are answered from what
#: came before it anyway.
CODING_CHARS = 12_000


class NewsArticleExtractor(Extractor):
    """Reads one landed news page into the corpus, and codes it."""

    target = "records"

    def __init__(self, issues: tuple[Profile, ...] | None = None, *, code: bool = True) -> None:
        self._issues = issues
        self._code = code

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == "news-monitoring" and landed.dataset == "articles"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        extra = landed.extra or {}
        article = read_article(landed)
        issues = self._issues if self._issues is not None else profiles()

        matched: dict[str, Match] = {}
        for issue in issues:
            try:
                dictionary: Dictionary = issue.dictionary()
            except Exception as error:  # noqa: BLE001 - an unreadable dictionary
                log.warning("news.dictionary.failed", issue=issue.slug, error=str(error)[:200])
                continue
            found = dictionary.match(article["text"])
            if found.candidate:
                matched[issue.slug] = found

        yield _record(
            ARTICLES_DATASET,
            1,
            {
                "url": landed.source_url,
                "canonical_url": extra.get("canonical_url"),
                "outlet": extra.get("outlet"),
                "outlet_host": extra.get("outlet_host"),
                "outlet_geo_id": extra.get("outlet_geo_id"),
                "outlet_province": extra.get("outlet_province"),
                "title": article["title"] or extra.get("title"),
                "lead": article["lead"] or extra.get("lead"),
                "body": article["body"],
                "body_chars": len(article["body"]),
                "discovered_by": extra.get("discovered_by"),
                "matched_terms": json.dumps(
                    sorted({term for found in matched.values() for term in found.terms})
                ),
                "matched_categories": json.dumps(
                    sorted({name for found in matched.values() for name in found.categories})
                ),
                "issues": json.dumps(sorted(matched)),
                "http_status": extra.get("http_status"),
            },
        )

        if not (self._code and matched):
            return

        client = shared()
        for issue in issues:
            if issue.slug not in matched:
                continue
            yield from self._coding(issue, landed, article, matched[issue.slug], client)

    def _coding(
        self,
        issue: Profile,
        landed: Landed,
        article: dict[str, Any],
        found: Match,
        client: Any,
    ) -> Iterator[dict[str, Any]]:
        """Put the profile's questions to one article, then its doubts to the second reader."""
        common = {
            "profile": issue.slug,
            "url": landed.source_url,
            "outlet_host": (landed.extra or {}).get("outlet_host"),
            "outlet_geo_id": (landed.extra or {}).get("outlet_geo_id"),
            "matched_terms": json.dumps(sorted(found.terms)),
            "matched_categories": json.dumps(sorted(found.categories)),
            "dictionary_score": round(found.score, 3),
        }

        if not client.available():
            # No coding, but the article is on the record as one that wanted
            # coding. This row is what a later recode walks.
            yield _record(issue.codings_dataset, 1, common | {"engine": "unavailable"})
            return

        try:
            answers = client.ask(article["text"][:CODING_CHARS], issue.questions())
        except JevUnavailable as error:
            log.warning("news.coding.unavailable", url=landed.source_url, error=str(error)[:200])
            yield _record(issue.codings_dataset, 1, common | {"engine": "unavailable"})
            return

        subject = {
            "title": article["title"],
            "lead": article["lead"],
            "body": article["body"],
            "url": landed.source_url,
            "published_at": landed.published_at,
            "outlet_geo_id": (landed.extra or {}).get("outlet_geo_id"),
        }
        threshold = client.settings.gate_threshold
        coding = issue.code(answers, subject, threshold)

        if issue.deepen is not None:
            try:
                coding = issue.deepen(coding, subject, threshold)
            except DeepUnavailable as error:
                # The cheap coding stands. It already carries the confidences
                # that made it a candidate for a second reading, so the article
                # can be found again when the second reader is back.
                log.warning("news.deep.unavailable", url=landed.source_url, error=str(error)[:200])
            else:
                if coding.deepened:
                    log.info(
                        "news.deep.filled",
                        url=landed.source_url,
                        reason=coding.escalation_reason,
                        fields=list(coding.deepened),
                    )

        row = common | {
            "engine": coding.engine,
            "accepted": "true" if coding.accepted else "false",
            "gate_probability": coding.gate_probability,
            "confidence": json.dumps(coding.confidence),
            # Why a second reader was called, and what it supplied. Empty on
            # the great majority of rows, which is the number to watch: a
            # column that is populated everywhere is a cheap classifier being
            # paid for twice.
            "escalation_reason": coding.escalation_reason,
            "deepened": json.dumps(list(coding.deepened)),
        }
        # Every coded field as its own key, so Bronze can be compared with the
        # human record field by field rather than through a blob.
        row.update(coding.row)
        yield _record(issue.codings_dataset, 1, row)


def _record(dataset: str, row_number: int, columns: dict[str, Any]) -> dict[str, Any]:
    """One Bronze tabular row.

    Bronze holds values as text in a map, which is how every other tabular
    source lands: deciding that `gate_probability` is a float is Silver's call,
    not this one's, and a column that is a number in one extractor and a string
    in another is how a union of fifty sources stops being queryable.
    """
    return {
        "dataset": dataset,
        "row_number": row_number,
        "columns": {key: ("" if value is None else str(value)) for key, value in columns.items()},
    }


class NewsScreenshotExtractor(Extractor):
    """Records that an article was photographed, and where the file is.

    The image itself is not read — there is nothing in a PNG for Bronze — but
    its existence is a fact the corpus needs: without a row, finding the shot
    for an article means walking RAW, and the API cannot walk RAW.
    """

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == "news-monitoring" and landed.dataset == "screenshots"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        extra = landed.extra or {}
        yield _record(
            SCREENSHOTS_DATASET,
            1,
            {
                "url": extra.get("of") or landed.source_url,
                "outlet_host": extra.get("outlet_host"),
                "captured_at": landed.retrieved_at,
                "media_type": landed.media_type,
                "size_bytes": landed.size_bytes,
            },
        )


class NewsTallyExtractor(Extractor):
    """What each outlet published, by day, whether or not it was kept.

    This is the denominator. The events say how many incidents the press
    reported; without knowing how many articles were read to find them, a rise
    is indistinguishable from a crawl that got luckier — a new outlet, a fixed
    search box, a wider lexicon. One row per outlet per day, carrying all
    three numbers, so the ratio can be taken at any grain.
    """

    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == "news-monitoring" and landed.dataset == "daily-tallies"

    def extract(self, landed: Landed) -> Iterator[dict[str, Any]]:
        try:
            record = json.loads(landed.path.read_text())
        except (OSError, ValueError) as error:
            log.warning("news.tally.unreadable", path=str(landed.path), error=str(error)[:200])
            return

        for index, (day, counts) in enumerate(sorted((record.get("days") or {}).items()), 1):
            yield _record(
                TALLIES_DATASET,
                index,
                {
                    "date": day,
                    "outlet": record.get("outlet"),
                    "outlet_host": record.get("outlet_host"),
                    "outlet_geo_id": record.get("outlet_geo_id"),
                    "outlet_province": record.get("outlet_province"),
                    "profile": record.get("profile"),
                    # Candidates discovery turned up for this outlet, before
                    # the window and the gate. Zero with a reachable site means
                    # discovery is broken for it.
                    "discovered": record.get("discovered", 0),
                    # Read at all.
                    "scanned": counts.get("scanned", 0),
                    # Matched the lexicon — a candidate, not a finding.
                    "matched": counts.get("matched", 0),
                    # Kept: the classifier agreed it is about the issue.
                    "recorded": counts.get("recorded", 0),
                },
            )
