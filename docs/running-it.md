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

Running it in containers instead, with the lake on a shared folder, is
[docs/docker.md](docker.md). Everything below is the same either way — the
commands are the same CLI.

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
make vews-silver       # collective violence, one indicator per measure
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

This is for showing someone the portal, not for deploying it. The URL needs a
sign-in unless `AUTH_REQUIRED=false` was set, `PIPELINES_ENABLED` is on for the
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

- **Who may read it.** Sign-in is required by default (`AUTH_REQUIRED=true`);
  `false` serves everything to anyone with the URL. For an internal deployment,
  also consider [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/)
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

## Revealed comparative advantage

Two sources, one question: where Indonesia exports more of a product than its
share of world trade would predict, and how that compares with ASEAN and the
economies it competes against. Both cover the same reporters, declared once in
[`trade.py`](../pipelines/src/terusan_pipelines/trade.py): ASEAN (Timor-Leste
included), the other RCEP members, Brazil, Mexico, Türkiye, South Africa and
Bangladesh.

**The base years** come from two Stata working files the trade team built, put
in `tmp/rca` (or `RCA_DROP_DIR`, or `--param dir=...`):

```bash
make rca-ingest                                   # terusan sources run rca-seed
uv run terusan warehouse extract statistics rca-seed
./scripts/normalize-rca.sh                        # from the project root
```

- `rca clean_EG lists.dta`: Indonesia's HS92 six-digit exports and RCA from a
  WITS bulk download, 1995–2025. Landed as it came.
- `RCA_country_product_year_hs6_raw.dta`: the Atlas of Economic Complexity
  panel, every country, 1995–2024. At 3.7 GB it is cut to the reporters
  (3.0 of its 23.9 million rows) and landed as Parquet. The original is public
  and CC0 (Harvard Dataverse, doi:10.7910/DVN/T4CHWJ), so nothing dropped is
  lost for good.

Which file is which is read off its Stata variables, not its name. The goods
lists merged onto the first file are committed as
[`reference/trade/environmental-goods.csv`](../reference/trade/environmental-goods.csv):
561 HS92 codes across TESSD, APEC, ACCTS, SAGEA, EU–NZ, UK–NZ, OECD and UNCTAD.

The extractor sums the product rows into series for each list, and for the
union of all eight (`any`):

| Indicator | From | Unit |
| --- | --- | --- |
| `atlas_eg_rca_<list>` | Atlas, every reporter | index, world = 1 |
| `atlas_eg_exports_<list>` | Atlas | US$ |
| `atlas_eg_products_rca_<list>` | Atlas | listed HS6 products with RCA ≥ 1 |
| `atlas_eci`, `atlas_coi`, `atlas_diversity`, `atlas_growth_proj` | Atlas | as the Atlas states them |
| `wits_hs6_eg_exports_<list>` | WITS, Indonesia | US$ thousand |
| `wits_hs6_eg_export_share_<list>` | WITS, Indonesia | percent of total exports |
| `wits_hs6_eg_products_rca_<list>` | WITS, Indonesia | count |
| `wits_hs6_products_rca` | WITS, Indonesia | all HS6 products with RCA ≥ 1 |

The WITS file has no basket RCA. It only has rows for products Indonesia
exported, so about sixty of the listed codes are missing each year. A basket
taken over what is there leaves those products out of the world's side and
overstates Indonesia's advantage. The Atlas file has the world's exports of
every product, so its basket RCA is safe to use. The product rows themselves
stay in RAW, and one DuckDB query over the Parquet reads any subheading.

**The years after that** come from WITS's TradeStats API. It is public, needs
no key, and the `wits-tradestats` source collects it monthly:

```bash
uv run terusan sources run wits-tradestats        # 63 calls, ~40 s
uv run terusan warehouse extract statistics wits-tradestats
./scripts/normalize-rca.sh
```

The API stops at sector level. It covers RCA, exports (US$ thousand) and export
shares by the 16 HS sections, UNCTAD's SITC groups and the four stages of
processing, 1989 on, as `wits_rca_<group>`, `wits_export_value_<group>` and
`wits_export_share_<group>`. It has no HS six-digit RCA, so new product-level
years mean a fresh WITS bulk download dropped into `tmp/rca`. Two limits of the
endpoint:

- One reporter per call. Two come back as a 200 that reads `Response too
  large`, which the extractor refuses.
- Timor-Leste answers 404 until WITS holds data for it.

## UCDP organized violence

What <https://ucdp.uu.se/country/850> charts for Indonesia — deaths in
organized violence since 1989 — as 24 annual series:

