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
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from .dimensions import CommodityRegistry, GeographyRegistry
from .periods import Period, UnparseablePeriod, parse_period, try_parse_period
from .schema import VALUE_SCALE
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

    #: Long form where the period is split across columns — a month in one and
    #: a year in another, which is how a spreadsheet usually holds it. Joined
    #: with a space, which reads as a period label the parser already knows.
    period_parts: tuple[str, ...] = ()

    #: Wide form: each named column is a period, and its cell is the value.
    value_columns: tuple[str, ...] = ()

    #: Wide form where the columns are not known in advance. Any column whose
    #: header reads as a period becomes a value column.
    #:
    #: Needed because a daily table's columns *are* the dates, so they change
    #: with every release — listing them would mean editing the mapping each
    #: run, and getting it wrong the first time nobody did.
    value_columns_are_periods: bool = False

    geo_column: str | None = None
    commodity_column: str | None = None

    #: The commodity every row is about, where no column says so.
    #:
    #: A commodity is a row dimension in one shape and the series' identity in
    #: another: Bank Indonesia prices thirty-one foods in one table, so the
    #: commodity is a column, while Yahoo prices gold in a series of its own and
    #: no column in the file names it. Without this the second kind never
    #: reaches the dimension, and a reader looking for gold finds only
    #: groceries. Resolved through the registry like a column value, so the
    #: figure carries a `commodity_id` rather than a label.
    commodity: str | None = None

    unit_column: str | None = None
    unit: str | None = None

    number_format: NumberFormat = NumberFormat.AUTO

    #: Rows whose column matches a value are dropped. Published tables carry
    #: totals and subtotals that would double-count if kept.
    exclude_where: dict[str, tuple[str, ...]] = field(default_factory=dict)

    #: Rows are kept only where the column matches. One file often carries
    #: several series — a survey workbook holds three confidence indices — and
    #: each is its own indicator, so a mapping needs to say which it is reading
    #: rather than excluding all the others by name.
    include_where: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        long_form = (self.period_column or self.period_parts) and self.value_column
        wide_form = self.value_columns or self.value_columns_are_periods
        if not long_form and not wide_form:
            raise MappingError(
                f"mapping for {self.indicator_id} declares neither a period/value pair "
                "nor value columns; one of the two is needed"
            )

    @property
    def is_wide(self) -> bool:
        return bool(self.value_columns) or self.value_columns_are_periods


@dataclass(slots=True)
class NormalizationResult:
    rows_in: int = 0
    observations: int = 0
    skipped_excluded: int = 0
    skipped_no_period: int = 0
    unresolved_geo: int = 0
    unresolved_commodity: int = 0
    #: Rows that restated an observation already seen, identically. A source
    #: that repeats a line, not a mapping that lost a dimension — the latter
    #: is refused rather than counted.
    duplicate_rows: int = 0
    #: Rows superseded by a later retrieval of the same observation. A daily
    #: series re-pulled before its session closed revises its last figure.
    revised_rows: int = 0
    ambiguous_values: int = 0
    #: Kept rather than dropped: a value that failed to parse is a signal about
    #: the source, and discarding it hides the problem (program.md §42).
    unparseable_values: int = 0


def observation_id(
    indicator_id: str,
    period: str,
    geo: str | None,
    commodity: str | None,
) -> str:
    """A stable identifier for one observation.

    Derived from what identifies the observation rather than assigned, so
    re-running normalization produces the same ids and a revision can be
    matched to the figure it revises.

    Each dimension is passed as its resolved identifier where there is one and
    its raw label otherwise. An unresolved dimension is still a dimension: a
    file of daily prices for forty commodities, none of them in the registry,
    is forty series and not one. Keying on the identifier alone would collapse
    them onto a single id and leave forty different figures claiming to be the
    same observation.
    """
    key = "|".join([indicator_id, period, geo or "", commodity or ""])
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
        for column, wanted in self._mapping.include_where.items():
            value = (columns.get(column) or "").strip().lower()
            if value not in {w.lower() for w in wanted}:
                return True
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
            names = (
                # Every column whose header reads as a period. The other
                # columns — a name, a code, a row number — do not, which is
                # what makes this safe to do by inspection.
                [name for name in columns if try_parse_period(name) is not None]
                if mapping.value_columns_are_periods
                else list(mapping.value_columns)
            )
            pairs = [(name, columns.get(name)) for name in names]
        else:
            period = (
                " ".join(
                    part
                    for name in mapping.period_parts
                    if (part := (columns.get(name) or "").strip())
                )
                if mapping.period_parts
                else columns.get(mapping.period_column or "")
            )
            pairs = [(period or None, columns.get(mapping.value_column or ""))]

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
                mapping.indicator_id,
                period.label,
                geo_id or geo_raw,
                commodity_id or commodity_raw,
            ),
            "indicator_id": mapping.indicator_id,
            "period": period.label,
            "period_start": period.start,
            "period_end": period.end,
            "temporal_resolution": str(period.resolution),
            "value": _fit(parsed.value),
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
            "source_url": record.get("source_url"),
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
        mapping = self._mapping
        # The column wins where a mapping declares both: a table that names a
        # commodity per row is saying something the series-wide constant
        # cannot, and silently overriding it would collapse thirty-one foods
        # into one.
        raw = ""
        if mapping.commodity_column:
            raw = (columns.get(mapping.commodity_column) or "").strip()
        if not raw and mapping.commodity:
            raw = mapping.commodity.strip()
        if not raw:
            return None, None
        resolved = self._commodities.resolve(raw)
        if not resolved.resolved:
            outcome.unresolved_commodity += 1
        return resolved.identifier, raw


def _fit(value: Decimal | None) -> Decimal | None:
    """Round a figure to the precision Silver stores.

    Arrow refuses to rescale a decimal rather than rounding it away, which is
    the right default — but a spreadsheet that divides two numbers produces
    twenty decimal places, and refusing the whole table over that helps nobody.
    """
    if value is None:
        return None
    try:
        return value.quantize(Decimal(1).scaleb(-VALUE_SCALE))
    except InvalidOperation:
        # Too large to represent at all, which is a different problem and one
        # the caller should see rather than have rounded away.
        return value


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
