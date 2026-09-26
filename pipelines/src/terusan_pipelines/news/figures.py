"""Counting what an article says was done to people.

The classifier answers between declared options and cannot return a number, so
casualties are read here. This is deliberately literal: a figure is taken only
where the text states one near a word that says what it counts. Nothing is
inferred, and an article that does not say how many were hurt yields the
missing marker rather than a zero.

That distinction is the whole reason this is careful. VEWS writes an empty cell
when nobody was hurt and `-99` when the reporting did not say, and Silver's
counting treats them differently: blank contributes zero to a sum, `-99`
contributes nothing at all. Guess one for the other and a province with no
casualties becomes indistinguishable from a province nobody reported on.
"""

from __future__ import annotations

import re

#: What VEWS writes when the reporting did not say.
NUM_MISSING = -99

#: Indonesian number words, up to the range casualty reporting uses. Beyond a
#: dozen, reports give digits.
_WORDS = {
    "nol": 0,
    "satu": 1,
    "seorang": 1,
    "seseorang": 1,
    "dua": 2,
    "tiga": 3,
    "empat": 4,
    "lima": 5,
    "enam": 6,
    "tujuh": 7,
    "delapan": 8,
    "sembilan": 9,
    "sepuluh": 10,
    "sebelas": 11,
    "belasan": 12,
    "puluhan": 20,
    "ratusan": 100,
}

#: Words meaning nobody. Distinct from absence: "tidak ada korban jiwa" is a
#: report of zero deaths, which is a fact, not a silence.
_NONE = re.compile(
    r"\b(tidak ada|tak ada|nihil)\s+(korban\s+)?(jiwa|meninggal|tewas|luka|cedera)", re.I
)

_NUMBER = r"(\d{1,4}|" + "|".join(_WORDS) + r")"

#: The noun a report puts between the number and the casualty word. Indonesian
#: reporting says who far more often than it says "orang": "satu remaja tewas",
#: "seorang nelayan meninggal dunia". Allowing only "orang" and "warga" read
#: those as unreported — a silence — on exactly the articles that state the
#: count most plainly, which is the one error this module exists to avoid.
_PERSON = (
    r"(?:orang|warga|korban|remaja|pemuda|pemudi|pelajar|siswa|siswi|mahasiswa|anak|"
    r"bocah|balita|pria|laki-laki|wanita|perempuan|ibu|bapak|kakek|nenek|pelaku|"
    r"tersangka|nelayan|petani|buruh|pekerja|karyawan|sopir|pengemudi|pengendara|"
    r"penumpang|polisi|anggota|prajurit|santri|jemaah|turis|wisatawan)"
)

#: Up to two of them, because reporting stacks the nouns — "seorang remaja
#: laki-laki tewas". Contiguous only, so a number never reaches a casualty word
#: in the next clause.
_WHO = rf"(?:{_PERSON}\s+){{0,2}}"

#: Each figure is a phrase that names what is counted and a number beside it.
#: Both orders appear — "dua orang tewas" and "tewas dua orang" — so both are
#: matched, within a short window so a number from the next clause is not
#: pulled in.
_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "num_death": (
        re.compile(rf"{_NUMBER}\s+{_WHO}(?:tewas|meninggal|tercatat tewas)", re.I),
        re.compile(rf"(?:tewas|meninggal dunia|korban jiwa)\s+(?:sebanyak\s+)?{_NUMBER}\b", re.I),
    ),
    "num_injured": (
        re.compile(rf"{_NUMBER}\s+{_WHO}(?:luka|terluka|cedera|dirawat)", re.I),
        re.compile(rf"(?:luka-luka|terluka|mengalami luka)\s+(?:sebanyak\s+)?{_NUMBER}\b", re.I),
        # Being beaten is the commonest injury in this corpus and is almost
        # never written as "luka": "seorang warga babak belur dihajar massa"
        # is one injured person, stated as plainly as the reporting ever does.
        re.compile(
            rf"{_NUMBER}\s+{_WHO}"
            r"(?:babak belur|dihajar|dikeroyok|dianiaya|dipukuli|bonyok)",
            re.I,
        ),
    ),
    "infra_damage": (
        re.compile(
            rf"{_NUMBER}\s+(?:unit\s+)?"
            r"(?:rumah|bangunan|kios|lapak|kendaraan|motor|mobil)\s+(?:rusak|dirusak)",
            re.I,
        ),
    ),
    "infra_destroyed": (
        re.compile(
            rf"{_NUMBER}\s+(?:unit\s+)?"
            r"(?:rumah|bangunan|kios|lapak|kendaraan|motor|mobil)\s+"
            r"(?:dibakar|terbakar|hangus|ludes|hancur)",
            re.I,
        ),
    ),
}

