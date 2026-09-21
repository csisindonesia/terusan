# Adding a source

Everything that acquires data lives in
[`pipelines/src/terusan_pipelines/sources/`](../pipelines/src/terusan_pipelines/sources/),
one package per source. Scrapers, API pullers, feed readers and bulk
downloaders all go here — scraping is one `collection_method` among several,
and they all end the same way: bytes in RAW with a provenance record.

## The one rule

**A source fetches. It does not parse.**

Extraction belongs in `extract/`, normalization in `normalize/`. A scraper that
returns clean rows has thrown away the original, so improving a parser means
re-scraping — and by then the portal has usually changed or gone (program.md
§2.1).

## What you write, and what you get

A source yields `Artifact` objects. That is the whole interface:

```python
class Inflation(Source):
    meta = SourceMeta(
        slug="bps-inflation",
        name="BPS — Consumer Price Index",
        category=Category.STATISTICS,
        source_type=SourceType.GOVERNMENT_API,
        collection_method=CollectionMethod.API,
        update_frequency=UpdateFrequency.MONTHLY,
        schedule="0 3 2 * *",
    )

    def collect(self, ctx: ScrapeContext) -> Iterator[Artifact]:
        yield Artifact(
            content=response.content,
            filename="cpi-2026-01.json",
            dataset="inflation",
            partition=("year=2026",),
            source_url=url,
            published_at=date(2026, 1, 1),
        )
```

The runner supplies everything else: content hashing, deduplication, the RAW
path, the `metadata.json` sidecar, per-host rate limiting, concurrency, and the
run record. That is deliberate — with fifty sources, anything left to the
source author gets done fifty different ways, and provenance is the one thing
that cannot be reconstructed later.

Working examples, runnable offline:
[`sources/example/native.py`](../pipelines/src/terusan_pipelines/sources/example/native.py)
and [`sources/example/adapted.py`](../pipelines/src/terusan_pipelines/sources/example/adapted.py).

## Bringing an existing script in

Do not rewrite it first. Wrap it, land its output, migrate later:

```python
from .scrape_bps import main          # your existing script, untouched

BPSLegacy = legacy_source(
    main,
    meta=SourceMeta(slug="bps-legacy", ...),
    dataset="bulk",
    output_subdir="out",              # where the script writes
)
```

The script runs inside a scratch directory, so its relative paths (`out/`,
`./data`) keep working. Every file it leaves behind is hashed, landed and
recorded. It needs no arguments; if it does accept one, it is given the output
directory.

What you lose until you convert it properly:

- **No incremental runs.** `ctx.since` and `ctx.limit` cannot be honoured, so
  every run re-fetches everything. Content-addressed landing makes that cheap
  on disk but not on the source's bandwidth.
- **No concurrency.** Legacy scripts move the process working directory, so the
  runner runs them one at a time, before the parallel phase.
- **Coarse provenance.** One `source_url` per script rather than per document.

Convert the ones that run most often, or that hit a source you would rather not
annoy. The rest can sit in the adapter indefinitely.

## When the generic extractor cannot read it

Most sources publish CSV, HTML or plain JSON, and the built-in extractors
handle them. A source that wraps its payload in an envelope of its own needs a
reader that knows the shape — the World Bank API answers
`[metadata, [records...]]`, which a generic JSON reader sees as a two-element
list and turns into two useless rows.

Such an extractor claims artifacts by source slug and is registered ahead of
the generic ones in `DEFAULT_EXTRACTORS`:

```python
class WorldBankExtractor(Extractor):
    target = "records"

    def handles(self, landed: Landed) -> bool:
        return landed.source_slug == "worldbank-gdp"
```

See [extract/worldbank.py](../pipelines/src/terusan_pipelines/extract/worldbank.py).

## Fetching

Use the shared client rather than httpx directly:

```python
from ..http import fetcher

with fetcher() as http:
    response = http.get(url)          # retries transient failures, raises otherwise
    maybe = http.try_get(other_url)   # None instead of raising, for fan-out
```

It retries 408, 429, 5xx and transport errors with jittered backoff, and never
retries a 404 or 403 — those mean the source changed or the code is wrong, and
retrying only hammers someone else's server.

Landing verifies magic bytes, so a binary artifact whose content does not match
its extension is refused before it reaches RAW. That is the single commonest
silent failure in scraping, and RAW is permanent.

## When the portal will not serve the file

