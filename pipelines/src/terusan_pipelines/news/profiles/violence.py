"""Collective violence, coded in the columns VEWS coders use.

The point of this profile is comparability. VEWS publishes a human-coded record
of collective violence in Indonesia, and the only way to know whether a machine
coding is any good is to put the two side by side — which requires the machine
to answer the same questions, from the same vocabularies, into the same
columns. So the questions below are the coding form, and the vocabularies they
offer are read out of the human record rather than written here.

The division of labour is strict. The classifier is asked only to pick between
categories a coder would have picked between. Dates, places and casualty
figures are read from the text by `news.places` and `news.figures`, because a
model that cannot emit a number should never be asked for one — and a model
asked to choose a district from a list of five hundred will choose a plausible
one whether or not the article names it.

**Two readers.** JEV codes every article. The codings it was unsure of — a gate
probability sitting on the threshold, a category chosen with thin confidence, a
field that came back `TIDAK JELAS` — are put to a large model, with the same
vocabularies, and what it fills in is merged into the doubtful fields only.
`deepen` below is that stage; `news.deep` is the transport. A confident answer
from the cheap reader is never overwritten by the expensive one.

Rows here are never verified. A human-coded VEWS row has a `coder_id` and a
`ver_id`, two people who read the report; a row from this profile has neither,
and carries `machine_coded = 1` so it can never be mistaken for one that did.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

from ..codes import ENUM_MISSING, actor_examples, actor_names, code_list
from ..deep import DeepReader, DeepUnavailable, Question
from ..deep import shared as deep_reader
from ..dictionary import violence_dictionary
from ..figures import NUM_MISSING, casualties
from ..jev import Answer, choice, noul
from ..lexicon import violence_lexicon
from ..places import resolve
from .base import Coding, Profile, register

#: How the gate question is put. Deliberately narrow: collective violence is
#: two or more people acting together, which is what separates it from the
#: assaults and robberies that fill the same crime desk.
GATE_INSTRUCTION = (
    "Apakah berita ini MELAPORKAN peristiwa kekerasan kolektif yang baru terjadi — "
    "yaitu kekerasan fisik yang dilakukan atau dialami oleh sekelompok orang (dua orang "
    "atau lebih yang bertindak bersama), seperti bentrokan, pengeroyokan, tawuran, "
    "kerusuhan, penyerangan massa, atau kekerasan aparat terhadap warga? "
    "Jawab TIDAK jika berita hanya membahas tindak lanjut dari peristiwa yang sudah "
    "lewat — pemeriksaan saksi, penyidikan, penetapan tersangka, penangkapan, "
    "persidangan, vonis, santunan, atau pernyataan pejabat — tanpa menceritakan "
    "peristiwa kekerasannya sendiri. Jawab TIDAK juga jika tidak ada kekerasan fisik."
)
GATE_YES = (
    "Ya, berita menceritakan peristiwa kekerasan kolektif itu sendiri, kapan dan di "
    "mana terjadinya"
)
GATE_NO = (
    "Tidak — berita hanya membahas tindak lanjut hukum, penyidikan, persidangan, "
    "kebijakan, imbauan, atau kejahatan individual tanpa keterlibatan kelompok, atau "
    "tidak melaporkan kekerasan fisik sama sekali"
)

#: How far the incident got. This is the one column VEWS does not have, and it
#: is written here rather than mined for that reason — there is no human list
#: to read it off. It is kept because the question monitoring is asked is not
#: only how many incidents there were but whether they are getting worse, and a
#: count of incidents cannot answer that: a standoff that dispersed and a
#: village burned down are both one incident.
#:
#: Ordered from least to most, and machine-coded only, so it is never compared
#: with a human coding that does not exist.
ESCALATION: dict[str, str] = {
    "ANCAMAN/KETEGANGAN": (
        "Ketegangan, ancaman, saling ejek, atau pengerahan massa tanpa kekerasan fisik"
    ),
    "KEKERASAN TERBATAS": (
        "Kekerasan fisik terjadi tetapi terbatas — satu perkelahian atau pengeroyokan, "
        "tanpa meluas"
    ),
    "KEKERASAN MELUAS": (
        "Kekerasan menyebar ke banyak orang atau tempat: bentrokan berulang, serangan "
        "balasan, perusakan atau pembakaran"
    ),
    "KERUSUHAN": (
        "Kerusuhan massal — massa besar, penjarahan, pembakaran luas, atau lumpuhnya "
        "wilayah"
    ),
    "MEREDA": "Kekerasan sudah berhenti atau berhasil diredam pada saat berita ditulis",
    ENUM_MISSING: "Tidak dapat ditentukan dari berita",
}

#: Escalation ranked, least severe first. Clustering needs it: several papers
#: report one incident at different moments, one filed while a crowd was still
#: gathering and another after the market burned. Taking the first named answer
#: would make an event's escalation a fact about which paper was fastest, so
#: the most severe reading wins — the same rule the casualty figures follow,
#: and for the same reason.
ESCALATION_ORDER: tuple[str, ...] = (
    ENUM_MISSING,
    "MEREDA",
    "ANCAMAN/KETEGANGAN",
    "KEKERASAN TERBATAS",
    "KEKERASAN MELUAS",
    "KERUSUHAN",
)

#: Plain-language glosses for the categories whose mined labels are terse.
#: Only a description is supplied; the label itself always comes from Bronze,
#: so a category renamed there is renamed here without an edit.
DESCRIPTIONS: dict[str, str] = {
    "SERANGAN TANPA SENJATA API": "Pemukulan, pengeroyokan, perkelahian tanpa senjata api",
    "SERANGAN BERSENJATA": "Serangan memakai senjata tajam, senjata api, atau benda keras",
    "SERANGAN INFRASTRUKTUR/PENUTUPAN PAKSA": (
        "Perusakan bangunan atau fasilitas, atau penutupan paksa"
    ),
    "KEKERASAN SEKSUAL": "Pemerkosaan atau pelecehan seksual",
    "INTIMIDASI MASSA/PUBLIK": "Ancaman atau intimidasi massa tanpa serangan fisik",
    "PENCULIKAN/PENGURUNGAN": "Penculikan, penyanderaan, atau pengurungan paksa",
    "PENGGUSURAN/PEMINDAHAN PAKSA": "Penggusuran atau pengusiran paksa penduduk",
    "PENGEBOMAN/LEDAKAN": "Bom atau ledakan",
    "PESAN ULTIMATUM": "Ultimatum atau ancaman tertulis atau lisan",
    "SENJATA JARAK DEKAT": "Parang, celurit, pisau, kayu, batu, panah",
    "SENJATA API": "Senjata api, termasuk senapan angin dan senjata rakitan",
    "ALAT PEMBAKAR": "Bom molotov, bensin, atau alat pembakar lain",
    "BOM/BAHAN PELEDAK": "Bom atau bahan peledak",
    "BAHAN KIMIA/BIOLOGIS": "Gas air mata, bahan kimia, atau bahan biologis",
    "KENDARAAN, DILUAR BOM": "Kendaraan dipakai untuk menyerang",
    "AKTOR NEGARA": "Polisi, TNI, Satpol PP, atau aparat pemerintah lain",
    "AKTOR NON NEGARA": "Warga, pemuda, pelajar, massa, kelompok, atau perorangan",
    "AKTOR PERUSAHAAN SWASTA": "Perusahaan, satuan pengamanan perusahaan, atau pekerjanya",
    "BERHASIL": "Pihak yang melerai berhasil menghentikan kekerasan",
    "TIDAK BERHASIL": "Pihak yang melerai tidak berhasil menghentikan kekerasan",
    ENUM_MISSING: "Tidak dapat ditentukan dari berita",
}

#: The questions whose answer is a label from a vocabulary.
CHOICE_FIELDS = (
    "violence_form1",
    "violence_form2",
    "weapon_type1",
    "weapon_type2",
    "issue_type1",
    "issue_type2",
    "actor1a",
    "actor1a_t",
    "actor2a",
    "actor2a_t",
    "escalation",
    "intervene_actor1",
    "intervene_actor_type1",
    "intervene_result",
)

#: The questions whose answer is yes or no, written as the coders write it.
BOOLEAN_FIELDS = ("actor1a_vm", "actor2a_vm", "intervene", "pol_rel")

#: The fields a second reading is worth paying for. Not every column: the
#: secondary form, weapon and issue are usually absent because there was only
#: one of each, and asking a large model to find a second one is asking it to
#: invent one.
DEEP_FIELDS = (
    "violence_form1",
    "weapon_type1",
    "issue_type1",
    "actor1a",
    "actor1a_t",
    "actor2a",
    "actor2a_t",
    "escalation",
)


def _criteria(vocabulary: str, *, actors: bool = False) -> dict[str, str]:
    """Build a choice's options from a mined vocabulary."""
    if actors:
        listing = actor_names()
        examples = actor_examples()
        return {
            label: (
                f"{label.title()}"
                + (f"; misalnya {', '.join(examples[label][:3])}" if label in examples else "")
            )
            for label in listing.options()
        } | {ENUM_MISSING: DESCRIPTIONS[ENUM_MISSING]}
    return {
        label: DESCRIPTIONS.get(label, label.title()) for label in code_list(vocabulary).options()
    }


