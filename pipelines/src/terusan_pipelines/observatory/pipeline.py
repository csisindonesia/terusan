"""Building the observatory's tables from RAW and Bronze.

A build is a full rebuild. It reads every landed report's sidecar for the
source reports, every Bronze record the providers' extractors wrote for the
observations, and replaces the observatory's Silver tables with the result —
except the run log, which is appended to. Rebuilding rather than appending is
what makes re-ingestion produce no duplicates: the same Bronze gives the same
record hashes, and there is no earlier copy for them to duplicate.

Revisions survive the rebuild because RAW does. A provider that republishes a
figure with a new value has landed a second report, content-addressed beside
the first; both are read, both values become observations under one identity,
and the later one is current.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import structlog

from ..extract.base import PARSER_VERSION, Landed
from ..extract.runner import walk_raw
from ..normalize.schema import VALUE_SCALE
from ..sources.registry import registry
from ..storage import Layer, StorageResolver
from ..storage.config import StorageConfig
from ..warehouse import ParquetWriter, Warehouse
from . import model as m
from .adapters import Adapter, BronzeRow, adapters
from .methodology import CHANGES

log = structlog.get_logger(__name__)

_QUANTUM = Decimal(1).scaleb(-VALUE_SCALE)


@dataclass(slots=True)
class ProviderResult:
    provider: str
    reports: int = 0
    rows_extracted: int = 0
    accepted: int = 0
    quarantined: int = 0
    last_checked_at: datetime | None = None
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BuildResult:
    providers: dict[str, ProviderResult] = field(default_factory=dict)
    observations: int = 0
    revisions: int = 0
    quarantined: int = 0
    reports: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "reports": self.reports,
            "observations": self.observations,
            "revised_identities": self.revisions,
            "quarantined": self.quarantined,
            "providers": {
                k: {
                    "reports": v.reports,
                    "rows_extracted": v.rows_extracted,
                    "accepted": v.accepted,
                    "quarantined": v.quarantined,
                    "last_checked_at": v.last_checked_at.isoformat() if v.last_checked_at else None,
                    "errors": v.errors,
                }
                for k, v in self.providers.items()
            },
        }


class ObservatoryBuilder:
    """Rebuilds the observatory's Silver tables."""

    def __init__(self, resolver: StorageResolver | None = None) -> None:
        self._resolver = resolver or StorageResolver(StorageConfig())

    def build(self, *, write: bool = True) -> BuildResult:
        started = datetime.now(UTC)
        registry.ensure_loaded()
        result = BuildResult()

        reports: list[dict[str, Any]] = []
        observations: list[m.Observation] = []
        quarantined: list[m.Quarantined] = []
        report_ids_by_source: dict[str, list[tuple[datetime | None, str]]] = defaultdict(list)

        with Warehouse(self._resolver) as warehouse:
            for adapter in adapters():
                provider = ProviderResult(provider=adapter.provider.value)
                result.providers[provider.provider] = provider
                landed = list(self._landed(adapter))
                report_index = {item.document_id: m.report_id(item.content_hash) for item in landed}
                provider.reports = len(landed)
                provider.last_checked_at = max(
                    (item.retrieved_at for item in landed if item.retrieved_at), default=None
                )

                kept: list[m.Observation] = []
                for row in self._bronze(warehouse, adapter, report_index):
                    provider.rows_extracted += 1
                    outcome = adapter.normalize(row)
                    if isinstance(outcome, m.Observation):
                        kept.append(outcome)
                    elif isinstance(outcome, m.Quarantined):
                        quarantined.append(outcome)
                        provider.quarantined += 1
                kept, refused = adapter.validate(kept)
                quarantined.extend(refused)
                provider.quarantined += len(refused)

                kept, conflicting = _deduplicate_within_reports(adapter, kept)
                quarantined.extend(conflicting)
                provider.quarantined += len(conflicting)
                provider.accepted = len(kept)
                observations.extend(kept)

                counts: dict[str, int] = defaultdict(int)
                spans: dict[str, list[m.Observation]] = defaultdict(list)
                for observation in kept:
                    counts[observation.source_report_id] += 1
                    spans[observation.source_report_id].append(observation)
                for item in landed:
                    rid = report_index[item.document_id]
                    reports.append(_report_row(adapter, item, rid, counts[rid], spans[rid]))
                    report_ids_by_source[item.source_slug].append((item.retrieved_at, rid))

        rows, revised = _revisions(observations)
        result.observations = len(rows)
        result.revisions = revised
        result.quarantined = len(quarantined)
        result.reports = len(reports)

        if write:
            processed = datetime.now(UTC)
            self._replace(m.REPORTS_TABLE, _table(m.SOURCE_REPORTS, reports, processed))
            self._replace(m.OBSERVATIONS_TABLE, _observations_table(rows, processed))
            self._replace(
                m.QUARANTINE_TABLE,
                _table(m.QUARANTINE, [_quarantine_row(q) for q in quarantined], processed),
            )
            self._replace(m.METHODOLOGY_TABLE, _methodology_table(report_ids_by_source))
            self._append_runs(result, started)
        return result

    # -- reading -----------------------------------------------------------

    def _landed(self, adapter: Adapter) -> Iterator[Landed]:
        for source in adapter.discover():
            yield from walk_raw(self._resolver, str(source.meta.category), source.meta.slug)

    def _bronze(
        self, warehouse: Warehouse, adapter: Adapter, report_index: dict[str, str]
    ) -> Iterator[BronzeRow]:
        for slug in adapter.sources:
            partition = f"source_id={slug}"
            if not Path(self._resolver.resolve(Layer.BRONZE, "records", partition)).is_dir():
                continue
            relation = warehouse.query(
                f"""
                SELECT dataset, columns, document_id, content_hash, raw_path, source_url,
                       -- As microseconds: DuckDB hands a TIMESTAMPTZ to Python
                       -- only through pytz, which the pipelines do not carry.
                       epoch_us(retrieved_at), parser_version
                FROM {warehouse.source(Layer.BRONZE, "records", partition)}
                -- A document extracted twice keeps both extractions in Bronze —
                -- under two parser versions, or under one with --reprocess. Only
                -- the newest is what the extractor now says, and provenance is
                -- stamped once per document, so its time picks one extraction.
                QUALIFY processed_at = max(processed_at) OVER (PARTITION BY document_id)
                ORDER BY document_id, row_number
                """
            )
            while batch := relation.fetchmany(50_000):
                for (
                    dataset,
                    columns,
                    document_id,
                    content_hash,
                    raw_path,
                    url,
                    retrieved,
                    pv,
                ) in batch:
                    rid = report_index.get(document_id)
                    if rid is None:
                        # Bronze holding a document RAW no longer has a sidecar
                        # for cannot be traced to evidence, so it is not read.
                        log.warning("observatory.untraceable", document_id=document_id)
                        continue
                    yield BronzeRow(
                        source_id=slug,
                        dataset=dataset,
                        columns=dict(columns or {}),
                        document_id=document_id,
                        content_hash=content_hash,
                        raw_path=raw_path,
                        source_url=url,
                        retrieved_at=None
                        if retrieved is None
                        else datetime.fromtimestamp(retrieved / 1_000_000, UTC),
                        parser_version=pv,
                        source_report_id=rid,
                    )

    # -- writing -----------------------------------------------------------

    def _replace(self, dataset: str, table: pa.Table) -> None:
        target = Path(self._resolver.resolve_for_write(Layer.SILVER, dataset))
        shutil.rmtree(target, ignore_errors=True)
        ParquetWriter(self._resolver).write(Layer.SILVER, dataset, table)

    def _append_runs(self, result: BuildResult, started: datetime) -> None:
        finished = datetime.now(UTC)
        code = _code_version()
        rows = [
            {
                "id": str(uuid.uuid4()),
                "provider": p.provider,
                "started_at": started,
                "finished_at": finished,
                "status": "failed" if p.errors else ("empty" if not p.reports else "succeeded"),
                "report_count": p.reports,
                "rows_extracted": p.rows_extracted,
                "rows_accepted": p.accepted,
                "rows_quarantined": p.quarantined,
                "last_checked_at": p.last_checked_at,
                "error_summary": "; ".join(p.errors) or None,
                "code_version": code,
                "parser_version": PARSER_VERSION,
                "adapter_version": m.ADAPTER_VERSION,
            }
            for p in result.providers.values()
        ]
        ParquetWriter(self._resolver).write(
            Layer.SILVER, m.RUNS_TABLE, pa.Table.from_pylist(rows, schema=m.INGESTION_RUNS)
        )


