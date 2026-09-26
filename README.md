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
| Data portal | [apps/portal/](apps/portal/) | TanStack Start, React 19, shadcn/ui, TanStack Table |
| Serving layer | [services/api/](services/api/) | Go 1.26, net/http, DuckDB |
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
terusan sources run worldbank-gdp --dry-run --limit 1
terusan sources run --workers 8
```

Fetching goes through a shared client that retries transient failures — 408,
429, 5xx, timeouts — with jittered backoff, and never retries a 404 or a 403,
which would fail identically next time. Landing checks magic bytes: an HTML
error page served with a 200 and named `TABEL1_1.xls` is refused before it
reaches RAW, because RAW is permanent.

Three sources are ported from an earlier warehouse:

| Source | Shape |
|---|---|
| `worldbank-gdp` | Paginated JSON API, one artifact per page |
| `bi-seki` | HTML index fanning out to ~108 legacy `.xls` tables |
| `bi-sdds-real-sector` | One HTML page |

SEKI shows the pattern for a flaky fan-out source: per-table failures are
collected rather than raised, so one dead link does not discard the other 107 —
but the run fails below a 90% success ratio, because a site-wide outage looks
exactly like one flaky table, one table at a time, and a partial month must not
masquerade as a complete one.

Most portals are one of three shapes — an API, an index of files, a page that
is itself the document — so those are engines in
[sources/portals.py](pipelines/src/terusan_pipelines/sources/portals.py) and a
source declares only its own URLs. The fifty Indonesian portals surveyed in
`indonesia_dataset_sources.xlsx` are all registered against one: twenty-six
collect today, and the twenty-four that cannot each say what would open them —
an account, an API key, a subscription, an hour of reconnaissance. See
[docs/indonesia-sources.md](docs/indonesia-sources.md).

See [docs/adding-a-source.md](docs/adding-a-source.md).

## News monitoring

One source makes its own figures rather than republishing somebody else's:
sixty-nine Indonesian newspapers are read every day, and what they report is
coded against the controlled vocabulary the VEWS project's human coders use.
The corpus, the coded incidents and the counts are three separate collections,
because they answer different questions — and machine-coded rows stay in their
own collection, unverified, so they can never be cited as VEWS.

See [docs/news-monitoring.md](docs/news-monitoring.md).

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

### Daily

```bash
make daily              # fetch, extract and normalize the daily sources, now
make schedule-install   # run that at 05:00 every day, through launchd
make schedule-status    # loaded? last exit code? tail of the last log
make schedule-uninstall # stop it
```

`scripts/daily.sh` fetches, extracts and normalizes: a figure that reaches
Bronze and stops there is invisible, because the catalogue the portal and the
API read is derived from Silver. It names the sources that declare a daily
schedule rather than running every scheduled source — a monthly release does not
want fetching thirty times a month, which is the point of a cron per registry
record. It holds a lock in `.cache/`, so a laptop waking into a missed run
cannot fetch twice at once, and logs each day to `.cache/logs/daily-<date>.log`.

launchd rather than cron: cron skips a run on a sleeping machine, launchd fires
it on wake. Set `REQUIRE_CATALOG=1` once PostgreSQL is running, so a scheduled
run that cannot record its history fails loudly instead of quietly.

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

  Names resolve against committed reference data in [reference/](reference/):
  217 countries, 78 World Bank aggregates, and Indonesia's 38 provinces with
  their BPS codes and the aliases sources actually publish — `Jabar`,
  `West Java`, `32`, `Prov. Jawa Barat` all reach `ID-32`. Provinces created in
  2022 carry the date of the law that created them, so a 2015 figure cannot be
  filed under an area that did not exist when it was collected.

  ```bash
  terusan silver resolve Sultra        # what does this name reach, and how?
  terusan silver dimensions            # publish the dimensions into Silver
  ```
- **Flatten a gap.** `-`, `x` and `...` mean missing, suppressed and not-yet —
  different facts, and treating any of them as zero is a fabrication.

Values are `decimal128`, not float: published statistics are decimal
quantities, and summing a million float-parsed figures drifts in a way nobody
can explain to whoever defends the total.

The column mapping is declared, not inferred. A column headed `2026` is a
period in a wide table and a value in a long one, and nothing in the data says
which.

## What the figures were read from

Every artifact a scraper lands carries a provenance sidecar, and every
observation carries the `document_id` of the file it came out of. `terusan
silver documents` turns those sidecars into a catalogue: what was collected,
who published it, what licence it carries, and how many series and figures rest
on it — counted from the observations themselves, so the catalogue cannot claim
a link the figures do not make.

Documents only: the PDFs, Word files and papers somebody would open and read. A
scraper lands far more — spreadsheets, CSVs, the pages crawled to find a series
— and those stay in RAW with their provenance travelling on the observation row
itself (`document_id`, `content_hash`, `source_url`, `raw_path`) rather than
through a table whose job is to help a reader find something to read.

```bash
terusan silver documents      # rebuild it; cheap, and run after every scrape
```

The original is kept, not just linked. An agency reorganises its site and the
citation breaks; the preserved copy does not, and `GET /v1/documents/{id}/file`
hands it back with the licence beside it (program.md §2.1).

A PDF reads in the browser rather than only downloading. `/documents/{id}`
describes the document and `/documents/{id}/preview` is the reader — its own
URL, so the link somebody is sent opens the handbook rather than a page about
it. The frame points at `?inline=1`, which the API grants for PDFs alone and
serves sandboxed. Anything else is bytes from a third-party site and downloads
as an attachment, because a landed HTML page rendered inline in the API's
origin would run whatever script it arrived with.

Query any layer through DuckDB, against whichever backend holds the lake:

```bash
terusan warehouse query "SELECT source_id, count(*) FROM read_parquet('...') GROUP BY 1"
```

## The portal and the API

```bash
make dev        # API on :8080, portal on :3000, Ctrl-C stops both
                # both reload on change: vite HMR for the portal,
                # a rebuild-and-restart for the API on any .go edit
