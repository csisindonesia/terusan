"""One Chromium, shared.

Two jobs need a real browser. Some outlets answer plain HTTP with 403 and only
serve a page to something that looks like a person — three in the list do, and
dropping them would lose three provinces. And every article that passes the
lexicon gate is screenshotted, because the page as it looked on the day is what
a human verifier reads, and an HTML archive does not show a paywall, a correction
banner or a photograph.

Shared because starting Chromium costs seconds and a crawl shard visits dozens
of pages. Lazily, because most runs of most other sources never need it and
importing this module should not cost a browser.
"""

from __future__ import annotations

import threading
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: Long enough for a news page's own scripts to settle, short enough that a
#: page which never settles does not hold the shard.
DEFAULT_SETTLE_MS = 1200
DEFAULT_TIMEOUT_MS = 35_000

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_lock = threading.Lock()
_shared: Browser | None = None


class BrowserUnavailable(RuntimeError):
    """Playwright is not installed, or Chromium was never downloaded.

    A recoverable state rather than a failure: the crawl carries on over plain
    HTTP, loses the outlets that need rendering, and says so.
    """


class Browser:
    """A headless Chromium with one page reused across requests."""

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    def start(self) -> Browser:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:  # pragma: no cover - depends on the extra
            raise BrowserUnavailable("playwright is not installed") from error
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self._headless)
            self._context = self._browser.new_context(
                user_agent=USER_AGENT, viewport={"width": 1366, "height": 900}
            )
        except Exception as error:  # noqa: BLE001 - playwright raises its own types
            raise BrowserUnavailable(str(error)[:200]) from error
        return self

    def _ready(self) -> Any:
        if self._context is None:
            self.start()
        return self._context

    def render(self, url: str, *, settle_ms: int = DEFAULT_SETTLE_MS) -> tuple[int, str]:
        """Fetch a page as a browser would. Returns the status and the HTML."""
        page = self._ready().new_page()
        try:
            response = page.goto(url, timeout=DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_timeout(settle_ms)
            return (response.status if response else 0), page.content()
        except Exception as error:  # noqa: BLE001
            log.warning("news.browser.render-failed", url=url, error=str(error)[:200])
            return 0, ""
        finally:
            page.close()

    def screenshot(self, url: str, *, settle_ms: int = DEFAULT_SETTLE_MS) -> bytes | None:
        """A full-page PNG of the article as it stands.

        Full page rather than viewport: a screenshot that stops above the fold
        does not show what the article said, which is the only reason to keep
        one.
        """
        page = self._ready().new_page()
        try:
            page.goto(url, timeout=DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_timeout(settle_ms)
            return page.screenshot(full_page=True, type="png")
        except Exception as error:  # noqa: BLE001
            log.warning("news.browser.shot-failed", url=url, error=str(error)[:200])
            return None
        finally:
            page.close()

    def close(self) -> None:
        for handle in (self._context, self._browser, self._playwright):
            try:
                if handle is not None:
                    (handle.stop if hasattr(handle, "stop") else handle.close)()
            except Exception:  # noqa: BLE001, S110 - shutdown must not fail a run
                pass
        self._context = self._browser = self._playwright = None


def shared() -> Browser:
    """The process's browser, started on first use."""
    global _shared
    with _lock:
        if _shared is None:
            _shared = Browser().start()
        return _shared


def close_shared() -> None:
    """Shut the browser down at the end of a run."""
    global _shared
    with _lock:
        if _shared is not None:
            _shared.close()
            _shared = None
