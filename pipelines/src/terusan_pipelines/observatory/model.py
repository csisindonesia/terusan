"""The observatory's tables: what a row is, how it is identified, its schema.

Five tables, after the spec's data model (§4), written to Silver:

- `restriction_source_reports` — one row per landed artifact: the immutable
  evidence, by content hash, with when it was retrieved and what it covers.
- `restriction_observations` — one figure per row, with its metric, unit,
  period, requester and scope, and the report it came from.
- `restriction_quarantine` — what the adapters would not vouch for, and why.
- `restriction_ingestion_runs` — one row per provider per build.
- `restriction_methodology_changes` — what changed in a provider's reporting,
  declared with the evidence for it.

Two hashes identify an observation. The *identity* is everything that says
which figure it is — provider, platform, country, period, metric, scope, the
publisher's breakdown and label — and not the value. The *record hash* is the
identity plus the value. Re-ingesting a report reproduces every record hash, so
nothing is duplicated; a revised report that changes a figure produces a new
record hash under the same identity, which is a revision, kept beside the
figure it revised rather than over it.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import pyarrow as pa

from ..normalize.schema import VALUE_TYPE

#: Namespace for the deterministic identifiers below. Fixed forever: changing
#: it renumbers every report and observation the observatory has published.
NAMESPACE = uuid.UUID("5b0c7f0e-3a51-4d0e-9f0a-1e6b2a7c4d10")

#: Bumped when the adapters' output changes in a way that alters observations.
#: Recorded on every row and every run, beside the extractor's parser version.
ADAPTER_VERSION = "1"


def _digest(*parts: object) -> str:
    joined = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def report_id(content_hash: str) -> str:
    """A source report's id: derived from its bytes, so it is the same id on
    every machine and every rebuild."""
    return str(uuid.uuid5(NAMESPACE, f"report:{content_hash}"))


@dataclass(slots=True)
class Observation:
    """One published figure, in the observatory's terms."""

    source_report_id: str
    provider: str
    country_code: str | None
    country_raw: str | None
    platform: str
    product: str | None
    period: str | None
    period_start: date
    period_end: date
    temporal_resolution: str
    requester_type: str
    requester_raw: str | None
    #: Never defaulted to Komdigi. Set only where the source itself is the
    #: agency, or names it.
    requester_agency: str | None
    legal_basis: str | None
    reason_category_raw: str | None
    reason_category_normalized: str | None
    reason_crosswalk_confidence: str | None
    metric: str
    #: Null when the publisher marks the figure unavailable. Never zero for it.
    metric_value: Decimal | None
    unit: str
    value_status: str
    raw_value: str
    scope: str
    geographic_basis: str
    source_label: str
    source_table: str
    source_row_locator: str
    extraction_method: str
    confidence: str
    comparability_group: str
    notes: str | None
    #: Stable name of the table a figure came from, for the identity: TikTok
    #: names each release's file differently, and a figure is not a new figure
    #: because the file it is in was renamed.
    table_key: str
    #: What the provider breaks a figure down by beyond the named columns —
    #: TikTok's policy and task — so two rows differing only there are not
    #: taken for one.
    breakdown: str = ""
    quality_flags: list[str] = field(default_factory=list)
    # Provenance, carried so a row answers "where did this come from" alone.
    document_id: str | None = None
    content_hash: str | None = None
    raw_path: str | None = None
    source_url: str | None = None
    retrieved_at: datetime | None = None
    parser_version: str | None = None

    @property
    def identity_hash(self) -> str:
        return _digest(
            self.provider,
            self.platform,
            self.product,
            self.country_code or self.country_raw,
            self.period_start,
            self.period_end,
            self.metric,
            self.scope,
            self.requester_type,
            self.requester_raw,
            self.reason_category_raw,
            self.source_label,
            self.table_key,
            self.breakdown,
        )

    @property
    def record_hash(self) -> str:
        value = "" if self.metric_value is None else format(self.metric_value.normalize(), "f")
        return _digest(self.identity_hash, value, self.unit, self.value_status)

    @property
    def id(self) -> str:
        return str(uuid.uuid5(NAMESPACE, f"observation:{self.record_hash}"))


@dataclass(slots=True)
class Quarantined:
    """A Bronze record the adapters would not turn into an observation."""

    provider: str
    reason: str
    record: dict[str, str]
    document_id: str | None = None
    source_report_id: str | None = None
    source_row_locator: str | None = None


_TS = pa.timestamp("us", tz="UTC")

SOURCE_REPORTS = pa.schema(
    [
        pa.field("id", pa.string(), nullable=False),
        pa.field("provider", pa.string(), nullable=False),
        pa.field("source_slug", pa.string(), nullable=False),
        pa.field("report_title", pa.string()),
        pa.field("report_url", pa.string()),
        pa.field("download_url", pa.string()),
        pa.field("published_at", pa.date32()),
        pa.field("retrieved_at", _TS),
        pa.field("period_start", pa.date32()),
        pa.field("period_end", pa.date32()),
        pa.field("country_code", pa.string()),
        pa.field("raw_object_key", pa.string(), nullable=False),
        pa.field("sha256", pa.string(), nullable=False),
        pa.field("mime_type", pa.string()),
        pa.field("source_method", pa.string(), nullable=False),
        pa.field("source_revision", pa.string()),
        pa.field("parser_version", pa.string()),
        pa.field("license_notes", pa.string()),
        pa.field("methodology_notes", pa.string()),
        pa.field("document_id", pa.string(), nullable=False),
        pa.field("dataset", pa.string()),
        #: Whether any observation was read from it. A landed report page, a
        #: PDF nobody parses yet, a hand-saved Meta export: evidence kept, not
        #: yet figures, and the coverage view says so.
        pa.field("parsed", pa.bool_(), nullable=False),
        pa.field("observation_count", pa.int64(), nullable=False),
        pa.field("processed_at", _TS, nullable=False),
    ]
)

