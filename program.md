# Product Requirements Document
## Research Data Warehouse & Data Portal

**Version:** 1.0  
**Status:** Draft  
**Product Type:** Research Data Infrastructure  
**Primary Storage:** Apache Parquet  
**Primary Analytical Engine:** DuckDB  
**Portal:** TanStack Start  
**Application Database:** PostgreSQL

---

# 1. Product Vision

Build a centralized, source-traceable research data infrastructure capable of collecting, preserving, normalizing, cataloging, querying, and serving heterogeneous research datasets.

The platform should support data originating from:

- Government portals
- Official statistical agencies
- APIs
- Web scraping
- Regulations and legal documents
- News and media clippings
- Research publications
- Reports
- PDF/DOCX documents
- CSV/XLSX files
- Internal datasets
- Economic and financial statistics
- Geographic datasets

The platform is primarily a:

> **Research Data Warehouse + Data Catalog + Data Serving Platform**

It is **not primarily a visualization or BI platform**.

Visualization and advanced analysis should be performed by external consumers such as:

- Power BI
- Apache Superset
- Metabase
- Tableau
- Grafana
- Jupyter
- Python
- R
- AI applications
- Custom web applications

---

# 2. Core Principles

## 2.1 Source Preservation

Original source data must be retained whenever technically and legally possible.

```text
Original PDF
     ↓
RAW Storage
     ↓
Extract
     ↓
Parquet
```

The original remains available for verification and future reprocessing.

## 2.2 Provenance

Every meaningful data point must be traceable:

```text
Observation
    ↓
Dataset
    ↓
Source
    ↓
Original document/API/page
```

## 2.3 Separation of Storage and Serving

```text
Parquet
   ↓
Query Engine
   ↓
Serving Layer
   ↓
Consumer
```

## 2.4 Tool Agnostic

The warehouse must not depend on a specific visualization platform.

## 2.5 AI Is Not a Data Source

LLMs may discover data, understand questions, generate query plans, and summarize results.

LLMs must not be considered authoritative sources for statistical values.

---

# 3. High-Level Architecture

```text
                         DATA SOURCES
                              │
          ┌───────────────────┼──────────────────┐
          │                   │                  │
          ▼                   ▼                  ▼
     Official Portal         APIs              Files
     Web Scraping            BPS/BI            PDF
     Regulations             Ministries        XLSX
     News                    Statistics        CSV
     Research                                   DOCX
          │                   │                  │
          └───────────────────┼──────────────────┘
                              ▼
                     INGESTION PIPELINE
                              │
                              ▼
                       RAW DATA STORAGE
                              │
                     PDF / HTML / JSON
                              │
                              ▼
                    EXTRACTION / PARSING
                              │
                              ▼
                          BRONZE
                         Parquet
                              │
                              ▼
                       NORMALIZATION
                              │
                              ▼
                          SILVER
                         Parquet
                              │
                              ▼
                    ANALYTICAL MODELING
                              │
                              ▼
                           GOLD
                         Parquet
                              │
                              ▼
                    ANALYTICAL ENGINE
                          DuckDB
                              │
                              ▼
                      SERVING LAYER
                              │
             ┌────────────────┼────────────────┐
             ▼                ▼                ▼
          REST API        SQL Access       File Export
             │                │                │
             └────────────────┼────────────────┘
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
          Data Portal        BI Tools       Researchers
          TanStack           Power BI       Python
                             Metabase       R
                             Superset       Jupyter
```

---

# 4. Data Architecture

The warehouse follows a four-layer model:

```text
RAW → BRONZE → SILVER → GOLD
```

---

# 5. RAW Layer

Stores original source material.

```text
${STORAGE_ROOT}/
└── raw/
    ├── government/
    ├── statistics/
    ├── regulations/
    ├── news/
    ├── research/
    ├── scraping/
    └── documents/
```

Example:

```text
raw/
└── regulations/
    └── bpk/
        └── 2026/
            └── doc_01234/
                ├── original.pdf
                ├── page.html
                └── metadata.json
```

RAW should be immutable whenever possible.

---

# 6. Bronze Layer

Bronze contains machine-readable extraction of RAW data.

```text
bronze/

documents/
statistics/
web_pages/
api_responses/
regulations/
```

Typical fields:

```text
document_id
source_id
source_type
source_url

raw_text
raw_html

original_filename
raw_path

content_hash

published_at
retrieved_at
processed_at

parser_version
pipeline_version
```

Bronze may contain imperfect or partially normalized information.

---

# 7. Silver Layer

Silver contains standardized and normalized datasets.

```text
silver/

├── documents/
├── regulations/
├── regulation_articles/
├── regulation_relations/
├── news/
├── statistics/
├── indicators/
├── observations/
├── geography/
├── entities/
├── commodities/
├── organizations/
└── classifications/
```

Silver should be the primary source for reusable analytical datasets.

---

# 8. Gold Layer

Gold contains curated, analysis-ready and, where appropriate, pre-aggregated datasets.

