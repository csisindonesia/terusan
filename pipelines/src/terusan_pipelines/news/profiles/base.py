"""What every issue profile has to provide."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..dictionary import Dictionary
from ..lexicon import Lexicon


@dataclass(frozen=True, slots=True)
class Coding:
    """One article, coded.

    `row` is the event in whatever columns the profile declares — for violence,
    the VEWS ones. `confidence` carries the classifier's probability per field
    beside it rather than inside it, so the event row stays the shape a human
    coder's row is and a verifier can still see which field to doubt.
    """

    #: False when the article is not about this issue at all.
    accepted: bool

    #: What the gate thought, kept whether or not it passed: a near-miss is
    #: the interesting thing to look at when recall is being argued about.
    gate_probability: float | None

    row: dict[str, Any] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)

    #: Which classifier answered, or `unavailable`. On every row, because a row
    #: coded by a model that was not reachable is a row coded by nothing.
    engine: str = "unavailable"

    #: Fields the second reader supplied, because the first was unsure of them
    #: or left them blank. Named rather than counted: a row where the two
    #: readers split the work is one a verifier reads differently from a row
    #: one reader coded whole, and `engine` alone cannot say which fields.
    deepened: tuple[str, ...] = ()

    #: Why the coding was sent to the second reader, or None if it never was.
    #: Kept whether or not the second reading changed anything — a call that
    #: confirmed the cheap coding is a fact about the cheap coding, and a
    #: reason recorded with no `deepened` fields beside it is how an
    #: unconfigured or unreachable second reader is found in Bronze.
    escalation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Profile:
    """An issue: how to find it, how to ask about it, what comes out."""

    #: Stable name. Reaches Bronze on every article and event row.
    slug: str

    title: str

    #: The Bronze collection this profile's clustered events land in — one row
    #: per incident, after several papers' reports of it have been collapsed.
    dataset: str

    #: The Bronze collection holding one row per *article* coded. Separate from
    #: `dataset` because they are different facts: a coding is what one paper's
    #: report says, an event is what several of them agree happened.
    codings_dataset: str

    #: Search terms, mined per issue. What goes into an outlet's search box.
    lexicon: Callable[[], Lexicon]

    #: The keyword dictionary that decides which fetched article is a
    #: candidate. Separate from the lexicon because they are asked different
    #: questions: the lexicon is what a search engine can be given, the
    #: dictionary is what a page can be judged by.
    dictionary: Callable[[], Dictionary]

    #: The questions put to one article's text, built fresh per call because
    #: the vocabularies they offer are mined from Bronze and can change.
    questions: Callable[[], dict[str, dict[str, Any]]]

    #: The single question that decides whether an article is about this issue
    #: at all. Separate from `questions` because it is asked at a different
    #: moment and for a different reason: the crawl asks it to decide whether
    #: to keep the page, and one question costs a fraction of the full coding
    #: form — which matters when it is asked of every article published that
    #: day rather than of the few that survive.
    gate: Callable[[], dict[str, dict[str, Any]]]

    #: Turn the classifier's answers, plus the article, into an event row.
    code: Callable[..., Coding]

    #: Give an ambiguous coding to a large model and take what it fills in.
    #: Optional: an issue without one simply lands its doubtful codings as the
    #: classifier left them, carrying the confidences that say so.
    deepen: Callable[..., Coding] | None = None

    #: Rebuild an event row from a coding already written, keeping the labels
    #: the readers chose and re-reading everything the readers never supplied —
    #: the date, the place, the casualty figures. This is what lets a corrected
    #: parser be applied to the whole corpus without asking a model anything a
    #: second time. Optional: an issue without one cannot be recoded in place,
    #: and its codings are corrected the expensive way, by asking again.
    recode: Callable[[dict[str, str], dict[str, Any]], dict[str, Any]] | None = None

    #: How many search terms the crawl spends on this issue per outlet. The
    #: full lexicon still gates every article fetched; this bounds only how
    #: many searches are issued, which is what the crawl's time is spent on.
    search_terms: int = 25


_REGISTRY: dict[str, Profile] = {}


def register(entry: Profile) -> Profile:
    """Add a profile. Called at import time by each profile module."""
    _REGISTRY[entry.slug] = entry
    return entry


def profiles() -> tuple[Profile, ...]:
    """Every registered issue."""
    return tuple(_REGISTRY.values())


def profile(slug: str) -> Profile:
    """One issue by name."""
    try:
        return _REGISTRY[slug]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise KeyError(f"no news profile {slug!r}; registered: {known}") from None
