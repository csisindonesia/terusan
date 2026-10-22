"""What an observatory adapter is, and the parts every adapter shares.

The spec asks each provider's adapter for `discover()`, `fetch()`, `parse()`,
`validate()` and `normalize()`. In this warehouse the first three already have
homes, and an adapter points at them rather than doing them again:

- `discover()` and `fetch()` are the provider's sources. They find the current
  report, land it in RAW and record its provenance; `discover()` returns them
  and `fetch()` runs their collection without landing, for inspection.
- `parse()` is the provider's extractor, which turns a landed report into
  Bronze records naming the publisher's own labels.
- `normalize()` and `validate()` are this module's: one Bronze record into one
  observation — or a quarantine entry saying why not — and then the checks
  that need more than one row, such as whether a published total agrees with
  the figures it totals.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ...sources.base import Artifact, ScrapeContext, Source
from ...sources.registry import registry
from .. import taxonomy as tx
from ..model import Observation, Quarantined
from ..quality import parse_value


@dataclass(frozen=True, slots=True)
class BronzeRow:
    """One Bronze record and the provenance it was extracted with."""

    source_id: str
    dataset: str
    columns: dict[str, str]
    document_id: str
    content_hash: str
    raw_path: str | None
    source_url: str | None
    retrieved_at: datetime | None
    parser_version: str | None
    source_report_id: str

    def get(self, key: str) -> str:
        return (self.columns.get(key) or "").strip()


class Adapter(ABC):
    """One provider's path from landed report to observations."""

    provider: tx.Provider

    #: The sources that discover and fetch this provider's reports.
    sources: tuple[str, ...] = ()

    #: Said once per provider, on every source report row.
    license_notes: str | None = None
    methodology_notes: str | None = None

    #: The extractor class that parses this provider's reports, by name — for
    #: `extraction_method` on each row.
    extractor: str = ""

    # -- discover / fetch / parse -------------------------------------------

    def discover(self) -> tuple[type[Source], ...]:
        registry.ensure_loaded()
        return tuple(registry.get(slug) for slug in self.sources)

    def fetch(self, ctx: ScrapeContext | None = None) -> Iterator[Artifact]:
        """Collect without landing. `terusan sources run <slug>` lands."""
        for source in self.discover():
            yield from source().collect(ctx or ScrapeContext(dry_run=True))

    def parse(self, landed: Any) -> Iterator[dict[str, Any]]:
        from ...extract.runner import DEFAULT_EXTRACTORS

        for extractor in DEFAULT_EXTRACTORS:
            if extractor.handles(landed):
                yield from extractor.extract(landed)
                return

    # -- normalize / validate ----------------------------------------------

    @abstractmethod
    def normalize(self, row: BronzeRow) -> Observation | Quarantined | None:
        """One Bronze record as an observation, or the reason it is not one.

        None means the record is held for `validate` — a published total, which
        is evidence about other rows rather than a figure of its own.
        """

    def validate(
        self, observations: Sequence[Observation]
    ) -> tuple[list[Observation], list[Quarantined]]:
        """Checks across rows. Returns what is kept and what is quarantined.

        The default keeps everything; a provider whose reports state totals
        beside their components overrides it to check one against the other.
        """
        return list(observations), []

    def source_method(self, media_type: str | None, extra: dict[str, Any]) -> str:
        if extra.get("source_method") == "manual":
            return tx.SourceMethod.MANUAL.value
        if media_type == "application/pdf":
            return tx.SourceMethod.PDF.value
        if media_type == "text/html":
            return tx.SourceMethod.PUBLIC_HTML.value
        return tx.SourceMethod.OFFICIAL_DOWNLOAD.value

    # -- shared building ---------------------------------------------------

    def quarantine(self, row: BronzeRow, reason: str) -> Quarantined:
        return Quarantined(
            provider=self.provider.value,
            reason=reason,
            record=dict(row.columns),
            document_id=row.document_id,
            source_report_id=row.source_report_id,
            source_row_locator=row.get("row_locator") or None,
        )

    def build(
        self,
        row: BronzeRow,
        measure: tx.Measure,
        *,
        platform: str,
        requester_type: tx.RequesterType,
        geographic_basis: tx.GeographicBasis,
        table_key: str,
        product: str | None = None,
        requester_raw: str | None = None,
        requester_agency: str | None = None,
        reason_raw: str | None = None,
        breakdown: str = "",
        notes: str | None = None,
    ) -> Observation | Quarantined:
        """An observation from a record whose meaning has been decided.

        Everything that can go wrong with the figure itself — a value that is
        not a number, a count that is not whole, a period nobody could read —
        is a quarantine here rather than a coercion.
        """
        raw_value = row.get("value")
        value, status, problem = parse_value(raw_value, measure.unit)
        if problem:
            return self.quarantine(row, problem)

        start, end = _date(row.get("period_start")), _date(row.get("period_end"))
        if start is None or end is None:
            return self.quarantine(row, f"period {row.get('period_raw')!r} could not be read")
        if start > end:
            return self.quarantine(row, f"period starts {start} after it ends {end}")

        period = row.get("period") or None
        resolution = _resolution(period)
        country_raw = row.get("country_raw") or None
        country_code = row.get("country_code") or None
        reason_normalized, crosswalk = tx.reason_category(self.provider, reason_raw or "")

        flags: list[str] = []
        if country_raw and not country_code:
            flags.append("country_unresolved")
        if not country_raw:
            flags.append("worldwide")
        if row.retrieved_at is not None and end > row.retrieved_at.date():
            # A period that had not ended when the report was retrieved: a
            # partial figure, which a trend line must not read as a fall.
            flags.append("period_incomplete_at_retrieval")

        return Observation(
            source_report_id=row.source_report_id,
            provider=self.provider.value,
            country_code=country_code,
            country_raw=country_raw,
            platform=platform,
            product=product or None,
            period=period,
            period_start=start,
            period_end=end,
            temporal_resolution=resolution,
            requester_type=requester_type.value,
            requester_raw=requester_raw or None,
            requester_agency=requester_agency,
            legal_basis=None,
            reason_category_raw=reason_raw or None,
            reason_category_normalized=reason_normalized,
            reason_crosswalk_confidence=crosswalk.value if crosswalk else None,
            metric=measure.metric.value,
            metric_value=value,
            unit=measure.unit.value,
            value_status=status,
            raw_value=raw_value,
            scope=measure.scope.value,
            geographic_basis=geographic_basis.value,
            source_label=measure.label,
            source_table=row.get("source_table"),
            source_row_locator=row.get("row_locator"),
            extraction_method=f"{self.extractor}@{row.parser_version or '?'}",
            confidence=measure.confidence.value,
            comparability_group=comparability_group(
                self.provider,
                measure,
                requester_type,
                geographic_basis,
                resolution,
                platform=platform,
            ),
            notes=" ".join(n for n in (measure.note, notes) if n) or None,
            table_key=table_key,
            breakdown=breakdown,
            quality_flags=flags,
            document_id=row.document_id,
            content_hash=row.content_hash,
            raw_path=row.raw_path,
            source_url=row.source_url,
            retrieved_at=row.retrieved_at,
            parser_version=row.parser_version,
        )