Some portals answer their API and refuse their own downloads. BNPB's CKAN is
one: `/api/3/action/` answers a plain client, and every `/download/...xlsx`
comes back as a Cloudflare challenge — an HTML page carrying a `.xlsx` name,
which landing refuses on sight and should.

Where the publisher exposes the same material through an interface that does
answer, collect that and say in the module what it costs. BNPB's tables come
from CKAN's datastore: the agency's own parse of each workbook rather than the
workbook, so a merged header it dropped cannot be recovered by re-reading RAW
later. That is a real loss against §2.1 and worth stating rather than glossing.

Keep attempting the original, once per run. A challenge is site-wide, so the
first refusal answers for every file in the run — and the day the rule is
relaxed, the published bytes start landing beside the fallback without anyone
editing the scraper. See [bnpb/disaster.py](../pipelines/src/terusan_pipelines/sources/bnpb/disaster.py).

Do not work around the challenge itself. A scraper that pretends to be a
browser is a scraper that breaks silently and rudely.

## Fan-out sources

A source fetching many files needs to distinguish one flaky endpoint from an
outage. Collect per-file failures rather than raising, then fail the run below a
success ratio — see [bank_indonesia/seki.py](../pipelines/src/terusan_pipelines/sources/bank_indonesia/seki.py).
Without the ratio, a site-wide outage arrives as a successful run holding almost
nothing.

## Vendored scrapers

An existing scraper that already separates fetching from parsing does not need
rewriting. It moves into `sources/<agency>/legacy/` as it is, minus its entry
point and any output path — landing belongs to the platform — and a thin
`Source` beside it calls its fetch function and yields the bytes:

```python
class ConsumerSurvey(Source):
    meta = SourceMeta(slug="bi-consumer-survey", ...)

    def collect(self, ctx):
        yield Artifact(
            content=consumer_survey.download_zip(),
            filename="survei-konsumen.zip",
            dataset="consumer-survey",
            source_url=consumer_survey.ZIP_URL,
        )
```

Vendored modules are excluded from ruff. Restyling them buries the next real
change in a diff of cosmetic ones, and their docstrings are worth keeping
verbatim — they record things like *bi.go.id resets the connection for a short
User-Agent*, which is not obvious and took someone a while to find.

Their parse functions come along unused, waiting for the extractor that will
read the landed bytes. Fetching and parsing in one step is what the RAW layer
exists to undo (program.md §2.1).

## Registration

None. Subclassing `Source` inside the package is enough — the registry walks
the package and picks up everything concrete. A central list of fifty sources
is a merge conflict that silently drops a scraper when someone resolves it
badly.

Intermediate base classes declare themselves out:

```python
class PortalScraper(Source, abstract=True): ...
```

Slugs must be unique: they key both the `sources` table and the RAW path, so a
collision would put two providers in one directory. A duplicate fails at
import.

## Rate limiting

Limits are per **host**, not per source. Several sources routinely sit behind
one server, and a server's tolerance belongs to the server. Keyed by source,
ten polite scrapers run concurrently and together take a host down.

If your source uses its own HTTP client, route through the shared limiter:

```python
from ..runner import wait_for_slot

wait_for_slot(limiter, url)
response = client.get(url)
```

`--workers` raises how many sources run at once; it does not raise the load on
any one host.

## Running

```bash
terusan sources list                       # everything registered
terusan sources list --scheduled           # only those with a schedule
terusan sources show bps-inflation
terusan sources run bps-inflation --limit 5 --dry-run
terusan sources run                        # every scheduled source
terusan sources run --workers 8 --rate 2
```

A source that raises becomes a failed result, not an exception: one broken
scraper must not abort a batch of fifty. Failures are summarized at the end and
the command exits non-zero.

Always `--dry-run --limit 5` first against a live source. It exercises the real
fetch path without writing to RAW.

Every run records itself, dry runs included:

```bash
terusan runs --source bps-inflation        # what it did, and when
terusan runs --failed                      # only what broke
```

