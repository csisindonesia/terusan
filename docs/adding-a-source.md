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

## Fan-out sources

A source fetching many files needs to distinguish one flaky endpoint from an
outage. Collect per-file failures rather than raising, then fail the run below a
success ratio — see [bank_indonesia/seki.py](../pipelines/src/terusan_pipelines/sources/bank_indonesia/seki.py).
Without the ratio, a site-wide outage arrives as a successful run holding almost
nothing.

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

## Checklist

- [ ] `collect` fetches and yields; no parsing
- [ ] `dataset` and `partition` prune queries — never an id (program.md §46)
- [ ] `source_url` and `published_at` set where the source exposes them
- [ ] `ctx.since` honoured, so scheduled runs stay incremental
- [ ] `ctx.limit` honoured, so smoke tests stay cheap
- [ ] `schedule` set if it should run unattended
- [ ] `max_requests_per_second` reflects what the source tolerates
- [ ] `license` recorded — it propagates to the catalog and the citation
