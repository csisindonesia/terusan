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
make catalog-sync   # source registry → PostgreSQL
make ingest         # sources  → RAW      original bytes, immutable, hashed
make extract        # RAW      → Bronze   machine-readable rows, provenance attached
#                     Bronze   → Silver   typed values, bounded periods, resolved dimensions
make compact        # merge small Parquet files (program.md §47)
make runs           # what ran, when, and what it produced
```

Both stages are idempotent. Landing is content-addressed, so re-fetching
unchanged material writes nothing; extraction skips documents already in Bronze
at the current parser version. A refresh over a stable archive should move no
bytes — if it does, something upstream changed.

Every run is recorded in `pipeline_runs` — opened before the work starts, so a
process killed mid-run still leaves a trace. `terusan catalog stale` surfaces
runs that died without closing their row, which is how an ingestion that
stopped three weeks ago gets noticed.

The catalog is optional. Without `DATABASE_URL` the pipeline runs and records
nothing: the lake is the system of record for data, the catalog records what
happened to it. Scheduled runs should pass `--require-catalog`, where losing
history silently is worse than failing loudly.

## Silver

Bronze keeps everything as text so a value cannot change type between
partitions. Silver is where that is decided, once, with the whole column in
view — and where the domain judgement lives:

```bash
terusan silver check "Triwulan I 2026"    # how would this be read?
terusan silver check "1.234,56"

terusan silver normalize NICKEL_PRODUCTION \
  --dataset produksi --value-columns "2024,2025,2026" \
  --geo-column provinsi --unit ton --number-format id \
  --exclude "provinsi=Jumlah"
```

Three things it refuses to do quietly:

- **Guess a number.** `1.234` is one thousand two hundred, or one point two
  three four, and reading it wrong is off by a factor of a thousand while
  looking entirely plausible. Where both separators appear the data settles it;
  where only one does, `--number-format id|en` settles it and `auto` marks the
  value as assumed so it can be excluded.
- **Guess a place.** An unresolved name stays unresolved and keeps its raw
  text. A figure filed under the wrong province is worse than one filed under
  none, because the second is visible.
- **Flatten a gap.** `-`, `x` and `...` mean missing, suppressed and not-yet —
  different facts, and treating any of them as zero is a fabrication.

Values are `decimal128`, not float: published statistics are decimal
quantities, and summing a million float-parsed figures drifts in a way nobody
can explain to whoever defends the total.

The column mapping is declared, not inferred. A column headed `2026` is a
period in a wide table and a value in a long one, and nothing in the data says
which.

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
an API skeleton with health and readiness, and a portal shell.

Also working: the PostgreSQL catalog is wired in — source registry sync,
pipeline run history, dataset registration and versioning.

Also working: normalization into Silver — typed observations with bounded
periods, resolved geography and commodity dimensions, and explicit handling of
ambiguous or absent values.

Not yet built: curation into Gold, the REST and SQL surfaces, scheduling,
search, and authentication.

Roadmap in program.md §60–63.