```text
gold/

├── economics/
│   ├── inflation/
│   ├── gdp/
│   ├── exchange_rates/
│   ├── interest_rates/
│   ├── employment/
│   └── poverty/
│
├── trade/
│   ├── exports/
│   ├── imports/
│   ├── commodities/
│   └── bilateral_trade/
│
├── minerals/
│   ├── nickel/
│   ├── coal/
│   ├── copper/
│   └── bauxite/
│
├── legal/
│   ├── regulation_timeline/
│   ├── regulation_network/
│   ├── references/
│   └── topics/
│
├── media/
│   ├── media_mentions/
│   ├── topic_trends/
│   └── organization_coverage/
│
└── research/
    ├── indicators/
    ├── entity_statistics/
    └── cross_domain/
```

Gold is not a replacement for Silver.

Silver represents normalized source-level information.

Gold represents datasets optimized for recurring research and consumption.

Example:

```text
Silver

trade_observations
~100,000,000 rows
        ↓
Gold transformation
        ↓
nickel_exports_monthly
~5,000 rows
```

This allows dashboards and external applications to avoid repeatedly scanning the entire source dataset.

---

# 9. Universal Dataset Model

Every published dataset must have a unique identifier.

Example:

```text
dataset_id:
IDN-ECON-INFLATION-MONTHLY

slug:
indonesia-monthly-inflation

name:
Indonesia Monthly Inflation

category:
economics

subcategory:
inflation
```

Minimum metadata:

```text
dataset_id
slug
name
description

category
subcategory

provider
source_id

coverage_start
coverage_end

update_frequency

geographic_coverage
temporal_resolution

license

access_level

storage_format
storage_path

schema_version

created_at
updated_at
last_ingested_at
```

---

# 10. Statistical Data Model

Statistical observations should use a common model wherever practical.

## Indicator

```text
indicator_id
name
canonical_name
description

category
unit
frequency

methodology

source_id
```

Examples:

```text
CPI_INFLATION_YOY
GDP_REAL_GROWTH
USD_IDR
POLICY_RATE
UNEMPLOYMENT_RATE

NICKEL_PRODUCTION
NICKEL_EXPORT_VOLUME
NICKEL_EXPORT_VALUE
```

## Observation

```text
observation_id

indicator_id

period
period_start
period_end

value
unit

geo_id
commodity_id

source_id
dataset_id

release_date
retrieved_at

revision
status
```

This model allows:

```text
indicator
+
time
+
geography
+
dimensions
=
observation
```

---

# 11. Geography Dimension

Geographic identifiers should be normalized independently from source naming.

```text
geo_id
name
official_name

geo_type

parent_geo_id

country_code
province_code
regency_code

bps_code
iso_code

valid_from
valid_to
```

Hierarchy example:

```text
Indonesia
    │
    ├── Jawa Barat
    │      │
    │      ├── Kota Bandung
    │      └── Kabupaten Bandung
    │
    └── Sulawesi Tenggara
```

Historical administrative changes must not silently overwrite previous geographic definitions.

---

# 12. Commodity Dimension

A central commodity registry should normalize terminology.

```text
commodity_id
canonical_name
description

category
subcategory

hs_code
hs_version

unit_default

aliases
```

Example:

```text
commodity_id:
NICKEL_ORE

canonical_name:
Nickel Ore

aliases:
- nickel
- nikel
- bijih nikel
- nickel ore
```

Where required, different commodity concepts must remain distinct:

```text
NICKEL_ORE
NICKEL_MATTE
FERRONICKEL
NPI
STAINLESS_STEEL
```

They should not all be collapsed into `NICKEL`.

---

# 13. Document Model

The warehouse must support unstructured information alongside statistical data.

```text
document_id
document_type

title
subtitle

content
language

author
publisher

published_at
retrieved_at

source_id
source_url

content_hash
raw_path

metadata
```

Document types:

```text
regulation
news
report
research
publication
press_release
court_decision
clipping
web_page
```

---

# 14. Legal Data Model

Regulations require a specialized structure.

## Regulations

```text
regulation_id
document_id

regulation_type
regulation_number
regulation_year

title

issuing_body
jurisdiction

issued_date
effective_date

status

source_id
source_url
```

## Articles

```text
article_id
regulation_id

chapter
section

article_number
paragraph_number

sequence
text
```

## Relations

```text
relation_id

source_regulation_id
target_regulation_id

relation_type

source_article
target_article

confidence
extraction_method
```

Supported relationships may include:

```text
references
implements
amends
revokes
replaces
based_on
related_to
```

AI-derived relationships must be marked as inferred rather than authoritative source metadata.

---

# 15. Entity Model

The platform should maintain a global entity registry.

```text
entity_id
entity_type

canonical_name
normalized_name

aliases

external_ids
metadata
```

Entity types:

```text
PERSON
ORGANIZATION
COMPANY
GOVERNMENT_AGENCY
LOCATION
COMMODITY
INDUSTRY
REGULATION
EVENT
TOPIC
```

Documents can reference entities through:

```text
document_entities
```

with:

```text
document_id
entity_id

mention_text

confidence
extraction_method
```

---

# 16. Source Registry

Every data provider should have a source record.

```text
source_id

name
organization

source_type

base_url
country

license

collection_method

update_frequency

active

created_at
updated_at
```

Example source types:

```text
official_portal
government_api
scraping
manual_upload
news
research_repository
internal
```

---

# 17. Provenance Model

Provenance is mandatory.

