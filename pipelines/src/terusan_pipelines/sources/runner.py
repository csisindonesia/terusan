"""Running sources, one or many.

The runner owns everything a scraper should not have to think about: how many
run at once, how hard a host gets hit, what happens when one raises, and what
gets recorded about the attempt (program.md §39).
"""

from __future__ import annotations

import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog

from ..storage import StorageConfig, StorageResolver
from .base import ScrapeContext, Source
from .landing import Landing
from .ratelimit import HostRateLimiter
from .sniff import ContentMismatch

if TYPE_CHECKING:
    from ..catalog import Reporter

log = structlog.get_logger(__name__)


class RunStatus(StrEnum):
    """Mirrors the `run_status` enum in db/migrations/002."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class RunResult:
    """What one source's run produced. Maps onto a `pipeline_runs` row."""

    source_slug: str
    status: RunStatus = RunStatus.PENDING

    artifacts_seen: int = 0
    artifacts_landed: int = 0
    artifacts_deduplicated: int = 0
    #: Fetched but refused at the door: the bytes were not the format claimed,
    #: usually an error page served with a 200.
    artifacts_rejected: int = 0
    bytes_written: int = 0

    error_message: str | None = None
    error_traceback: str | None = None

    started_at: datetime | None = None
    finished_at: datetime | None = None
    landed_paths: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def succeeded(self) -> bool:
        return self.status is RunStatus.SUCCEEDED


class Runner:
    """Executes sources and lands what they yield."""

    def __init__(
        self,
        resolver: StorageResolver | None = None,
        *,
        max_workers: int = 4,
        default_rate: float = 1.0,
        keep_paths: bool = False,
        reporter: Reporter | None = None,
        trigger: str = "manual",
    ) -> None:
        self._resolver = resolver or StorageResolver(StorageConfig())
        self._landing = Landing(self._resolver)
        self._limiter = HostRateLimiter(default_rate=default_rate)
        self._max_workers = max_workers
        # Defaults to recording nothing, so a run works with no database.
        self._reporter = reporter
        self._trigger = trigger
        # A full crawl can land hundreds of thousands of files; holding every
        # path is useful for tests and untenable in production.
        self._keep_paths = keep_paths

    @property
    def limiter(self) -> HostRateLimiter:
        """Shared limiter, so sources can pass it to their HTTP clients."""
        return self._limiter

    def run_one(self, source: Source, ctx: ScrapeContext | None = None) -> RunResult:
        """Run a single source to completion, capturing any failure.

        A raising scraper is an expected outcome — portals change layout
        without notice — so it becomes a failed result rather than an
        exception, and one bad source does not abort a batch of fifty.
        """
        context = ctx or ScrapeContext()
        result = RunResult(source_slug=source.meta.slug, status=RunStatus.RUNNING)
        result.started_at = datetime.now(UTC)
        bound = log.bind(source=source.meta.slug, dry_run=context.dry_run)
        bound.info("run.started")

        try:
            for artifact in source.collect(context):
                result.artifacts_seen += 1
                if context.dry_run:
                    continue

                try:
                    landed = self._landing.land(source.meta, artifact)
                except ContentMismatch as exc:
                    # One bad file must not discard the other hundred, but it
                    # must be visible rather than quietly missing.
                    result.artifacts_rejected += 1
                    bound.warning("landing.rejected", error=str(exc))
                    continue

                if landed.deduplicated:
                    result.artifacts_deduplicated += 1
                else:
                    result.artifacts_landed += 1
                    result.bytes_written += landed.bytes_written
                if self._keep_paths:
                    result.landed_paths.append(landed.path)

                if context.limit is not None and result.artifacts_seen >= context.limit:
                    break

            result.status = RunStatus.SUCCEEDED
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            result.status = RunStatus.FAILED
            result.error_message = f"{type(exc).__name__}: {exc}"
            result.error_traceback = traceback.format_exc()
            bound.error("run.failed", error=result.error_message)
        finally:
            result.finished_at = datetime.now(UTC)

        self._report(source, result)

        if result.succeeded:
            bound.info(
                "run.finished",
                seen=result.artifacts_seen,
                landed=result.artifacts_landed,
                deduplicated=result.artifacts_deduplicated,
                rejected=result.artifacts_rejected,
                seconds=round(result.duration_seconds or 0, 2),
            )
        return result

    def _report(self, source: Source, result: RunResult) -> None:
        """Record the run, never letting the catalog break the ingestion.

        The lake already has the data by this point. A catalog that is down,
        slow or mid-migration must not turn a successful acquisition into a
        failed one.
        """
        if self._reporter is None:
            return
        try:
            self._reporter.source_run(source.meta, result, self._trigger)
        except Exception as exc:  # noqa: BLE001 - history is not worth the data
            log.warning("catalog.report_failed", source=source.meta.slug, error=str(exc))

    def run_many(self, sources: list[Source], ctx: ScrapeContext | None = None) -> list[RunResult]:
        """Run sources concurrently, respecting each one's safety.

        Sources declaring `concurrency_safe = False` — legacy scripts, which
        move the process working directory — run first and alone. Everything
        else runs in the pool. Rate limiting is per host inside the pool, so
        raising `max_workers` does not raise the load on any single server.
        """
        serial = [s for s in sources if not getattr(s, "concurrency_safe", True)]
        parallel = [s for s in sources if getattr(s, "concurrency_safe", True)]
        results: list[RunResult] = []

        if serial:
            log.info("run.serial_phase", count=len(serial))
            results.extend(self.run_one(source, ctx) for source in serial)

        if parallel:
            log.info("run.parallel_phase", count=len(parallel), workers=self._max_workers)
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                futures = {pool.submit(self.run_one, s, ctx): s for s in parallel}
                for future in as_completed(futures):
                    results.append(future.result())

        return sorted(results, key=lambda r: r.source_slug)


def summarize(results: list[RunResult]) -> dict[str, object]:
    """Condense a batch into something worth printing or alerting on."""
    failed = [r for r in results if r.status is RunStatus.FAILED]
    return {
        "sources": len(results),
        "succeeded": sum(1 for r in results if r.succeeded),
        "failed": len(failed),
        "artifacts_landed": sum(r.artifacts_landed for r in results),
        "artifacts_deduplicated": sum(r.artifacts_deduplicated for r in results),
        "artifacts_rejected": sum(r.artifacts_rejected for r in results),
        "bytes_written": sum(r.bytes_written for r in results),
        "failures": {r.source_slug: r.error_message for r in failed},
    }


def wait_for_slot(limiter: HostRateLimiter, url: str) -> None:
    """Block until `url`'s host allows another request.

    Exposed so a scraper using its own HTTP client still shares the limiter
    with every other source hitting that host.
    """
    started = time.monotonic()
    limiter.acquire(url)
    waited = time.monotonic() - started
    if waited > 1:
        log.debug("ratelimit.waited", url=url, seconds=round(waited, 2))
