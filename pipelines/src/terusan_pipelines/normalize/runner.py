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
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import structlog

from .. import datasets as dataset_registry
from ..extract.base import PARSER_VERSION
from ..extract.runner import walk_raw
from ..identifiers import dataset_code, indicator_code, is_code
from ..storage import Layer, StorageResolver, slugify
from ..tagging import SourceFacts, dataset_tags, indicator_tags, source_tags
from ..warehouse import ParquetWriter, Warehouse, table_from_rows
from . import documents as document_catalogue
from .dimensions import CommodityRegistry, Geography, GeographyRegistry
from .observations import ColumnMapping, NormalizationResult, ObservationNormalizer
from .schema import (
    SILVER_COMMODITIES,
    SILVER_DATASETS,
    SILVER_DOCUMENTS,
    SILVER_GEOGRAPHY,
    SILVER_INDICATORS,
    SILVER_OBSERVATIONS,
    SILVER_SOURCES,
)

log = structlog.get_logger(__name__)

PIPELINE_VERSION = "1"


class CollapsedDimension(ValueError):
    """One observation id carries two different figures.

    An observation is identified by its indicator, its period, and the
    dimensions that vary within it. When a mapping omits a column the data
    actually varies along — the commodity in a table of food prices, the place
    in a table of provincial figures — every row in that period lands on one
    id, and the warehouse ends up asserting that forty prices are one price.

    Nothing downstream can detect this: the rows look well-formed, the counts
    look plausible, and a chart of them plots whichever row happened to be
    read last. So it is caught here, at the moment of writing, and refused.
    """