# -- assembling --------------------------------------------------------------


def _deduplicate_within_reports(
    adapter: Adapter, observations: list[m.Observation]
) -> tuple[list[m.Observation], list[m.Quarantined]]:
    """One figure per identity per report.

    The same figure twice in one report is collapsed. Two different figures
    for one identity in one report is a contradiction the report makes, and
    neither can be published as the figure: both are quarantined.
    """
    groups: dict[tuple[str, str], list[m.Observation]] = defaultdict(list)
    for observation in observations:
        groups[(observation.source_report_id, observation.identity_hash)].append(observation)

    kept: list[m.Observation] = []
    refused: list[m.Quarantined] = []
    for group in groups.values():
        if len({o.record_hash for o in group}) == 1:
            kept.append(group[0])
            continue
        values = sorted({o.raw_value for o in group})
        for observation in group:
            refused.append(
                m.Quarantined(
                    provider=adapter.provider.value,
                    reason=(
                        f"one report gives {len(values)} values for this figure: "
                        + ", ".join(values)
                    ),
                    record={
                        "measure": observation.source_label,
                        "value": observation.raw_value,
                        "country_raw": observation.country_raw or "",
                        "period_start": observation.period_start.isoformat(),
                        "period_end": observation.period_end.isoformat(),
                    },
                    document_id=observation.document_id,
                    source_report_id=observation.source_report_id,
                    source_row_locator=observation.source_row_locator,
                )
            )
    return kept, refused