```bash
uv run terusan sources run ucdp-organized-violence   # ~170 kB, one archive per release
uv run terusan warehouse extract statistics ucdp-organized-violence
./scripts/normalize-ucdp.sh                          # from the project root
```

The archive link is read off <https://ucdp.uu.se/downloads/> rather than
hardcoded: its filename carries the version — `organizedviolencecy-261-csv.zip`
is UCDP 26.1 — which changes with every June release, and the version is what a
citation states. The download is global and lands whole; the extractor keeps
country 850 and reads one indicator per column:

| Indicator | Unit |
| --- | --- |
| `ucdp_state_based_deaths` | deaths — a government against an organised opponent |
| `ucdp_intrastate_deaths` | deaths — the same, fought inside the country |
| `ucdp_interstate_deaths` | deaths — the same, against another state |
| `ucdp_non_state_deaths` | deaths — organised groups fighting each other |
| `ucdp_one_sided_deaths` | deaths — an armed actor killing civilians |
| `ucdp_organized_violence_deaths` | deaths — all three together |
| `ucdp_civilian_deaths` | deaths — civilians, across all three |
| `ucdp_combatant_deaths` | deaths — combatants, across all three |
| `ucdp_unattributed_deaths` | deaths — side unknown, which is what the range is made of |
| `ucdp_state_based_dyads` | dyads — pairs of actors fighting that year |
| `ucdp_non_state_dyads` | dyads |
| `ucdp_one_sided_actors` | dyads — actors killing civilians |

The first six also publish `_low` and `_high` series. A death toll in a
conflict is a range UCDP bounds deliberately, and a Silver observation carries
a value rather than a range — so the bounds are their own indicators, and
charting the best estimate alone states a precision UCDP does not claim.

A zero is a figure here. Indonesia has had no non-state conflict deaths since
2016, and that is the finding rather than a gap; only an empty cell yields no
observation.

The API at `ucdpapi.pcr.uu.se` is not used: it now answers an unauthenticated
caller with `API token required`, and the same release is public on the
downloads page. UCDP's event-level GED — the other half of the country page —
is not collected yet: it is a 39 MB global archive and nothing downstream reads
events.

## VEWS collective violence

The Violence Early Warning System codes collective violence in Indonesia one
incident at a time — a date, a district, two sides, the form the violence took,
what it was about, who was hurt, and whether anyone stepped in — and publishes a
verified export once a year. There is no portal and no URL: the files arrive by
hand.

Put them somewhere and run the source:

```bash
cp ~/Downloads/'Yearly Dataset 2025 - VEWS Dataset (v.1.0).xlsx' tmp/vews/
uv run terusan sources run vews-collective-violence
uv run terusan warehouse extract research vews-collective-violence
./scripts/normalize-vews.sh                          # from the project root
```

`tmp/vews` is only the default; `VEWS_DROP_DIR` in `.env` or
`--param dir=/some/where` moves it. The inbox can be emptied afterwards — what
matters is the copy in RAW, which is landed with provenance like everything
else.

**The year comes from the filename.** VEWS names each export for the year it
covers, and that decides which figures it is authoritative for: an export
carries a tail of incidents dated to its neighbours, because a coder files an
incident when they read about it. The 2021 export reaches back to 2017 and
forward into 2022; the 2025 one into 2026. Counted, the 2025 export's six weeks
of 2024 would overwrite the 2024 export's whole year and publish a 90% fall in
violence that is really a file boundary. So only the export's own year is
counted, and a file whose name carries no year is skipped rather than landed
under a guess.

Two Bronze collections come out of each export:

| Collection | Grain |
| --- | --- |
| `collective-violence-incidents` | one row per coded incident, every column VEWS wrote |
| `collective-violence-early-warning` | the year's incidents counted, per province and for the country |

Only the second is normalized. An incident is not an observation: two brawls in
one regency on one day are two facts, and no indicator, period and place tells
them apart. The incidents stay in Bronze, where a query can group them by
actor, weapon or issue — none of which Silver holds a dimension for.

| Indicator | Unit |
| --- | --- |
| `vews_incidents` | incidents |
| `vews_deaths` | deaths |
| `vews_injured` | people |
| `vews_female_deaths` | deaths — women and girls, a subset of the total |
| `vews_female_injured` | people — the same |
| `vews_child_deaths` | deaths — children, a subset of the total |
| `vews_child_injured` | people — the same |
| `vews_infrastructure_damaged` | structures |
| `vews_infrastructure_destroyed` | structures |
| `vews_incidents_with_intervention` | incidents a third party intervened in |

Three things to know before charting them.

**Indonesia is the whole year, not the sum of the provinces.** It counts every
incident, including the handful whose province was never coded. Adding the two
levels counts the year twice.

