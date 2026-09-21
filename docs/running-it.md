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

# 5. documents — the catalogue of what all this was read out of
uv run terusan silver documents
```

A dataset that holds hundreds of series — FRED's Indonesian crawl is six
hundred — takes the same mapping once instead:

```bash
# one indicator per distinct value of a Bronze column
uv run terusan silver normalize-each --by indicator \
  --dataset fred-indonesia-series --source fred-indonesia \
  --period-column period --value-column value \
  --unit-column units --geo-column country --number-format en \
  --name-column title --code-column series_id \
  --describe-dataset fred-indonesia-series-pages \
  --description-column notes --publisher-column publisher --release-column release
```

Bronze is read once and each series is normalized from memory, so six hundred
indicators cost one scan rather than six hundred. The column has to name the
indicator already: composing an identifier belongs in the extractor, where the
source's own titles and codes are still at hand.

Two collections have their mapping written down as a script, because the
mapping is the part worth reading rather than the invocation:

```bash
make pihps-silver      # food prices, one indicator per market
make bnpb-silver       # disaster impact, one indicator per measure and hazard
```

`--name-column` publishes the Silver indicators table as well as the figures,
which is what the portal prints when an identifier is only a code — FRED's are
eight characters, because its titles are too long for a URL and too alike to
shorten. `--describe-dataset` says the rest of the description lives in a
second Bronze dataset, one row per indicator: FRED's notes run to three
paragraphs, and copying them onto each of a quarter-million observations would
say the same thing six hundred times.

Every stage is idempotent. Re-running the first writes nothing if the source is
unchanged, the second skips documents already extracted, and the fourth rebuilds
its indicator from scratch. A refresh over a stable archive should move no bytes.

## Identifiers, and what a reader sees instead

A series is published under a derived code — `v9io529v` — not under the key its
mapping declares. The key stays, as `slug` in the Silver indicators table,
because it is what a maintainer greps for and what a reader recognises; it is
just not what the partition, the URL or the API parameter carries. A publisher
renaming a series would otherwise orphan every partition and every saved link
written under the old name (program.md §10). Datasets work the same way:
`consumer-survey` is the slug, `118v9w17` is the identifier.

Nothing changes in how a mapping is declared — `terusan silver normalize
GDP_CURRENT_USD …` still takes the readable key, and the run reports both:

```json
{ "indicator": "4nvcqzsd", "slug": "GDP_CURRENT_USD", "observations": 66 }
```

A lake written before this rule moves onto codes with one command. It rewrites
the observations, their `dataset_id`, and the observation ids derived from the
indicator, then republishes the indicators table and the dataset catalogue:

```bash
uv run terusan silver recode --dry-run   # what would move
uv run terusan silver recode
```

Running it twice changes nothing, and a lake built from scratch never needs it.

## Tags

Every catalogue record — series, dataset, source — carries at least five tags,
and they are derived rather than typed: the source and its organization, the
cadence, the unit, the place, and the topics the title reads for. They are
rebuilt whenever the record is published, so a tag cannot drift from the thing
it describes. The rules live in `terusan_pipelines/tagging.py`; what a dataset
is called and the topics its slug cannot be read for live in
`terusan_pipelines/datasets.py`.

`terusan silver dimensions` publishes the dataset catalogue along with the
geography, commodity and source dimensions, so run it after adding a source or
a collection — otherwise the portal has a code where a title should be.

The serving layer caches rendered responses in Redis when `REDIS_URL` is set,
for `CACHE_TTL_SECONDS` (60 by default). That TTL is the bound on how stale the
portal can be after a pipeline run: normalize at 05:00 and the new figures are
visible by 05:01. Nothing invalidates the cache explicitly, so if you need a
run to show immediately, `redis-cli --scan --pattern 'terusan:v1:*' | xargs
redis-cli unlink` — or just wait.

With no `REDIS_URL` nothing is cached and every request is derived from
Parquet. That is correct and slower, and it is the right setting while you are
changing a handler and want to see the change.

`terusan silver documents` publishes the catalogue of collected material: one
row per artifact in RAW, built from the provenance sidecar landing writes beside
each file. It counts what each document contributed from the observations' own
`document_id`, so run it *after* normalizing — a lake normalized afterwards
reports zeroes until it runs again. It is rebuilt from scratch each time and
costs a scan of the sidecars, so running it after every scrape is the intended
use rather than an indulgence.

It catalogues documents, not everything landed: an artifact classified
`data_file` or `web_page` is left out, so a warehouse of spreadsheets publishes
an empty catalogue rather than fifteen hundred rows nobody would read. Nothing
is lost — RAW keeps every byte, and a Silver observation carries its own
`document_id`, `content_hash`, `source_url` and `raw_path`, so provenance is
answered on the row rather than by this table.

A source that knows its documents' real titles should say so in the artifact's
metadata — `Artifact(metadata={"title": ..., "document_type": ...})`. The
catalogue prefers that over anything it can derive, and re-landing unchanged
bytes refreshes the sidecar, so improving a scraper's metadata improves every
copy it has ever landed rather than only what it collects next.

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

### Showing it to somebody else

```bash
brew install cloudflared      # once
make tunnel
```

```text
public https://quiet-marble-otter.trycloudflare.com
log    .cache/logs/tunnel-20260921-114300.log
API    http://localhost:8080   (rebuilds on .go change)
portal http://localhost:3000   (vite HMR)
```

A Cloudflare quick tunnel (<https://try.cloudflare.com>). `cloudflared` opens an
outbound connection to the nearest Cloudflare location and requests come back
down it, so nothing here listens on a public port and there is no account, no
DNS record and no router to change. The hostname is three random words and
lasts as long as the process; Ctrl-C ends the tunnel and the stack together.

One tunnel carries both halves. The obvious arrangement — a tunnel for the
portal and another for the API — breaks the login: `trycloudflare.com` is on the
public suffix list, so two hostnames under it are *cross-site* to a browser, and
the session cookie is `SameSite=Lax` and would simply never be sent. So the dev
server proxies `/v1`, `/healthz` and `/readyz` to the API and the whole stack
answers on one origin. `make tunnel` sets the three things that follow from
that: `VITE_API_URL` to the tunnel's own URL, the same URL appended to
`API_CORS_ORIGINS`, and `AUTH_SECURE_COOKIES=true` — TLS ends at Cloudflare and
the request reaches this process over plain HTTP, so the API cannot otherwise
tell the portal is on https.

Vite answers to `*.trycloudflare.com` because `PORTAL_ALLOWED_HOSTS` defaults to
that suffix. Another tunnel provider (`ngrok`, `tailscale funnel`) needs its own
suffix there, and nothing else:

```bash
PORTAL_ALLOWED_HOSTS=.ngrok-free.app make dev
```

While a tunnel is up, `AUTH_SECURE_COOKIES=true` means a login on
`http://localhost:3000` will not stick — use the public URL, or `make dev` when
you want the local one back.

