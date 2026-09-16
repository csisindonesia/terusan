# Terusan

Research data warehouse, data catalog and data serving platform.

Heterogeneous research data — government portals, statistical agencies, APIs,
scraped pages, regulations, news, PDFs and spreadsheets — is collected,
preserved, normalized, cataloged and served. Visualization happens in external
tools (Power BI, Superset, Metabase, Jupyter, R); this platform serves the data
they read.

Full specification: [program.md](program.md).

## Architecture

```text
Sources → Ingestion → RAW → Bronze → Silver → Gold → DuckDB → Serving → Consumers
```

| Component | Location | Stack |
|---|---|---|
| Data portal | [apps/portal/](apps/portal/) | TanStack Start, React 19, TypeScript |
| Serving layer | [services/api/](services/api/) | Go 1.26, net/http |
| Ingestion pipelines | [pipelines/](pipelines/) | Python 3.12+, uv |
| Application catalog | [db/migrations/](db/migrations/) | PostgreSQL 18 |
| Analytical engine | — | DuckDB over Parquet |

## Storage

Code lives in the repository. Data does not.

The data lake sits under a single logical root, `STORAGE_ROOT`, holding
`raw/ bronze/ silver/ gold/ exports/ temporary/`. That root resolves to a
different physical backend per profile, and nothing else in the codebase
changes:

| Profile | `STORAGE_ROOT` | Backend |
|---|---|---|
| `local` | `./.data` | local filesystem |
| `shared-dev` | `/Volumes/research/terusan` | NAS |
| `staging` | `s3://terusan-staging` | MinIO / R2 |
| `production` | `s3://terusan-warehouse` | S3-compatible |

Two rules the code enforces rather than documents:

- **All physical paths come from the storage resolver.** Building them by hand
  is what makes a lake impossible to move. Go:
  [`internal/storage`](services/api/internal/storage/resolver.go). Python:
  [`terusan_pipelines.storage`](pipelines/src/terusan_pipelines/storage/resolver.py).
  Both run the same fixture, [`fixtures/storage/contract.json`](fixtures/storage/contract.json),
  because the pipelines write datasets the API has to read back.
- **Absent configuration resolves to `local`.** A missing `.env` must not reach
  a shared backend, and non-local profiles refuse writes until
  `STORAGE_ALLOW_SHARED_WRITES=true` is set explicitly.

See program.md §45 for NAS layout, bucket lifecycle, backup priority and
capacity planning.

## Adding a source

Scrapers, API pullers and feed readers all live in
[pipelines/src/terusan_pipelines/sources/](pipelines/src/terusan_pipelines/sources/),
one package per source. A source yields artifacts; the runner handles hashing,
deduplication, RAW paths, provenance, per-host rate limiting and concurrency.
Existing standalone scripts run unmodified through the legacy adapter.

```bash
terusan sources list
terusan sources run bps-inflation --dry-run --limit 5
terusan sources run --workers 8
```

See [docs/adding-a-source.md](docs/adding-a-source.md).

## The pipeline

```bash
make ingest     # sources  → RAW      original bytes, immutable, hashed
make extract    # RAW      → Bronze   machine-readable rows, provenance attached
make compact    # merge small Parquet files (program.md §47)
```

Both stages are idempotent. Landing is content-addressed, so re-fetching
unchanged material writes nothing; extraction skips documents already in Bronze
at the current parser version. A refresh over a stable archive should move no
bytes — if it does, something upstream changed.

Query any layer through DuckDB, against whichever backend holds the lake:

```bash
terusan warehouse query "SELECT source_id, count(*) FROM read_parquet('...') GROUP BY 1"
```

## Setup

Requires Go 1.26+, Python 3.12+ with [uv](https://docs.astral.sh/uv/),
Node 22+ with corepack, and PostgreSQL 18.

```bash
cp .env.example .env
make setup          # install toolchains, create ./.data layers
make db-create
make db-migrate
```

## Running

```bash
make dev-api        # serving layer on :8080
make dev-portal     # data portal on :3000
```

```bash
make test           # Go + Python suites
make lint           # gofmt, go vet, ruff, tsc
make build          # every deployable artifact
```

`make help` lists every target.

## Repository layout

```text
apps/portal/        data portal (TanStack Start)
services/api/       serving layer (Go)
pipelines/          ingestion, extraction, normalization (Python)
packages/           shared TypeScript contracts
db/migrations/      PostgreSQL catalog schema
sql/                DuckDB transformations, by layer
schemas/            dataset schema definitions
fixtures/           small committed test inputs
infra/              deployment configuration
docs/               additional documentation
.data/    [ignored] local storage root
.cache/   [ignored] DuckDB spill and scratch
```

## Status

Early scaffold. Working end to end: source acquisition into RAW with
provenance and deduplication, extraction into Bronze Parquet, partitioning and
compaction, and DuckDB queries across all three storage backends. Also present:
the PostgreSQL catalog schema, an API skeleton with health and readiness, and a
portal shell.

Not yet built: normalization into Silver, curation into Gold, writing pipeline
runs to the catalog, the REST and SQL surfaces, scheduling, search, and
authentication.

Roadmap in program.md §60–63.
