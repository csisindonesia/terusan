"""The second reader: a large model, for the articles the classifier was unsure of.

JEV codes every article, cheaply, into categories it is made to choose between.
It is right most of the time and it says how sure it is, which is the part that
matters here: the codings it is *unsure* of are a named subset, not a suspicion,
and they can be handed to something that reads better and costs more.

That is the whole of this module. An article whose gate probability sits near
the threshold, or whose form, actors or issue came back as `TIDAK JELAS`, or
whose chosen categories carry a thin probability, goes to DeepSeek with the
vocabularies JEV was offered and is asked the same questions in prose. What
comes back fills the gaps and nothing else.

**Never overwrites a confident answer.** The large model is the second reader,
not the appeal court. A field JEV answered above the confidence floor stands as
JEV answered it, because two readers disagreeing is a fact worth keeping and
silently preferring the expensive one destroys it. Only missing and doubtful
fields are replaced, and every replaced field is named in the row.

**Bounded by design.** The escalation rate is the point — a stage that fires on
every article is the cheap classifier deleted and replaced with an expensive
one. The band and the confidence floor are the two dials, both in the
environment, and the rate is logged per run so that a change to either is
visible as a change in spend.

**Off unless configured.** Without `DEEPSEEK_API_KEY` the pipeline behaves
exactly as it did: ambiguous codings land ambiguous, carrying the confidences
that say so, and the queue of articles worth a second reading can be asked for
in Bronze at any time. Nothing waits on this stage and nothing is lost when it
is absent.

The provider is DeepSeek through its OpenAI-compatible chat endpoint. Any other
vendor speaking that dialect — and most do — is a base URL and a model name,
which is why those are settings rather than constants.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from typing import Any

import httpx
import structlog
from pydantic import Field as SettingField
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..storage.root import env_file

log = structlog.get_logger(__name__)


class DeepUnavailable(RuntimeError):
    """The second reader could not be reached, or refused.

    Raised rather than returned for the same reason `JevUnavailable` is: the
    caller does one thing in every case — keep the coding as the classifier
    left it — and only the log line differs.
    """


class DeepSettings(BaseSettings):
    """Where the second reader is, and when it is worth calling."""

    model_config = SettingsConfigDict(
        env_file=env_file(), env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    api_key: SecretStr | None = SettingField(default=None, alias="DEEPSEEK_API_KEY")
    model: str = SettingField(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    base_url: str = SettingField(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")

    #: How near the gate threshold counts as undecided. A gate probability
    #: within this of the threshold is a coin toss the cheap model lost, either
    #: way it fell, and it is exactly the article a person would want read
    #: twice.
    band: float = SettingField(default=0.15, alias="NEWS_DEEP_BAND")

    #: A coded field the classifier chose with less confidence than this is
    #: treated as unanswered. Below the gate threshold deliberately: a field is
    #: one of twenty and a doubtful one does less damage than a doubtful gate,
    #: so the bar for spending money on it is higher.
    min_confidence: float = SettingField(default=0.55, alias="NEWS_DEEP_MIN_CONFIDENCE")

    #: How much of the article the second reader is shown. Larger than JEV's
    #: window because the point of the call is the reading, and a long context
    #: is what is being paid for.
    max_chars: int = SettingField(default=20_000, alias="NEWS_DEEP_MAX_CHARS")

    #: Temperature. Zero: this is extraction, and a coding that changes between
    #: two runs of the same bytes is a coding nothing downstream can trust.
    temperature: float = SettingField(default=0.0, alias="NEWS_DEEP_TEMPERATURE")

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True, slots=True)
class Question:
    """One thing the second reader is asked, and what it may answer.

    `options` is the same vocabulary JEV was offered. Passing it again is not
    belt and braces: a large model asked an open question about an Indonesian
    brawl will write a reasonable category that no human coder ever used, and a
    column holding both is a column that cannot be counted.
    """

    field: str
    instruction: str

    #: The permitted answers, or None for a yes/no.
    options: tuple[str, ...] | None = None

    @property
    def kind(self) -> str:
        return "choice" if self.options else "boolean"


@dataclass(frozen=True, slots=True)
class DeepAnswer:
    """One answer from the second reader.

    Shaped like `jev.Answer` on purpose — `value` and `probability` — so a
    merge can treat the two readers' answers as the same kind of thing.
    """

    field: str
    value: Any
    probability: float | None = None


class DeepReader:
    """A client for one account's large model."""

    def __init__(self, settings: DeepSettings | None = None, *, timeout: float = 180.0) -> None:
        self._settings = settings or DeepSettings()
        self._timeout = timeout

    @property
    def settings(self) -> DeepSettings:
        return self._settings

    def available(self) -> bool:
        """Whether this stage is configured at all.

        A credential check rather than a live call, unlike JEV's. The second
        reader runs on a small fraction of articles, so probing it once per
        process would be a measurable share of its traffic — and a failure at
        call time is handled identically anyway.
        """
        if not self._settings.configured:
            log.debug("news.deep.unconfigured")
            return False
        return True

    def ask(
        self, state: str, questions: tuple[Question, ...], *, context: str = ""
    ) -> dict[str, DeepAnswer]:
        """Put the questions to one article, in one call."""
        key = self._settings.api_key
        if not key:
            raise DeepUnavailable("DEEPSEEK_API_KEY is not set")

        payload = {
            "model": self._settings.model,
            "temperature": self._settings.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": _prompt(
                        state[: self._settings.max_chars], questions, context=context
                    ),
                },
            ],
        }
        try:
            response = httpx.post(
                self._settings.endpoint,
                json=payload,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as error:
            raise DeepUnavailable(f"transport: {error}") from error

        if response.status_code >= 400:
            raise DeepUnavailable(f"HTTP {response.status_code}: {response.text[:200]}")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as error:
            raise DeepUnavailable(f"unreadable reply: {response.text[:200]}") from error

        return _parse(content, questions)


#: What the second reader is told it is. Indonesian, because the articles are,
#: and because a model asked in English about Indonesian prose answers with
#: English categories however firmly the vocabulary is stated.
_SYSTEM = (
    "Anda adalah pengkode (coder) peristiwa kekerasan kolektif di Indonesia. "
    "Anda membaca satu artikel berita dan menjawab pertanyaan pengkodean. "
    "Jawaban HARUS dipilih dari daftar pilihan yang diberikan, persis seperti "
    "tertulis. Jika artikel tidak menyebutkan sesuatu, pilih nilai yang berarti "
    "tidak jelas — jangan menebak. Balas HANYA dengan JSON."
)


def _prompt(text: str, questions: tuple[Question, ...], *, context: str = "") -> str:
    """The article, the questions, and the shape of the answer."""
    lines = [
        "ARTIKEL:",
        text,
        "",
    ]
    if context:
        lines += ["CATATAN DARI PENGKODE PERTAMA (model kecil):", context, ""]
    lines.append("PERTANYAAN:")
    for question in questions:
        lines.append(f"- {question.field}: {question.instruction}")
        if question.options:
            lines.append(f"  pilihan: {json.dumps(list(question.options), ensure_ascii=False)}")
        else:
            lines.append("  pilihan: true atau false")
    lines += [
        "",
        "Balas dengan JSON berbentuk:",
        '{"fields": {"<nama_pertanyaan>": {"value": <jawaban>, '
        '"confidence": <0.0-1.0>}}, "notes": "<ringkasan satu kalimat>"}',
        "",
        "`confidence` adalah seberapa yakin Anda pada jawaban itu berdasarkan "
        "isi artikel, bukan seberapa umum peristiwanya.",
    ]
    return "\n".join(lines)


def _parse(content: str, questions: tuple[Question, ...]) -> dict[str, DeepAnswer]:
    """Read the reply, and drop anything that is not an allowed answer.

    A value outside the vocabulary is discarded rather than recorded: the whole
    reason for handing the model a closed list is that the column stays
    comparable with the human record, and a plausible invention is the failure
    this guards against, not a malformed reply.
    """
    try:
        body = json.loads(content)
    except ValueError as error:
        raise DeepUnavailable(f"non-JSON answer: {content[:200]}") from error

    fields = body.get("fields")
    if not isinstance(fields, dict):
        raise DeepUnavailable(f"no fields in answer: {content[:200]}")

    asked = {question.field: question for question in questions}
    answers: dict[str, DeepAnswer] = {}
    for name, payload in fields.items():
        question = asked.get(name)
        if question is None:
            continue
        value, probability = _value(payload)
        if value is None:
            continue
        if question.options is not None:
            picked = _in_vocabulary(str(value), question.options)
            if picked is None:
                log.info("news.deep.out-of-vocabulary", field=name, value=str(value)[:80])
                continue
            answers[name] = DeepAnswer(field=name, value=picked, probability=probability)
        else:
            truth = _as_bool(value)
            if truth is None:
                continue
            answers[name] = DeepAnswer(field=name, value=truth, probability=probability)

    notes = body.get("notes")
    if isinstance(notes, str) and notes.strip():
        answers["_notes"] = DeepAnswer(field="_notes", value=notes.strip()[:500])
    return answers


def _value(payload: Any) -> tuple[Any, float | None]:
    """A field's value and confidence, however the model wrapped them."""
    if isinstance(payload, dict):
        confidence = payload.get("confidence")
        try:
            probability = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            probability = None
        return payload.get("value"), probability
    # A model that answered with the bare value rather than the envelope is
    # answering the question; refusing it would waste the call over a wrapper.
    return payload, None


def _in_vocabulary(value: str, options: tuple[str, ...]) -> str | None:
    """Match an answer to an option, tolerating case and stray punctuation."""
    stripped = value.strip()
    for option in options:
        if stripped == option:
            return option
    folded = stripped.casefold()
    for option in options:
        if folded == option.casefold():
            return option
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        folded = value.strip().casefold()
        if folded in ("true", "ya", "iya", "yes", "1"):
            return True
        if folded in ("false", "tidak", "no", "0"):
            return False
    return None


@cache
def shared() -> DeepReader:
    """One client per process."""
    return DeepReader()