This is for showing someone the portal, not for deploying it. The URL is open to
anyone who has it unless `AUTH_REQUIRED=true`, `PIPELINES_ENABLED` is on for the
local stack — which means the visitor can start scrapers on this machine — and
the tunnel reaches a laptop, not a server. For anything longer-lived, a named
tunnel with Cloudflare Access in front of it is the thing, not this.

### A permanent hostname

`make tunnel` is for showing somebody the portal for an afternoon. A hostname
that stays — `terusan.csis.or.id` — is a *named* tunnel: it has a name, a
credentials file on this machine and a CNAME in the zone, so it comes back with
the same identity and the same URL after a restart.

```bash
brew install cloudflared
make tunnel-install       # once: authorize, create the tunnel, write the config
make serve-install        # build and run both halves, and keep them running
make tunnel-service       # keep the connector running
make serve-status         # is it up, and does the hostname answer?
```

`make serve` runs the same stack in the foreground, for watching it start.

Once it is installed, four commands are the whole of running it day to day:

```bash
make restart    # after deploying a change: rebuilds, then listens
make stop       # take it down, freeing :8080 and :3000 for `make dev`
make start      # bring it back
make logs       # follow it
```

`make restart` installs the agent if it is not loaded, so it is safe as the
first command on a machine that has only ever run `make dev`.

`make tunnel-install` asks Cloudflare to authorize this machine (a browser
opens; the account has to be one that holds `csis.or.id`), creates a tunnel
called `terusan`, renders [`infra/cloudflare/config.yml`](../infra/cloudflare/config.yml)
into `~/.cloudflared/config.yml`, and points the hostname at it.

**It will not take a name that is already in use without being told to.**
`terusan.csis.or.id` resolves to a server today; routing the tunnel replaces
that record and whatever answers there now stops being reachable under this
name. When that is the intention:

```bash
OVERWRITE_DNS=1 make tunnel-install
```

Another hostname instead, and everything downstream follows it:

