"""Arrow schemas for the Silver layer.

Silver is where types are decided, once, with the whole column in view — Bronze
deliberately keeps everything as text so a value cannot change type between
partitions (program.md §6, §7).

Observations use `decimal128` rather than `double`. Published statistics are
decimal quantities: a figure like 1234.56 has no exact binary representation,
and summing a million of them in float drifts in a way that is impossible to
explain to whoever has to defend the total.
"""

from __future__ import annotations

import pyarrow as pa

#: Currency and index figures need more than the two decimal places money uses;
#: exchange rates are quoted to four and trade volumes run to twelve digits.
VALUE_TYPE = pa.decimal128(38, 9)

#: Decimal places `VALUE_TYPE` can hold. A figure carrying more is quantized to
#: this before it is written — Arrow refuses to rescale rather than round
#: silently, which is the right default but means the rounding has to be
#: deliberate. Nine places is far beyond the precision of any published
#: statistic; the extra digits come from a spreadsheet's own arithmetic.
VALUE_SCALE = 9

#: Provenance carried from Bronze so a Silver row still answers "where did this
#: come from" without a join (program.md §17).
SILVER_PROVENANCE_FIELDS: list[pa.Field] = [
    pa.field("source_id", pa.string(), nullable=False),
    # The page or endpoint the figure came from. `raw_path` points at the
    # preserved copy; this points at where it was published, which is what
    # "where did this number come from" usually means (program.md §2.2).
    pa.field("source_url", pa.string()),
    pa.field("document_id", pa.string()),
    pa.field("dataset_id", pa.string()),
    pa.field("content_hash", pa.string()),
    pa.field("raw_path", pa.string()),
    pa.field("retrieved_at", pa.timestamp("us", tz="UTC")),
    pa.field("processed_at", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("pipeline_version", pa.string()),
]

#: Indicators: what is being measured (program.md §10).
SILVER_INDICATORS = pa.schema(
    [
        pa.field("indicator_id", pa.string(), nullable=False),
        # The readable key the mapping was declared with — `retail_sales_index`
        # — kept beside the derived identifier rather than instead of it. It is
        # what a maintainer greps for and what a reader recognises, but it is
        # not the identifier: a publisher renaming a series must not orphan the
        # partitions and links written under the old name.
        pa.field("slug", pa.string()),
        pa.field("name", pa.string(), nullable=False),
        # The publisher's own identifier for the series — FRED's
        # `NASDAQNQID55LMN`, BPS's table number. Kept because it is what a
        # reader takes back to the source, and because our own identifier is a
        # derived code for sources whose titles cannot be one.
        pa.field("code", pa.string()),
        pa.field("canonical_name", pa.string()),
        # What the series counts, in the publisher's own words. A figure whose
        # definition is missing is a figure nobody can use safely, and FRED's
        # notes are where that definition lives.
        pa.field("description", pa.string()),
        # Who produced the figures, and in which release they arrive. Distinct
        # from `source_id`, which is where we collected them: FRED redistributes
        # the Treasury's, the OECD's and the IMF's series under its own roof,
        # and crediting FRED for a Treasury figure would be wrong.
        pa.field("publisher", pa.string()),
        pa.field("release", pa.string()),
        pa.field("category", pa.string()),
        pa.field("subcategory", pa.string()),
        pa.field("unit", pa.string()),
        pa.field("frequency", pa.string()),
        pa.field("methodology", pa.string()),
        pa.field("source_id", pa.string()),
        # The collection the series belongs to, so a dataset can list what is
        # inside it without a scan of a quarter of a million observations.
        pa.field("dataset_id", pa.string()),
        # What a reader searches by. Derived from the facts already in this row
        # — the source, the cadence, the unit — plus the topics the title reads
        # for, so they are rebuilt with the series and cannot drift from it.
        pa.field("tags", pa.list_(pa.string())),
        pa.field("processed_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

#: Observations: the fact table. Indicator + time + geography + dimensions
#: (program.md §10).
SILVER_OBSERVATIONS = pa.schema(
    [
        pa.field("observation_id", pa.string(), nullable=False),
        pa.field("indicator_id", pa.string(), nullable=False),
        # Canonical label plus explicit bounds: a period string cannot be
        # compared or filtered, and most questions here are about ranges.
        pa.field("period", pa.string(), nullable=False),
        pa.field("period_start", pa.date32(), nullable=False),
        pa.field("period_end", pa.date32(), nullable=False),
        pa.field("temporal_resolution", pa.string(), nullable=False),
        pa.field("value", VALUE_TYPE),
        pa.field("unit", pa.string()),
        # Why a value is absent. "Not collected" and "collected and zero" are
        # different facts, and a null alone cannot tell them apart.
        pa.field("status", pa.string(), nullable=False),
        # False where the number was read under an assumption that could have
        # gone the other way — see normalize.values.
        pa.field("value_unambiguous", pa.bool_(), nullable=False),
        pa.field("raw_value", pa.string()),
        pa.field("geo_id", pa.string()),
        pa.field("geo_name_raw", pa.string()),
        pa.field("commodity_id", pa.string()),
        pa.field("commodity_name_raw", pa.string()),
        pa.field("release_date", pa.date32()),
        pa.field("revision", pa.int32()),
        *SILVER_PROVENANCE_FIELDS,
    ]
)

#: Documents: the source material behind the figures (program.md §13).
#:
#: One row per artifact landed in RAW, built from the provenance sidecar every
#: landing writes. The grain is the retrieval, not the publication: two
#: editions of one handbook are two documents, because they are two files with
#: two content hashes and the figures point at one or the other.
#:
#: The provenance columns are spelled out rather than spliced in from
#: `SILVER_PROVENANCE_FIELDS`, which carries `document_id` and `source_url` of
#: its own — here those are the row's identity and its own address, not a
#: pointer to somewhere else, and Arrow would otherwise hold two fields by each
#: name and let a writer fill either one.
#:
#: Full text is deliberately absent. Bronze holds it for the documents an
#: extractor has read, and copying it here would make the catalogue too heavy
#: to list while saying nothing a reader scanning for a document needs.
SILVER_DOCUMENTS = pa.schema(
    [
        pa.field("document_id", pa.string(), nullable=False),
        # regulation, report, publication, web_page, data_file — see
        # `normalize.documents.classify`. Never null: an unrecognised artifact
        # is a `data_file`, which is what an unread download is.
        pa.field("document_type", pa.string(), nullable=False),
        pa.field("title", pa.string(), nullable=False),
        pa.field("subtitle", pa.string()),
        pa.field("language", pa.string()),
        pa.field("author", pa.string()),
        # Who issued it, which is not who we collected it from: FRED
        # redistributes the Treasury's releases, and crediting FRED for a
        # Treasury document would be wrong (see SILVER_INDICATORS.publisher).
        pa.field("publisher", pa.string()),
        pa.field("published_at", pa.date32()),
        # Where the publisher put it. `raw_path` is our preserved copy; this is
        # the address that breaks when an agency reorganises its site, which is
        # the whole reason the copy exists (program.md §2.1).
        pa.field("source_url", pa.string()),
        pa.field("original_filename", pa.string()),
        pa.field("media_type", pa.string()),
        pa.field("size_bytes", pa.int64()),
        # Known only for what an extractor has actually read, so null for most.
        pa.field("page_count", pa.int32()),
        pa.field("word_count", pa.int32()),
        # What the document contributed, counted at build time. Derived from
        # the observations' own `document_id`, so it cannot claim a link the
        # figures do not make — and so a document that backs nothing is
        # visibly a document that backs nothing.
        pa.field("indicator_count", pa.int32(), nullable=False),
        pa.field("observation_count", pa.int64(), nullable=False),
        # The collection it was landed under, as a code matching
        # SILVER_DATASETS.dataset_id.
        pa.field("dataset_id", pa.string()),
        pa.field("dataset_slug", pa.string()),
        # The RAW partition it landed in — ("edition=2025",). Kept because it
        # is how a publisher's own editions are told apart, and the filename
        # often does not say.
        pa.field("partition", pa.list_(pa.string())),
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("content_hash", pa.string()),
        # Relative to the lake root, so the catalogue survives the lake moving
        # between a mount and a bucket (program.md §45.4).
        pa.field("raw_path", pa.string()),
        pa.field("retrieved_at", pa.timestamp("us", tz="UTC")),
        pa.field("processed_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("pipeline_version", pa.string()),
    ]
)

#: Geography dimension (program.md §11).
SILVER_GEOGRAPHY = pa.schema(
    [
        pa.field("geo_id", pa.string(), nullable=False),
        pa.field("name", pa.string(), nullable=False),
        pa.field("official_name", pa.string()),
        pa.field("geo_type", pa.string(), nullable=False),
        pa.field("parent_geo_id", pa.string()),
        pa.field("country_code", pa.string()),
        pa.field("province_code", pa.string()),
        pa.field("regency_code", pa.string()),
        pa.field("bps_code", pa.string()),
        pa.field("iso_code", pa.string()),
        # Administrative changes must not overwrite earlier definitions
        # (program.md §11), so validity is part of the row.
        pa.field("valid_from", pa.date32()),
        pa.field("valid_to", pa.date32()),
        pa.field("aliases", pa.list_(pa.string())),
    ]
)

#: Commodity dimension (program.md §12).
#: The newspapers the news monitor reads, published so the serving layer can
#: list them without reading the repository.
#:
#: A dimension rather than a lookup table: an article's outlet is one of the
#: things a reader filters and groups by, exactly as they do with a place or a
#: commodity, and the retired outlets belong in it too — a paper that published
#: until last year is why a province's coverage changed.
SILVER_NEWS_OUTLETS = pa.schema(
    [
        pa.field("host", pa.string(), nullable=False),
        pa.field("outlet", pa.string(), nullable=False),
        pa.field("province", pa.string()),
        pa.field("geo_id", pa.string()),
        pa.field("bps_code", pa.string()),
        pa.field("base_url", pa.string()),
        pa.field("section_path", pa.string()),
        pa.field("adapter", pa.string()),
        pa.field("active", pa.bool_(), nullable=False),
        pa.field("note", pa.string()),
    ]
)

SILVER_COMMODITIES = pa.schema(
    [
        pa.field("commodity_id", pa.string(), nullable=False),
        pa.field("canonical_name", pa.string(), nullable=False),
        pa.field("description", pa.string()),
        pa.field("category", pa.string()),
        pa.field("subcategory", pa.string()),
        pa.field("hs_code", pa.string()),
        pa.field("hs_version", pa.string()),
        pa.field("unit_default", pa.string()),
        pa.field("aliases", pa.list_(pa.string())),
    ]
)

#: The source registry, published so the serving layer can read it.
#:
#: These are the settings a source is collected under — when it runs, how hard
#: it may be hit, what licence its figures carry — and they live in code, in
#: `SourceMeta`. Writing them into the lake means the read-only API can answer
#: "how is this series kept up to date" without a connection to the catalog
#: database, which is optional (program.md §16).
SILVER_SOURCES = pa.schema(
    [
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("name", pa.string(), nullable=False),
        pa.field("organization", pa.string()),
        pa.field("category", pa.string(), nullable=False),
        pa.field("source_type", pa.string(), nullable=False),
        pa.field("collection_method", pa.string(), nullable=False),
        pa.field("base_url", pa.string()),
        pa.field("country", pa.string()),
        pa.field("license", pa.string()),
        pa.field("update_frequency", pa.string(), nullable=False),
        #: Cron, or null for manual-only.
        pa.field("schedule", pa.string()),
        pa.field("active", pa.bool_(), nullable=False),
        pa.field("max_requests_per_second", pa.float64(), nullable=False),
        pa.field("notes", pa.string()),
        pa.field("tags", pa.list_(pa.string())),
    ]
)

#: Datasets: a collection as its publisher issues it (program.md §9).
#:
#: Published as a table of its own rather than derived from the observations,
#: because the things a reader wants of a collection — what it is called, what
#: it holds, what it may be used for — are nowhere in the figures. The figures
#: carry `dataset_id` and nothing else.
SILVER_DATASETS = pa.schema(
    [
        pa.field("dataset_id", pa.string(), nullable=False),
        # The name extraction gives it, which Bronze records still carry. Kept
        # so a Bronze record can be traced to the catalogue entry it feeds.
        pa.field("slug", pa.string(), nullable=False),
        pa.field("title", pa.string(), nullable=False),
        pa.field("description", pa.string()),
        pa.field("source_id", pa.string()),
        pa.field("tags", pa.list_(pa.string())),
        pa.field("processed_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

#: Events: things that happened, or will, on dates a series can be read
#: against — a holiday, a cuti bersama, the month of Ramadan.
#:
#: Not observations: an event has no figure, only a span of days, and what a
#: reader asks of it is "what did prices do around it". Kept generic so that
#: elections, policy changes and disasters can join the holidays under their
#: own `category` without a table each.
SILVER_EVENTS = pa.schema(
    [
        pa.field("event_id", pa.string(), nullable=False),
        # The family: `holiday` today; `election`, `policy`, `disaster` later.
        pa.field("category", pa.string(), nullable=False),
        # Within it: `libur_nasional`, `cuti_bersama`, `ramadan`.
        pa.field("kind", pa.string(), nullable=False),
        # What recurs: `idul_fitri` is one key across every year, so Lebaran
        # 2020 and Lebaran 2026 are one series of events.
        pa.field("key", pa.string(), nullable=False),
        pa.field("name", pa.string(), nullable=False),
        pa.field("name_en", pa.string()),
        # As the decree printed it, OCR and all.
        pa.field("name_printed", pa.string()),
        pa.field("religion", pa.string()),
        # How its date moves: gregorian, hijri, lunisolar.
        pa.field("calendar", pa.string()),
        pa.field("year", pa.int32(), nullable=False),
        pa.field("start_date", pa.date32(), nullable=False),
        pa.field("end_date", pa.date32(), nullable=False),
        # Every day it covers. Cuti bersama around Lebaran skip the weekend
        # between them, so start to end is not always every day.
        pa.field("dates", pa.list_(pa.date32()), nullable=False),
        pa.field("geo_id", pa.string(), nullable=False),
        # True where the dates are derived rather than decreed.
        pa.field("approximate", pa.bool_(), nullable=False),
        # What fixed the dates: the decree, or how they were derived.
        pa.field("basis", pa.string()),
        pa.field("decree_id", pa.string()),
        pa.field("decree_enacted", pa.date32()),
        *SILVER_PROVENANCE_FIELDS,
    ]
)

SILVER_SCHEMAS: dict[str, pa.Schema] = {
    "indicators": SILVER_INDICATORS,
    "observations": SILVER_OBSERVATIONS,
    "documents": SILVER_DOCUMENTS,
    "geography": SILVER_GEOGRAPHY,
    "commodities": SILVER_COMMODITIES,
    "news_outlets": SILVER_NEWS_OUTLETS,
    "sources": SILVER_SOURCES,
    "datasets": SILVER_DATASETS,
    "events": SILVER_EVENTS,
}