def _revisions(
    observations: list[m.Observation],
) -> tuple[list[tuple[m.Observation, int, bool, str]], int]:
    """Each distinct figure once, numbered within its identity, current marked.

    A figure repeated unchanged by a later report is one record: it keeps the
    report it first appeared in, and notes the latest one that repeated it.
    """
    by_identity: dict[str, list[m.Observation]] = defaultdict(list)
    for observation in observations:
        by_identity[observation.identity_hash].append(observation)

    def when(o: m.Observation) -> datetime:
        return o.retrieved_at or datetime.min.replace(tzinfo=UTC)

    rows: list[tuple[m.Observation, int, bool, str]] = []
    revised = 0
    for group in by_identity.values():
        group.sort(key=when)
        first: dict[str, m.Observation] = {}
        last_seen: dict[str, str] = {}
        for observation in group:
            first.setdefault(observation.record_hash, observation)
            last_seen[observation.record_hash] = observation.source_report_id
        latest = group[-1].record_hash
        if len(first) > 1:
            revised += 1
        for number, (record_hash, observation) in enumerate(first.items(), start=1):
            rows.append((observation, number, record_hash == latest, last_seen[record_hash]))
    return rows, revised


def _report_row(
    adapter: Adapter,
    landed: Landed,
    rid: str,
    count: int,
    observations: list[m.Observation],
) -> dict[str, Any]:
    extra = landed.extra or {}
    countries = {o.country_code for o in observations if o.country_code}
    return {
        "id": rid,
        "provider": adapter.provider.value,
        "source_slug": landed.source_slug,
        "report_title": extra.get("resource_name")
        or extra.get("dataset_title")
        or landed.original_filename,
        "report_url": extra.get("report_page") or extra.get("index_url") or landed.source_url,
        "download_url": landed.source_url,
        "published_at": landed.published_at,
        "retrieved_at": landed.retrieved_at,
        "period_start": min((o.period_start for o in observations), default=None),
        "period_end": max((o.period_end for o in observations), default=None),
        "country_code": next(iter(countries)) if len(countries) == 1 else None,
        "raw_object_key": str(landed.path),
        "sha256": landed.content_hash,
        "mime_type": landed.media_type,
        "source_method": adapter.source_method(landed.media_type, extra),
        "source_revision": extra.get("release") or extra.get("etag") or landed.content_hash[:16],
        "parser_version": PARSER_VERSION if count else None,
        "license_notes": adapter.license_notes,
        "methodology_notes": adapter.methodology_notes,
        "document_id": landed.document_id,
        "dataset": landed.dataset,
        "parsed": count > 0,
        "observation_count": count,
    }


