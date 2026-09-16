"""Turning Bronze records into observations.

A Bronze record is a flat map of source column names to text. Becoming an
observation means deciding which column is the period, which is the value,
which name a place, and what the number actually says — the judgement Bronze
deliberately defers (program.md §7, §10).

The mapping is declared per dataset rather than inferred. Inference over
published tables is wrong often enough that a research warehouse cannot rely on
it: a column headed `2026` might be a period or a value depending on whether
the table is long or wide, and nothing in the data settles it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import structlog

from .dimensions import CommodityRegistry, GeographyRegistry
from .periods import Period, UnparseablePeriod, parse_period
from .values import NumberFormat, ParsedValue, ValueStatus, parse_value

log = structlog.get_logger(__name__)


class MappingError(ValueError):
    """A dataset mapping that cannot be applied."""


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    """How one Bronze dataset's columns become observations.

    Declared, not guessed. `value_columns` carries the wide case, where each
    column header is itself a period — the commonest shape in published
    statistical tables.
    """

    indicator_id: str

    #: Long form: one column holds the period, another the value.
    period_column: str | None = None
    value_column: str | None = None

    #: Wide form: each named column is a period, and its cell is the value.
    value_columns: tuple[str, ...] = ()

    geo_column: str | None = None
    commodity_column: str | None = None
    unit_column: str | None = None
    unit: str | None = None

    number_format: NumberFormat = NumberFormat.AUTO

    #: Rows whose column matches a value are dropped. Published tables carry
    #: totals and subtotals that would double-count if kept.
    exclude_where: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        long_form = self.period_column and self.value_column
        if not long_form and not self.value_columns:
            raise MappingError(
                f"mapping for {self.indicator_id} declares neither a period/value pair "
                "nor value_columns; one of the two is needed"
            )

    @property
    def is_wide(self) -> bool:
        return bool(self.value_columns)


@dataclass(slots=True)
class NormalizationResult:
    rows_in: int = 0
    observations: int = 0
    skipped_excluded: int = 0
    skipped_no_period: int = 0
    unresolved_geo: int = 0
    unresolved_commodity: int = 0
    ambiguous_values: int = 0
    #: Kept rather than dropped: a value that failed to parse is a signal about
    #: the source, and discarding it hides the problem (program.md §42).
    unparseable_values: int = 0


def observation_id(
    indicator_id: str, period: str, geo_id: str | None, commodity_id: str | None
) -> str:
    """A stable identifier for one observation.

    Derived from what identifies the observation rather than assigned, so
    re-running normalization produces the same ids and a revision can be
    matched to the figure it revises.
    """
    key = "|".join([indicator_id, period, geo_id or "", commodity_id or ""])
    return f"obs_{hashlib.sha256(key.encode()).hexdigest()[:20]}"


class ObservationNormalizer:
    """Applies a column mapping to Bronze records."""

    def __init__(
        self,
        mapping: ColumnMapping,
        *,
        geography: GeographyRegistry | None = None,
        commodities: CommodityRegistry | None = None,
        pipeline_version: str = "1",
    ) -> None:
        self._mapping = mapping
        self._geography = geography or GeographyRegistry()
        self._commodities = commodities or CommodityRegistry()
        self._pipeline_version = pipeline_version

    def normalize(
        self, records: list[dict[str, Any]], result: NormalizationResult | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield Silver observation rows from Bronze record rows."""
        outcome = result if result is not None else NormalizationResult()

        for record in records:
            outcome.rows_in += 1
            columns = _columns_of(record)

            if self._excluded(columns):
                outcome.skipped_excluded += 1
                continue

            yield from self._from_record(record, columns, outcome)

    # ---- internals -----------------------------------------------------

    def _excluded(self, columns: dict[str, str]) -> bool:
        for column, unwanted in self._mapping.exclude_where.items():
            value = (columns.get(column) or "").strip().lower()
            if value in {u.lower() for u in unwanted}:
                return True
        return False

    def _from_record(
        self, record: dict[str, Any], columns: dict[str, str], outcome: NormalizationResult
    ) -> Iterator[dict[str, Any]]:
        mapping = self._mapping
        geo_id, geo_raw = self._resolve_geo(columns, outcome)
        commodity_id, commodity_raw = self._resolve_commodity(columns, outcome)

        if mapping.is_wide:
            pairs = [(name, columns.get(name)) for name in mapping.value_columns]
        else:
            pairs = [
                (
                    columns.get(mapping.period_column or ""),
                    columns.get(mapping.value_column or ""),
                )
            ]

        for period_text, raw_value in pairs:
            if period_text is None:
                continue
            try:
                period = parse_period(period_text)
            except UnparseablePeriod:
                outcome.skipped_no_period += 1
                log.debug("normalize.bad_period", period=period_text)
                continue

            parsed = parse_value(raw_value or "", mapping.number_format)
            if not parsed.unambiguous:
                outcome.ambiguous_values += 1
            if parsed.status is ValueStatus.UNPARSEABLE:
                outcome.unparseable_values += 1

            outcome.observations += 1
            yield self._row(record, period, parsed, geo_id, geo_raw, commodity_id, commodity_raw)

    def _row(
        self,
        record: dict[str, Any],
        period: Period,
        parsed: ParsedValue,
        geo_id: str | None,
        geo_raw: str | None,
        commodity_id: str | None,
        commodity_raw: str | None,
    ) -> dict[str, Any]:
        mapping = self._mapping
        columns = _columns_of(record)
        unit = (
            parsed.unit
            or (columns.get(mapping.unit_column) if mapping.unit_column else None)
            or mapping.unit
        )
        return {
            "observation_id": observation_id(
                mapping.indicator_id, period.label, geo_id, commodity_id
            ),
            "indicator_id": mapping.indicator_id,
            "period": period.label,
            "period_start": period.start,
            "period_end": period.end,
            "temporal_resolution": str(period.resolution),
            "value": parsed.value,
            "unit": unit,
            "status": str(parsed.status),
            "value_unambiguous": parsed.unambiguous,
            "raw_value": parsed.raw,
            "geo_id": geo_id,
            "geo_name_raw": geo_raw,
            "commodity_id": commodity_id,
            "commodity_name_raw": commodity_raw,
            "release_date": _as_date(record.get("published_at")),
            "revision": None,
            "source_id": record.get("source_id") or "unknown",
            "document_id": record.get("document_id"),
            "dataset_id": record.get("dataset"),
            "content_hash": record.get("content_hash"),
            "raw_path": record.get("raw_path"),
            "retrieved_at": record.get("retrieved_at"),
            "processed_at": datetime.now(UTC),
            "pipeline_version": self._pipeline_version,
        }

    def _resolve_geo(
        self, columns: dict[str, str], outcome: NormalizationResult
    ) -> tuple[str | None, str | None]:
        column = self._mapping.geo_column
        if not column:
            return None, None
        raw = (columns.get(column) or "").strip()
        if not raw:
            return None, None
        resolved = self._geography.resolve(raw)
        if not resolved.resolved:
            outcome.unresolved_geo += 1
        # The raw name is kept either way: an unresolved figure stays usable
        # and the gap stays visible.
        return resolved.identifier, raw

    def _resolve_commodity(
        self, columns: dict[str, str], outcome: NormalizationResult
    ) -> tuple[str | None, str | None]:
        column = self._mapping.commodity_column
        if not column:
            return None, None
        raw = (columns.get(column) or "").strip()
        if not raw:
            return None, None
        resolved = self._commodities.resolve(raw)
        if not resolved.resolved:
            outcome.unresolved_commodity += 1
        return resolved.identifier, raw


def _columns_of(record: dict[str, Any]) -> dict[str, str]:
    """Read the `columns` map off a Bronze record.

    Arrow hands a map column back as a list of pairs, so both shapes arrive
    depending on whether the row came from Parquet or from the extractor.
    """
    raw = record.get("columns")
    if isinstance(raw, dict):
        return {str(k): "" if v is None else str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        return {str(k): "" if v is None else str(v) for k, v in raw}
    if isinstance(raw, str):
        try:
            return {str(k): str(v) for k, v in json.loads(raw).items()}
        except (json.JSONDecodeError, AttributeError):
            return {}
    return {}


def _as_date(value: Any) -> date | None:
    if value is None or isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None
