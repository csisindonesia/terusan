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

Watch the counters rather than the exit code. `unresolved_geo`,
`ambiguous_values` and `unparseable_values` are how the run tells you something
needs attention — a name the reference data does not know, or a number that
could be read two ways.

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
