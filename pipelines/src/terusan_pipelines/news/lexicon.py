"""The words the crawl searches for.

Recall is bounded by this list and by nothing else: an incident reported in
terms no term here matches is an incident the dataset will never contain. That
makes the lexicon the most consequential thing in the feature, and the reason
it is mined rather than written is that a person writing it produces the words
they can think of, while the human record contains the words Indonesian
reporting actually uses.

The mining is over `inc_desc` — the coder's own summary of each incident, of
which Bronze holds some twelve thousand. Phrases of two and three words are
counted across them, and the ones that recur are kept. Two words minimum
because single words are useless as search terms here: `warga` appears in
almost every description and matches almost every article in an Indonesian
newspaper.

Frequency alone does not produce search terms. The commonest phrases in the
corpus are the coders' own template — `isu yang diidentifikasi adalah isu` —
and the places incidents happened in: `jawa barat` summarises nine hundred
incidents and would match every article the paper published that week. So the
candidates are judged, one batch at a time, by the same classifier that codes
the articles: a phrase is a term if the model says it signals collective
violence in a news report. `jawa barat` scores 0.08 and `korban pengeroyokan`
0.91, which is the distinction frequency cannot make.

The judgement is memoised on disk against a hash of the candidates, because it
is worth several hundred questions the first time and nothing at all on every
run after, until the human record grows.

A seed list is carried alongside. It is the hand-written lexicon the first
version of this crawl used, kept because it holds newsroom idiom that a coder
summarising an incident does not write — `geng motor`, `klitih`, `main hakim
sendiri` — and because losing terms that were already known to work would be a
silent regression in recall. Seed terms are never judged: they are the floor.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from functools import cache

import structlog

from ..storage import Layer, StorageConfig, StorageResolver
from ..warehouse.query import warehouse
from .jev import Jev, JevUnavailable, noul, shared

log = structlog.get_logger(__name__)

INCIDENTS_DATASET = "collective-violence-incidents"

#: Terms the first version of this crawl searched, grouped by the concept they
#: target so a gap in coverage is visible. Kept as the floor of the lexicon.
SEED: dict[str, tuple[str, ...]] = {
    "bentrok": (
        "bentrok warga",
        "bentrokan",
        "tawuran",
        "tawuran pelajar",
        "perang antar kampung",
        "bentrok antar kelompok",
    ),
    "pengeroyokan": (
        "pengeroyokan",
        "dikeroyok",
        "keroyok massa",
        "main hakim sendiri",
        "amuk massa",
        "massa mengamuk",
        "warga mengamuk",
    ),
    "kerusuhan": (
        "kerusuhan",
        "ricuh",
        "demo ricuh",
        "unjuk rasa ricuh",
        "bakar ban massa",
    ),
    "serangan_bersenjata": (
        "pembacokan",
        "bacok massal",
        "penyerangan kelompok",
        "serangan celurit",
        "penganiayaan massal",
        "geng motor",
        "klitih",
        "begal",
    ),
    "aktor_negara": (
        "polisi tembak warga",
        "aparat tembak warga",
        "represif aparat",
        "brutalitas polisi",
        "penggusuran paksa",
        "satpol pp bentrok",
    ),
    "konflik_sumberdaya": (
        "konflik lahan",
        "sengketa lahan bentrok",
        "konflik tambang",
        "penjarahan",
        "pembakaran rumah warga",
    ),
    "kelompok_bersenjata": (
        "kkb serang",
        "kelompok kriminal bersenjata",
        "penyerangan pos",
        "teror bom",
    ),
}

#: Words that carry no meaning on their own. A phrase made only of these is
#: grammar, not a search term. Deliberately short: this is not a linguistic
#: stopword list, it is the set of tokens that would otherwise dominate the
#: counts of a corpus of incident summaries.
STOPWORDS = frozenset(
    {
        "yang", "di", "ke", "dari", "dan", "atau", "pada", "untuk", "dengan", "itu", "ini",
        "dalam", "oleh", "karena", "saat", "sebuah", "para", "akan", "tidak", "ada", "adalah",
        "telah", "sudah", "juga", "saya", "dia", "mereka", "kami", "kita", "bahwa", "agar",
        "serta", "setelah", "sebelum", "antara", "hingga", "sampai", "lalu", "kemudian",
        "namun", "tetapi", "jika", "maka", "bisa", "dapat", "orang", "seorang", "sejumlah",
        "beberapa", "satu", "dua", "tiga", "kata", "ujar", "menurut", "terjadi", "tersebut",
        "kec", "kab", "kota", "pukul", "wib", "jalan", "desa", "kelurahan", "kecamatan",
        "kabupaten", "provinsi",
    }
)

#: A phrase must summarise this many incidents to be trusted as a term. Low
#: because the corpus is twelve thousand summaries, not a web corpus: a phrase
#: five coders reached for independently is a phrase reporters use.
MIN_INCIDENTS = 8

#: Phrase lengths mined. Two and three words; four-word phrases are almost
#: always a sentence fragment that no search box will match.
NGRAM_SIZES = (2, 3)

#: How many of the commonest candidates are put to the classifier. Beyond this
#: the phrases summarise a handful of incidents each and the crawl budget is
#: better spent on the terms above them.
MAX_CANDIDATES = 600

#: Questions per call. The classifier takes one piece of text and many
#: questions about it, so the phrases ride together and the fixed cost of the
#: call is paid once per batch rather than once per phrase.
JUDGE_BATCH = 40

#: A phrase is kept when the classifier is at least this sure it signals
#: violence. The same threshold the article gate uses, for the same reason.
JUDGE_THRESHOLD = 0.6

#: Words that mark a coder's template rather than a reported event. Dropped
#: before judging, because they are the bulk of the candidates and paying the
#: classifier to reject them is paying for a known answer.
TEMPLATE_WORDS = frozenset(
    {
        "isu", "diidentifikasi", "motif", "tanggal", "januari", "februari", "maret", "april",
        "mei", "juni", "juli", "agustus", "september", "oktober", "november", "desember",
        "nomor", "sumber", "berita",
    }
)

_TOKEN = re.compile(r"[a-z]+")


@dataclass(frozen=True, slots=True)
class Lexicon:
    """The terms one profile searches with."""

    #: Mined phrase to the number of incidents it summarises.
    mined: dict[str, int]

    #: The hand-written floor, flattened.
    seed: tuple[str, ...]

    def terms(self, limit: int | None = None) -> tuple[str, ...]:
        """Search terms, the seed first and the mined phrases by weight.

        The seed leads because those terms are known to return articles; the
        mined tail is where new recall comes from, and truncating it is how a
        crawl's budget is spent on the terms most likely to pay.
        """
        seeded = set(self.seed)
        ordered = [*self.seed, *(phrase for phrase in self.mined if phrase not in seeded)]
        return tuple(ordered[:limit] if limit else ordered)

    def matches(self, text: str) -> tuple[str, ...]:
        """Which terms appear in a piece of text.

        This is the gate: an article matching nothing is stored but never
        coded, and never screenshotted. Substring matching on a normalised
        string rather than token matching, because Indonesian affixes mean
        `dikeroyok` and `keroyok` should both hit `keroyok`.
        """
        haystack = " ".join(_TOKEN.findall(text.lower()))
        return tuple(term for term in self.terms() if term in haystack)


def _ngrams(text: str) -> set[str]:
    """Phrases in one description, deduplicated within it.

    Deduplicated so the count is incidents-that-used-a-phrase rather than
    total uses: a summary that repeats `bentrokan` four times is still one
    incident's worth of evidence that the word is used.
    """
    tokens = [token for token in _TOKEN.findall(text.lower()) if len(token) > 2]
    found: set[str] = set()
    for size in NGRAM_SIZES:
        for index in range(len(tokens) - size + 1):
            window = tokens[index : index + size]
            if all(token in STOPWORDS for token in window):
                continue
            if window[0] in STOPWORDS or window[-1] in STOPWORDS:
                # A phrase that opens or closes on a function word is a
                # fragment — `di kampung`, `warga yang` — not a term.
                continue
            found.add(" ".join(window))
    return found


@cache
def _place_words() -> frozenset[str]:
    """Words that name a province, regency or city.

    A phrase containing one is a location, and a location is what every
    article on the page has in common. Read from the geography reference
    rather than listed, so a new regency needs no edit here.
    """
    from ..storage.root import project_root

    words: set[str] = set()
    directory = project_root() / "reference" / "geography"
    for name in ("indonesia-provinces.csv", "indonesia-regencies.csv"):
        path = directory / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                continue
            words.update(token for token in _TOKEN.findall(line.lower()) if len(token) > 3)
    # `kota` and `kabupaten` are in every row and are already stopwords; the
    # words that matter are the names themselves.
    return frozenset(words - STOPWORDS)


def _plausible(phrase: str) -> bool:
    """Whether a candidate is worth the classifier's time."""
    tokens = phrase.split()
    if any(token in TEMPLATE_WORDS for token in tokens):
        return False
    return not any(token in _place_words() for token in tokens)