def gate() -> dict[str, dict[str, Any]]:
    """The keep-or-discard question, asked while crawling.

    The same wording as the gate inside the full coding form, so an article
    kept here and coded later is judged by one standard rather than two.
    """
    return {"gate": noul(GATE_INSTRUCTION, GATE_YES, GATE_NO)}


def questions() -> dict[str, dict[str, Any]]:
    """The coding form, put to one article in one call.

    Built per call rather than at import, because the vocabularies are mined
    from Bronze and a freshly-landed VEWS export should reach the next run.
    """
    actors = _criteria("", actors=True)
    return {
        "gate": noul(GATE_INSTRUCTION, GATE_YES, GATE_NO),
        "violence_form1": choice(
            "Bentuk kekerasan yang paling utama dalam peristiwa ini",
            _criteria("violence_form"),
        ),
        "violence_form2": choice(
            "Bentuk kekerasan kedua, jika ada selain bentuk utama",
            _criteria("violence_form"),
        ),
        "weapon_type1": choice(
            "Jenis senjata utama yang dipakai dalam peristiwa ini", _criteria("weapon_type")
        ),
        "weapon_type2": choice("Jenis senjata kedua, jika ada", _criteria("weapon_type")),
        "issue_type1": choice(
            "Isu atau pemicu utama peristiwa kekerasan ini", _criteria("issue_type")
        ),
        "issue_type2": choice("Isu atau pemicu kedua, jika ada", _criteria("issue_type")),
        "actor1a": choice("Pihak pertama yang melakukan kekerasan", actors),
        "actor1a_t": choice("Jenis pihak pertama tersebut", _criteria("actor_type")),
        "actor2a": choice("Pihak kedua, yaitu sasaran atau lawan dari pihak pertama", actors),
        "actor2a_t": choice("Jenis pihak kedua tersebut", _criteria("actor_type")),
        "escalation": choice("Sejauh mana peristiwa ini meningkat (eskalasi)", ESCALATION),
        "actor1a_vm": noul(
            "Apakah pihak pertama adalah kelompok (dua orang atau lebih), bukan perorangan?",
            "Ya, pihak pertama adalah kelompok",
            "Tidak, pihak pertama adalah perorangan",
        ),
        "actor2a_vm": noul(
            "Apakah pihak kedua adalah kelompok (dua orang atau lebih), bukan perorangan?",
            "Ya, pihak kedua adalah kelompok",
            "Tidak, pihak kedua adalah perorangan",
        ),
        "intervene": noul(
            "Apakah ada pihak yang melerai, membubarkan, atau menghentikan kekerasan ini?",
            "Ya, ada pihak yang melerai",
            "Tidak ada yang melerai",
        ),
        "intervene_actor1": choice("Pihak yang melerai kekerasan ini", actors),
        "intervene_actor_type1": choice("Jenis pihak yang melerai", _criteria("actor_type")),
        "intervene_result": choice(
            "Hasil dari upaya melerai tersebut", _criteria("intervene_result")
        ),
        "pol_rel": noul(
            "Apakah peristiwa ini berkaitan dengan politik, pemilihan, atau jabatan publik?",
            "Ya, berkaitan dengan politik atau pemilihan",
            "Tidak berkaitan dengan politik",
        ),
    }


