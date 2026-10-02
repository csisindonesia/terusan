"""The HTTP client sources fetch through.

Two retry levels exist and conflating them is a classic bug. In-run retry, here,
handles transient faults — timeouts, resets, 5xx, 429 — inside one process.
Cross-run retry is the scheduler starting a new run, recorded in the catalog.
This module is only the first.

4xx other than 408 and 429 are never retried: they mean the source changed or
our code is wrong, and retrying only hammers someone else's server.

Every request passes the shared per-host limiter first, so a source with its own
client still queues behind every other source hitting that host.

Two safety rules sit here too, because this is the one place every source's
traffic goes through. URLs are redacted before they are logged, since BPS and
others put their API key in the URL itself and an error message quotes the URL
in full. And a client opened by `fetcher` refuses to talk to anything but a
public host, on the first request and on every redirect after it: a sitemap or
a portal that redirects to `http://postgres:5432/` or `169.254.169.254` must not
turn the pipelines container into a probe of the network it runs in.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

import httpx
import structlog
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .ratelimit import HostRateLimiter

log = structlog.get_logger(__name__)

#: Worth trying again: the server is overloaded, rate limiting us, or a proxy
#: failed. Not 404 or 403, which will fail identically next time.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504, 507, 509})

DEFAULT_ATTEMPTS = 5
DEFAULT_BACKOFF_SECONDS = 5.0
DEFAULT_MAX_BACKOFF_SECONDS = 300.0
DEFAULT_TIMEOUT_SECONDS = 60.0

USER_AGENT = "terusan/0.1 (research data collection)"

#: What stands in for a secret in a logged URL.
REDACTED = "REDACTED"

#: A query parameter whose value is a credential: `key`, `apikey`, `api_key`,
#: `access_token`, `client_secret` and the like. Anchored on the whole name, so
#: `keyword` or `tokens_per_page` keep their values and stay useful in a log.
_SECRET_PARAM = re.compile(
    r"(?i)([?&;](?:[\w.-]*[_-])?(?:api[_-]?key|key|token|secret|password|passwd|signature|sig)=)"
    r"[^&#;\s'\"]*"
)

#: A credential carried as a path segment, the way BPS writes `.../key/<key>/`.
_SECRET_SEGMENT = re.compile(r"(?i)(/(?:api[_-]?key|key|token|secret)/)[^/?#\s'\"]+")


def redact(text: str) -> str:
    """`text` — a URL, or an error message quoting one — with its secrets blanked.

    A pattern over the whole string rather than a URL parser, because what
    gets logged is usually not a URL but an httpx message with the URL inside
    it, and a secret is just as leaked from the middle of a sentence.
    """
    text = _SECRET_SEGMENT.sub(rf"\1{REDACTED}", text)
    return _SECRET_PARAM.sub(rf"\1{REDACTED}", text)


def _describe(exc: BaseException | None, limit: int = 200) -> str | None:
    """An exception as a log field: redacted first, then cut to length.

    Redacting before cutting matters. Cut first, and a key that straddles the
    cut leaves its first half in the log, where no pattern recognises it.
    """
    return redact(str(exc))[:limit] if exc is not None else None


class UnsafeURLError(httpx.RequestError):
    """A request to a host that is not on the public internet, refused unsent.

    A `RequestError` so every caller that already treats a failed fetch as a
    failed fetch — `try_get`, the news crawl, the portal engines — does so here
    too. Deliberately not a `TransportError`: that would make it transient, and
    retrying a refusal five times with backoff only wastes a run.
    """


def _public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # An IPv4 address written as IPv6 (`::ffff:127.0.0.1`) is judged as the
    # IPv4 address it is, or it slips past every IPv4 rule below.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


@lru_cache(maxsize=1024)
def _resolved_public(host: str) -> bool:
    """Whether every address `host` resolves to is public.

    Every address, not the first: a name with one public and one private
    record is a name httpx may connect to on the private one. Cached because a
    crawl asks the same few hosts thousands of times; a lookup that fails
    raises instead, and an exception is not cached, so a DNS blip is retried
    on the next request rather than remembered.
    """
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return all(_public_ip(ipaddress.ip_address(info[4][0].split("%", 1)[0])) for info in infos)


def ensure_public_url(url: httpx.URL | str) -> None:
    """Raise `UnsafeURLError` unless `url` is http(s) to a public host.

    Refused: any other scheme; a bare single-label name such as `api`,
    `postgres` or `redis`, which on the NAS are the other containers on the
    compose network; and a host that is, or resolves to, a private, loopback,
    link-local, multicast, reserved or unspecified address. A name that does not
    resolve at all is let through, so that it fails the way it always has — as
    a transient connection error httpx raises and the retry policy handles.

    This checks the name, not the connection, so a host that answers its DNS
    lookup one way here and another way when httpx connects is not caught. That
    attack needs a hostile DNS server rather than a hostile page, and closing it
    would mean pinning the address inside the transport.
    """
    try:
        parsed = httpx.URL(url) if isinstance(url, str) else url
    except httpx.InvalidURL as exc:
        raise UnsafeURLError(f"refused a malformed URL: {exc}") from exc
    shown = redact(str(parsed))
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError(f"refused {shown}: only http and https are fetched")
    host = parsed.host.rstrip(".").lower()
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _public_ip(literal):
            raise UnsafeURLError(f"refused {shown}: {host} is not a public address")
        return
    if "." not in host:
        raise UnsafeURLError(f"refused {shown}: {host!r} is not a public host name")
    try:
        public = _resolved_public(host)
    except (OSError, UnicodeError):
        return
    if not public:
        raise UnsafeURLError(f"refused {shown}: {host} resolves to a non-public address")


def _refuse_private(request: httpx.Request) -> None:
    # An httpx request hook runs before the first request and again before
    # every redirect hop, which is the point: a public page that answers with
    # a redirect to an internal one is the usual shape of this attack.
    ensure_public_url(request.url)


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    if isinstance(exc, httpx.TransportError):
        # Timeouts, connection resets, DNS failures, exhausted pools.
        return True
    return getattr(exc, "transient", False) is True


def _log_retry(state: RetryCallState) -> None:
    exc = state.outcome.exception() if state.outcome else None
    log.warning(
        "http.retry",
        attempt=state.attempt_number,
        sleep_seconds=round(state.idle_for, 1),
        error=_describe(exc),
    )


def retrying(
    attempts: int = DEFAULT_ATTEMPTS,
    initial_seconds: float = DEFAULT_BACKOFF_SECONDS,
    max_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
) -> Retrying:
    """A tenacity controller with the policy above.

    Jittered backoff rather than plain exponential: without jitter, a hundred
    tables that all failed at once all retry at once.
    """
    return Retrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential_jitter(initial=initial_seconds, max=max_seconds),
        retry=retry_if_exception(is_transient),
        before_sleep=_log_retry,
        reraise=True,
    )


def retry_after(response: httpx.Response) -> float | None:
    """Honour a server's Retry-After header. Staying polite keeps us unblocked."""
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        # The HTTP-date form. Backoff is close enough, and parsing dates here
        # is not worth the surface.
        return None