def _judge(phrases: list[str], client: Jev) -> dict[str, float]:
    """Ask the classifier which candidates are violence terms."""
    verdicts: dict[str, float] = {}
    instruction = (
        'Apakah frasa "{phrase}" adalah istilah yang menandakan peristiwa kekerasan '
        "kolektif (bentrokan, pengeroyokan, kerusuhan, penyerangan massa) dalam berita?"
    )
    for start in range(0, len(phrases), JUDGE_BATCH):
        batch = phrases[start : start + JUDGE_BATCH]
        questions = {
            f"p{index}": noul(
                instruction.format(phrase=phrase),
                "Ya, frasa ini menandakan kekerasan",
                "Tidak, frasa ini umum atau hanya menyebut tempat",
            )
            for index, phrase in enumerate(batch)
        }
        try:
            answers = client.ask(
                "Penilaian istilah pencarian untuk pemantauan berita kekerasan "
                "kolektif di Indonesia.",
                questions,
            )
        except JevUnavailable as error:
            # Without the classifier the lexicon falls back to its seed, which
            # is the floor rather than nothing. Said out loud: a crawl running
            # on seed terms alone has materially lower recall.
            log.warning("news.lexicon.unjudged", error=str(error)[:200], phrases=len(batch))
            return verdicts
        for index, phrase in enumerate(batch):
            answer = answers.get(f"p{index}")
            if answer and answer.probability is not None:
                verdicts[phrase] = answer.probability
    return verdicts


