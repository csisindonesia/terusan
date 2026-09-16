# Running it, and seeing the data

Everything below runs against a local lake at `./.data`. No database and no
network are needed except where a step fetches from a real source.

## Once

```bash
cp .env.example .env
make setup            # toolchains, then creates the layer directories
```

`.env` needs no editing for local work. `STORAGE_ROOT=./.data` is relative and
anchors to the project directory, so it names the same lake whichever
subdirectory you run from.

```bash
make storage-info     # where the lake actually is
```

## The pipeline

Four stages, each independently runnable:

```bash
cd pipelines

# 1. acquire — bytes land in RAW with provenance, immutable and hashed
uv run terusan sources list
uv run terusan sources run worldbank-gdp --limit 1

# 2. extract — RAW becomes machine-readable Bronze rows
uv run terusan warehouse extract

# 3. dimensions — geography and commodities into Silver, from reference/
uv run terusan silver dimensions

# 4. normalize — Bronze text becomes typed observations
uv run terusan silver normalize GDP_CURRENT_USD \
  --dataset gdp --period-column year --value-column gdp_usd \
  --geo-column country_iso3 --unit USD --number-format en
```

Every stage is idempotent. Re-running the first writes nothing if the source is
unchanged, the second skips documents already extracted, and the fourth rebuilds
its indicator from scratch. A refresh over a stable archive should move no bytes.

Bumping `PARSER_VERSION` in `extract/base.py` is what makes a changed extractor
run again — extraction is idempotent on document and parser version, so without
the bump the improvement simply does not take. Re-extraction appends rather than
replaces, keeping a partition traceable to the code that produced it, and Silver
reads only the current version.

Watch the counters rather than the exit code. `unresolved_geo`,
`ambiguous_values` and `unparseable_values` are how the run tells you something
needs attention — a name the reference data does not know, or a number that
could be read two ways.

## The portal and the API

```bash
make dev
```

```text
API    http://localhost:8080
portal http://localhost:3000
Ctrl-C stops both.
```

Both run in one process group, so interrupting stops both — including the
binary `go run` compiles and launches as a further child, which is what
survives a plain `make -j2` and leaves the port held.

If a port is already taken the script says so and stops, rather than letting
vite move to the next free one. A portal on 3001 talking to an API that only
allows 3000 fails in a way nobody enjoys diagnosing.

Either can also run alone:

```bash
make dev-api        # :8080
make dev-portal     # :3000
```

Ports move with the environment:

```bash
API_PORT=9000 PORTAL_PORT=4000 make dev
```

The portal reads `VITE_API_URL` at build time; with a non-default API port, set
it in `apps/portal/.env` and add the portal's origin to `API_CORS_ORIGINS`.

### Checking it works

```bash
make smoke      # against a running `make dev`
```

It asks the one question the other checks cannot: does the client JavaScript
load? A portal whose client entry fails renders perfectly server-side and then
sits there — typecheck passes, the build passes, and the HTML looks complete.
The last check renders the page in headless Chrome and fails if the table is
still showing loading skeletons.

### Production

```bash
cd apps/portal && pnpm build && pnpm start
```

The build emits a platform-agnostic fetch handler rather than a listening
server, so `server.mjs` bridges it to Node and serves the static assets. Set
`PORT` and `HOST` to move it.

## Seeing the data

Every dataset is registered as a view named `<layer>_<dataset>`:

```bash
cd pipelines
uv run terusan warehouse tables
```

```text
bronze_records                     17,160 rows
silver_geography                      333 rows
silver_observations                17,160 rows
```

Then query them by name:

```bash
uv run terusan warehouse query "
  SELECT g.name, o.period, o.value
  FROM silver_observations o
  JOIN silver_geography g USING (geo_id)
  WHERE g.geo_type = 'country' AND o.period = '2023'
  ORDER BY o.value DESC NULLS LAST LIMIT 10"
```

**Join to `silver_geography` and filter on `geo_type`.** World Bank aggregates —
`WLD`, `ARB`, `EAS` — are kept as `geo_type = 'region'` because they are real
published figures, but every country sits inside several of them. Summing the
whole table gives about seven times the truth.

### Straight from DuckDB

The lake is plain Parquet, so nothing here is required:

```bash
duckdb -c "
  SELECT * FROM read_parquet('.data/silver/observations/**/*.parquet',
                             hive_partitioning=true) LIMIT 5"
```

Or interactively:

```bash
duckdb
D CREATE VIEW obs AS SELECT * FROM read_parquet('.data/silver/observations/**/*.parquet', hive_partitioning=true);
D SELECT count(*) FROM obs;
```

### From Python, R, or a notebook

```python
import duckdb
con = duckdb.connect()
df = con.sql("""
    SELECT * FROM read_parquet('.data/silver/observations/**/*.parquet',
                               hive_partitioning=true)
""").df()
```

Any tool that reads Parquet works — Polars, pandas, Power BI, Metabase. That is
the point of keeping storage and serving separate (program.md §2.3).

## Scope

The World Bank series are pulled for Indonesia (`COUNTRIES = "IDN"` in
`sources/worldbank/gdp.py`). Widen it and re-ingest to add comparators —
`"IDN;MYS;THA;VNM"` for regional neighbours — rather than pulling every country
and burying the figures this warehouse exists for.

`terusan silver dimensions` publishes only the places the observations refer
to, plus Indonesia and its 38 provinces. `--all` publishes the full reference,
for when a comparator series is about to land.

## Adding another series

The World Bank publishes thousands of indicators through one endpoint shape, so
a second series is a declaration rather than a file:

```python
class WorldBankPopulation(WorldBankIndicator):
    indicator_code = "SP.POP.TOTL"
    dataset = "population"
    meta = SourceMeta(slug="worldbank-population", ...)
```

Then the usual four stages, with `--dataset population` and an indicator id of
your choosing:

```bash
uv run terusan sources run worldbank-population --limit 1
uv run terusan warehouse extract
uv run terusan silver normalize POPULATION_TOTAL \
  --dataset population --period-column year --value-column value \
  --geo-column country_iso3 --unit persons --number-format en
```

## Working out a mapping

Before normalizing a new table, check how its cells will be read:

```bash
uv run terusan silver check "Triwulan I 2026"
uv run terusan silver check "1.234,56"
uv run terusan silver resolve "Sultra"
```

## Run history

Optional, and off unless `DATABASE_URL` points somewhere real:

```bash
createdb terusan
make db-migrate
make catalog-sync     # source registry into PostgreSQL
make runs             # what ran, when, and what it produced
```

Without it the pipeline runs and records nothing: the lake is the system of
record for data, the catalog only records what happened to it.

## What is not wired up yet

The portal (`make dev-portal`) is a shell — it does not read the catalog. The
API (`make dev-api`) serves `/healthz` and `/readyz` only; it has no data
endpoints. SQL is the way to read the lake today.