Every observation or document should be traceable through:

```text
DATA
 │
 ▼
DATASET
 │
 ▼
SOURCE
 │
 ▼
INGESTION RUN
 │
 ▼
RAW OBJECT
```

Example:

```text
Observation:
OBS-293881

Dataset:
NICKEL-PRODUCTION

Source:
ESDM

Ingestion:
RUN-20260916-001

Original:
raw://esdm/minerals/2026/source.xlsx
```

The portal must expose provenance information where the user's permissions allow it.

---

# 18. Content Hashing and Deduplication

Downloaded objects should receive a cryptographic content hash.

Recommended:

```text
SHA-256
```

Example:

```text
content_hash:
sha256:91c3...
```

Uses:

- duplicate detection
- change detection
- corruption detection
- re-download identification
- cross-source duplicate discovery

Do not rely exclusively on URLs as document identity.

---

# 19. Data Catalog

PostgreSQL should maintain the operational data catalog.

Core catalog entities:

```text
datasets
dataset_versions
sources
schemas
fields

files
ingestion_jobs
pipeline_runs

data_quality_runs

users
organizations
permissions

api_keys
audit_logs
```

Parquet contains analytical data.

PostgreSQL contains information **about the analytical data** and application state.

---

# 20. Data Portal

The primary frontend is a data discovery and documentation portal.

Recommended stack:

```text
TanStack Start
TypeScript
TanStack Query
TanStack Table
Zod
```

Heavy visualization libraries are not required for the core portal.

Small previews may be added later.

---

# 21. Main Navigation

```text
Home

Data Catalog
├── Datasets
├── Indicators
├── Documents
├── Regulations
├── Sources
└── Organizations

Explore
├── Search
├── Topics
├── Geography
├── Commodities
└── Entities

Developers
├── API
├── SQL
├── Downloads
├── Examples
└── Authentication

Documentation
├── Getting Started
├── Methodology
├── Data Dictionary
├── Classification
├── Data Quality
├── Licensing
└── Changelog

Account
├── API Keys
├── Saved Datasets
└── Usage
```

Administrative functions should be separated from public/research-facing navigation.

---

# 22. Dataset Detail Page

A dataset page is one of the most important interfaces.

Example:

```text
Indonesia Nickel Production
────────────────────────────────────────────

Nickel production statistics for Indonesia
and subnational regions.

Provider
Ministry / Agency

Category
Minerals

Coverage
2000–2026

Frequency
Annual

Geographic Coverage
Indonesia / Province

Last Updated
15 September 2026

Format
Parquet
CSV
JSON

────────────────────────────────────────────

DATA DICTIONARY

Field           Type       Description

period          date       Observation period
geo_id          string     Geography identifier
commodity_id    string     Commodity
value           double     Production value
unit            string     Measurement unit

────────────────────────────────────────────

METHODOLOGY

...

────────────────────────────────────────────

SOURCES

...

────────────────────────────────────────────

ACCESS

[API]
[CSV]
[Parquet]

────────────────────────────────────────────

VERSION HISTORY

v1.3   15 Sep 2026
v1.2   01 Aug 2026
v1.1   14 Jun 2026
```

---

# 23. Dataset Preview

The portal may display a limited sample.

Example:

```text
Preview — first 100 rows

Year    Province             Value       Unit

2025    Indonesia            ...         tonnes
2025    Sulawesi Tengah      ...         tonnes
2025    Maluku Utara         ...         tonnes
```

Preview requests must not retrieve entire large datasets.

Default:

```text
100 rows
```

Maximum should be configurable.

---

# 24. Serving Layer

The serving layer is the primary interface between warehouse data and consumers.

```text
                        CONSUMERS

              ┌────────────┼────────────┐
              ▼            ▼            ▼
            Portal       BI Tools       Apps
              │            │            │
              └────────────┼────────────┘
                           ▼
                    SERVING LAYER
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
       REST API        SQL Access        Export
          │                │                │
          └────────────────┼────────────────┘
                           ▼
                      Query Engine
                           ▼
                        DuckDB
                           ▼
                        Parquet
```

---

# 25. REST API

Recommended API namespaces:

```text
/api/v1/catalog
/api/v1/datasets
/api/v1/indicators
/api/v1/observations
/api/v1/documents
/api/v1/regulations
/api/v1/entities
/api/v1/sources
/api/v1/query
/api/v1/export
```

Example:

```text
GET /api/v1/datasets
```

```text
GET /api/v1/datasets/{dataset_id}
```

```text
GET /api/v1/indicators/{indicator_id}/observations
```

Parameters:

```text
geo
start
end
frequency
commodity
limit
offset
```

---

# 26. Analytical Query API

A generic analytical endpoint should prevent the need for hundreds of custom endpoints.

```text
POST /api/v1/query
```

Example request:

```json
{
  "metrics": [
    "NICKEL_PRODUCTION"
  ],
  "dimensions": [
    "year"
  ],
  "filters": {
    "country": "ID"
  },
  "period": {
    "from": "2015",
    "to": "2025"
  }
}
```

The request must be validated against the semantic/catalog layer.

Pipeline:

```text
API Request
    ↓
Authentication
    ↓
Authorization
    ↓
Schema Validation
    ↓
Semantic Resolution
    ↓
Query Builder
    ↓
DuckDB
    ↓
Parquet
    ↓
Result
```

---

# 27. Semantic Layer

Consumers should not need to understand physical Parquet schemas.

Concept:

```text
NICKEL_PRODUCTION
        ↓
Semantic Layer
        ↓
Dataset
        ↓
Field
        ↓
Aggregation
        ↓
Unit
```

Semantic metadata:

```text
metric_id
name
description

dataset_id
field

aggregation

unit
format

allowed_dimensions
allowed_filters
```

Example:

```text
metric_id:
NICKEL_EXPORT_VALUE

dataset:
trade_statistics

field:
trade_value_usd

aggregation:
SUM

unit:
USD

dimensions:
year
month
country
province
destination
```

The same semantic layer should later support AI.

---

# 28. Export Service

Users must be able to export data independently from the visualization layer.

Supported formats:

```text
CSV
Parquet
JSON
```

Example:

```text
POST /api/v1/export
```

Large exports should use asynchronous jobs:

```text
Request
   ↓
Export Job
   ↓
Processing
   ↓
Object Storage
   ↓
Signed Download URL
```

Do not generate extremely large files synchronously through normal HTTP API requests.

---

# 29. SQL Access

Direct SQL access is optional for the initial release.

Initial architecture:

```text
Consumer
   ↓
REST API
   ↓
Query Service
   ↓
DuckDB
```

Future:

```text
Power BI
Metabase
Superset
Tableau
   │
   ▼
Server SQL Engine
   │
   ▼
Parquet / Iceberg
```

A server-oriented analytical engine such as Trino may be introduced if concurrent external SQL access becomes necessary.

DuckDB should remain useful for:

- internal analytics
- transformations
- API queries
- local research
- validation
- development

---

# 30. External Visualization

Visualization is intentionally outside the core warehouse.

Potential consumers:

```text
Power BI
Metabase
Apache Superset
Tableau
Grafana

Jupyter
Python
R

Custom HTML
TanStack applications
Mobile applications
AI agents
```

Therefore:

```text
Warehouse ≠ Dashboard
```

Instead:

```text
Warehouse
    ↓
Serving Layer
    ↓
Any Visualization Tool
```

---

# 31. AI Integration

AI should be treated as another consumer of the serving layer.

```text
User
  ↓
AI Application
  ↓
DeepSeek
  ↓
Query Plan
  ↓
Semantic API
  ↓
DuckDB
  ↓
Parquet
  ↓
Verified Data
  ↓
AI
  ↓
Explanation
```

The LLM should not receive direct unrestricted database access.

---

# 32. AI Query Endpoint

Potential future endpoint:

```text
POST /api/v1/ai/query
```

Request:

```json
{
  "question":
  "Show Indonesian nickel production from 2015 to 2025."
}
```

Response:

```json
{
  "interpretation": {
    "metric": "NICKEL_PRODUCTION",
    "geography": "ID",
    "period": {
      "from": 2015,
      "to": 2025
    }
  },

  "data": [],

  "suggested_visualization": {
    "type": "line",
    "x": "year",
    "y": "value"
  },

  "sources": []
}
```

The visualization recommendation is metadata only.

The consuming application decides how it should be rendered.

---

# 33. Search

Search should support both structured and document-oriented discovery.

Initial:

```text
Dataset search
Indicator search
Document search
Regulation search
Source search
Entity search
```

Search fields:

```text
title
description
keywords
organization
topic
geography
commodity
document content
```

Future:

```text
semantic search
hybrid search
entity search
cross-dataset search
```

---

# 34. API Authentication

Support:

```text
Public
Authenticated
Restricted
Internal
```

Potential authentication:

```text
API Key
User Session
Service Account
```

Example:

```text
Authorization:
Bearer <API_KEY>
```

API keys should support:

```text
name
owner
organization

permissions
dataset_scope

created_at
expires_at

last_used_at
revoked_at
```

Raw API keys must not be stored in plaintext.

---

# 35. Authorization

Recommended model:

```text
RBAC
+
dataset-level policy
```

Example roles:

```text
Public
Researcher
Data Analyst
Data Steward
Data Engineer
Administrator
```

Potential permissions:

```text
dataset.read
dataset.download

document.read

api.query

source.manage

pipeline.run

dataset.publish

catalog.manage
```

---

# 36. Dataset Access Levels

Every dataset should have:

```text
PUBLIC
REGISTERED
RESTRICTED
INTERNAL
```

Example:

```text
BPS public statistics
→ PUBLIC

Licensed research dataset
→ RESTRICTED

Internal organization data
→ INTERNAL
```

The same access policy must apply to:

```text
Portal
API
Export
AI
SQL
```

AI must never bypass dataset permissions.

---

# 37. Ingestion System

An ingestion job should represent a reproducible collection process.

```text
Source
   ↓
Connector
   ↓
Fetch
   ↓
RAW
   ↓
Parse
   ↓
Validate
   ↓
Transform
   ↓
Publish
```

Supported connectors should eventually include:

```text
HTTP
REST API
HTML scraper
RSS
CSV
Excel
PDF
JSON
XML
Manual upload
S3-compatible storage
```