def _cache_path(digest: str):
    """Where a judged candidate set is remembered.

    Scratch rather than the lake: it is derived, it is cheap to rebuild, and it
    must not travel to a shared backend where another machine would read a
    judgement made against a different corpus.
    """
    resolver = StorageResolver(StorageConfig())
    return resolver.scratch("news", "lexicon") / f"{digest}.json"


@cache
def violence_lexicon() -> Lexicon:
    """Mine the collective-violence lexicon from the human record."""
    seed = tuple(term for group in SEED.values() for term in group)
    counter: Counter[str] = Counter()
    try:
        with warehouse() as house:
            expression = house.source(Layer.BRONZE, "records")
            rows = house.query(
                f"SELECT columns['inc_desc'] FROM {expression} "
                f"WHERE dataset = '{INCIDENTS_DATASET}' AND columns['inc_desc'] IS NOT NULL"
            ).fetchall()
    except Exception as error:  # noqa: BLE001 - an absent lake is a normal state
        log.warning("news.lexicon.unreadable", error=str(error)[:200])
        return Lexicon(mined={}, seed=seed)

    for (description,) in rows:
        counter.update(_ngrams(str(description)))

    candidates = [
        phrase
        for phrase, count in counter.most_common()
        if count >= MIN_INCIDENTS and phrase not in seed and _plausible(phrase)
    ][:MAX_CANDIDATES]

    digest = hashlib.sha256("\n".join(candidates).encode()).hexdigest()[:16]
    path = _cache_path(digest)
    if path.exists():
        verdicts = {k: float(v) for k, v in json.loads(path.read_text()).items()}
        log.info("news.lexicon.cached", phrases=len(verdicts))
    else:
        verdicts = _judge(candidates, shared())
        if verdicts:
            path.write_text(json.dumps(verdicts, indent=2, sort_keys=True))

    mined = {
        phrase: counter[phrase]
        for phrase in candidates
        if verdicts.get(phrase, 0.0) >= JUDGE_THRESHOLD
    }
    log.info(
        "news.lexicon.mined",
        descriptions=len(rows),
        candidates=len(candidates),
        kept=len(mined),
        seed=len(seed),
    )
    return Lexicon(mined=mined, seed=seed)