def _quarantine_row(q: m.Quarantined) -> dict[str, Any]:
    return {
        "provider": q.provider,
        "reason": q.reason,
        "source_report_id": q.source_report_id,
        "document_id": q.document_id,
        "source_row_locator": q.source_row_locator,
        "record": list(q.record.items()),
        "adapter_version": m.ADAPTER_VERSION,
    }


def _table(schema: pa.Schema, rows: list[dict[str, Any]], processed: datetime) -> pa.Table:
    stamp = "processed_at" if "processed_at" in schema.names else "detected_at"
    return pa.Table.from_pylist([{**r, stamp: processed} for r in rows], schema=schema)


def _observations_table(
    rows: list[tuple[m.Observation, int, bool, str]], processed: datetime
) -> pa.Table:
    records = []
    for o, revision, current, last_seen in rows:
        records.append(
            {
                "id": o.id,
                "source_report_id": o.source_report_id,
                "provider": o.provider,
                "country_code": o.country_code,
                "country_raw": o.country_raw,
                "platform": o.platform,
                "product": o.product,
                "period": o.period,
                "period_start": o.period_start,
                "period_end": o.period_end,
                "temporal_resolution": o.temporal_resolution,
                "requester_type": o.requester_type,
                "requester_raw": o.requester_raw,
                "requester_agency": o.requester_agency,
                "legal_basis": o.legal_basis,
                "reason_category_raw": o.reason_category_raw,
                "reason_category_normalized": o.reason_category_normalized,
                "reason_crosswalk_confidence": o.reason_crosswalk_confidence,
                "metric": o.metric,
                "metric_value": None
                if o.metric_value is None
                else o.metric_value.quantize(_QUANTUM),
                "unit": o.unit,
                "value_status": o.value_status,
                "raw_value": o.raw_value,
                "scope": o.scope,
                "geographic_basis": o.geographic_basis,
                "source_label": o.source_label,
                "source_table": o.source_table,
                "source_row_locator": o.source_row_locator,
                "extraction_method": o.extraction_method,
                "confidence": o.confidence,
                "comparability_group": o.comparability_group,
                "notes": o.notes,
                "quality_flags": o.quality_flags or None,
                "identity_hash": o.identity_hash,
                "record_hash": o.record_hash,
                "revision": revision,
                "is_current": current,
                "last_seen_report_id": last_seen,
                "document_id": o.document_id,
                "content_hash": o.content_hash,
                "raw_path": o.raw_path,
                "source_url": o.source_url,
                "retrieved_at": o.retrieved_at,
                "parser_version": o.parser_version,
                "adapter_version": m.ADAPTER_VERSION,
                "processed_at": processed,
            }
        )
    return pa.Table.from_pylist(records, schema=m.OBSERVATIONS)


def _methodology_table(
    report_ids: dict[str, list[tuple[datetime | None, str]]],
) -> pa.Table:
    def newest(slug: str | None) -> str | None:
        found = report_ids.get(slug or "") or []
        dated = sorted(found, key=lambda t: t[0] or datetime.min.replace(tzinfo=UTC))
        return dated[-1][1] if dated else None

    rows = [
        {
            "provider": c.provider.value,
            "effective_date": c.effective_date,
            "description": c.description,
            "affected_metrics": [x.value for x in c.affected_metrics],
            "evidence": c.evidence,
            "source_report_id": newest(c.evidence_source),
        }
        for c in CHANGES
    ]
    return pa.Table.from_pylist(rows, schema=m.METHODOLOGY_CHANGES)


def _code_version() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=Path(__file__).parent,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None