**An empty cell is a zero and `-99` is not.** VEWS leaves the casualty cell
blank when nobody was hurt and writes `-99` when the reporting did not say, so a
blank contributes zero to the sum and `-99` contributes nothing at all. A
province with incidents and no deaths publishes a zero, which is a finding; a
province with no incident at all publishes nothing, because a quiet year and a
province dropped from an export are not distinguishable from here.

**The province comes from the name, not the code.** VEWS carries a
`province_id` beside the name and it is a spreadsheet formula over the incident
id, so a mistyped id silently renumbers the province — some two dozen rows
across the four exports have a code that contradicts a name that is plainly
right. The name is what a coder typed and a verifier checked. Where one
province is written two ways — `Sumatra` and `Sumatera`, Jakarta's old formal
name and the one it was given in 2024 — `SPELLINGS` in `extract/vews.py` picks
one. A variant that is not in that table fails the Silver run outright
(`CollapsedDimension`), rather than publishing half a province's violence:
adding a line is the fix.

2022 has no export, so the series skip it.

## SEKI and Meta's movement data

Two sources ported from standalone scrapers. Each is a source that lands
bytes, an extractor that knows the publication's shape, and a normalization
script.

Each block runs from the project root:

```bash
# Bank Indonesia's SEKI — ~108 legacy .xls tables, 212 mapped series
uv --project pipelines run terusan sources run bi-seki
uv --project pipelines run terusan warehouse extract statistics bi-seki
./scripts/normalize-seki.sh

# ESDM's statistical publications — oil and gas, electricity, minerals
uv --project pipelines run terusan sources run esdm-publications
uv --project pipelines run terusan warehouse extract statistics esdm-publications

# ESDM's energy handbook — one PDF per edition, held as a document only
uv --project pipelines run terusan sources run esdm-heesi   # --limit N for older editions
uv --project pipelines run terusan warehouse extract statistics esdm-heesi

# Meta's movement distribution via HDX — ~100 MB per release, Indonesia kept
uv --project pipelines run terusan sources run hdx-meta-movement-distribution
uv --project pipelines run terusan warehouse extract research
./scripts/normalize-mobility.sh

# Air quality per province, computed in Google Earth Engine — Sentinel-5P
# columns and CAMS surface PM, one CSV per month. Needs EE_PROJECT in .env and
# a one-off `uv --project pipelines run --extra gee earthengine authenticate`
# (or EE_SERVICE_ACCOUNT_KEY). Takes the last three full months unless told:
uv --project pipelines run --extra gee terusan sources run gee-air-quality \
  --param start=2018-07          # a backfill; omit for the last three months
uv --project pipelines run terusan warehouse extract research gee-air-quality
./scripts/normalize-air-quality.sh

# Thirty more Earth Engine products — rainfall, temperature, vegetation, fire,
# night lights, land cover, forest loss, population, terrain, soil — one source
# each (`gee-<slug>`), declared in sources/gee/catalog.py. Same credentials.
# Monthly ones take the last three full months unless told; annual and static
# ones take every year they hold. A backfill from each product's first month:
uv --project pipelines run --extra gee terusan sources run gee-chirps-rainfall \
  --param start=1900-01
uv --project pipelines run terusan warehouse extract research
./scripts/normalize-gee.sh                  # or: ./scripts/normalize-gee.sh chirps-rainfall

# Yahoo's commodity futures — fourteen instruments, four price series each
uv --project pipelines run terusan sources run yahoo-gold yahoo-copper
uv --project pipelines run terusan warehouse extract statistics
./scripts/normalize-yahoo.sh

# The LME's official base-metal prices via Westmetall — nickel, tin, copper,
# aluminium, zinc, lead; cash, three-month and stocks, 2008 onwards
uv --project pipelines run terusan sources run westmetall-lme   # no --since: full pull
uv --project pipelines run terusan warehouse extract statistics westmetall-lme
./scripts/normalize-lme.sh

# Shanghai and Dalian futures via Sina — nickel, tin, stainless, aluminium,
# zinc, iron ore, coking coal; CNY/t, full history every run
uv --project pipelines run terusan sources run sina-shfe-nickel sina-shfe-tin
uv --project pipelines run terusan warehouse extract statistics
./scripts/normalize-sina.sh

# ICE Newcastle thermal coal via Trading Economics' market chart, 2008 onwards
uv --project pipelines run terusan sources run tradingeconomics-coal
uv --project pipelines run terusan warehouse extract statistics tradingeconomics-coal
./scripts/normalize-coal.sh

# Yahoo's exchange rates — eight pairs in one dataset, four price series each
uv --project pipelines run terusan sources run yahoo-exchange-rates
uv --project pipelines run terusan warehouse extract statistics yahoo-exchange-rates
./scripts/normalize-fx.sh

# National holidays and cuti bersama — every SKB 3 Menteri since 2019, by OCR.
# Needs `tesseract` with the `ind` model (brew install tesseract tesseract-lang)
uv --project pipelines run terusan sources run menpan-hari-libur
uv --project pipelines run terusan warehouse extract regulations menpan-hari-libur
uv --project pipelines run terusan silver events   # the calendar the API serves
```