#: Who was hurt, for the figures VEWS keeps separately. Matched only in the
#: sentence that states a figure, never across the article: "ibu kota Provinsi"
#: is a capital city, "AKBP Anak Agung" is a police chief's name, and "Ramah
#: Anak" is a link in a newspaper's footer. Each of those read as a casualty
#: once, on articles whose deaths were counted elsewhere in the page.
#:
#: Two of the words carry a second meaning that the sentence rule cannot catch,
#: because it turns up in the same sentence as the figure: `ibu kota` is a
#: capital city, and `Anak Agung` is a Balinese title that opens a great many
#: officials' names. Both are excluded by what follows them — a fixed word, and
#: a capital letter — rather than by a list of names, which would need one
#: entry per official the press quotes.
_FEMALE = re.compile(r"\b(perempuan|wanita|ibu(?!\s+kota)|gadis|siswi)\b", re.I)
_CHILD = re.compile(r"\b(anak(?!\s+(?-i:[A-Z]))|bocah|balita|pelajar sd|siswa sd)\b", re.I)

#: What ends a sentence, for the purpose of deciding what is near a figure.
#: Semicolons and newlines included: a news page's furniture is separated from
#: its prose by line breaks and by nothing else.
_STOPS = ".!?;\n"


def _value(token: str) -> int | None:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return _WORDS.get(token)


def count(text: str, field: str) -> int:
    """The figure an article states for one field.

    Returns the largest stated figure rather than the first. Reports commonly
    open with a partial count and correct it further down — "satu tewas" in the
    lead, "dua tewas" once the second victim died in hospital — and the fuller
    number is the one a coder would take.
    """
    patterns = _PATTERNS.get(field, ())
    found = [
        value
        for pattern in patterns
        for match in pattern.finditer(text)
        if (value := _value(match.group(1))) is not None
    ]
    if found:
        return max(found)
    if field in ("num_death", "num_injured") and _NONE.search(text):
        return 0
    return NUM_MISSING


def _stating(text: str, field: str) -> list[str]:
    """The sentences in which this field's figure is actually stated.

    Cheap sentence-finding rather than a parser: the window has to hold the
    whole clause a figure sits in — "dua warga tewas, seorang di antaranya
    perempuan" is one sentence and one fact — while stopping before the next
    one, and splitting on stops does both.
    """
    clauses = []
    for pattern in _PATTERNS.get(field, ()):
        for match in pattern.finditer(text):
            left = max((text.rfind(stop, 0, match.start()) for stop in _STOPS), default=-1)
            right = min(
                (found for stop in _STOPS if (found := text.find(stop, match.end())) != -1),
                default=len(text),
            )
            clauses.append(text[left + 1 : right])
    return clauses


def casualties(text: str) -> dict[str, int]:
    """Every casualty and damage figure, in the VEWS column names.

    `death_injured` is the sum VEWS carries as its own column, and it is only
    a sum where both parts were actually reported: adding a figure to a
    missing marker would publish a total that is smaller than one of its parts.
    """
    figures = {name: count(text, name) for name in _PATTERNS}
    deaths, injured = figures["num_death"], figures["num_injured"]
    figures["death_injured"] = (
        deaths + injured if deaths != NUM_MISSING and injured != NUM_MISSING else NUM_MISSING
    )
    for field, pattern, part in (
        ("fem_death", _FEMALE, "num_death"),
        ("fem_injured", _FEMALE, "num_injured"),
        ("child_death", _CHILD, "num_death"),
        ("child_injured", _CHILD, "num_injured"),
    ):
        if figures[part] == NUM_MISSING:
            # No figure to attribute to anybody. Zero would assert that no
            # women or children were hurt in an incident nobody counted.
            figures[field] = NUM_MISSING
        elif figures[part] == 0:
            # Nobody was hurt, so nobody of any description was.
            figures[field] = 0
        else:
            # Claimed only where the word appears in a sentence that states a
            # figure. Anywhere else in the page it is a name, a place or the
            # newspaper's own furniture, and an article reporting two deaths
            # beside the phrase "ibu kota" is not reporting two women's.
            named = any(pattern.search(clause) for clause in _stating(text, part))
            figures[field] = figures[part] if named else NUM_MISSING
    return figures