```

| Endpoint | What it answers |
|---|---|
| `GET /v1/datasets` | collections as their publishers issue them |
| `GET /v1/datasets/{id}` | one collection, and the series inside it |
| `GET /v1/storage` | what is physically in the lake |
| `GET /v1/indicators` | each series, its coverage, how many places |
| `GET /v1/observations` | figures, filtered by indicator, place, place type, commodity, status, year, period |
| `GET /v1/observations/facets` | what those filters can offer for a series, counted |
| `GET /v1/observations/series` | the same figures as a chart needs them — a line per member, bucketed |
| `GET /v1/geography` | the geography dimension |
| `GET /v1/commodities` | the commodity dimension, read off the figures themselves |
| `GET /v1/sources` | the source registry — schedule, licence, rate limit |
| `GET /v1/documents` | the publications collected — handbooks, reports, papers |
| `GET /v1/documents/{id}` | one document, with its licence and its publisher |
| `GET /v1/documents/{id}/file` | the preserved original; `?inline=1` to read a PDF |
| `GET /v1/documents/{id}/file/{name}` | the same bytes, named, so a viewer can title it |
| `GET /v1/documents/{id}/indicators` | the series read out of one document |
| `GET /v1/indicators/{id}/documents` | the material one series was read from |

Every response uses the envelope from program.md §53 — `data`, plus `meta` or
`error` — so a consumer writes one parser rather than one per route.

### Speed

The warehouse is columnar files, not a database with a buffer pool: every
request re-opens Parquet and re-aggregates. Three things make that quick.

**The lake is not listed twice.** DuckDB prunes partitions *after* expanding
the glob, so a filter on the partition key does not save the listing — and over
the observations, which are one directory per series, the listing is the whole
cost. Counting one series through the full glob measures 185ms; through its own
partition, 0.3ms. A query that names its series now globs only those
directories. It is an optimisation and never a filter: the `WHERE` clause is
unchanged, and anything that cannot be narrowed safely reads the whole dataset.

**The shape of the lake is remembered.** "Does this dataset exist" was asked
several times per request and cost 176ms each time over the observations,
because it expanded that same glob. It is now answered from the filesystem and
held for 30 seconds.

**Rendered responses are cached in Redis**, keyed by path and canonical query,
for `CACHE_TTL_SECONDS`. Optional: with no `REDIS_URL` the API derives every
response and behaves exactly as it did without it. Failures are never cached —
a 500 from a warehouse that was briefly unreachable would otherwise be served
for the rest of the TTL. Every response carries `X-Cache: hit|miss`, and
`/readyz` reports hits, misses and errors, because a cache that has quietly
stopped working looks exactly like one that is working, only slower.

```bash
brew services start redis     # or: docker run -p 6379:6379 redis
export REDIS_URL=redis://localhost:6379/0
```

Measured on the development lake — 484k observations, 266k regulations:

| Endpoint | Before | After | Cached |
|---|--:|--:|--:|
| `/v1/indicators/{id}` | 823 ms | 8 ms | 0.8 ms |
| `/v1/observations?indicator=…` | 1301 ms | 6 ms | 0.5 ms |
| `/v1/indicators` | 1177 ms | 687 ms | 1.6 ms |
| `/v1/observations` (unfiltered) | 1862 ms | 1066 ms | 0.7 ms |
| `/v1/storage` | 982 ms | 592 ms | 0.5 ms |

The unfiltered scans are still slow to derive, and the reason is the layout
rather than the query: `silver/observations` is 34MB across **1,639 Parquet
files**, one directory per series, because normalization partitions by
`indicator_id` so it can replace one series idempotently. File count dominates
— 14.4M rows of regulation sections in 90 files count in 11ms, while 484k
observations in 1,639 files take 226ms. `check_partition_keys` exists to catch
exactly this and cannot see it, because each normalization run writes one
series and looks reasonable on its own. Fixing it means changing what a
partition means to the normalizer, which is a pipeline change rather than a
serving one.

Values come back as **strings**, not JSON numbers. Silver stores `decimal128`
because published statistics are decimal quantities; serialising through a JSON
number hands every consumer a float64 and reintroduces exactly the drift the
decimal storage exists to prevent.

Parameters are validated against patterns and every value reaching SQL is bound.
`order` cannot be bound, so the caller picks a key and the server supplies the
clause — nothing from a request is interpolated.

The portal's own patterns — how a table page is put together, how a detail page
splits across tabs, and the rules both follow — are in
[docs/design.md](docs/design.md).

## Running it

See [docs/running-it.md](docs/running-it.md) for the full walkthrough. The short
version:

```bash
cp .env.example .env
make setup