**National holidays** are read off the joint decree of the Ministers of
Religious Affairs, Manpower and State Apparatus that fixes each year's
national holidays and cuti bersama. JDIH KemenPANRB holds all of them from 2019
(the 2020 calendar) onwards, amendments included — 2020's was changed four
times. The PDFs are scans, so
[`extract/menpan.py`](../pipelines/src/terusan_pipelines/extract/menpan.py)
finds the annex's ruled tables, reads each cell with Tesseract, and keeps a row
only when its dates fall on the weekdays printed beside them; a row that fails
is logged and counted in `rows_refused`, never guessed at. An amendment
restates the whole annex, so a year's calendar is the rows of the decree with
the latest `decree_enacted` for that `year`. Election-day holidays (14 February
2024, the 2020 and 2024 pilkada) are set by presidential decree, not by this
one, and are not here.

`silver events` turns those rows into the **event calendar** that
`GET /v1/events` serves (`?key=idul_fitri,ramadan&from=2024-01-01&to=2024-12-31`,
also `kind`, `religion`, `category`). Each year's latest decree is its
calendar; consecutive days under one holiday are one event, so Lebaran's two
days of national holiday are one row and the cuti bersama around them another.
Names are resolved to keys in
[`reference/events/holidays.csv`](../reference/events/holidays.csv) —
`idul_fitri` is one key from 2020 to 2027 however the decree spelled it — and a
day whose name OCR could not read takes its key from another version of that
year's calendar, or from the holiday its cuti bersama bridges. Ramadan is
derived as the thirty days before Idul Fitri and marked `approximate`: the
decree leaves 1 Ramadan to the Minister of Religious Affairs.

The assistant reads it too. A question naming a holiday and a price — "harga
beras menjelang lebaran", "analisis harga beras dengan hari besar keagamaan" —
is answered with an **event chart**
([`assistant_events.go`](../services/api/internal/httpapi/assistant_events.go)):
the daily series carrying the commodity asked about, from H-30 to H+14 around
each holiday, set to 100 on the window's first week and averaged across the
years. National holidays and cuti bersama are left out of the prices, since few
markets report on them — PIHPS's national rice price jumped 15% on Lebaran's
cuti bersama in 2022 and fell back the next working day. A question about the
dates themselves ("kapan cuti bersama lebaran 2026?") is handed the calendar.

The table is generic on purpose. `category` is `holiday` today; elections,
policy changes and disasters belong in it too, as other categories written by
their own builders in
[`normalize/events.py`](../pipelines/src/terusan_pipelines/normalize/events.py).

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
figures. Bauxite is the current example: its export is banned and no free daily
price exists for it.

**Nickel and tin** have no Yahoo contract (`NI=F`, `LN=F`, `TIN=F`, `SN=F` all
answer "Not Found"), so their prices come from two other places: the LME's
official settlement as Westmetall republishes it, one HTML table per metal per
year, and the Shanghai Futures Exchange's continuous contract through Sina's
quote API. The two are different markets in different currencies — SHFE is
quoted in yuan including VAT — and the gap between them is itself worth
reading. Both are licensed data republished openly: fine for research, not for
redistributing the raw series.

**Thermal coal** stopped on Yahoo in December 2025 (`MTF=F`, API2, still
answers with dated rows and no prices). ICE Newcastle — the Asian benchmark,
nearer Indonesian coal than API2 ever was — comes from Trading Economics'
market chart endpoint, which caches on its `v` parameter and ignores the date
range; see
[`markets.py`](../pipelines/src/terusan_pipelines/sources/trading_economics/markets.py).

**Yahoo's exchange rates** are one dataset rather than eight, because a reader
asking for "the exchange rate" wants the table. The pairs are declared in
[`currencies.py`](../pipelines/src/terusan_pipelines/sources/yahoo_finance/currencies.py)
and told apart in Silver by `--include symbol=`, which selects on the ticker
Bronze carries rather than on the file the figures arrived in.

