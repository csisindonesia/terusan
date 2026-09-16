"""Bronze to Silver.

Reads Bronze through DuckDB rather than pyarrow: Bronze is partitioned by
source and a normalization run usually wants one dataset out of it, so pushing
the filter into the scan avoids reading partitions it will discard.

Silver is rewritten rather than appended. Normalization is a pure function of
Bronze plus the mapping, so the whole of an indicator can be rebuilt from
scratch — and rebuilding is what happens whenever a mapping is corrected, which
is often. Appending would leave the old, wrong rows in place beside the new.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import structlog

from ..storage import Layer, StorageResolver, slugify
from ..warehouse import ParquetWriter, Warehouse, table_from_rows
from .dimensions import CommodityRegistry, Geography, GeographyRegistry
from .observations import ColumnMapping, NormalizationResult, ObservationNormalizer
from .schema import SILVER_COMMODITIES, SILVER_GEOGRAPHY, SILVER_OBSERVATIONS

log = structlog.get_logger(__name__)

PIPELINE_VERSION = "1"


@dataclass(slots=True)
class SilverResult:
    """What one normalization run produced."""

    indicator_id: str
    stats: NormalizationResult = field(default_factory=NormalizationResult)
    files_written: int = 0
    bytes_written: int = 0
    paths: list[str] = field(default_factory=list)

    @property
    def observations(self) -> int:
        return self.stats.observations


class SilverRunner:
    """Normalizes Bronze records into Silver observations."""

    def __init__(
        self,
        resolver: StorageResolver,
        *,
        geography: GeographyRegistry | None = None,
        commodities: CommodityRegistry | None = None,
        writer: ParquetWriter | None = None,
    ) -> None:
        self._resolver = resolver
        self._geography = geography or GeographyRegistry()
        self._commodities = commodities or CommodityRegistry()
        self._writer = writer or ParquetWriter(resolver)

    def normalize(
        self,
        mapping: ColumnMapping,
        *,
        dataset: str | None = None,
        source_id: str | None = None,
        dry_run: bool = False,
    ) -> SilverResult:
        """Normalize one Bronze dataset into one indicator's observations."""
        result = SilverResult(indicator_id=mapping.indicator_id)
        records = self._read_bronze(dataset=dataset, source_id=source_id)

        normalizer = ObservationNormalizer(
            mapping,
            geography=self._geography,
            commodities=self._commodities,
            pipeline_version=PIPELINE_VERSION,
        )
        rows = list(normalizer.normalize(records, result.stats))

        if dry_run or not rows:
            self._log(result)
            return result

        table = table_from_rows(rows, SILVER_OBSERVATIONS)
        # Replaced, not appended: a corrected mapping must not leave the rows
        # it previously produced sitting alongside the new ones.
        self._replace(Layer.SILVER, "observations", slugify(mapping.indicator_id))

        written = self._writer.write(
            Layer.SILVER,
            "observations",
            table,
            partition_by=["indicator_id", "temporal_resolution"],
            run_id=slugify(mapping.indicator_id),
        )
        result.files_written = written.files
        result.bytes_written = written.bytes_written
        result.paths = written.paths
        self._log(result)
        return result

    def write_geography(self, geographies: list[Geography]) -> int:
        """Publish the geography dimension (program.md §11)."""
        rows = [
            {
                "geo_id": g.geo_id,
                "name": g.name,
                "official_name": None,
                "geo_type": str(g.geo_type),
                "parent_geo_id": g.parent_geo_id,
                "country_code": g.country_code,
                "province_code": g.province_code,
                "regency_code": g.regency_code,
                "bps_code": g.bps_code,
                "iso_code": g.iso_code,
                "valid_from": g.valid_from,
                "valid_to": g.valid_to,
                "aliases": list(g.aliases),
            }
            for g in geographies
        ]
        return self._write_dimension("geography", rows, SILVER_GEOGRAPHY)

    def write_commodities(self, commodities: list) -> int:
        """Publish the commodity dimension (program.md §12)."""
        rows = [
            {
                "commodity_id": c.commodity_id,
                "canonical_name": c.canonical_name,
                "description": None,
                "category": c.category,
                "subcategory": c.subcategory,
                "hs_code": c.hs_code,
                "hs_version": c.hs_version,
                "unit_default": c.unit_default,
                "aliases": list(c.aliases),
            }
            for c in commodities
        ]
        return self._write_dimension("commodities", rows, SILVER_COMMODITIES)

    # ---- internals -----------------------------------------------------

    def _write_dimension(self, name: str, rows: list[dict], schema: pa.Schema) -> int:
        if not rows:
            return 0
        self._replace(Layer.SILVER, name)
        written = self._writer.write(
            Layer.SILVER, name, table_from_rows(rows, schema), run_id="dimension"
        )
        return written.rows

    def _read_bronze(self, *, dataset: str | None, source_id: str | None) -> list[dict]:
        """Read Bronze records, filtering in the scan."""
        root = Path(self._resolver.resolve(Layer.BRONZE, "records"))
        if not any(root.rglob("*.parquet")):
            return []

        pattern = self._resolver.glob(Layer.BRONZE, "records")
        sql = "SELECT * FROM read_parquet(?, union_by_name=true, hive_partitioning=true)"
        conditions, params = [], [pattern]
        if dataset:
            conditions.append("dataset = ?")
            params.append(dataset)
        if source_id:
            conditions.append("source_id = ?")
            params.append(source_id)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        with Warehouse(self._resolver) as warehouse:
            # Via Arrow rather than fetchall(): DuckDB converts timestamptz to
            # Python through pytz, which is an extra dependency for no gain,
            # and Arrow carries the timezone itself.
            return warehouse.query(sql, params).arrow().read_all().to_pylist()

    def _replace(self, layer: Layer, dataset: str, *segments: str) -> None:
        """Clear a Silver target before rewriting it."""
        target = Path(self._resolver.resolve(layer, dataset, *segments))
        if segments:
            # Partitioned by indicator, so only this indicator's directories go.
            for directory in Path(self._resolver.resolve(layer, dataset)).glob(
                f"indicator_id={segments[0]}"
            ):
                shutil.rmtree(directory, ignore_errors=True)
            return
        shutil.rmtree(target, ignore_errors=True)

    @staticmethod
    def _log(result: SilverResult) -> None:
        stats = result.stats
        log.info(
            "silver.normalized",
            indicator=result.indicator_id,
            rows_in=stats.rows_in,
            observations=stats.observations,
            unresolved_geo=stats.unresolved_geo,
            ambiguous_values=stats.ambiguous_values,
            unparseable=stats.unparseable_values,
            files=result.files_written,
        )