---

# 38. Pipeline Definition

Each dataset should define its pipeline.

Example:

```yaml
dataset: nickel-production

source:
  type: api

schedule:
  frequency: monthly

pipeline:
  - fetch
  - validate
  - normalize
  - deduplicate
  - write_silver
  - aggregate
  - write_gold
  - quality_check
  - publish
```

Pipeline definitions should be version-controlled.

---

# 39. Ingestion Runs

Every execution must generate a run record.

```text
run_id

pipeline_id
dataset_id

started_at
finished_at

records_found
records_downloaded
records_created
records_updated
records_failed

bytes_downloaded

pipeline_version

status
error_message
```

Status:

```text
QUEUED
RUNNING
SUCCESS
PARTIAL
FAILED
CANCELLED
```

---

# 40. Dataset Versioning

Datasets must support version history.

Example:

```text
nickel-production

v1.0
2026-01-01

v1.1
2026-03-01

v1.2
2026-09-15
```

Changes should document:

```text
new observations
corrections
source revisions
schema changes
methodology changes
pipeline changes
```

---

# 41. Schema Evolution

Every dataset schema requires a version.

```text
schema_version:
1.2
```

Safe changes may include:

```text
add nullable column
add metadata
extend classification
```

Breaking changes require a new major schema version or controlled migration.

Consumers should be able to inspect schema history.

---

# 42. Data Quality Framework

Quality checks should include:

```text
Completeness
Uniqueness
Validity
Consistency
Timeliness
Referential Integrity
Schema Conformance
```

Example:

```text
Nickel Production

Rows                18,492
Missing values       0.21%
Duplicate keys       0
Invalid geography    3
Freshness            1 day
Schema               PASS
```

Quality results must be stored historically.

---

# 43. Data Quality Rules

Example:

```text
Rule:
observation.value IS NOT NULL

Rule:
observation.indicator_id exists

Rule:
geo_id exists in geography

Rule:
period_start <= period_end

Rule:
content_hash is unique within source

Rule:
value is numeric
```

Dataset-specific rules should also be supported.

---

# 44. Data Lineage

The platform should maintain lineage between transformations.

Example:

```text
ESDM XLSX
   ↓
RAW-92811
   ↓
bronze_nickel
   ↓
silver_commodity_observations
   ↓
gold_nickel_yearly
   ↓
API Query
```

Users with appropriate access should be able to answer:

> Where did this number come from?

---

# 45. Storage Strategy

## 45.1 Storage Classes

The platform distinguishes four storage classes with different durability, size, and access characteristics.

```text
CLASS            CONTENT                        GROWTH     DURABILITY
─────────────────────────────────────────────────────────────────────
Code             Application source, pipeline   small      git
                 definitions, schemas, docs

Data Lake        RAW / Bronze / Silver / Gold   large      backup + versioning
                 Parquet and original documents

Operational      PostgreSQL catalog, metadata,  medium     backup + PITR
                 audit logs, pipeline state

Ephemeral        Extraction scratch, DuckDB     transient  disposable
                 spill, compaction staging,
                 export build area
```

Recommended placement:

```text
RAW
→ NAS / Object Storage

Parquet
→ Object Storage / NAS

Catalog
→ PostgreSQL

Scratch / Spill
→ Local fast disk (SSD)
```

## 45.2 Logical Storage Hierarchy

All data-lake content lives under a single logical root, referred to as `STORAGE_ROOT`.

```text
${STORAGE_ROOT}/

├── raw/          original source material, immutable
├── bronze/       machine-readable extraction
├── silver/       normalized datasets
├── gold/         curated analytical datasets
├── exports/                  generated download artifacts
└── temporary/                staging, compaction, spill
```

The logical hierarchy should remain independent from physical storage provider.

This allows movement between:

```text
NAS
MinIO
Cloudflare R2
S3-compatible storage
AWS S3
```

without redesigning dataset semantics.

## 45.3 Physical Location Rule

> Code lives in the project.
> Data lives outside the project.

The repository must never become the data lake. Only fixtures and small samples may live in-tree.

```text
IN PROJECT (git-tracked)
  pipeline definitions
  schema definitions
  transformation SQL
  dataset metadata / catalog seeds
  documentation
  test fixtures            (< 1 MB per file)
  sample datasets          (< 10 MB total)

IN PROJECT (git-ignored)
  ./.data/                 local dev storage root
  ./.cache/                DuckDB cache, temp artifacts
  ./.env                   storage credentials

OUTSIDE PROJECT
  RAW originals            PDF / HTML / XLSX / JSON
  Bronze / Silver / Gold   Parquet
  Exports
  Database volumes
  Backups
```

## 45.4 Storage Profiles

`STORAGE_ROOT` resolves differently per environment. Nothing else in the codebase changes.

```text
PROFILE        STORAGE_ROOT                          BACKEND
──────────────────────────────────────────────────────────────────
local          ./.data                               local filesystem
shared-dev     /Volumes/research/terusan             NAS (mounted)
staging        s3://terusan-staging                  MinIO / R2
production     s3://terusan-warehouse                S3-compatible
```

Selection is driven by configuration only:

```text
STORAGE_BACKEND     = local | nas | s3
STORAGE_ROOT        = ./.data
                    | /Volumes/research/terusan
                    | s3://terusan-warehouse

S3_ENDPOINT         = https://<account>.r2.cloudflarestorage.com
S3_REGION           = auto
S3_ACCESS_KEY_ID    = ...
S3_SECRET_ACCESS_KEY= ...

SCRATCH_DIR         = ./.cache            (local SSD, never NAS)
EXPORT_DIR          = ${STORAGE_ROOT}/exports
```

All path construction must go through a single storage-path resolver. Direct filesystem string concatenation to physical paths is prohibited outside that module.

```text
resolve("silver", "observations", "year=2026")
  local  → ./.data/silver/observations/year=2026
  nas    → /Volumes/research/terusan/silver/observations/year=2026
  s3     → s3://terusan-warehouse/silver/observations/year=2026
```

DuckDB reads the same logical dataset in either case:

```text
NAS     read_parquet('/Volumes/research/terusan/silver/observations/**/*.parquet')
S3      read_parquet('s3://terusan-warehouse/silver/observations/**/*.parquet')
```

## 45.5 In-Project Layout

```text
terusan/
├── src/                      application + serving code
├── pipelines/                ingestion pipeline definitions
├── schemas/                  dataset schema definitions
├── sql/                      transformation SQL
├── migrations/               PostgreSQL migrations
├── docs/
├── fixtures/                 small committed test inputs
│   ├── documents/
│   └── statistics/
├── .data/         [ignored]  local dev storage root
│   ├── raw/
│   ├── bronze/
│   ├── silver/
│   ├── gold/
│   ├── exports/
│   └── temporary/
├── .cache/        [ignored]  DuckDB spill, scratch
└── .env           [ignored]
```

Required `.gitignore` entries:

```text
.data/
.cache/
.env
*.parquet
*.duckdb
*.duckdb.wal
```

The `*.parquet` rule is intentionally aggressive. Committed sample Parquet must be added with an explicit negation, for example `!fixtures/**/*.parquet`.

## 45.6 NAS Layout

When the data lake lives on NAS, the share holds the storage root plus operational directories that are not part of the logical hierarchy.

```text
/Volumes/research/terusan/          ← STORAGE_ROOT
├── raw/
│   ├── government/
│   ├── statistics/
│   ├── regulations/
│   ├── news/
│   ├── research/
│   ├── scraping/
│   └── documents/
├── bronze/
├── silver/
├── gold/
├── exports/
└── temporary/

/Volumes/research/_ops/             ← not part of STORAGE_ROOT
├── backups/
│   ├── postgres/
│   └── lake-snapshots/
├── incoming/                       manual drop zone, pre-ingestion
└── quarantine/                     failed or legally blocked material
```

Operational requirements:

```text
Mount              read-write for ingestion workers
                   read-only for query and serving nodes

RAW directories    read-only after write (immutability)

Path stability     mount point must be stable across reboots;
                   scripts must not depend on user-session automounts

Scratch            never on NAS — latency and lock behaviour
                   degrade DuckDB spill and compaction

Filenames          ASCII-safe, no colons; source titles must be
                   slugified, never used raw as a path segment
```

NAS is appropriate for a single-site deployment with a small number of query nodes. It is not appropriate as the primary read path for high-concurrency serving.

## 45.7 Object Storage Layout

```text
s3://terusan-warehouse/
├── raw/
├── bronze/
├── silver/
├── gold/
├── exports/
└── temporary/

s3://terusan-backups/
├── postgres/
└── lake-snapshots/
```

Bucket-level settings:

```text
Versioning         enabled on the warehouse bucket
Lifecycle          temporary/  expire after 7 days
                   exports/    expire after 30 days
                   raw/        never expire
Access             ingestion role: write to raw/ bronze/
                   transform role: read all, write silver/ gold/
                   serving role:   read-only
```

## 45.8 Placement by Layer

```text
LAYER       LOCATION                  MUTABILITY   BACKUP
────────────────────────────────────────────────────────────
raw         NAS / Object Storage      immutable    yes, priority 1
bronze      NAS / Object Storage      rebuildable  optional
silver      NAS / Object Storage      rebuildable  yes
gold        NAS / Object Storage      rebuildable  yes
exports     Object Storage / NAS      disposable   no
temporary   local SSD                 disposable   no
catalog     PostgreSQL volume         mutable      yes, priority 1
cache       Redis / local             disposable   no
```

Bronze, Silver, and Gold are reproducible from RAW plus pipeline code. RAW and the PostgreSQL catalog are not reproducible and carry the highest backup priority.

## 45.9 Local Development

Developers work against a local storage root with a reduced dataset.

```text
Option A — subset sync
  rsync a dated subset of raw/ and silver/ from NAS to ./.data

Option B — read-through
  STORAGE_ROOT points at a read-only NAS mount;
  writes are redirected to ./.data via an overlay prefix

Option C — fixtures only
  ./fixtures + pipeline run produces a small local lake
```

Development must never write to the shared NAS or production bucket. Write access is granted by profile, and the `local` profile must be the default when configuration is absent.

## 45.10 Capacity Planning

Initial sizing assumptions to be revised against real ingestion volume:

```text
raw/         dominant consumer; PDFs and HTML snapshots
             plan for 10-100x the size of derived Parquet

bronze/      raw text extraction; compresses well

silver/      normalized, columnar; typically < 10% of raw

gold/        aggregated; small relative to silver

exports/     bounded by lifecycle policy, not by growth
```

Storage alerts should fire on free-space thresholds for the NAS share and on bucket growth rate for object storage.

---

# 46. Parquet Partitioning

Partition only on fields that provide meaningful pruning.

Example:

```text
statistics/
└── indicator_group=economics/
    └── year=2026/
        ├── part-00001.parquet
        └── part-00002.parquet
```

Potential partition dimensions:

```text
year
month
dataset
document_type
source
```

Avoid:

```text
observation_id
document_id
```

as primary partition dimensions.

---

# 47. Small File Management

Avoid:

```text
1 observation = 1 Parquet file
```

and generally avoid:

```text
1 document = 1 Parquet file
```

for analytical layers.

Pipeline jobs should periodically compact small files.

Target file sizes should be configurable, with a practical initial target in the hundreds-of-megabytes range where workloads support it.

---

# 48. Application Database

PostgreSQL should contain application and operational state.

```text
users
organizations

datasets
sources
schemas

pipeline_definitions
pipeline_runs

api_keys

saved_datasets

permissions

audit_logs
```

Do not duplicate the complete analytical warehouse into PostgreSQL without a specific serving requirement.

---

# 49. Caching

API responses for expensive or frequently requested analytical queries may be cached.

Architecture:

```text
Consumer
   ↓
API
   ↓
Cache
   │
 HIT ─────────────→ Response
   │
 MISS
   ↓
DuckDB
   ↓
Parquet
```

Redis can be introduced if required.

Cache keys should be derived from normalized query parameters and relevant dataset versions.

A dataset update must invalidate or version-out stale cache entries.

---

# 50. Observability

Monitor:

```text
API latency
API errors

DuckDB query duration
bytes scanned

pipeline duration
pipeline failures

storage usage

dataset freshness

export jobs

source failures

data quality failures
```

Recommended operational stack can include:

```text
Prometheus
Grafana
Loki
```

---

# 51. Audit Logs

Sensitive actions must be auditable.

Examples:

```text
dataset downloaded
restricted dataset accessed
API key created
API key revoked

dataset published
dataset modified

pipeline executed

source modified

permissions changed
```

Audit record:

```text
audit_id

actor_id
organization_id

action
resource_type
resource_id

timestamp

request_id
metadata
```

---

# 52. Developer Experience

The platform should provide developer documentation.

Example:

```text
Developers

Getting Started

Authentication

Datasets API

Indicators API

Query API

Export API

Pagination

Errors

Rate Limits

Examples
├── JavaScript
├── Python
├── R
└── cURL
```

---

# 53. API Response Standard

Example:

```json
{
  "data": [],
  "meta": {
    "dataset_id": "NICKEL-PRODUCTION",
    "dataset_version": "1.3",
    "rows": 100,
    "limit": 100,
    "offset": 0
  }
}
```

Errors:

```json
{
  "error": {
    "code": "DATASET_NOT_FOUND",
    "message": "Dataset was not found.",
    "request_id": "REQ-928182"
  }
}
```

---

# 54. API Rate Limiting

Rate limits should be configurable by consumer type.

Example conceptual tiers:

```text
Public
Authenticated
Internal
Service Account
```

Do not hard-code commercial plans into the warehouse architecture.

Usage should be measured:

```text
requests
rows returned
bytes returned
exports
query duration
```

This leaves open the possibility of offering a public or commercial Data API later.

---

# 55. Bulk Data Access

Large datasets should be distributed through file downloads rather than huge JSON responses.

Example:

```text
Dataset: Indonesian Local Regulations

Rows:
260,000+

Available:

[Download Parquet]
[Download CSV]
```

Large downloads may be split:

```text
year
province
document_type
```

or delivered as complete snapshots.

---

# 56. Documentation Requirements

Every published dataset should ideally provide:

```text
Description

Source

Methodology

Coverage

Update Frequency

Data Dictionary

Units

Known Limitations

License

Citation Guidance

Version History

Changelog
```

This is one of the core functions of the frontend.

---

# 57. Citation Support

Dataset pages should provide recommended citation metadata.

Example:

```text
Dataset:
Indonesia Nickel Production

Publisher:
...

Version:
1.3

Accessed:
...

Persistent Dataset ID:
NICKEL-PRODUCTION
```

A future release may support DOI or another persistent identifier mechanism where appropriate.

---

# 58. Dataset Lifecycle

Dataset status:

```text
DRAFT
      ↓
VALIDATING
      ↓
PUBLISHED
      ↓
UPDATED
      ↓
DEPRECATED
      ↓
ARCHIVED
```

Deprecated datasets should remain discoverable where appropriate, with a pointer to their replacement.

---

# 59. Administration

Administrative portal:

```text
Admin

Data
├── Datasets
├── Sources
├── Schemas
├── Indicators
├── Classifications
└── Entities

Pipelines
├── Definitions
├── Runs
├── Schedules
└── Errors

Quality
├── Rules
├── Results
└── Issues

Access
├── Users
├── Organizations
├── Roles
└── API Keys

System
├── Storage
├── Logs
├── Audit
└── Settings
```

