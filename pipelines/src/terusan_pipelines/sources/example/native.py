"""A source written against the interface directly.

The shape to copy for anything new. Note what is absent: no path building, no
hashing, no metadata file, no retry loop. The runner does all of it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date

from ..base import (
    Artifact,
    Category,
    CollectionMethod,
    ScrapeContext,
    Source,
    SourceMeta,
    SourceType,
    UpdateFrequency,
)


class ExampleStatistics(Source):
    meta = SourceMeta(
        slug="example-statistics",
        name="Example — Monthly Statistics",
        organization="Example Agency",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        base_url="https://example.invalid/api",
        license="CC-BY-4.0",
        update_frequency=UpdateFrequency.MONTHLY,
        # A template for writing a source, not a publisher: its host is
        # `example.invalid`, and a scheduled run of it lands made-up figures.
        active=False,
        # Politeness ceiling for this source. The runner limits per host, so a
        # low number here does not slow down sources on other hosts.
        max_requests_per_second=2.0,
        # Second day of every month, 03:00.
        schedule="0 3 2 * *",
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        for month in self._months(ctx):
            payload = {"period": month.isoformat(), "value": 1.0 + month.month / 10}

            # Real sources fetch here. Route requests through the shared
            # limiter so every source hitting this host queues together:
            #
            #     from ..runner import wait_for_slot
            #     wait_for_slot(limiter, url)
            #     response = client.get(url)

            yield Artifact(
                content=json.dumps(payload).encode(),
                filename=f"{month.isoformat()}.json",
                dataset="monthly-index",
                # Partition on what prunes queries, not on identifiers
                # (program.md §46).
                partition=(f"year={month.year}",),
                source_url=f"{self.meta.base_url}/index/{month.isoformat()}",
                media_type="application/json",
                published_at=month,
                metadata={"api_version": "v1"},
            )

    def _months(self, ctx: ScrapeContext) -> list[date]:
        """Honour `since` so scheduled runs stay incremental."""
        months = [date(2026, m, 1) for m in range(1, 7)]
        if ctx.since:
            months = [m for m in months if m >= ctx.since]
        return months
