"""Per-host rate limiting.

Limits are keyed by host, not by source. Several sources routinely sit behind
one server — a statistics portal, its document archive and its API can share a
domain — and a server's tolerance is a property of that server. Keying by
source is how ten "polite" scrapers run concurrently and together hammer one
host off the air.
"""

from __future__ import annotations

import threading
import time
from urllib.parse import urlparse


class TokenBucket:
    """A refilling token bucket, safe across threads.

    `burst` allows a short run of requests at full speed — fetching a listing
    page and its first few documents — before settling to `rate`.
    """

    def __init__(self, rate: float, burst: int = 1) -> None:
        if rate <= 0:
            raise ValueError(f"rate must be positive, got {rate}")
        self._rate = rate
        self._capacity = max(1, burst)
        self._tokens = float(self._capacity)
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float | None = None) -> bool:
        """Block until a token is available. Returns False on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._updated) * self._rate
                )
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return True
                wait = (1 - self._tokens) / self._rate

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                wait = min(wait, remaining)
            time.sleep(wait)


class HostRateLimiter:
    """Hands out a bucket per host, created on first use."""

    def __init__(self, default_rate: float = 1.0, burst: int = 2) -> None:
        self._default_rate = default_rate
        self._burst = burst
        self._overrides: dict[str, float] = {}
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def set_rate(self, host: str, rate: float) -> None:
        """Override the rate for one host. Takes effect on the next bucket."""
        with self._lock:
            self._overrides[host] = rate
            self._buckets.pop(host, None)

    def acquire(self, url: str, timeout: float | None = None) -> bool:
        """Wait for permission to request `url`."""
        return self._bucket(host_of(url)).acquire(timeout=timeout)

    def _bucket(self, host: str) -> TokenBucket:
        with self._lock:
            bucket = self._buckets.get(host)
            if bucket is None:
                rate = self._overrides.get(host, self._default_rate)
                bucket = TokenBucket(rate=rate, burst=self._burst)
                self._buckets[host] = bucket
            return bucket


def host_of(url: str) -> str:
    """Extract the host from a URL, falling back to the whole string.

    A malformed URL still gets rate limited, under its own key, rather than
    slipping past the limiter entirely.
    """
    parsed = urlparse(url)
    return parsed.netloc.lower() or url