```bash
TUNNEL_HOSTNAME=data.csis.or.id make tunnel-install
TUNNEL_HOSTNAME=data.csis.or.id make serve
```

#### How one hostname serves both halves

The connector decides by path, in `infra/cloudflare/config.yml`:

```yml
ingress:
  - hostname: terusan.csis.or.id
    path: ^/(v1|healthz|readyz)(/|$)
    service: http://127.0.0.1:8080     # the API
  - hostname: terusan.csis.or.id
    service: http://127.0.0.1:3000     # the portal
  - service: http_status:404
```

Not the dev server's proxy, which is what `make dev` and `make tunnel` use: the
production portal is a built Node server with no proxy in it. Not two
hostnames either — one origin is what keeps the session cookie same-site and
CORS out of the picture entirely.

`cloudflared` will say which rule a URL hits before anything is published:

```bash
cloudflared tunnel ingress validate
cloudflared tunnel ingress rule https://terusan.csis.or.id/v1/indicators
```

#### What `make serve` sets, and why

| | |
| --- | --- |
| `VITE_API_URL=https://terusan.csis.or.id` | read at **build** time, so it is set before `vite build`, not after |
| `API_CORS_ORIGINS=https://terusan.csis.or.id` | a POST from the page carries an `Origin` header even same-origin, and the allow-list is never a wildcard |
| `AUTH_SECURE_COOKIES=true` | TLS ends at Cloudflare; the request reaches this process over plain HTTP and the API cannot otherwise tell the portal is on https |
| `PIPELINES_ENABLED=false` | the endpoint starts scrapers on the host and has nothing in front of it; `make dev` turns it on because the person clicking is the person at the machine, and nobody at a public hostname is |

Both halves bind `127.0.0.1`. The connector is the only way in — there is no
public port on this machine to find, which is the point of a tunnel.

#### Before it is really public

- **Who may read it.** `AUTH_REQUIRED=false` serves everything to anyone with
  the URL. Set it to `true`, or put [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/)
  in front of the hostname — which is the better answer for an internal
  deployment, because then nothing unauthenticated ever reaches this machine.
- **What it runs on.** A laptop that sleeps takes the hostname down with it.
  A named tunnel is stable; the thing behind it has to be too.
- **Keeping it up.** Two launchd user agents, not one:
  [`com.terusan.serve`](../infra/local/com.terusan.serve.plist) builds and runs
  the stack, [`com.terusan.tunnel`](../infra/local/com.terusan.tunnel.plist)
  runs the connector. Separate so the tunnel stays up while the stack is
  rebuilt — a request during a restart then gets a 502 from Cloudflare rather
  than a hostname that has stopped resolving. User agents rather than
  `sudo cloudflared service install`, whose LaunchDaemon runs as root and would
  need its own copy of the credentials in `~/.cloudflared`; the cost is that
  both are up while this user is logged in. `make serve-uninstall` and
  `make tunnel-service-remove` take them back out.
- **DNS caches.** The old record's TTL outlives the change. A machine that
  loaded `terusan.csis.or.id` in the last five minutes keeps reaching the old
  server; `sudo dscacheutil -flushcache` on macOS, or simply wait.

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

## SIPRI military expenditure

One workbook, six series, Indonesia only:

```bash
uv run terusan sources run sipri-milex          # ~900 kB, one file per release
uv run terusan warehouse extract statistics sipri-milex
./scripts/normalize-sipri.sh                    # from the project root
```

The download link is read off <https://www.sipri.org/databases/milex> rather
than hardcoded: its filename carries the coverage and the revision
(`SIPRI-Milex-data-1949-2025_v1.2.xlsx`), and both change when SIPRI
republishes, so a pinned URL keeps serving last year's figures.

The workbook holds every country and lands whole; the extractor keeps
Indonesia and reads one indicator per sheet:

| Indicator | Unit |
| --- | --- |
| `sipri_milex_constant_usd` | US$ m., constant prices — base year from the sheet name |
| `sipri_milex_current_usd` | US$ m., current prices |
| `sipri_milex_local_currency` | rupiah, current prices |
| `sipri_milex_per_capita` | US$ per capita, current prices |
| `sipri_milex_share_gdp` | share of GDP |
| `sipri_milex_share_govt_spending` | share of government spending |

The two share series are fractions of 1 — SIPRI stores `0.0086` under a
`0.00%` cell format — so their unit says *share of*, not *percent*. Multiply by
100 when displaying, not in the warehouse.

Each row also carries `estimate` and `uncertain`, which the workbook states
only as blue and red font. A figure SIPRI flags as an estimate should not read
as a measurement.