Which pairs exist was settled by asking the API, not by assuming. A cross
quoted in rupiah — `EURIDR=X` — exists with five years behind it for the
dollar, euro, yen, pound, Singapore dollar, ringgit and baht. It does not exist
for the yuan, the peso, the dong, the Brunei dollar, the riel, the kip or the
kyat: Yahoo answers `CNYIDR=X` and `PHPIDR=X` with a single row dated today and
404s the rest. China being Indonesia's largest trading partner, the yuan is
carried as `USDCNY=X` and quoted in yuan per dollar; the cross against the
rupiah is a division the reader can do and not a figure to store. The other six
ASEAN currencies are each available on the same dollar basis — `USDPHP=X`,
`USDVND=X`, `USDBND=X`, `USDKHR=X`, `USDLAK=X`, `USDMMK=X` — and adding them is
one line each in `PAIRS`, at the cost of a table that quotes some currencies in
rupiah and others in dollars.

Foreign exchange is also why Yahoo's bars are dated by the exchange's clock
rather than by UTC. Yahoo keeps FX on Europe/London and stamps each bar at
local midnight, which under British Summer Time is 23:00 UTC the day before —
so read as UTC, every summer rate was dated a day early and a trading week ran
from Sunday to Thursday. The reader adds back the offset Yahoo states, which
moves no other instrument: Jakarta and New York are both stamped at an hour
that already falls on the session's own date. A session Yahoo is still pricing
arrives twice, once as its own bar with a null close and again as the running
quote, and the later reading supersedes the earlier.

**ESDM's annual handbook** is collected as a document and nothing more. Its
fourteen tables need a reader written for them one table at a time; that reader
was begun and not finished, so it and the figures it had produced were taken
out rather than left half-working. The PDFs still land, one per edition, and
show up under Documents — searchable, citable, served back with their licence —
while no series is published from them. `esdm-publications` excludes the
handbook so the same PDF does not arrive twice.

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

## The scheduled run

Every registry record may carry a cron: `0 18 * * 1-5` for IHSG, after the
Jakarta close; `0 22 * * 1-5` for the commodity futures, after New York;
`7 * * * *` for BMKG's earthquakes. Those records are now the instruction.

```bash
make due          # what this hour owes, collecting nothing
make scheduled    # collect it
make daily        # every active daily source, whatever its cron says
```

A launchd agent runs `scripts/scheduled.sh` on the hour — `make
schedule-install`, `make schedule-status` to see it. Hourly rather than daily
because an 05:00 agent cannot serve a source that wants 18:00; most hours owe
nothing and exit in about a second.

It was not always so, and the failure is worth recording. One agent fired at
05:00 and ran a list of two slugs written into `daily.sh` when there were two
daily sources. Thirteen more were added and the list was never touched, so they
were collected only when somebody remembered — and IHSG, had it been on the
list, would have been asked at five in the morning for a session that closes at
four in the afternoon. Nothing anywhere read the `schedule` field.

So nothing is listed by hand any more. `scripts/scheduled.sh` asks `terusan
sources due` which crons fired in the last hour; `scripts/daily.sh` with no
arguments asks the registry which active sources are daily. Adding a source
with a schedule needs no second edit.

```bash
uv run terusan sources due --verbose                   # this hour, with schedules
uv run terusan sources due --at 2026-09-25T22:00       # what a Friday evening owes
uv run terusan sources due --frequency daily           # ignore the rest
scripts/scheduled.sh --list --at 2026-09-25T18:00      # the same, end to end
```

`--list` comes first on `scheduled.sh` for a reason: without it, asking what a
Friday evening owes collects a Friday evening.

**Due is a window, not an instant.** An agent starts a few seconds late, or a
laptop wakes at 09:03 into the 09:00 it slept through. A matcher demanding the
exact minute would skip that run in silence, so the question asked is whether a
firing fell anywhere in the last hour. Widen it to catch up — `scripts/scheduled.sh
--window 180` — and keep it equal to the agent's interval otherwise, or a firing
falls between two runs. The reader is `sources/schedule.py`: five fields, no
`@daily`, no `L` or `#`, and an expression outside that dialect raises rather
than quietly never matching. Schedules are read against the machine's local
clock, as cron and launchd read them.

### What one run does

`scripts/daily.sh` is the run itself — fetch, extract, normalize, then
republish the dimensions and the document catalogue. Two things keep its work
proportional to a night's figures.

**It asks for a week, not for everything.** The lake already holds the history,
so re-pulling five years each night is asking a publisher for what was landed
yesterday — Yahoo answers with the whole window whatever it is asked for, and a
full pull is a thousand bars to learn one. A week rather than a day so a night
the agent did not fire leaves no hole, and because publishers restate: a
restated figure arrives as a second document and normalization resolves the two
by retrieval time, so an overlapping window corrects rather than duplicates.

```bash
SINCE_DAYS=90 scripts/daily.sh      # a catch-up after the laptop was shut
SINCE_DAYS=0 scripts/daily.sh       # the full window, which a first run wants
```

