"""The vocabularies a coding is allowed to use, read from the human record.

VEWS's coders pick from closed lists — actor types, forms of violence, weapon
types, what the incident was about. Those lists are not published anywhere; they
exist as the set of values twelve thousand coded incidents actually contain. So
they are read from Bronze rather than transcribed here, and the machine coder
offers the model exactly the categories a human coder had in front of them.

Reading them live has a consequence worth stating: a new VEWS export that adds
a category changes what the model may answer, without a line of this repository
changing. That is the intent — the machine coder is meant to track the human
one — but it means an unexplained shift in a series should be checked against
the code lists before it is checked against the crawl.

Values are counted, and rare ones are dropped. A list built from every distinct
string includes the typos, and a typo offered to the model as a category is a
category the model will sometimes choose.

Only the recent years are read, which is what keeps a renamed category from
appearing twice. VEWS relabelled one in 2025 — `SERANGAN TANPA SENJATA` became
`SERANGAN TANPA SENJATA API` — and both spellings are correct for the years
they were used in. Offered together they are two options for one thing, and the
model splits its probability between them, so a real coding loses to a
synonym. Taking the window rather than aliasing by hand means the next rename
needs no edit here: the label the coders are using now is the label on offer,
because a machine coding of today's news is being compared with today's coders.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache

import structlog

from ..storage import Layer
from ..warehouse.query import warehouse

log = structlog.get_logger(__name__)

#: The Bronze collection holding one row per human-coded incident.
INCIDENTS_DATASET = "collective-violence-incidents"

#: What VEWS writes when the reporting did not say.
ENUM_MISSING = "TIDAK JELAS"

#: A value must appear this many times to be offered as a category. Below it,
#: the string is almost always a coder's typo or a one-off free-text answer.
MIN_OCCURRENCES = 5

#: Labels that mean one category and are both in use. VEWS revised its
#: dropdowns in 2025 and the revision did not reach every column: an incident's
#: primary form is coded `SERANGAN TANPA SENJATA API` while its secondary form
#: is still coded `SERANGAN TANPA SENJATA`, in the same file, in the same year.
#: Offered as two options they are two names for one thing and the model's
#: probability splits between them.
#:
#: The trailing-parenthetical cases — `SENJATA API (TERMASUK SENAPAN ANGIN,
#: SENJATA RAKITAN, DSB)` beside `SENJATA API` — need no entry: those are
#: handled by rule, because a parenthetical is an elaboration of the label in
#: front of it rather than a different label.
SYNONYMS: dict[str, str] = {
    "SERANGAN TANPA SENJATA": "SERANGAN TANPA SENJATA API",
    # The same 2025 revision, in the issue list: `PEMILIHAN` ran from 2021 and
    # `ELEKTORAL` appears alongside it from 2025, both on incidents about an
    # election or an appointment.
    "ISU PEMILIHAN DAN JABATAN": "ISU ELEKTORAL DAN JABATAN",
}

#: How many years back the vocabulary is read from. Two rather than one so a
#: category used only in alternate years survives, and a freshly-landed export
#: covering six weeks does not narrow the lists to what those weeks contained.
RECENT_YEARS = 2

#: Columns that share one vocabulary, by the name that vocabulary is known by.
ENUM_COLUMNS: dict[str, tuple[str, ...]] = {
    "actor_type": (
        "actor1a_t",
        "actor1b_t",
        "actor2a_t",
        "actor2b_t",
        "intervene_actor_type1",
        "intervene_actor_type2",
    ),
    "violence_form": ("violence_form1", "violence_form2"),
    "weapon_type": ("weapon_type1", "weapon_type2"),
    "issue_type": ("issue_type1", "issue_type2"),
    "intervene_result": ("intervene_result",),
    "yes_no": ("intervene", "pol_rel", "covid_rel"),
}


@dataclass(frozen=True, slots=True)
class CodeList:
    """One controlled vocabulary, as the coders use it."""

    name: str

    #: Category to how many incidents carry it, commonest first.
    values: dict[str, int] = field(default_factory=dict)

    def options(self) -> tuple[str, ...]:
        """The categories, with the missing marker always last and present.

        Always present because a model that cannot say "the reporting did not
        say" will say something else instead, and a forced guess is worse than
        a blank.
        """
        ordered = [value for value in self.values if value != ENUM_MISSING]
        return (*ordered, ENUM_MISSING)


def canonical(value: str) -> str:
    """The one spelling a category is offered under.

    A trailing parenthetical is dropped because it elaborates the label rather
    than changing it, and a declared synonym is folded onto the label the
    coders now use. Everything else is returned as written: a vocabulary that
    silently rewrites what a coder typed is one nobody can check.
    """
    label = re.sub(r"\s*\([^)]*\)\s*$", "", value.strip()).strip()
    return SYNONYMS.get(label, label)


def _year(column: str = "year") -> str:
    """The incident year as a number.

    The cell is text, and across the exports it is written `2023`, `2023.0` and
    once `#REF!` — a spreadsheet formula that lost its reference. `try_cast`
    turns everything it cannot read into NULL, which is the right answer for
    all three.
    """
    return f"try_cast(replace(trim(columns['{column}']), '.0', '') AS INTEGER)"


def _sql(expression: str, columns: tuple[str, ...], since_year: int) -> str:
    """Count every value across a vocabulary's columns in one pass.

    The VEWS columns live inside a `columns` map on the Bronze row rather than
    as top-level columns, because Bronze keeps the file's own shape.
    """
    unions = "\nUNION ALL\n".join(
        f"SELECT upper(trim(columns['{column}'])) AS value FROM {expression} "
        f"WHERE dataset = '{INCIDENTS_DATASET}' AND {_year()} >= {since_year}"
        for column in columns
    )
    return f"""
        SELECT value, count(*) AS n
        FROM ({unions})
        WHERE value IS NOT NULL AND value <> '' AND value NOT IN ('-99', 'NAN', 'NULL')
          AND try_cast(value AS DOUBLE) IS NULL
        GROUP BY value
        HAVING count(*) >= {MIN_OCCURRENCES}
        ORDER BY n DESC
    """


def _latest_year(house, expression: str) -> int:
    """The most recent year the human record actually codes.

    Read rather than taken from the clock: the newest export can be a year old,
    and anchoring the window to today would then mine an empty vocabulary.
    """
    row = house.query(
        f"SELECT max({_year()}) FROM {expression} WHERE dataset = '{INCIDENTS_DATASET}'"
    ).fetchone()
    return int(row[0]) if row and row[0] else 0


@cache
def code_lists() -> dict[str, CodeList]:
    """Every vocabulary, mined from Bronze.

    Cached for the process: a coding run asks for these once per article and
    the answer cannot change under it.
    """
    out: dict[str, CodeList] = {}
    with warehouse() as house:
        expression = house.source(Layer.BRONZE, "records")
        try:
            since = _latest_year(house, expression) - (RECENT_YEARS - 1)
        except Exception as error:  # noqa: BLE001 - an absent lake is a normal state
            log.warning("news.codes.no-incidents", error=str(error)[:200])
            return {name: CodeList(name=name) for name in ENUM_COLUMNS}
        log.info("news.codes.window", since_year=since)
        for name, columns in ENUM_COLUMNS.items():
            try:
                rows = house.query(_sql(expression, columns, since)).fetchall()
            except Exception as error:  # noqa: BLE001 - an absent lake is a normal state
                log.warning("news.codes.unreadable", vocabulary=name, error=str(error)[:200])
                rows = []
            folded: dict[str, int] = {}
            for value, n in rows:
                folded[canonical(str(value))] = folded.get(canonical(str(value)), 0) + int(n)
            out[name] = CodeList(
                name=name,
                values=dict(sorted(folded.items(), key=lambda kv: kv[1], reverse=True)),
            )
            log.info("news.codes.mined", vocabulary=name, categories=len(out[name].values))
    return out


def code_list(name: str) -> CodeList:
    """One vocabulary by name, empty rather than missing if nothing was mined."""
    return code_lists().get(name, CodeList(name=name))


@cache
def actor_examples(limit: int = 4) -> dict[str, tuple[str, ...]]:
    """Named actors seen under each actor type, to describe it to the model.

    A category called `MASSA` means little on its own; the same category
    described as "misalnya warga kampung, massa aksi, simpatisan" is the thing
    a coder would recognise. The examples are the free-text actor names that
    were filed under each type.
    """
    pairs = (("actor1a", "actor1a_t"), ("actor1b", "actor1b_t"), ("actor2a", "actor2a_t"))
    unions = "\nUNION ALL\n".join(
        f"SELECT upper(trim(columns['{kind}'])) AS type, trim(columns['{name}']) AS actor "
        f"FROM {{expression}} WHERE dataset = '{INCIDENTS_DATASET}'"
        for name, kind in pairs
    )
    out: dict[str, list[str]] = {}
    with warehouse() as house:
        sql = f"""
            SELECT type, actor, count(*) AS n
            FROM ({unions.format(expression=house.source(Layer.BRONZE, "records"))})
            WHERE type IS NOT NULL AND type <> '' AND actor IS NOT NULL AND actor <> ''
              AND lower(actor) NOT IN ('-99', 'nan', 'tidak jelas')
            GROUP BY type, actor
            QUALIFY row_number() OVER (PARTITION BY type ORDER BY count(*) DESC) <= {limit}
            ORDER BY type, n DESC
        """
        try:
            rows = house.query(sql).fetchall()
        except Exception as error:  # noqa: BLE001
            log.warning("news.codes.actors-unreadable", error=str(error)[:200])
            rows = []
    for kind, actor, _ in rows:
        out.setdefault(str(kind), []).append(str(actor))
    return {kind: tuple(actors) for kind, actors in out.items()}


@cache
def actor_names(limit: int = 30) -> CodeList:
    """The actors coders actually name, as a vocabulary to pick from.

    Nearly seven hundred distinct strings appear in the actor columns, which
    looks open-ended until the parentheticals come off: `POLISI (KEPOLISIAN
    RESOR MIMIKA)` and `POLISI (KEPOLISIAN SEKTOR TEGALSARI)` are one actor
    named two ways. Folded, the top thirty cover most incidents, and the tail
    is specific police units rather than new kinds of actor.

    A closed list loses the long tail, and that is the trade being made: the
    classifier cannot invent an actor, so it cannot name a village militia
    nobody has coded before. The article's text is kept beside the event, which
    is where that detail survives.
    """
    columns = ("actor1a", "actor1b", "actor2a", "actor2b", "intervene_actor1")
    counted: dict[str, int] = {}
    with warehouse() as house:
        expression = house.source(Layer.BRONZE, "records")
        try:
            since = _latest_year(house, expression) - (RECENT_YEARS - 1)
            rows = house.query(_sql(expression, columns, since)).fetchall()
        except Exception as error:  # noqa: BLE001
            log.warning("news.codes.actors-unreadable", error=str(error)[:200])
            rows = []
    for value, n in rows:
        label = canonical(str(value))
        counted[label] = counted.get(label, 0) + int(n)
    ordered = dict(sorted(counted.items(), key=lambda kv: kv[1], reverse=True)[:limit])
    return CodeList(name="actor_name", values=ordered)