def comparability_group(
    provider: str,
    measure: tx.Measure,
    requester_type: tx.RequesterType,
    geographic_basis: tx.GeographicBasis,
    resolution: str,
    *,
    platform: str,
) -> str:
    """What a figure can be compared with: only figures in the same group.

    The provider is part of it. No two providers in the observatory define a
    request, an item or an action the same way, and none has been verified to —
    so a cross-provider comparison is not "like for like" by construction, and
    the analytics refuse it unless a reader asks for it knowingly. Within a
    provider the group lets countries be compared, and periods within a
    country, on one definition.
    """
    return ":".join(
        (
            str(provider),
            platform if provider == tx.Provider.KOMDIGI else "*",
            measure.metric.value,
            measure.scope.value,
            requester_type.value,
            geographic_basis.value,
            resolution,
            measure.unit.value,
        )
    )


def _date(text: str) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _resolution(period: str | None) -> str:
    if not period:
        return "irregular"
    if "-S" in period:
        return "semiannual"
    if period.isdigit():
        return "annual"
    return "irregular"


def sum_values(observations: Sequence[Observation]) -> Decimal | None:
    """The sum of reported figures, or None where any is unavailable."""
    if any(o.metric_value is None for o in observations):
        return None
    return sum((o.metric_value for o in observations if o.metric_value is not None), Decimal(0))