A source that does not read `--since` ignores it — Trading Economics scrapes
today's pages whatever it is told.

**It extracts the subtrees it fetched.** RAW is laid out `category/slug`, and
extraction given no subtree walks the whole lake: sixteen thousand documents
visited to find the eight this run landed. The category comes off the source's
registry record, so the subtree is derived rather than listed twice. For the
exchange rates that is `seen=16` instead of `seen=16550`, and 56 Bronze rows
instead of 10,432.

The cost of that: a file landed outside this script — a manual `sources run`, a
backfill, something copied into RAW by hand — is no longer swept up. Run
`terusan warehouse extract` with no arguments after doing that.

Normalization is per source, through a `case` naming each source's script. It
is not narrowed further: normalizing rebuilds a series from all of its Bronze
rows, which is what makes re-running it safe. A source with no mapping falls
through — JDIHN's legal documents and Geofabrik's extract are not series; they
land, reach Bronze, and `silver documents` catalogues them.

**One source's failure does not end the hour.** Extraction exits non-zero when
every document in a subtree fails, and under `set -e` that used to abort the
run where it stood. An hour that owed GDELT and IHSG collected both, failed on
GDELT's archives, and left IHSG unextracted and unnormalized without saying so
— the log simply stopped. Each subtree and each mapping is now allowed to fail
on its own, the failures are collected by name, and the run still exits
non-zero at the end:

```
--- extract: news/gdelt-events
!!! extract failed for news/gdelt-events — continuing with the rest
--- extract: statistics/yahoo-ihsg
=== normalize: yahoo-ihsg ===
=== done, with failures: extract:news/gdelt-events ===
```

Nothing is hidden and nothing else is lost. A run that ends this way wants
looking at, not ignoring — but it has done the rest of its work first.

**GDELT is the current standing failure.** Its hourly archives hold a CSV, and
`ZippedWorkbookExtractor` claims every `.zip` and then fails for want of a
workbook, so nothing GDELT lands has ever reached Bronze. Fixing it means
deciding whether a warehouse about Indonesia wants the global event stream —
hourly, and several hundred kilobytes a file — which is a scope question rather
than a bug to patch quietly.

## Three sources of food prices

Indonesia publishes daily food prices three times over, and the temptation is
to keep one and drop the rest. They are not the same figures.

| | PIHPS (BI) | SP2KP (Kemendag) | Panel Harga (Bapanas) |
|---|---|---|---|
| status | live, 8.7M observations | live, two sources | registered, inactive |
| geography | 34 provinces + national | **513 regencies and cities**, and national | 34 provinces |
| commodities | 31 grades of 10 foods | 42 national, 17 by regency | the same families |
| breakdown | four market types | the government ceiling | producer / wholesale / consumer |
| history | daily since March 2017 | national since Feb 2024; regency forward only | — |
| unit | IDR/kg | IDR/kg | — |
| blocked on | — | a browser, for the regency half only | an API key from Bapanas |

```bash
# SP2KP — needs the browser extra, once
uv --project pipelines sync --extra browser
uv --project pipelines run playwright install chromium

uv --project pipelines run terusan sources run kemendag-sp2kp-prices
uv --project pipelines run terusan warehouse extract statistics kemendag-sp2kp-prices
./scripts/normalize-sp2kp.sh
```

**PIHPS** is the long series and the one to reach for: `food-prices`, daily
since March 2017, traditional, modern, wholesale and farmgate markets. It is
the only one of the three that can answer a question about last year.

**SP2KP** is collected twice, because the ministry publishes it twice and
neither half contains the other.

```bash
# The API — no browser, no key, and rebuildable from nothing
uv --project pipelines run terusan sources run kemendag-sp2kp-national
uv --project pipelines run terusan warehouse extract statistics kemendag-sp2kp-national
./scripts/normalize-sp2kp-national.sh

# The Tableau crosstab — needs the browser extra, once
uv --project pipelines sync --extra browser
uv --project pipelines run playwright install chromium

uv --project pipelines run terusan sources run kemendag-sp2kp-prices
uv --project pipelines run terusan warehouse extract statistics kemendag-sp2kp-prices
./scripts/normalize-sp2kp.sh
```

| | `kemendag-sp2kp-national` | `kemendag-sp2kp-prices` |
|---|---|---|
| via | the dashboard's JSON API | a Tableau crosstab, through Chromium |
| place | the country, one weighted price | 513 regencies and cities |
| goods | 42 | 17 |
| history | daily since 2024-02-01 | the day it is run |
| backfill | one call per commodity | impossible |
| a run takes | ~2 seconds | ~23 seconds |