class Fetcher:
    """An HTTP client that retries, and waits its turn per host."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        limiter: HostRateLimiter | None = None,
        attempts: int = DEFAULT_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
    ) -> None:
        self._client = client
        self._limiter = limiter
        self._attempts = attempts
        self._backoff = backoff_seconds
        self._max_backoff = max_backoff_seconds

    @property
    def client(self) -> httpx.Client:
        return self._client

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """Fetch a URL, retrying transient failures.

        Raises on a non-transient error rather than returning it: a caller that
        wants to keep going past one bad URL says so explicitly, which is what
        `try_get` is for.
        """
        for attempt in retrying(self._attempts, self._backoff, self._max_backoff):
            with attempt:
                if self._limiter is not None:
                    self._limiter.acquire(url)
                response = self._client.get(url, **kwargs)
                response.raise_for_status()
                return response
        raise AssertionError("unreachable: retrying re-raises once attempts are spent")

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """`get`, for a portal that pages by POST — a Livewire component
        answers its next page only to a call on `/livewire/update`."""
        for attempt in retrying(self._attempts, self._backoff, self._max_backoff):
            with attempt:
                if self._limiter is not None:
                    self._limiter.acquire(url)
                response = self._client.post(url, **kwargs)
                response.raise_for_status()
                return response
        raise AssertionError("unreachable: retrying re-raises once attempts are spent")

    def try_get(self, url: str, **kwargs: Any) -> httpx.Response | None:
        """`get`, returning None instead of raising.

        For fanning out over many URLs where one failure must not discard the
        rest — the shape of every index-page source.
        """
        try:
            return self.get(url, **kwargs)
        except (httpx.HTTPError, OSError) as exc:
            log.warning("http.failed", url=redact(url), error=_describe(exc))
            return None


@contextmanager
def fetcher(
    *,
    limiter: HostRateLimiter | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    headers: dict[str, str] | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
    **client_kwargs: Any,
) -> Iterator[Fetcher]:
    """Open a Fetcher and close its connections afterwards.

    The client refuses non-public hosts (see `ensure_public_url`). A test that
    wants to point a source at a stub builds its own `Fetcher` around a client
    with a mock transport, as the suite does throughout, rather than this.
    """
    hooks = dict(client_kwargs.pop("event_hooks", None) or {})
    hooks["request"] = [_refuse_private, *hooks.get("request", ())]
    with httpx.Client(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
        follow_redirects=True,
        event_hooks=hooks,
        **client_kwargs,
    ) as client:
        yield Fetcher(client, limiter=limiter, attempts=attempts)
