"""JEV: the classifier that reads an article and answers typed questions.

`typesafe/jev` runs on Cloudflare Workers AI and answers three kinds of
question — `noul` (a yes/no with a calibrated probability), `choice` (one of up
to 255 options, with a probability per option) and `score`. It does not emit
free text, numbers or dates. That is the point: every answer is a value from a
vocabulary the caller declared, so a coding cannot invent a category that no
human coder ever used, and every answer arrives with a number saying how sure
it is.

What it cannot do is read a casualty count or a village name out of prose.
Those come from `news.extract_fields`, which is ordinary parsing, and the split
is worth keeping clear: the model decides between declared alternatives, the
parser reads what is literally written.

The model bills through AI Gateway unified billing. A request that returns a
2021 error is an account without credit rather than a broken call, which is
`JevBillingError` — a different thing to retry and a different thing to tell
somebody about.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from typing import Any

import httpx
import structlog
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..storage.root import env_file

log = structlog.get_logger(__name__)


class JevUnavailable(RuntimeError):
    """The classifier could not be reached, or refused the call.

    Raised rather than returned because the caller's response is the same in
    every case: keep the article as a candidate and code it later. What
    differs is only what gets written in the log.
    """


class JevBillingError(JevUnavailable):
    """Partner models bill through AI Gateway unified billing.

    The account needs credit loaded, or a TypeSafe key attached to the gateway
    as BYOK. Distinguished from every other failure because no amount of
    retrying fixes it and the fix is not in this repository.
    """


class JevSettings(BaseSettings):
    """Where the classifier is and what talks to it."""

    model_config = SettingsConfigDict(
        env_file=env_file(), env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    account_id: str = Field(default="", alias="CF_ACCOUNT_ID")
    token: SecretStr | None = Field(default=None, alias="JEV_CLOUDFLARE")
    model: str = Field(default="typesafe/jev", alias="JEV_MODEL")

    #: Optional: route through a named AI Gateway instead of the account
    #: default, which is how per-project spend gets its own meter.
    gateway_id: str = Field(default="", alias="AI_GATEWAY_ID")

    #: A `noul` at or above this is treated as a yes by the gate. 0.6 rather
    #: than 0.5 because a false positive costs a coder's time reading a wrong
    #: article, and a false negative costs one article out of many reporting
    #: the same incident.
    gate_threshold: float = Field(default=0.6, alias="JEV_GATE_THRESHOLD")

    @property
    def endpoint(self) -> str:
        """The universal `ai/run` endpoint.

        Third-party models are only reachable through the account-level route
        with a `{model, input}` envelope; the per-model path `/ai/run/{vendor}/
        {model}` exists for `@cf/` models alone.
        """
        if self.gateway_id:
            return (
                f"https://gateway.ai.cloudflare.com/v1/{self.account_id}/"
                f"{self.gateway_id}/compat/ai/run"
            )
        return f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run"


@dataclass(frozen=True, slots=True)
class Answer:
    """One question's answer, with how sure the model was.

    `probability` is the `noul` reading for a yes/no and the winning option's
    share for a choice. It is kept on every coded field rather than only on the
    gate, because a field the model was unsure of is exactly what a human
    verifier should be shown first.
    """

    field: str
    value: Any
    probability: float | None = None
    confidence: float | None = None
    distribution: dict[str, float] | None = None


def noul(instructions: str, yes: str, no: str) -> dict[str, Any]:
    """A yes/no question. `criteria` names what each answer would mean."""
    return {"type": "noul", "instructions": instructions, "criteria": {"true": yes, "false": no}}


def choice(instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
    """A pick-one question over a declared vocabulary."""
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


class Jev:
    """A client for one account's classifier."""

    def __init__(self, settings: JevSettings | None = None, *, timeout: float = 120.0) -> None:
        self._settings = settings or JevSettings()
        self._timeout = timeout
        self._available: bool | None = None

    @property
    def settings(self) -> JevSettings:
        return self._settings

    def available(self) -> bool:
        """Whether the classifier answers at all, asked once per process.

        A real question rather than a credential check: the token can be valid,
        the account can exist, and the partner model still not be enabled.
        """
        if self._available is not None:
            return self._available
        if not (self._settings.account_id and self._settings.token):
            log.warning("jev.unconfigured", have_account=bool(self._settings.account_id))
            self._available = False
            return False
        try:
            self.ask("tes ketersediaan model", {"ok": noul("Apakah ini teks?", "Ya", "Tidak")})
            self._available = True
        except JevUnavailable as error:
            log.warning("jev.unavailable", error=str(error)[:200])
            self._available = False
        return self._available

    def ask(self, state: str, questions: dict[str, dict[str, Any]]) -> dict[str, Answer]:
        """Put a question set to one piece of text.

        All the questions go in one call. Asking them separately would re-read
        the article once per field, at one article's token cost each — for the
        violence profile that is some thirty times the spend for the same
        answers.
        """
        token = self._settings.token
        if not (self._settings.account_id and token):
            raise JevUnavailable("CF_ACCOUNT_ID or JEV_CLOUDFLARE is not set")

        payload = {"model": self._settings.model, "input": {"state": state, "questions": questions}}
        try:
            response = httpx.post(
                self._settings.endpoint,
                json=payload,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {token.get_secret_value()}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as error:
            raise JevUnavailable(f"transport: {error}") from error

        if response.status_code >= 400 and response.status_code not in (400, 403, 404):
            raise JevUnavailable(f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as error:
            raise JevUnavailable(f"non-JSON response: {response.text[:200]}") from error

        if not body.get("success", True):
            errors = body.get("errors") or []
            codes = {e.get("code") for e in errors if isinstance(e, dict)}
            message = json.dumps(errors or body)[:300]
            if 2021 in codes:
                raise JevBillingError(message)
            raise JevUnavailable(message)

        return _parse(body.get("result", body))


def _unwrap(result: Any) -> dict[str, Any]:
    """Peel the Workers AI envelope down to the answers.

    The reply nests `result.result.answers`, and the gateway route nests it one
    deeper again, so this descends until it finds the answers rather than
    indexing a fixed number of times.
    """
    node = result
    for _ in range(4):
        if not isinstance(node, dict):
            return {}
        if "answers" in node:
            answers = node["answers"]
            return answers if isinstance(answers, dict) else {}
        node = node.get("result", {})
    return {}


def _parse(result: Any) -> dict[str, Answer]:
    """Turn the model's reply into one `Answer` per question asked."""
    answers: dict[str, Answer] = {}
    for field, payload in _unwrap(result).items():
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if kind == "noul":
            probability = _as_float(payload.get("noul"))
            answers[field] = Answer(field=field, value=probability, probability=probability)
        elif kind == "choice":
            distribution = payload.get("probabilities")
            distribution = distribution if isinstance(distribution, dict) else None
            value = payload.get("choice")
            answers[field] = Answer(
                field=field,
                value=value,
                probability=_as_float((distribution or {}).get(value)),
                confidence=_as_float(payload.get("confidence")),
                distribution=distribution,
            )
        elif kind == "score":
            score = _as_float(payload.get("score"))
            answers[field] = Answer(field=field, value=score, confidence=score)
    return answers


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@cache
def shared() -> Jev:
    """One client per process, so availability is probed once."""
    return Jev()