def _vocabulary(field: str) -> tuple[str, ...]:
    """The labels one field may hold, as the second reader is shown them."""
    if field == "escalation":
        return tuple(ESCALATION)
    if field in ("actor1a", "actor2a", "intervene_actor1"):
        return tuple(_criteria("", actors=True))
    if field in ("actor1a_t", "actor2a_t", "intervene_actor_type1"):
        return tuple(_criteria("actor_type"))
    if field.startswith("violence_form"):
        return tuple(_criteria("violence_form"))
    if field.startswith("weapon_type"):
        return tuple(_criteria("weapon_type"))
    if field.startswith("issue_type"):
        return tuple(_criteria("issue_type"))
    if field == "intervene_result":
        return tuple(_criteria("intervene_result"))
    return (ENUM_MISSING,)


def _yes_no(answer: Answer | None, threshold: float) -> str:
    """A yes/no answer in the words the coders write."""
    if answer is None or answer.probability is None:
        return ENUM_MISSING
    return "IYA" if answer.probability >= threshold else "TIDAK"


def _picked(answer: Answer | None) -> str:
    value = answer.value if answer else None
    return str(value) if value else ENUM_MISSING


def _assemble(article: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    """One event row, from whichever reader supplied each label.

    Kept separate from `code` because the second reader produces the same row
    from different labels, and a row assembled twice by two pieces of code is
    a row whose consistency rules — an intervention with nobody intervening, a
    second form identical to the first — hold in one of the two.
    """
    text = f"{article.get('title', '')}\n{article.get('body') or article.get('lead') or ''}"
    published = article.get("published_at")
    when = published if isinstance(published, date) else None
    province, regency = resolve(text, hint_geo_id=article.get("outlet_geo_id"))

    row: dict[str, Any] = {
        "date": when.isoformat() if when else None,
        "day": when.day if when else None,
        "month": when.month if when else None,
        "year": when.year if when else None,
        "province": province.name if province else None,
        "province_id": province.bps_code if province else None,
        "district_city": regency.name if regency else None,
        "district_city_id": regency.bps_code if regency else None,
        "sub_district": None,
        "village": None,
        "actor1_tot": NUM_MISSING,
        "actor2_tot": NUM_MISSING,
        "inc_desc": _summary(article),
        "notes": None,
        "source": article.get("url"),
        "machine_coded": 1,
        "coder_id": None,
        "ver_id": None,
    }
    row.update({field: values.get(field, ENUM_MISSING) for field in CHOICE_FIELDS})
    row.update({field: values.get(field, ENUM_MISSING) for field in BOOLEAN_FIELDS})
    row.update(casualties(text))

    # Nobody intervened means there is no intervening actor and no result to
    # report. Left as the model answered them, an incident coded `TIDAK` for
    # intervention would still name who stepped in.
    if row["intervene"] != "IYA":
        row["intervene_actor1"] = ENUM_MISSING
        row["intervene_actor_type1"] = ENUM_MISSING
        row["intervene_result"] = ENUM_MISSING

    # A second form, weapon or issue identical to the first is the model
    # answering the same question twice, not a second finding.
    for first, second in (
        ("violence_form1", "violence_form2"),
        ("weapon_type1", "weapon_type2"),
        ("issue_type1", "issue_type2"),
    ):
        if row[second] == row[first]:
            row[second] = ENUM_MISSING

    return row


def code(answers: dict[str, Answer], article: dict[str, Any], threshold: float = 0.6) -> Coding:
    """Turn one article's answers into a VEWS-shaped event row."""
    gate = answers.get("gate")
    probability = gate.probability if gate else None
    if probability is None or probability < threshold:
        return Coding(accepted=False, gate_probability=probability, engine="jev")

    values = {field: _picked(answers.get(field)) for field in CHOICE_FIELDS}
    values |= {field: _yes_no(answers.get(field), threshold) for field in BOOLEAN_FIELDS}

    confidence = {
        field: answer.probability
        for field, answer in answers.items()
        if answer.probability is not None
    }
    return Coding(
        accepted=True,
        gate_probability=probability,
        row=_assemble(article, values),
        confidence=confidence,
        engine="jev",
    )


# -- the second reading -----------------------------------------------------


def ambiguous(coding: Coding, threshold: float, band: float, floor: float) -> str | None:
    """Why this coding wants a second reader, or None if it does not.

    Three ways a coding earns a second reading, and they are different
    failures. A gate sitting inside the band is an article the cheap reader
    could not decide was violence at all — the expensive question, because
    getting it wrong either admits crime reporting or loses an incident. A
    field left `TIDAK JELAS` is a question it declined. A field chosen below
    the floor is a question it answered by guessing.
    """
    probability = coding.gate_probability
    if probability is not None and abs(probability - threshold) <= band:
        return "gate-borderline"

    if not coding.accepted:
        # Confidently not violence. The commonest outcome by far, and the one
        # that must never be paid for twice.
        return None

    missing = [
        field for field in DEEP_FIELDS if coding.row.get(field, ENUM_MISSING) == ENUM_MISSING
    ]
    if missing:
        return f"missing:{','.join(missing[:3])}"

    doubtful = [
        field
        for field in DEEP_FIELDS
        if (value := coding.confidence.get(field)) is not None and value < floor
    ]
    if doubtful:
        return f"low-confidence:{','.join(doubtful[:3])}"
    return None


def _context(coding: Coding) -> str:
    """What the first reader made of it, for the second reader to weigh.

    Given rather than withheld. A blind second opinion sounds more rigorous,
    but the second reader is not being asked to referee — it is being asked to
    fill gaps, and telling it which answers are already settled is what keeps
    it from re-litigating the whole form at length.
    """
    if not coding.accepted:
        return (
            "Pengkode pertama TIDAK yakin apakah artikel ini melaporkan kekerasan "
            f"kolektif (keyakinan {coding.gate_probability:.2f})."
            if coding.gate_probability is not None
            else "Pengkode pertama tidak dapat menilai artikel ini."
        )
    settled = [
        f"{field}={coding.row[field]} ({coding.confidence[field]:.2f})"
        for field in DEEP_FIELDS
        if coding.row.get(field, ENUM_MISSING) != ENUM_MISSING and field in coding.confidence
    ]
    return "Jawaban yang sudah mantap: " + ("; ".join(settled) if settled else "tidak ada")


def deepen(
    coding: Coding,
    article: dict[str, Any],
    threshold: float = 0.6,
    *,
    reader: DeepReader | None = None,
) -> Coding:
    """Put an ambiguous coding to the large model and merge what comes back.

    Returns the coding untouched when it was not ambiguous, and returns it
    carrying only `escalation_reason` when the second reader is unconfigured or
    unreachable — which is what makes the queue of articles worth re-reading
    a question Bronze can answer rather than a thing to remember.
    """
    client = reader or deep_reader()
    settings = client.settings
    reason = ambiguous(coding, threshold, settings.band, settings.min_confidence)
    if reason is None:
        return coding
    if not client.available():
        return replace(coding, escalation_reason=reason)

    asked: list[Question] = [
        Question(field="gate", instruction=GATE_INSTRUCTION),
        *(
            Question(
                field=field,
                instruction=_INSTRUCTIONS[field],
                options=_vocabulary(field),
            )
            for field in DEEP_FIELDS
        ),
    ]
    text = f"{article.get('title', '')}\n{article.get('body') or article.get('lead') or ''}"
    try:
        answers = client.ask(text, tuple(asked), context=_context(coding))
    except DeepUnavailable:
        # Logged by the caller, which knows the URL. Recorded here as a coding
        # that wanted a second reading and did not get one.
        return replace(coding, escalation_reason=reason)

    verdict = answers.get("gate")
    accepted = coding.accepted if verdict is None else bool(verdict.value)
    if not accepted:
        # The second reader says this is not collective violence. Its opinion
        # carries on the gate specifically, because the gate is what it was
        # called about and a rejection is the cheaper error to accept.
        return replace(
            coding,
            accepted=False,
            row={},
            engine=f"{coding.engine}+{settings.model}",
            escalation_reason=reason,
        )

    values = {
        field: str(coding.row.get(field) or ENUM_MISSING)
        for field in (*CHOICE_FIELDS, *BOOLEAN_FIELDS)
    }
    confidence = dict(coding.confidence)
    filled: list[str] = []
    for field in DEEP_FIELDS:
        answer = answers.get(field)
        if answer is None:
            continue
        settled = values.get(field, ENUM_MISSING) != ENUM_MISSING
        sure = (confidence.get(field) or 0.0) >= settings.min_confidence
        if settled and sure:
            # The first reader answered it, and answered it confidently. Two
            # readers disagreeing is kept as a disagreement, not resolved in
            # favour of whichever cost more.
            continue
        values[field] = str(answer.value)
        if answer.probability is not None:
            confidence[field] = answer.probability
        filled.append(field)

    notes = answers.get("_notes")
    row = _assemble(article, values)
    if notes is not None:
        row["notes"] = str(notes.value)
    if verdict is not None and verdict.probability is not None:
        confidence["gate"] = verdict.probability

    return Coding(
        accepted=True,
        gate_probability=coding.gate_probability,
        row=row,
        confidence=confidence,
        engine=f"{coding.engine}+{settings.model}",
        deepened=tuple(filled),
        escalation_reason=reason,
    )


#: How each deepened field is put to the second reader. Shorter than the JEV
#: wording because the vocabulary rides with the question and the model is
#: reading the whole article rather than answering from a window of it.
_INSTRUCTIONS: dict[str, str] = {
    "violence_form1": "Bentuk kekerasan yang paling utama dalam peristiwa ini",
    "weapon_type1": "Jenis senjata utama yang dipakai; pilih tidak jelas jika tanpa senjata",
    "issue_type1": "Isu atau pemicu utama peristiwa kekerasan ini",
    "actor1a": "Pihak pertama yang melakukan kekerasan",
    "actor1a_t": "Jenis pihak pertama tersebut",
    "actor2a": "Pihak kedua, yaitu sasaran atau lawan dari pihak pertama",
    "actor2a_t": "Jenis pihak kedua tersebut",
    "escalation": "Sejauh mana peristiwa ini meningkat (eskalasi) menurut artikel",
}


def recode(stored: dict[str, str], article: dict[str, Any]) -> dict[str, Any]:
    """Everything on a coding that was read from the text rather than chosen.

    The division of labour is what makes this possible. A label — the form, the
    weapon, the actors, the escalation — was decided by a reader weighing
    declared alternatives, and re-deciding it means asking again. The date, the
    place, the casualty figures and the description were read out of the text by
    `places`, `figures` and `_summary`, so a fix to any of those can be applied
    to a coding already written, from the text alone.

    Only the read fields are returned, and the labels are not among them. A
    label the stored coding does not carry — a question added to the form after
    it was coded, or one the reader declined to answer — is left absent rather
    than filled with `TIDAK JELAS`, which would put a judgement on the row that
    nothing made.
    """
    values = {
        field: (stored.get(field) or ENUM_MISSING)
        for field in (*CHOICE_FIELDS, *BOOLEAN_FIELDS)
    }
    labels = {*CHOICE_FIELDS, *BOOLEAN_FIELDS}
    return {
        field: value
        for field, value in _assemble(article, values).items()
        if field not in labels
    }


def _summary(article: dict[str, Any], length: int = 400) -> str:
    """A description in the shape VEWS coders write, so the two compare.

    Their summaries open with the date and the place and then say what
    happened. This is the article's own lead, trimmed at a sentence boundary —
    not a generated paraphrase, because a paraphrase is one more thing that can
    be wrong and nobody asked for it.
    """
    text = (article.get("lead") or article.get("body") or "").strip()
    if len(text) <= length:
        return text
    cut = text[:length]
    stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: stop + 1] if stop > length // 2 else cut).strip()


VIOLENCE = register(
    Profile(
        slug="violence",
        title="Collective violence",
        dataset="news-violence-events",
        codings_dataset="news-violence-codings",
        lexicon=violence_lexicon,
        dictionary=violence_dictionary,
        questions=questions,
        gate=gate,
        code=code,
        deepen=deepen,
        recode=recode,
    )
)