## SEKI, HEESI and Meta's movement data

Three sources ported from standalone scrapers. Each is a source that lands
bytes, an extractor that knows the publication's shape, and a normalization
script.

Each block runs from the project root:

```bash
# Bank Indonesia's SEKI — ~108 legacy .xls tables, 212 mapped series
uv --project pipelines run terusan sources run bi-seki
uv --project pipelines run terusan warehouse extract statistics bi-seki
./scripts/normalize-seki.sh

# ESDM's energy handbook — one PDF per edition, 14 tables read out of it
uv --project pipelines run terusan sources run esdm-heesi   # --limit N for older editions
uv --project pipelines run terusan warehouse extract statistics esdm-heesi
./scripts/normalize-heesi.sh

# Meta's movement distribution via HDX — ~100 MB per release, Indonesia kept
uv --project pipelines run terusan sources run hdx-meta-movement-distribution
uv --project pipelines run terusan warehouse extract research
./scripts/normalize-mobility.sh

# Yahoo's commodity futures — seven instruments, four price series each
uv --project pipelines run terusan sources run yahoo-gold yahoo-copper
uv --project pipelines run terusan warehouse extract statistics
./scripts/normalize-yahoo.sh
```

**SEKI** publishes formatted sheets, not datasets: row 41 of table 8.1 is the
composite CPI and nothing in the file says so. Which row holds which series is
declared in [`reference/seki/variables.csv`](../reference/seki/variables.csv) —
committed reference data, so a Bank Indonesia layout change is a diff. Add a
series by adding a row there; no code changes.

**Yahoo's commodities** are the reason `--commodity` exists. A commodity is a
row dimension in one shape and the series' whole identity in another: Bank
Indonesia prices thirty-one foods in one table, so `--commodity-column name`
tells the rows apart, while Yahoo publishes one file per contract and no column
in it says "gold". Declaring `--commodity gold` resolves the series against
[`reference/commodities/commodities.csv`](../reference/commodities/commodities.csv)
and lands a `commodity_id` on every figure, which is what puts gold and thermal
coal on the Commodities page beside the groceries. A column wins over the
constant where a mapping declares both.

A commodity in that file with nothing collected against it is not a mistake —
the registry says what the warehouse can resolve, and the page lists what has
figures. Nickel is the current example: Indonesia's largest metal export,
present only as Bank Indonesia's monthly export value, with no price series
registered yet.

**HEESI** is a PDF, and its table parsers are vendored under
`extract/heesi/vendored/` with `sheets.yaml` beside them. A fifteenth table is
an entry in that YAML. The parsers keep the shape they arrived in and are
excluded from ruff, like the vendored agency scrapers.

**Movement distribution** releases are global — every country Meta reports on,
a hundred megabytes each — because HDX's datastore API, which could filter
server-side, now requires an account. So the file lands whole and the extractor
keeps `country == "IDN"`, streaming rather than decoding the file at once. A
run takes the newest release only; `--limit` asks for more.

Its districts are regencies and cities, which the geography registry does not
hold, so each is labelled `Bogor (IDN.9.4_1)` and stays unresolved. Without the
GADM code a bare `Gorontalo` resolves to the *province* of that name, and
`Gorontalo` and `Kota Gorontalo` land on one observation. Registering
Indonesian regencies is what would fix that properly.

## Working out a mapping

Before normalizing a new table, check how its cells will be read:

```bash
uv run terusan silver check "Triwulan I 2026"
uv run terusan silver check "1.234,56"
uv run terusan silver resolve "Sultra"
```

## Run history

Every run writes a row as it finishes — a scrape, an extraction, a
normalization — whether it succeeded or not. The row says what ran, when, how
long it took, how many records came in and went out, and what the error was
when there was one.

Two places hold it, and the lake one needs no setup:

```bash
make runs             # the lake's journal: what ran, when, and what it produced
make failures         # only the runs that failed
```

```bash
uv run terusan runs --source bps --limit 50
uv run terusan runs --pipeline ingest-bps
```

The journal lives in the lake at `silver/pipeline_runs/date=YYYY-MM-DD/`, one
Parquet part per run, and the portal reads it: every indicator page has a
**Logs** tab showing the ingestions of the sources behind that series and the
normalizations that produced it, `/v1/indicators/{id}/runs` in the API, with
`/v1/runs` for the whole history. A run that died without recording anything
writes no row at all, so a gap in the history is itself the signal — which is
why the tab shows when the last run and the last *success* were, rather than
only listing rows.