@dataclass(slots=True)
class SilverResult:
    """What one normalization run produced."""

    indicator_id: str
    #: The readable key the mapping was declared with, where it differs from
    #: the identifier. Reported so a run's output still names the series the
    #: maintainer asked for rather than only the code it landed under.
    slug: str | None = None
    stats: NormalizationResult = field(default_factory=NormalizationResult)
    files_written: int = 0
    bytes_written: int = 0
    paths: list[str] = field(default_factory=list)

    #: How coarse the observations turned out to be, where they agree. A series
    #: normalizing to two resolutions at once is a mapping reading two things,
    #: so this stays None rather than naming one of them.
    resolution: str | None = None

    #: When the run began and ended, for the run history (program.md §39).
    started_at: datetime | None = None
    finished_at: datetime | None = None
    #: A dry run writes no observations. Recorded so its zero does not read,
    #: in the history, as a mapping that stopped producing.
    dry_run: bool = False

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
        records: list[dict] | None = None,
    ) -> SilverResult:
        """Normalize one Bronze dataset into one indicator's observations.

        `records` lets a caller supply the Bronze rows it has already read.
        Without it, normalizing a dataset that holds many series means one scan
        of Bronze per series, and a source like FRED publishes seven hundred —
        the same partitions read seven hundred times to produce seven hundred
        indicators. `read_bronze` plus this argument reads them once.
        """
        started = datetime.now(UTC)
        if records is None:
            records = self.read_bronze(dataset=dataset, source_id=source_id)

        # The identifier a series is published under is a derived code, not the
        # readable key the mapping declares (program.md §10). Resolved here, at
        # the one place every normalization run passes through, so a caller
        # cannot write observations under a key that a later run would recode.
        slug, code = self._identity(mapping.indicator_id, source_id or _sole_source(records))
        mapping = replace(mapping, indicator_id=code)
        result = SilverResult(indicator_id=code, slug=slug, started_at=started, dry_run=dry_run)

        normalizer = ObservationNormalizer(
            mapping,
            geography=self._geography,
            commodities=self._commodities,
            pipeline_version=PIPELINE_VERSION,
        )
        rows = list(normalizer.normalize(records, result.stats))
        # Bronze carries the dataset by the name extraction gave it; the
        # catalogue identifies it by a code, for the same reason a series is
        # identified by one. The slug survives in the datasets table.
        for row in rows:
            slug_seen = row.get("dataset_id")
            row["dataset_id"] = dataset_code(str(slug_seen)) if slug_seen else None
        rows = _reject_collapsed(rows, mapping, result)
        # What survived, not what was emitted. The normalizer counts as it
        # yields, which is before duplicates are folded and revisions applied —
        # reporting that number would claim twice the observations a re-pulled
        # source actually produced.
        result.stats.observations = len(rows)
        resolutions = {str(row["temporal_resolution"]) for row in rows}
        result.resolution = resolutions.pop() if len(resolutions) == 1 else None

        if dry_run or not rows:
            result.finished_at = datetime.now(UTC)
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
        result.finished_at = datetime.now(UTC)
        self._log(result)
        return result

    def write_indicators(
        self,
        indicators: list[dict],
        *,
        source_id: str,
        dataset: str | None = None,
        merge: bool = False,
    ) -> int:
        """Publish what the indicators of one source are called (program.md §10).

        An identifier is not a name. Most series here carry a readable one, but
        a bulk source cannot: FRED's identifiers are eight-character codes, and
        without this table the portal has nothing to print but the code.

        Replaced one source at a time. The table is derived from Bronze, so a
        source's rows are rebuilt whenever it is normalized — but rebuilding
        FRED must not delete what Trading Economics published.

        `merge` keeps the source's other series while replacing these. A source
        whose series are normalized one command at a time — one per indicator,
        because the published table is wide and each column is its own figure —
        would otherwise end with only the last one named, every earlier name
        deleted by the call after it.

        Each row is `{indicator_id, name, slug?, code?, unit?, frequency?,
        description?, publisher?, release?}`; the rest of the schema stays null
        until something knows better than to guess it.

        Tags are not passed in. They are derived from this row and the registry
        record behind it, so every series is searchable the moment it is
        published and no caller can publish one that is not (see
        `terusan_pipelines.tagging`).
        """
        if not indicators:
            return 0

        processed_at = datetime.now(UTC)
        facts = _source_facts(source_id)
        rows = []
        for indicator in indicators:
            identifier = indicator["indicator_id"]
            # Per series, falling back to the run's dataset: one source can
            # publish several collections, and a table that filed them all
            # under the run's dataset would list the wrong series inside each.
            dataset_slug = indicator.get("dataset") or dataset
            dataset_id = indicator.get("dataset_id") or (
                dataset_code(dataset_slug) if dataset_slug else None
            )
            # A series whose identifier is a code still has a readable key —
            # the publisher's own series id — and it is what a reader searches
            # for. Without it, a FRED series is eight characters and a title.
            slug = indicator.get("slug") or (
                _slug_of(indicator.get("code")) if is_code(identifier) else identifier
            )
            rows.append(
                {
                    "indicator_id": identifier,
                    "slug": slug,
                    "name": indicator["name"],
                    "code": indicator.get("code"),
                    "canonical_name": None,
                    "description": indicator.get("description"),
                    "publisher": indicator.get("publisher"),
                    "release": indicator.get("release"),
                    "category": None,
                    "subcategory": None,
                    "unit": indicator.get("unit"),
                    "frequency": indicator.get("frequency"),
                    "methodology": None,
                    "source_id": source_id,
                    "dataset_id": dataset_id,
                    "tags": indicator_tags(
                        slug=slug,
                        name=indicator.get("name"),
                        unit=indicator.get("unit"),
                        frequency=indicator.get("frequency"),
                        publisher=indicator.get("publisher"),
                        dataset_slug=dataset_slug,
                        source=facts,
                        geographies=indicator.get("geographies") or (),
                    ),
                    "processed_at": processed_at,
                }
            )

        if merge:
            # Newly written rows win: this run read the same Bronze the earlier
            # one did, and a name that changed here changed on purpose.
            fresh = {row["indicator_id"] for row in rows}
            rows = [
                held
                for held in self._published_indicators(source_id)
                if held["indicator_id"] not in fresh
            ] + rows

        self._replace_partition(Layer.SILVER, "indicators", f"source_id={slugify(source_id)}")
        written = self._writer.write(
            Layer.SILVER,
            "indicators",
            table_from_rows(rows, SILVER_INDICATORS),
            partition_by=["source_id"],
            run_id=slugify(source_id),
        )
        log.info("silver.indicators", source=source_id, indicators=written.rows)
        return written.rows

    def collected_datasets(self) -> list[tuple[str, str | None]]:
        """Every dataset Bronze holds, with the source that collected it.

        Read from Bronze rather than from Silver because Bronze still carries
        the dataset by name. Silver identifies it by a code, and a catalogue
        rebuilt from codes alone could not say what any of them are called.
        """
        root = Path(self._resolver.resolve(Layer.BRONZE, "records"))
        if not any(root.rglob("*.parquet")):
            return []

        pattern = self._resolver.glob(Layer.BRONZE, "records")
        with Warehouse(self._resolver) as warehouse:
            rows = warehouse.query(
                "SELECT DISTINCT dataset, source_id FROM "
                "read_parquet(?, union_by_name=true, hive_partitioning=true) "
                "WHERE dataset IS NOT NULL ORDER BY dataset",
                [pattern],
            ).fetchall()
        return [(str(row[0]), str(row[1]) if row[1] else None) for row in rows]

    def _published_indicators(self, source_id: str) -> list[dict]:
        """The rows this source already has in the indicators table.

        Empty before the first write, which is the ordinary state and not a
        failure: a source publishes its names the first time one of its series
        is normalized.
        """
        partition = Path(
            self._resolver.resolve(Layer.SILVER, "indicators", f"source_id={slugify(source_id)}")
        )
        if not any(partition.rglob("*.parquet")):
            return []

        with Warehouse(self._resolver) as warehouse:
            # Through Arrow rather than as Python rows: the table carries a
            # zoned timestamp, and DuckDB's own conversion of one needs `pytz`,
            # which is not a dependency here and should not become one for the
            # sake of reading back what we wrote.
            held = (
                warehouse.query(
                    "SELECT * FROM read_parquet(?, union_by_name=true)",
                    [str(partition / "**" / "*.parquet")],
                )
                .fetch_arrow_table()
                .to_pylist()
            )

        # The partition column is not in the file; it is the directory name.
        for row in held:
            row["source_id"] = source_id
        return held

    def indicator_tags_by_dataset(self) -> dict[str, list[list[str]]]:
        """The tags of the series inside each dataset, as published.

        Empty where the indicators table has not been written yet, which is
        the normal state of a lake whose sources publish no names: the dataset
        still gets its own tags, just not the ones its members agree on.
        """
        root = Path(self._resolver.resolve(Layer.SILVER, "indicators"))
        if not any(root.rglob("*.parquet")):
            return {}

        pattern = self._resolver.glob(Layer.SILVER, "indicators")
        with Warehouse(self._resolver) as warehouse:
            rows = warehouse.query(
                "SELECT dataset_id, tags FROM "
                "read_parquet(?, union_by_name=true, hive_partitioning=true) "
                "WHERE dataset_id IS NOT NULL AND tags IS NOT NULL",
                [pattern],
            ).fetchall()

        grouped: dict[str, list[list[str]]] = {}
        for dataset_id, tags in rows:
            grouped.setdefault(str(dataset_id), []).append([str(tag) for tag in tags])
        return grouped

    def referenced_geo_ids(self) -> set[str]:
        """Geography identifiers that Silver observations actually use.

        A dimension exists to make its facts interpretable, so publishing
        members nothing refers to is noise — two hundred countries beside a
        warehouse holding Indonesian figures.
        """
        root = Path(self._resolver.resolve(Layer.SILVER, "observations"))
        if not any(root.rglob("*.parquet")):
            return set()

        pattern = self._resolver.glob(Layer.SILVER, "observations")
        with Warehouse(self._resolver) as warehouse:
            rows = warehouse.query(
                "SELECT DISTINCT geo_id FROM "
                "read_parquet(?, union_by_name=true, hive_partitioning=true) "
                "WHERE geo_id IS NOT NULL",
                [pattern],
            ).fetchall()
        return {row[0] for row in rows}

    def write_geography(self, geographies: list[Geography]) -> int:
        """Publish the geography dimension (program.md §11).

        Deduplicated by identifier, merging aliases. A place can be described by
        more than one reference file — Indonesia is in the country list and
        again in the provinces file, which is where the names agencies actually
        print live — and two rows for one identifier silently doubles every
        observation that joins to it.
        """
        merged: dict[str, Geography] = {}
        for geography in geographies:
            existing = merged.get(geography.geo_id)
            if existing is None:
                merged[geography.geo_id] = geography
                continue
            # Keep the first description, but no name is worth losing.
            merged[geography.geo_id] = replace(
                existing,
                aliases=tuple(dict.fromkeys([*existing.aliases, *geography.aliases])),
                bps_code=existing.bps_code or geography.bps_code,
                iso_code=existing.iso_code or geography.iso_code,
                parent_geo_id=existing.parent_geo_id or geography.parent_geo_id,
                valid_from=existing.valid_from or geography.valid_from,
            )
        geographies = list(merged.values())

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

    def write_sources(self, metas: list) -> int:
        """Publish the source registry (program.md §16).

        The registry is the authority and it lives in code. Writing it into the
        lake is what lets the serving layer answer "when does this refresh, and
        under what licence" — the catalog database holds the same record, but it
        is optional, and a reader should not need it to see how a series is
        kept.
        """
        rows = [
            {
                "source_id": meta.slug,
                "name": meta.name,
                "organization": meta.organization,
                "category": str(meta.category),
                "source_type": str(meta.source_type),
                "collection_method": str(meta.collection_method),
                "base_url": meta.base_url,
                "country": meta.country,
                "license": meta.license,
                "update_frequency": str(meta.update_frequency),
                "schedule": meta.schedule,
                "active": meta.active,
                "max_requests_per_second": float(meta.max_requests_per_second),
                "notes": meta.notes,
                "tags": source_tags(SourceFacts.from_meta(meta)),
            }
            for meta in metas
        ]
        return self._write_dimension("sources", rows, SILVER_SOURCES)

    def write_datasets(
        self,
        slugs: Iterable[tuple[str, str | None]],
        *,
        member_tags: dict[str, list[list[str]]] | None = None,
    ) -> int:
        """Publish the dataset catalogue (program.md §9).

        `slugs` is what the lake actually holds — `(dataset slug, source)` as
        Bronze recorded it — rather than the declared registry alone: a scraper
        landing a collection nobody has written a title for still belongs in
        the catalogue, described from its slug, instead of disappearing from it
        until someone edits a Python file.

        `member_tags` is the tags of the series inside each dataset, where they
        have been published. Only the tags they all share reach the dataset;
        see `tagging.dataset_tags`.
        """
        seen: dict[str, tuple[str, str | None]] = {}
        for slug, source in slugs:
            if slug:
                seen.setdefault(slug, (slug, source))
        for declared in dataset_registry.DATASETS:
            seen.setdefault(declared.slug, (declared.slug, declared.source))
        if not seen:
            return 0

        named = {dataset_code(slug) for slug in seen}

        processed_at = datetime.now(UTC)
        member_tags = member_tags or {}
        rows = []
        for slug, source in sorted(seen.values()):
            meta = dataset_registry.describe(slug, source=source)
            facts = _source_facts(meta.source) or (
                SourceFacts(source_id=meta.source) if meta.source != "unknown" else None
            )
            rows.append(
                {
                    "dataset_id": meta.dataset_id,
                    "slug": meta.slug,
                    "title": meta.title,
                    "description": meta.description,
                    "source_id": None if meta.source == "unknown" else meta.source,
                    "tags": dataset_tags(
                        slug=meta.slug,
                        title=meta.title,
                        description=meta.description,
                        declared=meta.tags,
                        source=facts,
                        indicator_tags_seen=member_tags.get(meta.dataset_id, []),
                    ),
                    "processed_at": processed_at,
                }
            )

        # A dataset the figures point at but nothing names — a Bronze dataset
        # renamed since the observations were normalized. Listed under its
        # code rather than left out: a reader following a series to its
        # collection should find a thin entry, not a 404, and a missing entry
        # is how a rename goes unnoticed.
        for dataset_id, source_id in self.referenced_datasets():
            if dataset_id in named:
                continue
            facts = _source_facts(source_id)
            rows.append(
                {
                    "dataset_id": dataset_id,
                    "slug": dataset_id,
                    "title": dataset_id,
                    "description": (
                        "Referenced by observations under this identifier; no "
                        "collection in Bronze or in the registry claims it."
                    ),
                    "source_id": source_id,
                    "tags": dataset_tags(slug=dataset_id, source=facts),
                    "processed_at": processed_at,
                }
            )
        return self._write_dimension("datasets", rows, SILVER_DATASETS)

    def write_documents(self) -> int:
        """Publish the document catalogue (program.md §13).

        Documents only: the PDFs, Word files and papers somebody would open and
        read. The spreadsheets and crawled pages a scraper lands are not
        catalogued here — a table of fifteen hundred CSVs under a heading that
        says Documents describes nothing anyone was looking for.

        Nothing is lost by leaving them out. RAW still holds every byte, and a
        Silver observation carries its own `document_id`, `content_hash`,
        `source_url` and `raw_path`, so "where did this figure come from" is
        answered on the row itself rather than by this table.

        Built from the provenance sidecars in RAW rather than from Bronze,
        because the lake parses far less than it lands: a catalogue of only the
        parsed part would list the handbook's tables and not the handbook.

        Three reads, each of the whole thing once. The alternative — asking the
        observations what a document backs, one document at a time — is a scan
        of a quarter-million-row table per document.
        """
        raw_root = self._resolver.resolve(Layer.RAW)
        landed = list(walk_raw(self._resolver))
        if not landed:
            return 0

        links = self._document_links()
        extracted = self._extracted_documents()

        rows = []
        for item in landed:
            built = document_catalogue.row(
                item,
                raw_root=raw_root,
                pipeline_version=PIPELINE_VERSION,
                publisher=item.source_organization,
                links=links.get(item.document_id, (0, 0)),
                extracted=extracted.get(item.document_id),
            )
            # Filtered after building rather than before, because what a thing
            # is comes out of `classify`, which needs the row's own reasoning —
            # the source's declared type, then the extractor's, then the media
            # type. Deciding it twice is how the two drift apart.
            if document_catalogue.is_readable(built["document_type"]):
                rows.append(built)

        log.info(
            "documents.catalogued",
            landed=len(landed),
            documents=len(rows),
            # Said plainly, because the gap is large and looks like a bug
            # otherwise: most of what a statistics warehouse lands is a CSV.
            not_documents=len(landed) - len(rows),
        )
        return self._write_dimension("documents", rows, SILVER_DOCUMENTS)

    def _document_links(self) -> dict[str, tuple[int, int]]:
        """What each document backs: distinct indicators, and observations.

        Counted from the figures' own `document_id` rather than asserted, so
        the catalogue cannot claim a provenance the observations do not make.
        """
        root = Path(self._resolver.resolve(Layer.SILVER, "observations"))
        if not any(root.rglob("*.parquet")):
            return {}
        pattern = self._resolver.glob(Layer.SILVER, "observations")
        with Warehouse(self._resolver) as warehouse:
            rows = warehouse.query(
                "SELECT document_id, count(DISTINCT indicator_id), count(*) FROM "
                "read_parquet(?, union_by_name=true, hive_partitioning=true) "
                "WHERE document_id IS NOT NULL GROUP BY document_id",
                [pattern],
            ).fetchall()
        return {str(row[0]): (int(row[1]), int(row[2])) for row in rows}

    def _extracted_documents(self) -> dict[str, dict]:
        """What an extractor learned about the documents it has read.

        The title it found inside the file, the language, the page count. Empty
        for a lake whose Bronze holds no documents, which is the ordinary case
        for a warehouse of tabular sources.
        """
        root = Path(self._resolver.resolve(Layer.BRONZE, "documents"))
        if not any(root.rglob("*.parquet")):
            return {}
        pattern = self._resolver.glob(Layer.BRONZE, "documents")
        with Warehouse(self._resolver) as warehouse:
            rows = warehouse.query(
                # Latest extraction wins: a document re-read by a better parser
                # has a row per parser version, and the newest is the one whose
                # title and page count should reach the catalogue.
                "SELECT document_id, "
                "  any_value(document_type ORDER BY processed_at DESC), "
                "  any_value(title ORDER BY processed_at DESC), "
                "  any_value(language ORDER BY processed_at DESC), "
                "  any_value(page_count ORDER BY processed_at DESC), "
                # Whitespace-split rather than a tokeniser: this is a size, not
                # a linguistic claim, and it is what a reader reads it as.
                "  any_value(len(string_split_regex(raw_text, '\\s+')) ORDER BY processed_at DESC) "
                "FROM read_parquet(?, union_by_name=true, hive_partitioning=true) "
                "GROUP BY document_id",
                [pattern],
            ).fetchall()
        return {
            str(row[0]): {
                "document_type": row[1],
                "title": row[2],
                "language": row[3],
                "page_count": int(row[4]) if row[4] is not None else None,
                "word_count": int(row[5]) if row[5] is not None else None,
            }
            for row in rows
        }

    def referenced_datasets(self) -> list[tuple[str, str | None]]:
        """The datasets Silver observations actually point at."""
        root = Path(self._resolver.resolve(Layer.SILVER, "observations"))
        if not any(root.rglob("*.parquet")):
            return []

        pattern = self._resolver.glob(Layer.SILVER, "observations")
        with Warehouse(self._resolver) as warehouse:
            rows = warehouse.query(
                "SELECT dataset_id, any_value(source_id) FROM "
                "read_parquet(?, union_by_name=true, hive_partitioning=true) "
                "WHERE dataset_id IS NOT NULL GROUP BY dataset_id ORDER BY dataset_id",
                [pattern],
            ).fetchall()
        return [(str(row[0]), str(row[1]) if row[1] else None) for row in rows]

    # ---- internals -----------------------------------------------------

    def _write_dimension(self, name: str, rows: list[dict], schema: pa.Schema) -> int:
        if not rows:
            return 0
        self._replace(Layer.SILVER, name)
        written = self._writer.write(
            Layer.SILVER, name, table_from_rows(rows, schema), run_id="dimension"
        )
        return written.rows

    def read_bronze(
        self, *, dataset: str | None = None, source_id: str | None = None
    ) -> list[dict]:
        """Read Bronze records, filtering in the scan.

        Public because a caller normalizing many indicators out of one
        dataset should read it once and hand the rows to `normalize`.
        """
        root = Path(self._resolver.resolve(Layer.BRONZE, "records"))
        if not any(root.rglob("*.parquet")):
            return []

        pattern = self._resolver.glob(Layer.BRONZE, "records")
        sql = "SELECT * FROM read_parquet(?, union_by_name=true, hive_partitioning=true)"
        # Re-extracting under a new parser version appends rows rather than
        # replacing them, which is what makes a partition traceable to the code
        # that produced it (program.md §17). The cost is that a read has to say
        # which version it wants: without this, improving a parser silently
        # doubles every observation downstream.
        conditions, params = ["parser_version = ?"], [pattern, PARSER_VERSION]
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

    def _identity(self, declared: str, source_id: str | None) -> tuple[str | None, str]:
        """The slug and the identifier a declared indicator key resolves to.

        A key that is already a code is left alone: an extractor that composed
        one — FRED's, whose titles cannot be identifiers — has already made
        this decision with the publisher's own series id in hand, and recoding
        it here would move every partition it has ever written.

        The source is part of the identity, so `gdp` collected from the World
        Bank and `gdp` collected from Trading Economics are two series. Where
        the records do not agree on one, the namespace falls back to a
        constant: two sources' figures under one mapping is already a mistake,
        and inventing a namespace would hide it behind a second one.
        """
        if is_code(declared):
            return None, declared
        return declared, indicator_code(source_id or "indicator", declared)

    def _replace_partition(self, layer: Layer, dataset: str, partition: str) -> None:
        """Clear one Hive partition of a Silver table, leaving the others."""
        target = Path(self._resolver.resolve(layer, dataset, partition))
        shutil.rmtree(target, ignore_errors=True)

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


