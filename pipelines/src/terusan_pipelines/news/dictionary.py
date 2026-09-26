"""The keyword dictionary: which articles are worth a classifier's attention.

Between the crawl and the classifier sits a filter that costs nothing. A
provincial paper publishes a few hundred pieces a week and almost none of them
report collective violence, so putting every page to a model is paying to be
told about council meetings. The dictionary rejects them before anything is
spent.

What makes it a dictionary rather than a word list is that its terms are
grouped by what they signal, and the grouping is the rule:

- **act** — `bentrok`, `pengeroyokan`, `kerusuhan`, and the escalation idiom
  that reports the same thing sideways (`berujung ricuh`, `amuk massa`).
- **collective** — who was involved, and whether they were a group at all:
  `massa`, `dua kelompok`, `antarwarga`.
- **evidence** — what a violent event leaves behind: weapons, casualties,
  damage.
- **context** — what it was about: land, adat, a demonstration.

An article is a candidate when it carries an *act* term and a *collective*
term, or an act term and evidence from two different categories. That pairing
is what the flat list could not express: `tewas` alone is a traffic accident,
`massa` alone is a concert, and `massa ... tewas` is worth reading. Requiring
the pair is the difference between a fifth of a paper's output reaching the
classifier and half of it.

**This is a recall bound, and it is the one to watch.** An incident reported in
terms no entry here matches never reaches the classifier and never enters the
dataset. The file is `reference/news/keywords.json`, under version control
beside the outlet list, because widening it is a deliberate act with a diff —
not a constant edited in a module nobody reads.

The dictionary does not replace the mined lexicon. They do different jobs: the
lexicon supplies the phrases typed into an outlet's search box, where a
two-word phrase is what a search engine can use, and the dictionary judges the
text that comes back, where single words carry the signal.

**Morphology.** Indonesian affixes mean a dictionary cannot be matched by
string equality: the entry is `pengeroyokan` and the article says `dikeroyok`.
Both reduce to the same stem, so single-word entries are matched against a
token's stem as well as the token itself. Phrases are matched as substrings,
because a phrase is already specific enough that a loose match is not the risk
— `sengketa lahan` means what it says whatever is affixed around it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache

import structlog

from ..storage.root import project_root

log = structlog.get_logger(__name__)

#: Where the dictionary lives. Reference data, versioned beside the outlets.
KEYWORDS_PATH = ("reference", "news", "keywords.json")

#: Categories naming the violent act itself. One of these is required: an
#: article with actors, weapons and a land dispute but no act is an article
#: about a land dispute.
ACT = frozenset({"violence_action", "riot_escalation"})

#: Categories saying more than one person was involved, which is what
#: `collective` violence means and what separates the crime desk from this.
COLLECTIVE = frozenset({"collective_actor", "group_relation"})

#: Categories describing what the act left behind.
EVIDENCE = frozenset({"weapon", "casualty", "damage"})

#: Categories naming what it was about.
CONTEXT = frozenset({"trigger"})

#: How much each category contributes to a candidate's score. The score orders
#: the queue and is carried onto the article row; it decides nothing on its
#: own, because a rule that can be read is worth more here than a threshold
#: that has to be tuned.
WEIGHTS: dict[str, float] = {
    "violence_action": 3.0,
    "riot_escalation": 3.0,
    "collective_actor": 2.0,
    "group_relation": 2.0,
    "weapon": 1.0,
    "casualty": 1.0,
    "damage": 1.0,
    "trigger": 1.0,
}

#: Evidence categories needed to admit an article whose collective signal is
#: absent. Two rather than one: `senjata tajam` beside `penganiayaan` is a
#: single assault, while a weapon *and* damage *and* an act is a crowd.
EVIDENCE_FLOOR = 2

#: Prefixes Indonesian verbs and nouns take. Longest first, because `meng` must
#: be tried before `me` or `mengeroyok` stems to `ngeroyok`.
PREFIXES = (
    "memper", "menper", "keber", "keter",
    "meng", "meny", "mem", "men", "peng", "peny", "pem", "pen",
    "ber", "ter", "per", "se", "me", "pe", "di", "ke",
)

#: Suffixes, longest first for the same reason.
SUFFIXES = ("kannya", "annya", "kan", "an", "nya", "i")

#: A nasal prefix elides the first letter of its stem: `meng` + `keroyok`
#: becomes `mengeroyok`, so stripping the prefix leaves `eroyok` and the
#: letter has to be put back before the stem is recognisable.
ELISION: dict[str, str] = {
    "meng": "k", "peng": "k",
    "meny": "s", "peny": "s",
    "mem": "p", "pem": "p",
    "men": "t", "pen": "t",
}

#: A stem shorter than this is not a word, it is what is left of one. Stripping
#: `di` off `dia` must not produce `a`.
MIN_STEM = 4

_TOKEN = re.compile(r"[a-z]+")


@dataclass(frozen=True, slots=True)
class Match:
    """What the dictionary found in one article."""

    #: Every entry that matched, in dictionary order.
    terms: tuple[str, ...]

    #: The categories those entries came from.
    categories: frozenset[str]

    #: Weighted category coverage, 0 to 1. Descriptive, not a threshold.
    score: float

    @property
    def candidate(self) -> bool:
        """Whether this article is worth putting to the classifier.

        An act, and then either somebody collective doing it or enough of its
        aftermath to say a crowd was there.
        """
        if not self.categories & ACT:
            return False
        if self.categories & COLLECTIVE:
            return True
        return len(self.categories & EVIDENCE) >= EVIDENCE_FLOOR

    #: Why it was admitted, or why it was not, in a word. Carried onto the
    #: article row: a corpus whose inclusion rule cannot be read back per
    #: article is one nobody can argue with when recall is questioned.
    @property
    def verdict(self) -> str:
        if not self.categories:
            return "no-terms"
        if not self.categories & ACT:
            return "no-act"
        if self.categories & COLLECTIVE:
            return "act+collective"
        if len(self.categories & EVIDENCE) >= EVIDENCE_FLOOR:
            return "act+evidence"
        return "act-alone"

    def as_dict(self) -> dict[str, object]:
        """The shape the crawl puts in an artifact's metadata."""
        return {
            "terms": list(self.terms),
            "categories": sorted(self.categories),
            "score": round(self.score, 3),
            "verdict": self.verdict,
        }