A run per part is a small file per run on purpose: batching them would lose
exactly the runs that crash. Fold them together when the count starts to
matter:

```bash
uv run terusan warehouse compact silver pipeline_runs
```

### Checking every source at once

The Logs tab answers "is this series still being fed". The question one level
up — "did everything that should have run, run" — is the journal read whole:

```bash
make runs             # everything, newest first
make failures         # only what failed
make schedule-status  # whether the 05:00 agent is loaded, and its last log
```

A source that is scheduled and has no recent row did not run at all, which is
the failure the journal cannot show you directly: a run that died before it
finished writes nothing. `uv run terusan sources list --scheduled` is the list
it should be checked against — a slug in that list with no row in `make runs`
since its schedule says it should have one is the thing to look at.

### Running one now, from the portal

The **Run ingestion now** entry in an indicator page's actions menu, and the
button beside its run history, start the scraper behind that series. It runs
`terusan sources run <source> --trigger portal` on the host the API is on, and
the output appears under the Logs tab while it goes; when it finishes, the row
it wrote to the journal joins the table below it.

It is off unless the API was started with `PIPELINES_ENABLED=true`, and the
menu entry says so where it is off. `make dev` switches it on, which is the
case it is for — the pipelines are checked out right there and the person
clicking is the person who would otherwise be typing the command.

Leave it off anywhere the API is reachable by people who should not be able to
start jobs on that host: there is no authentication in front of it
(program.md §34). What bounds it, where it is on: the slug is checked against
the source registry in the lake before it reaches a command line, no shell is
involved, one run per source at a time, `PIPELINES_MAX_CONCURRENT` runs in
total, and `PIPELINES_TIMEOUT_SECONDS` before a hung scraper's process group is
stopped. See `.env.example` and `services/api/internal/runner`.

Only ingestion. Extraction and normalization are still terminal work —
extraction reads the whole of RAW and belongs to no one series, and a
normalization needs a column mapping that is passed as flags and stored
nowhere the portal could read.

PostgreSQL holds the same history (program.md §48), with the dataset versions
and the audit log beside it. That half is optional and off unless
`DATABASE_URL` points somewhere real:

```bash
createdb terusan
make db-migrate
make catalog-sync     # source registry into PostgreSQL
make catalog-runs     # run history from PostgreSQL
```

Without it a run still records itself in the journal. The lake is the system of
record for data; the catalog records what happened to it, and neither one
failing may take an ingestion down with it.

## Collections and saved queries

A **collection** is a folder of records — series, datasets, documents,
regulations, commodities — gathered under one question, and a **saved query**
is a set of filters with a name on it. They are the one thing the serving layer
stores rather than derives: everything else it answers comes from the lake and
can be rebuilt by re-running a pipeline, and a folder somebody assembled
cannot.

Where they live is a deployment setting:

```bash
APP_DB=./.data/app.duckdb   # a DuckDB file beside the lake
COLLECTIONS_WRITE=true      # whether the routes accept changes
```

With a path, `/v1/collections` answers and a folder has an address —
`/collections/col-fc3521d0f50e` opens on any machine that can reach the portal.
Without one, the portal keeps them in the browser instead: that works, survives
a reload, and can be shared with nobody, because `localStorage` has no URL. The
page says which it is doing rather than leaving the reader to find out when a
link they sent opens empty. `make dev` sets the path.

The file sits beside the lake and not in it. The lake is pipeline-written and
immutable (program.md §45.3); this is the one thing in the system a person
wrote, and a re-ingestion must not be able to walk over it.

A session says who is reading (below), but nothing is scoped to a person yet:
one deployment is one shelf, and everyone who can reach the API sees the same
folders. That is why `COLLECTIONS_WRITE` exists — a public deployment can serve
a curated shelf read-only, and the portal then hides the buttons that would
write instead of showing them failing. `/v1/capabilities` reports both, which
is how the portal knows.

Moving a shelf:

```bash
# Everything this deployment holds, as the portal's export writes it.
curl -s localhost:8080/v1/collections > collections.json

# Merge a shelf in. Ids already here are left alone, so importing twice
# changes nothing.
curl -X POST localhost:8080/v1/collections/import \
  -H 'Content-Type: application/json' \
  --data-binary @terusan-collections.json
```

The portal's **Shelf → Export / Import** does the same thing from the browser,
which is how a shelf built before the API kept one is carried over to it.

## Logging in