def _source_facts(source_id: str | None) -> SourceFacts | None:
    """The registry record for a source, where the process can see one.

    Looked up lazily, and forgiven if it is not there: the registry is Python
    code that imports every scraper, and a normalization run reading a lake
    someone else filled should not fail because one scraper's module does not
    import. Tags derived without it are thinner, not wrong.
    """
    if not source_id:
        return None
    try:
        from ..sources import registry

        return SourceFacts.from_meta(registry.get(source_id).meta)
    except Exception:  # noqa: BLE001 - a missing registry is not a failed run
        return SourceFacts(source_id=source_id)


def _slug_of(value: str | None) -> str | None:
    """A publisher's own code, as a readable key."""
    if not value:
        return None
    try:
        return slugify(str(value), allow_partition=False)
    except Exception:  # noqa: BLE001 - a code that sanitises to nothing has no slug
        return None


def _sole_source(records: list[dict]) -> str | None:
    """The source every record came from, or None where they disagree."""
    sources = {record.get("source_id") for record in records}
    if len(sources) != 1:
        return None
    only = sources.pop()
    return str(only) if only else None


def _reject_collapsed(
    rows: list[dict[str, object]],
    mapping: ColumnMapping,
    result: SilverResult,
) -> list[dict[str, object]]:
    """Refuse a run whose rows collide on identity while disagreeing on value.

    Two rows sharing an id and a figure are the same observation stated twice —
    a source that repeats a line, or an overlapping file — and are safely
    deduplicated.

    Two that disagree are one of two different things, and the retrieval time
    tells them apart:

    * Retrieved at different moments, the later one supersedes. A daily API
      pulled twice returns the same window twice, and the last session is still
      moving — gold closed at 4330.00 in the morning pull and 4328.40 in the
      evening one. That is a revision, which is what a re-pull is for.
    * Retrieved at the same moment, they came from one snapshot, and one
      snapshot cannot hold two figures for one observation. That is a mapping
      that dropped a dimension, and it is never recoverable downstream.
    """
    seen: dict[str, dict[str, object]] = {}
    kept: dict[str, int] = {}
    out: list[dict[str, object]] = []

    for row in rows:
        identifier = str(row["observation_id"])
        first = seen.get(identifier)
        if first is None:
            seen[identifier] = row
            kept[identifier] = len(out)
            out.append(row)
            continue

        if _same_figure(first, row):
            result.stats.duplicate_rows += 1
            continue

        earlier, later = _by_retrieval(first, row)
        if earlier is None:
            raise CollapsedDimension(
                f"{mapping.indicator_id}: period {row['period']!r} holds two different "
                f"figures under one observation id — {first['value']} "
                f"{first['unit'] or ''} and {row['value']} {row['unit'] or ''}, "
                "both from the same retrieval. The rows differ along a dimension "
                "the mapping does not name; pass --geo-column or "
                "--commodity-column for the column they differ in."
            )

        result.stats.revised_rows += 1
        seen[identifier] = later
        out[kept[identifier]] = later

    return out


def _by_retrieval(
    first: dict[str, object], second: dict[str, object]
) -> tuple[dict[str, object] | None, dict[str, object]]:
    """The earlier and later of two rows, or `(None, …)` where they tie.

    A tie means one retrieval, so there is nothing to supersede.
    """
    left, right = first.get("retrieved_at"), second.get("retrieved_at")
    if left is None or right is None or left == right:
        return None, second
    return (first, second) if left < right else (second, first)  # type: ignore[operator]


def _same_figure(left: dict[str, object], right: dict[str, object]) -> bool:
    return all(left[field] == right[field] for field in ("value", "unit", "status"))