The same rows are what the portal's **Logs** tab shows on each indicator page,
so a scraper that quietly stopped working is visible from the series it feeds
rather than only from a terminal. See
[running-it.md](running-it.md#run-history).

## Where things land

```text
raw/<category>/<source>/<dataset>/<partition...>/<doc_id>/
    original.pdf       bytes exactly as received
    metadata.json      provenance
```

`doc_id` derives from the content hash, so identical bytes resolve to the path
already on disk and a re-run is a no-op. A stable archive should re-run as
almost entirely deduplications — if it does not, something upstream is
changing, which is worth knowing.

## Credentials

Most sources need none. Where one does, it goes in
`pipelines/src/terusan_pipelines/sources/credentials.py` as a `SecretStr`
field, read from the project's `.env` with a real environment variable taking
precedence — the same mechanism storage configuration uses, so a scheduled run
on a server and a command in a terminal resolve the same credential:

```python
hdx_api_token: SecretStr | None = Field(default=None, alias="HDX_API_TOKEN")
```

```python
headers = bearer(credentials().hdx_api_token)
with fetcher(timeout=TIMEOUT_SECONDS, headers=headers) as http:
    ...
```

Three rules. The name goes in `.env.example` with no value, so the next person
knows it exists. `SecretStr` rather than `str`, so a credential cannot reach a
log line or a traceback by being interpolated into one — `.get_secret_value()`
is a thing you write on purpose. And absent stays supported wherever the source
can still collect something: HDX's CSVs are public, so an unset token is a run
that logs `authenticated=False` and collects the same bytes, not a crash.

httpx drops an `Authorization` header on a cross-origin redirect, which is what
you want when a portal redirects a download to an object store: the credential
reaches the portal, not the bucket.

## Naming the collection

The `dataset` a scraper yields is a slug — `consumer-survey` — and it becomes a
RAW path segment, a Bronze column and, once normalized, a derived code in the
catalogue. The code is what the portal and the API address it by; the slug is
what the pipeline uses. Neither is a title, so add one to
`pipelines/src/terusan_pipelines/datasets.py`:

```python
DatasetMeta(
    slug="consumer-survey",
    title="Consumer survey",
    source="bi-consumer-survey",
    description="Bank Indonesia's Survei Konsumen: the confidence index and its components.",
    tags=("sentiment", "surveys", "consumption", "households", "monetary"),
)
```

Skipping it is not fatal — an undeclared collection is described from its slug
and still reaches the catalogue — but the portal then shows "Consumer survey"
only because the slug happened to read well, and the topics nobody can infer
from a slug are missing. The rest of the tags (the source, its organization,
the cadence, the place) are derived; `tags` here is only what the title and the
slug cannot be read for.

Run `terusan silver dimensions` afterwards to publish the catalogue.

## Naming the documents

Each artifact also becomes a row in the document catalogue, built from the
provenance sidecar landing writes beside it. Most of that row comes from the
`Artifact` for free — the source, the licence, the URL, the size, the partition
— but the title does not, and a catalogue of fifteen hundred files is only
usable if they are named.

So state it where the scraper knows it:

```python
Artifact(
    ...,
    metadata={
        "title": f"Handbook of Energy and Economic Statistics of Indonesia {year}",
        # One of program.md §13's types, plus `data_file`. Optional: a PDF is
        # a publication and an HTML page is a web_page without being told.
        "document_type": "publication",
    },
)
```

`document_type` decides whether the artifact is catalogued at all: `data_file`
and `web_page` are left out, and everything else becomes a row on the portal's
Documents page. A PDF is a publication and a spreadsheet is a data file without
being told, so state it only when the source knows better — a scraped news
article is `news` however the page was served, and saying so is what puts it in
front of a reader.

Without it the catalogue falls back to the filename, humanised — which is fine
for `handbook-of-energy-2025.pdf` and useless for a CKAN resource served under
its UUID. It will not dress an identifier up as a title; it names the document
for its collection instead, which is honest and unhelpful.

Improving this later is worth doing: re-landing unchanged bytes refreshes the
sidecar without rewriting the file, so a better title reaches every edition the
scraper has ever landed rather than only the next one.

## Checklist

- [ ] `collect` fetches and yields; no parsing
- [ ] `dataset` and `partition` prune queries — never an id (program.md §46)
- [ ] `source_url` and `published_at` set where the source exposes them
- [ ] `ctx.since` honoured, so scheduled runs stay incremental
- [ ] `ctx.limit` honoured, so smoke tests stay cheap
- [ ] `schedule` set if it should run unattended
- [ ] `max_requests_per_second` reflects what the source tolerates
- [ ] `license` recorded — it propagates to the catalog and the citation
- [ ] the collection declared in `datasets.py`, so it has a title and topics
- [ ] `metadata["title"]` set where the source names its documents, so the
      document catalogue lists them by name rather than by filename