*The API half* is the one to reach for. `hnt/history-series` takes a date
range, so the whole series can be rebuilt at any time — which is what makes a
missed day survivable. It is also wider: Bulog's SPHP rice and imported
soybeans are priced here and absent from the crosstab, and both are the kind of
thing a subsidy question turns on.

Its endpoints were found by watching what the dashboard requests, not by
guessing. Guessing is useless here: **the API answers 401 for a route it does
not have**, so a wrong path looks exactly like a locked door. `/report/api/harga`
and a dozen other plausible names all return "Invalid or expired token"; they
simply do not exist.

*The crosstab half* remains the only way to a regency. The API's province and
regency views are single-date endpoints with no range, so they cannot be
backfilled and are not collected. It also carries the government **ceiling** —
*Harga Eceran Tertinggi*, *Harga Acuan* — beside each price, which is the
pairing the system exists for: not "what does rice cost" but "is it selling
above the ceiling, and where". On the first day collected, 391 of the 458
regencies reporting red bird's eye chili were over it, and 388 of 498 for bulk
sugar.

Two details will bite whoever touches this next.

*The crosstab writes Indonesian thousands separators; the API does not.*
`41.500` from the crosstab is forty-one thousand five hundred rupiah — read as
an English decimal it is 41.5, a thousandfold error that looks entirely
plausible in a chart — so that mapping declares `--number-format id`. Every
value in the export matches `\d{1,3}(\.\d{3})*`, with no decimal point
anywhere to lose. The API answers with plain integers and needs no such flag.

*The API states a quantity and never a currency.* `satuan` is `kg`, or `lt` for
cooking oil, `bks` for instant noodles, `400gr` for toddler formula, `ekor` for
a free-range chicken. A unit of `kg` on a price is wrong where it lands, beside
`IDR/kg` from the same ministry's crosstab, so the extractor joins the two and
keeps both columns — `satuan` as the API wrote it, `unit` as the figure reads.

**Panel Harga** is the food agency's own panel, measured by its own enumerators
at producer, wholesale and consumer level. Two independent measures of the same
prices is not redundancy: it is how a wrong one gets noticed, and they diverge
in exactly the weeks that matter. It lands under `food-prices-{level}`, so
nothing collides.

### The commodity and geography they share

Both land on the same dimensions, which is what lets a reader put them side by
side, and getting there needed two additions to the reference data.

Twelve commodities were added to
[`commodities.csv`](../reference/commodities/commodities.csv) and three aliases
onto rows already there. The aliases are where the two ministries spell one
good differently — Kemendag's `Daging Ayam Ras` against Bank Indonesia's
`Daging Ayam Ras Segar`. The new rows are where they mean different things:
`Beras Medium` is the HET bracket Kemendag sells rice under and `Beras Kualitas
Medium I` is the quality BI's surveyor saw, so they stay two goods. Minyakita
is its own commodity rather than a grade of cooking oil, because it is a policy
instrument before it is an oil.

Twenty-six regencies gained a second BPS code in
[`indonesia-regencies.csv`](../reference/geography/indonesia-regencies.csv).
Papua was split into four provinces in 2022 and Papua Barat into two, and BPS
renumbered the regencies that moved: Merauke is `91.01` under the old scheme
and `9301` under the new one, which SP2KP prints. No boundary was redrawn, so
each stays one row and answers to both codes. Without that, a sixth of SP2KP's
regencies landed with no place at all.

The cost is recorded in the file: `parent_geo_id` still names the pre-2022
province, so a roll-up puts Papua Tengah's figures under Papua. Registering the
four new provinces and re-parenting those twenty-six is what would fix it, and
it would move every series already collected against the old parents.

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
- **Reading** needs a session too, unless the deployment says otherwise:

```bash
AUTH_REQUIRED=true   # the default: everything but /healthz, /readyz,
                     # /v1/capabilities and /v1/auth/login|logout|me|bootstrap
                     # needs a session or an API token
AUTH_REQUIRED=false  # serve the lake to anyone who can reach the API
```

On by default, so a deployment reachable from outside does not hand its lake
to whoever finds the address; serving it publicly is a decision somebody makes.
With it on and no `APP_DB` the API refuses to start — there would be no account
anybody could sign in with. Set `AUTH_SECURE_COOKIES=true` wherever TLS is
terminated by a proxy in front of the API, so the session cookie is marked
`Secure` even though this process cannot see the TLS.

### API tokens

A script, a notebook or another service has no browser to hold the cookie, so
it sends an API token instead:

```bash
curl -H "Authorization: Bearer trs_…" https://terusan.example.org/v1/indicators
```

Make one on the account page (**API tokens**), or from the terminal with the
API stopped:

```bash
cd services/api && go run ./cmd/authctl token -email you@example.org -name "notebook" -days 90
```

The token is printed once; the database keeps only its SHA-256. It expires
(90 days by default, a year at most, twenty live per account), reads exactly
what its account can read, and stops working the moment it is revoked or the
account is disabled. It cannot manage the account: the password, the logins
list and the tokens themselves answer only to a cookie session, so a token that
leaks out of a script cannot mint its own successor. An unknown or revoked
token is a 401, not a quiet anonymous read.

### Roles, registration and the Users page

Three roles:

| Role | What it can do |
|---|---|
| `admin` | Everything below, plus **Administration → Access → Users**: add accounts (with a generated password shown once), approve or reject requests, change role and department, reset passwords, disable accounts |
| `researcher` | The ordinary account: reads the warehouse, keeps collections, makes API tokens |
| `guest` | A researcher with an end date. At the end of that day sign-in, open sessions and API tokens all stop |

A stranger can ask for an account at `/register` (linked from the login page,
off with `AUTH_REGISTRATION=false`). The request lands as a *pending*
researcher that cannot sign in until an admin approves it — as researcher,
guest (with an end date) or admin. The form answers the same whether or not
the address already had an account, is limited to five requests an hour per
address, and holds at most 200 pending requests. From the terminal:
`authctl approve -email … -role researcher`.

Each user's page shows their access history: every sign-in, and use of the
portal or an API token at most once per five minutes per address, with the IP
address and — behind Cloudflare with `AUTH_TRUST_PROXY=true` — the reported
country and city. Kept for 180 days.

Accounts from before roles existed (`member`) become `researcher` on the first
start after upgrading.

Beyond account management and a guest's end date, roles grant nothing: every
account that can sign in sees the same warehouse and the same shelf.

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
  -d '{"status":"planned","note":"behind the SEKI backfill"}'
```

Statuses are `open`, `planned`, `ingested` and `declined`; the note is where a
maintainer says why. Filing one needs a session — a request nobody can ask a
follow-up question about is a dead end — and it lives in the same application
database as the shelf, so a deployment without `APP_DB` shows the reader the
contact address instead of a form that would swallow their message.

## How the assistant finds a regulation

The assistant answers only from what the portal's own search hands it, so a
regulation the search misses is one the assistant says does not exist. It used
to match the question's words against titles, through hand-kept tables of
acronyms and synonyms, and every word the tables did not know was a miss
nobody saw: "PKPU syarat capres" found nothing, though PKPU 19/2023 Pasal 13
lists exactly that.

It now searches a full-text index, built into Gold from the regulations in
Silver:

```bash
make regulations-index      # ~4 minutes; scripts/import-regulations.sh runs it too
make eval-assistant         # how well it finds what it should, against this lake
```

The index holds every regulation's title, subject and region, and the text of
every article in the central and ministerial corpora (`PASAL_TRACKS` widens
that). Words are stemmed with the Indonesian Snowball stemmer, so *persyaratan*,
*syarat* and *pencalonan*/*calon* meet; ranking is BM25, so a word as common as
*tidak* weighs almost nothing without a stop list saying so. The definitions
article and the closing provisions are left out of the article text: both
quote the titles of other regulations and matched every question better than
the article that answers it. What the stemmer cannot know is still read from
the acronym table in `services/api/internal/httpapi/assistant.go` — that KPU is
the Komisi Pemilihan Umum.

A regulation's score is its title match counted twice, plus its best three
articles, lifted for a higher instrument (UU, then PP, then Perpres) and for
being recent; no more than three come from one body's one kind of instrument,
and a title that holds two of the question's words side by side counts for
more. The article that matched best is quoted to the model, so "is there a
rule on X?" is answered from what the rule says.

It searches on every question, not only those that say *peraturan*. Where the
reader did not ask for regulations, they are shown only when one matches
nearly the whole question, names some of it in its title, and matches it
clearly better than the portal's datasets and series do — "kurs rupiah
terhadap dolar" is covered as fully by a Bank Indonesia regulation as by the
exchange-rate series, and is a question about the series.

`make eval-assistant` asks every question in
`services/api/internal/httpapi/testdata/regulation_eval.json` the way the
assistant does, without the router, and reports which it found, where, and
how the old title matching did on the same cases. It fails below a floor set
in the test. When a reader reports a miss, add it there first; a lake without
the index skips the test, so CI does not run it.

## What is not wired up yet

The portal (`make dev-portal`) is a shell — it does not read the catalog. The
API (`make dev-api`) serves `/healthz` and `/readyz` only; it has no data
endpoints. SQL is the way to read the lake today.
