"""The HTTP client sources fetch through.

Two retry levels exist and conflating them is a classic bug. In-run retry, here,
handles transient faults — timeouts, resets, 5xx, 429 — inside one process.
Cross-run retry is the scheduler starting a new run, recorded in the catalog.
This module is only the first.

4xx other than 408 and 429 are never retried: they mean the source changed or
our code is wrong, and retrying only hammers someone else's server.

Every request passes the shared per-host limiter first, so a source with its own
client still queues behind every other source hitting that host.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
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
        error=str(exc)[:200] if exc else None,
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

    def try_get(self, url: str, **kwargs: Any) -> httpx.Response | None:
        """`get`, returning None instead of raising.

        For fanning out over many URLs where one failure must not discard the
        rest — the shape of every index-page source.
        """
        try:
            return self.get(url, **kwargs)
        except (httpx.HTTPError, OSError) as exc:
            log.warning("http.failed", url=url, error=str(exc)[:200])
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
    """Open a Fetcher and close its connections afterwards."""
    with httpx.Client(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
        follow_redirects=True,
        **client_kwargs,
    ) as client:
        yield Fetcher(client, limiter=limiter, attempts=attempts)