Accounts live in the same application database as the shelf, so a deployment
with `APP_DB` set has a login page and one without has none — the portal checks
`/v1/capabilities` and shows the form only where there is something to log in
to.

Make an account:

```bash
make auth-user EMAIL=you@example.org     # prompts twice, no echo
make auth-users                          # who exists, and when they last came in
```

`authctl` opens the database directly, and DuckDB locks that file to one
process — **stop the API first**. For the very first account on a running
deployment there is a way round that: `POST /v1/auth/bootstrap` creates it
without downtime, answers only from loopback, and answers only while the
accounts table is empty.

```bash
curl -X POST localhost:8080/v1/auth/bootstrap \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.org","password":"a-long-passphrase","name":"You"}'
```

What a session is, in full: an opaque random token in an `HttpOnly` cookie,
twelve hours by default and thirty days with "remember me"
(`AUTH_SESSION_HOURS`, `AUTH_REMEMBER_DAYS`). The database holds the SHA-256 of
that token, never the token, and the password as PBKDF2-HMAC-SHA256 with a
per-password salt and its parameters in the encoding — so raising
`AUTH_PBKDF2_ITERATIONS` re-hashes on next login instead of locking everyone
out. Eight wrong passwords for one address and that address waits two minutes.

### What the gate actually refuses

One middleware sits in front of everything (`internal/httpapi/auth.go`), and it
asks two different questions:

- **Changing anything** needs a session as soon as the deployment has accounts.
  Every `POST`, `PATCH` and `DELETE` — collections, saved queries, starting an
  ingestion — is refused with 401 to a browser that is not signed in. This is
  not a setting: on a portal where people sign in, an anonymous request that
  renames somebody's collection is not a feature.
- **Reading** is a setting, because whether the figures are public is a
  decision about the deployment:

```bash
AUTH_REQUIRED=true   # everything but /healthz, /readyz, /v1/capabilities and
                     # /v1/auth/* needs a session
```

Off by default, because this serves a research warehouse whose point is that
figures can be traced. Turn it on where the lake holds something that is not
public, and set `AUTH_SECURE_COOKIES=true` wherever TLS is terminated by a
proxy in front of the API, so the session cookie is marked `Secure` even though
this process cannot see the TLS.

Roles are carried and shown and enforce nothing: everyone who can sign in sees
the same warehouse and the same shelf. The portal says so on the account page
rather than letting a badge imply a permission model.

### The account page

The three dots beside your name at the foot of the sidebar open it —
`/profile`, and every route behind it is scoped to the session making the call.
There is no endpoint that takes somebody else's user id.

- **Profile** — your display name. The address identifies the account and is
  changed with `authctl`, not here.
- **Password** — `POST /v1/auth/password`, which needs the current one and ends
  every *other* session you have open. That is the usual reason for changing a
  password; the browser doing the changing stays signed in.
- **Where you are signed in** — every live session, what browser it claimed to
  be, when it started and when it runs out, with the current one marked. End
  one, or end all the others at once. It takes effect immediately: the next
  request that cookie makes is a 401.
- **Sign out** — ends this session on the server and resets the query cache, so
  the next person at that machine does not see what the last one had open.

## Suggesting data to collect

The bar at the top of every page has a **Suggest data** button beside the bell.
It asks for four things — what it is, where it is published, how often it
changes, and what it covers — and files them on a queue.

A queue rather than an email, which is the opposite of what the portal's other
forms do. The report and contact pages compose a `mailto:` on purpose: a form
posting into a void is worse than no form. A request to ingest a source is a
different kind of thing — it wants a status, it wants to still be there next
month, and the next person about to ask for the same source should be able to
see that somebody already did. The dialog lists what has already been asked for
underneath, for exactly that reason.

```bash
curl localhost:8080/v1/suggestions                    # the queue, open ones first
curl -X POST localhost:8080/v1/suggestions ...        # needs a session
curl -X PATCH localhost:8080/v1/suggestions/<id> \
  -d '{"status":"planned","note":"behind the HEESI backfill"}'
```

Statuses are `open`, `planned`, `ingested` and `declined`; the note is where a
maintainer says why. Filing one needs a session — a request nobody can ask a
follow-up question about is a dead end — and it lives in the same application
database as the shelf, so a deployment without `APP_DB` shows the reader the
contact address instead of a form that would swallow their message.

## What is not wired up yet

The portal (`make dev-portal`) is a shell — it does not read the catalog. The
API (`make dev-api`) serves `/healthz` and `/readyz` only; it has no data
endpoints. SQL is the way to read the lake today.