OBSERVATIONS = pa.schema(
    [
        pa.field("id", pa.string(), nullable=False),
        pa.field("source_report_id", pa.string(), nullable=False),
        pa.field("provider", pa.string(), nullable=False),
        pa.field("country_code", pa.string()),
        pa.field("country_raw", pa.string()),
        pa.field("platform", pa.string(), nullable=False),
        pa.field("product", pa.string()),
        pa.field("period", pa.string()),
        pa.field("period_start", pa.date32(), nullable=False),
        pa.field("period_end", pa.date32(), nullable=False),
        pa.field("temporal_resolution", pa.string(), nullable=False),
        pa.field("requester_type", pa.string(), nullable=False),
        pa.field("requester_raw", pa.string()),
        pa.field("requester_agency", pa.string()),
        pa.field("legal_basis", pa.string()),
        pa.field("reason_category_raw", pa.string()),
        pa.field("reason_category_normalized", pa.string()),
        pa.field("reason_crosswalk_confidence", pa.string()),
        pa.field("metric", pa.string(), nullable=False),
        pa.field("metric_value", VALUE_TYPE),
        pa.field("unit", pa.string(), nullable=False),
        pa.field("value_status", pa.string(), nullable=False),
        pa.field("raw_value", pa.string()),
        pa.field("scope", pa.string(), nullable=False),
        pa.field("geographic_basis", pa.string(), nullable=False),
        pa.field("source_label", pa.string(), nullable=False),
        pa.field("source_table", pa.string()),
        pa.field("source_row_locator", pa.string()),
        pa.field("extraction_method", pa.string()),
        pa.field("confidence", pa.string(), nullable=False),
        pa.field("comparability_group", pa.string(), nullable=False),
        pa.field("notes", pa.string()),
        pa.field("quality_flags", pa.list_(pa.string())),
        pa.field("identity_hash", pa.string(), nullable=False),
        pa.field("record_hash", pa.string(), nullable=False),
        #: 1 for the first value seen under an identity, 2 for the first
        #: revision, and so on, in order of the reports' retrieval.
        pa.field("revision", pa.int32(), nullable=False),
        #: Whether this is the value the latest report holding the identity
        #: gives. Charts read current rows; the others are the audit trail.
        pa.field("is_current", pa.bool_(), nullable=False),
        #: The latest report that also states this exact figure, where a later
        #: release repeated it unchanged.
        pa.field("last_seen_report_id", pa.string()),
        pa.field("document_id", pa.string()),
        pa.field("content_hash", pa.string()),
        pa.field("raw_path", pa.string()),
        pa.field("source_url", pa.string()),
        pa.field("retrieved_at", _TS),
        pa.field("parser_version", pa.string()),
        pa.field("adapter_version", pa.string(), nullable=False),
        pa.field("processed_at", _TS, nullable=False),
    ]
)

QUARANTINE = pa.schema(
    [
        pa.field("provider", pa.string(), nullable=False),
        pa.field("reason", pa.string(), nullable=False),
        pa.field("source_report_id", pa.string()),
        pa.field("document_id", pa.string()),
        pa.field("source_row_locator", pa.string()),
        pa.field("record", pa.map_(pa.string(), pa.string())),
        pa.field("adapter_version", pa.string(), nullable=False),
        pa.field("detected_at", _TS, nullable=False),
    ]
)

INGESTION_RUNS = pa.schema(
    [
        pa.field("id", pa.string(), nullable=False),
        pa.field("provider", pa.string(), nullable=False),
        pa.field("started_at", _TS, nullable=False),
        pa.field("finished_at", _TS, nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("report_count", pa.int64(), nullable=False),
        pa.field("rows_extracted", pa.int64(), nullable=False),
        pa.field("rows_accepted", pa.int64(), nullable=False),
        pa.field("rows_quarantined", pa.int64(), nullable=False),
        #: When the provider's newest report was retrieved: the spec's
        #: `last_checked_at`, recorded even when nothing new was published.
        pa.field("last_checked_at", _TS),
        pa.field("error_summary", pa.string()),
        pa.field("code_version", pa.string()),
        pa.field("parser_version", pa.string()),
        pa.field("adapter_version", pa.string(), nullable=False),
    ]
)

METHODOLOGY_CHANGES = pa.schema(
    [
        pa.field("provider", pa.string(), nullable=False),
        pa.field("effective_date", pa.date32(), nullable=False),
        pa.field("description", pa.string(), nullable=False),
        pa.field("affected_metrics", pa.list_(pa.string())),
        pa.field("evidence", pa.string(), nullable=False),
        pa.field("source_report_id", pa.string()),
    ]
)

#: Silver dataset names. Prefixed so the views they register as —
#: `silver_restriction_observations` — say what they are among the lake's.
REPORTS_TABLE = "restriction_source_reports"
OBSERVATIONS_TABLE = "restriction_observations"
QUARANTINE_TABLE = "restriction_quarantine"
RUNS_TABLE = "restriction_ingestion_runs"
METHODOLOGY_TABLE = "restriction_methodology_changes"