---

# 60. MVP

The first production version should focus on infrastructure rather than AI or visualization.

## MVP 1 — Data Foundation

Implement:

```text
RAW storage
Parquet storage

Bronze
Silver
Gold

PostgreSQL catalog

Dataset registry
Source registry

Basic ingestion
Pipeline logging
Content hashing
```

## MVP 2 — Data Portal

Implement:

```text
TanStack Start

Dataset catalog
Dataset detail
Data dictionary
Sources
Indicators

Dataset preview

Documentation
Search
```

## MVP 3 — Serving Layer

Implement:

```text
REST API

Dataset API
Indicator API
Observation API
Query API

CSV export
Parquet export

API keys
RBAC
Rate limiting
```

## MVP 4 — Data Governance

Implement:

```text
Dataset versioning
Schema versioning
Data quality
Lineage
Audit logs
Dataset lifecycle
```

---

# 61. Phase 2

After the warehouse foundation is stable:

```text
Advanced search

Regulation corpus
Regulation article extraction

Entity extraction
Entity resolution

Full-text search

Semantic layer

Geographic datasets

Commodity classification
```

---

# 62. Phase 3

Introduce advanced consumers:

```text
Power BI
Metabase
Superset

Jupyter integration

Python examples
R examples

SQL access
```

If concurrent SQL workloads justify it:

```text
Parquet
   ↓
Iceberg
   ↓
Trino
```

This migration should not require rewriting the Data Portal.

---

# 63. Phase 4 — AI

AI is introduced after data discovery, provenance and semantic definitions are sufficiently mature.

Features:

```text
Natural-language dataset discovery

Natural-language analytical queries

Automatic metric resolution

Automatic query plans

Dataset recommendation

Research assistant

Document + statistical data retrieval

Suggested visualization metadata
```

Architecture:

```text
DeepSeek
    ↓
Semantic Layer
    ↓
Validated Query Plan
    ↓
Serving Layer
    ↓
DuckDB
    ↓
Parquet
```

---

# 64. Future Knowledge Layer

Long-term capabilities:

```text
Knowledge Graph

Legal Citation Graph

Entity Network

Policy Timeline

Contradiction Detection

Cross-document References

Economic Event Detection

Policy-to-Indicator Linking

Research Corpus RAG
```

Example:

```text
Regulation
     │
     ▼
Export Restriction
     │
     ├───────────────┐
     ▼               ▼
Commodity          Company
Nickel             Entity
     │
     ▼
Economic Indicator
     │
     ├── Export Value
     ├── Production
     └── Investment
```

This should be treated as an additional intelligence layer, not a replacement for the warehouse.

---

# 65. Non-Goals

The core platform is **not intended to become**:

```text
A full Power BI replacement

A Tableau replacement

A spreadsheet application

A chart-building application

An unrestricted SQL playground

An LLM-generated database

A document management system

A transactional ERP database
```

Its primary responsibility is:

> collect, preserve, normalize, catalog, govern, query and serve research data reliably.

---

# 66. Recommended Initial Stack

```text
DATA PORTAL

TanStack Start
TypeScript
TanStack Query
TanStack Table
Zod
```

```text
SERVING LAYER

Go
REST API
OpenAPI
```

```text
APPLICATION / CATALOG

PostgreSQL
```

```text
ANALYTICAL ENGINE

DuckDB
```

```text
DATA STORAGE

Apache Parquet
+
NAS / S3-compatible Object Storage
```

Optional:

```text
Redis
```

for caching and asynchronous job coordination.

Search infrastructure can be introduced separately when the document corpus requires it.

---

# 67. Final System Boundary

The final architecture should maintain clear boundaries:

```text
┌──────────────────────────────────────────────┐
│                 DATA PORTAL                  │
│                                              │
│ Catalog • Search • Docs • Download • API    │
│                                              │
│                TanStack Start                │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│                SERVING LAYER                 │
│                                              │
│ REST API • Query API • Export • Auth         │
│ Semantic Layer • Access Control              │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│               ANALYTICAL LAYER               │
│                                              │
│                    DuckDB                    │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│                 DATA LAYER                   │
│                                              │
│ RAW → Bronze → Silver → Gold                 │
│                                              │
│               Apache Parquet                 │
└──────────────────────────────────────────────┘

             ▲                    ▲
             │                    │
             │                    │

       INGESTION             CONSUMERS

       Scrapers              Power BI
       APIs                  Metabase
       Files                 Superset
       Portals               Python/R
       Crawlers              Jupyter
                             AI
                             Custom Apps
```

---

# 68. Key Architectural Decision

The platform should be designed around the following principle:

> **Data should outlive the application that currently consumes it.**

TanStack is replaceable.

DuckDB is replaceable.

DeepSeek is replaceable.

Power BI or Metabase is replaceable.

The durable assets are:

```text
RAW SOURCE
+
NORMALIZED DATA
+
PARQUET
+
METADATA
+
PROVENANCE
+
SEMANTIC DEFINITIONS
+
VERSION HISTORY
```

This ensures the warehouse remains useful even as frontend frameworks, AI models, visualization platforms, and analytical engines change over time.