cd pipelines
uv run terusan sources run worldbank-gdp --limit 1   # → RAW
uv run terusan warehouse extract                     # → Bronze
uv run terusan silver dimensions                     # → Silver dimensions
uv run terusan silver documents                      # → the document catalogue
uv run terusan warehouse tables                      # what is in the lake
uv run terusan warehouse query "SELECT * FROM silver_observations LIMIT 5"
```

Or in containers, with the lake on a shared folder — see
[docs/docker.md](docs/docker.md):

```bash
cp .env.example .env        # set DATA_DIR to the share
make docker-up              # the portal on :3000, the API on :8080
make docker-ingest CMD="sources run bnpb-disaster"
```

On the NAS, published through a Cloudflare Tunnel — the deployment this runs
as — see [docs/nas-deployment.md](docs/nas-deployment.md):

```bash
make nas-tunnel-install     # once: the tunnel, and terusan.csis.or.id
make nas-deploy             # copy to /volume2/terusan, render the stack's compose
                            # then start it: UGOS → Docker → Project → terusan
make nas-status             # the box, the lake, the hostname at the edge
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

Daily ingestion runs through a launchd agent (`make schedule-install`);
per-source crons in the registry are metadata until a scheduler reads them.

Not yet built: curation into Gold, the REST and SQL surfaces, search, and
authentication.

Scoped to Indonesia. The World Bank series are pulled for `IDN` rather than for
every country, and the geography dimension publishes the places in use plus
Indonesia's 38 provinces — a comparator series is a deliberate addition, not the
default.

World Bank aggregates are still recorded as `geo_type = 'region'` where they
appear, rather than dropped: they are real published figures, but every country
sits inside several overlapping groupings, so summing a dataset containing both
counts most places more than once. Recording the distinction lets a consumer
filter; deleting the rows would not, because nobody can filter what is absent.

Roadmap in program.md §60–63.