def _stems(token: str) -> set[str]:
    """A token and what it might be built from.

    Returns the token itself plus every plausible stem, rather than one answer,
    because this is not a stemmer being asked to be right — it is a filter
    being asked not to miss `dikeroyok` when the dictionary says
    `pengeroyokan`. An extra candidate stem costs a set lookup; a missing one
    costs an incident.
    """
    found = {token}
    body = token
    for suffix in SUFFIXES:
        if body.endswith(suffix) and len(body) - len(suffix) >= MIN_STEM:
            body = body[: -len(suffix)]
            found.add(body)
            break
    for prefix in PREFIXES:
        if not body.startswith(prefix):
            continue
        rest = body[len(prefix) :]
        if len(rest) < MIN_STEM:
            continue
        found.add(rest)
        letter = ELISION.get(prefix)
        if letter and rest[0] in "aeiou":
            found.add(letter + rest)
        break
    return found


@dataclass(frozen=True, slots=True)
class Dictionary:
    """One issue's keyword dictionary, grouped by what each term signals."""

    #: Category to its entries, as written in the file.
    categories: dict[str, tuple[str, ...]]

    #: Single-word entries, by the stem they reduce to. A stem carries every
    #: category it belongs to, not the first: `tewas` is both the act and the
    #: casualty, and collapsing it to one of them quietly weakens the rule
    #: that admits an article on its aftermath.
    _words: dict[str, tuple[tuple[str, str], ...]]

    #: Multi-word entries, as normalised phrases.
    _phrases: tuple[tuple[str, str, str], ...]

    @classmethod
    def build(cls, categories: dict[str, tuple[str, ...]]) -> Dictionary:
        words: dict[str, list[tuple[str, str]]] = {}
        phrases: list[tuple[str, str, str]] = []
        for category, entries in categories.items():
            for entry in entries:
                tokens = _TOKEN.findall(entry.lower())
                if not tokens:
                    continue
                if len(tokens) == 1:
                    for stem in _stems(tokens[0]):
                        words.setdefault(stem, []).append((entry, category))
                else:
                    phrases.append((" ".join(tokens), entry, category))
        return cls(
            categories=categories,
            _words={stem: tuple(found) for stem, found in words.items()},
            _phrases=tuple(phrases),
        )

    @property
    def terms(self) -> tuple[str, ...]:
        """Every entry, whatever category it came from."""
        return tuple(entry for entries in self.categories.values() for entry in entries)

    def match(self, text: str) -> Match:
        """What this article carries, and whether that makes it a candidate."""
        tokens = _TOKEN.findall(text.lower())
        haystack = " ".join(tokens)

        hits: dict[str, set[str]] = {}
        for token in tokens:
            for stem in _stems(token):
                for entry, category in self._words.get(stem, ()):
                    hits.setdefault(entry, set()).add(category)
        for phrase, entry, category in self._phrases:
            if phrase in haystack:
                hits.setdefault(entry, set()).add(category)

        categories = frozenset(name for names in hits.values() for name in names)
        total = sum(WEIGHTS.values()) or 1.0
        score = sum(WEIGHTS.get(category, 0.0) for category in categories) / total
        return Match(terms=tuple(hits), categories=categories, score=score)


@cache
def load(name: str = "keywords.json") -> Dictionary:
    """Read a dictionary off disk.

    An unreadable file is fatal rather than warned about. Every other failure
    in this crawl degrades into a smaller dataset; this one degrades into a
    dictionary that matches nothing, which is a crawl that appears to run
    perfectly and reports that Indonesia had a quiet month.
    """
    path = project_root().joinpath(*KEYWORDS_PATH[:-1], name)
    raw = json.loads(path.read_text(encoding="utf-8"))
    categories = {
        str(category): tuple(str(entry) for entry in entries)
        for category, entries in raw.items()
        if isinstance(entries, list)
    }
    unknown = sorted(set(categories) - set(WEIGHTS))
    if unknown:
        # Not an error: a new category can be added to the file before the rule
        # that uses it is written. But it contributes nothing until it is
        # weighted and named in ACT, COLLECTIVE or EVIDENCE, and silently
        # counting for nothing is how a widened dictionary fails to widen
        # anything.
        log.warning("news.dictionary.unweighted", categories=unknown)
    log.info(
        "news.dictionary.loaded",
        categories=len(categories),
        terms=sum(len(entries) for entries in categories.values()),
    )
    return Dictionary.build(categories)


def violence_dictionary() -> Dictionary:
    """The collective-violence dictionary."""
    return load("keywords.json")
