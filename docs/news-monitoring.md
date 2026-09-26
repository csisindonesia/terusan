# News monitoring

Sixty-nine Indonesian newspapers, read every day, coded into incidents.

Everything else in this warehouse republishes figures somebody else computed.
This is the one part that makes its own, which is why it is documented at
length: a number nobody else publishes is a number nobody else can check.

## What it produces

Everything a paper publishes is read and counted; only what is about a
monitored issue is stored. So the first collection is a count of what was read,
and the rest describe the small part that was kept.

| Collection | Grain | What it is |
|---|---|---|
| `news-daily-tallies` | one per outlet per day | How many articles were read, matched and confirmed, beside what discovery turned up. The denominator. A row exists for every outlet a run visited, including those that yielded nothing — no row means the crawl has never reached that paper. |
| `news-articles` | one per kept article | The corpus — what survived the gate. Outlet, date, headline, lead, body, matched terms. Each has its own page at `/news-articles/{document_id}`. |
| `news-violence-codings` | one per article | What the classifier made of one article, in VEWS column names. Five papers on one brawl produce five of these. |
| `news-violence-events` | one per incident | Those reports collapsed into the incidents they describe. |
| `news-violence-counts` | one per province-month-series | The incidents counted. Normalized into Silver observations like any other series. |
| `news-screenshots` | one per shot | A full-page PNG of each matched article as it looked on the day. |

Both the incident counts and the reading counts reach `/v1/observations` and
the portal's data explorer. The corpus and the events have their own routes —
an article is not an observation and not a ministry PDF, and pressing it into
either would bury both.

| Route | What it serves |
|---|---|
| `GET /v1/news/outlets` | The papers, with how much each was read and how much kept |
| `GET /v1/news/outlets/{host}` | One paper |
| `GET /v1/news/outlets/{host}/articles` | What was kept from it |
| `GET /v1/news/outlets/{host}/tallies` | The crawl's own log for that paper, a row per day: found, read, matched, kept |
| `GET /v1/news/articles` | The corpus |
| `GET /v1/news/articles/{id}` | One article: its coding, its provenance, which reader answered what, the incident it belongs to |
| `GET /v1/news/articles/{id}/screenshot` | The page as it stood the day it was collected |
| `GET /v1/news/events` | The incidents |

In the portal: `/news-outlets`, `/news-outlets/{host}` and
`/news-articles/{document_id}`. A paper's own page carries its registry entry
and its totals beside two tabs — the articles that were kept, and the crawl log
(`?tab=log`), a row per day saying how many pieces were read to find them.
Sixty read and two kept is the ordinary day, and the two only mean anything
beside the sixty. An article's page carries the coding and the
provenance under **Overview**, and the screenshot under **The page that day** —
its own tab, and its own URL (`?tab=page`), so a link sent to a colleague opens
the page as it was collected rather than a page describing it.

## How an article travels

```
newspapers -> discovery (search, sitemap, feed, section page)
           -> keyword dictionary          reference/news/keywords.json
           -> candidate articles
           -> JEV                         the cheap classifier, every candidate
           -> structured coding           violence? form? actors? escalation? confidence?
           -> confident        -> stored
              ambiguous        -> a large model -> deeper extraction -> stored
           -> clustering -> events -> counts
```

Two filters and two readers, cheapest first, and each one only sees what the
one before it let through. Discovery bounds the crawl to pages a paper
published in the window. The **dictionary** decides which of those is worth a
model at all — free, and it rejects the great majority. **JEV** codes every
candidate into the categories a human VEWS coder picks between, with a
probability on each answer. Where those probabilities say it was unsure, and
only there, a **large model** reads the article again and fills in what the
first reader could not.

The dictionary and the search lexicon are different lists doing different jobs,
and it is worth keeping them apart: the lexicon is what goes into an outlet's
search box, where a two-word phrase is what a search engine can use; the
dictionary judges the page that comes back, where single words carry the
signal.

## Running it

The source declares its own cron (`10 7-19 * * *`) and the scheduler picks it
up, so in normal operation nothing here needs running by hand.

```bash
# One shard of the outlet list — what a scheduled firing does.
terusan sources run news-monitoring

# One outlet, a few terms, no screenshots. What to run when debugging.
terusan sources run news-monitoring --param outlet=poskota.co.id --param terms=6 \
  --param screenshots=false --limit 5

# The whole list, for a backfill.
terusan sources run news-monitoring --param all=true --param window_days=30

# Go back to a keyword-bounded crawl. Far cheaper, and the daily totals then
# describe what the search terms found rather than what the paper published.
terusan sources run news-monitoring --param scan=keywords

# RAW -> Bronze, and the coding with it.
terusan warehouse extract news news-monitoring

# Reports -> incidents -> counts. Recomputed, so running it twice is a no-op.
terusan news cluster

# Counts -> Silver observations.
scripts/normalize-news.sh
```

`scripts/daily.sh` runs all five in order. The clustering step sits between
extraction and normalization on purpose: it is a recompute over the whole
window rather than an append, so it has to see everything the hour landed.

### Correcting codings already written

The readers choose between declared labels; the date, the place and the
casualty figures are read out of the text by a parser. So a fix to that parser
makes every coding already in Bronze wrong in the same way, and putting them
right needs neither the outlet nor the model:

```bash
# What a re-read would change, written nowhere.
terusan news recode --dry-run

# Apply it, then carry the corrections into the events and the counts.
terusan news recode
terusan news cluster
```

It re-reads the literal fields from the article text Bronze already holds,
keeps every label as the readers left it, and writes a coding only where
something actually moved. The corrected coding is appended rather than edited —
the newest one per article wins downstream — so what was published before stays
on the record next to what replaced it.

`--url` recodes one article, `--since`/`--until` a window. `--ask-model`
additionally picks up the articles that were never coded at all, the ones
landed with `engine = "unavailable"` because the classifier could not be
reached; that one costs a model call per article, and nothing else here does.

Other commands: `terusan news outlets` lists the papers, `terusan news lexicon`
shows the search terms, `terusan news dictionary` prints the keyword dictionary
— and with `--text "..."` runs one headline through it and says which rule
admitted or rejected it — and `terusan news validate YYYY-MM` scores a month
against the human record.

`terusan news coverage --quiet` answers "why is this outlet blank", which has
three quite different causes and three different fixes:

| State | What it means |
|---|---|
| never visited | No tally at all — the crawl has not reached it. Run it. |
| found nothing | Discovery returned no candidates. Its sitemap, feed, section page and search all came back empty: a fault in this repository's adapters, not in the newspaper. |
| read nothing | Candidates found, none fetchable. Usually an outlet that refuses robots, including a headless browser. |
| nothing violent | Working as intended. Its week held no collective violence, which is the common case. |

**A blank row on the outlets page means the crawl has not reached that paper
yet**, not that the paper published nothing. The scheduled run takes one shard
of the list per firing and walks the whole list across a day, so a freshly
deployed instance fills in over the first day. To fill it in one go — and it is
worth splitting into a few parallel processes, because the crawl is sequential
per outlet:

```bash
terusan sources run news-monitoring --param all=true --param window_days=3
```

## The decisions worth knowing

**The outlet list is reference data, not configuration.** It lives in
`reference/news/outlets.csv` and is committed, because which papers are read
decides what the dataset can contain — a change to it is a change to the
figures. Retired titles stay on the list with a note saying why: a paper that
stopped publishing is the reason its province's coverage thinned, and a list
that dropped it would make that look like a fall in violence.

**There is no crawl state.** No database of seen URLs, no queue. A URL's hash
is a partition segment in RAW, so "have we collected this?" is "does this
directory exist?", answered before anything is fetched. Everything else — the
recode queue, an outlet's coverage, what failed — is a question asked of Bronze.

**The dictionary is written; the vocabularies are mined.** The keyword
dictionary lives in `reference/news/keywords.json`, under version control
beside the outlet list, grouped by what each term signals — the act, who was
involved, what it left behind, what it was about. The grouping is the rule: an
article is a candidate when it carries an *act* term and a *collective* term,
or an act term and evidence from two different categories. That is what a flat
word list could not express. `tewas` alone is a traffic report, `massa` alone
is a concert, and the two together are worth a classifier's attention. It is
written rather than mined because widening it is a deliberate act that should
appear in a diff — and because it is the hardest recall bound in the feature:
an incident reported in terms it does not hold never reaches the classifier.

**The vocabularies are mined, not written.** The categories the classifier may
choose from are read out of the twelve thousand human-coded VEWS incidents in
Bronze, from the most recent years only. The search lexicon is mined from those
incidents' descriptions and then filtered by the classifier itself — raw
frequency yields the coders' own template and the names of provinces, and
`jawa barat` would match every article a paper published that week.

Two hand-maintained exceptions are declared in `news/codes.py`: VEWS revised its
dropdowns in 2025 and the revision did not reach every column, so
`SERANGAN TANPA SENJATA` and `SERANGAN TANPA SENJATA API` are both in use, in
the same file, in the same year. Offered as two options they are two names for
one thing and the model's probability splits between them.

**Read everything; keep almost nothing.** A provincial paper's day is council
meetings, football and the price of chillies. The crawl reads all of it — that
is the only way to know how much a paper published, which is the denominator
every rate needs — and stores an article only if it passes the dictionary and
then the classifier's one-question gate. On a typical day that is none of them.

The trade is real and runs one way. What is gained is a rate: nineteen articles
read, none of them violence, is a fact about a quiet week rather than a fact
about the crawl. What is lost is the ability to widen the dictionary later and
re-examine what was passed over — those pages were never stored, and reaching
them again means going back to an outlet that may have moved them.

There is deliberately no override for this. A flag that stored everything "just
for this period" would make the corpus mean two things depending on when a row
landed, and nothing downstream could tell which. `--param scan=keywords` goes
back to a keyword-bounded crawl, whose totals are then not totals.

An article the classifier reads and rejects is not kept either, and is not
served: it matched a word, not an issue. `/v1/news/*` excludes those, including
rows landed before this was the policy — the corpus is what is about an issue.
Those older files are still in RAW, which is immutable by design; nothing reads
them.

**When the classifier cannot be reached** the gate falls back to the dictionary
alone and keeps the candidate. Discarding articles on a classifier's
silence would leave a hole shaped exactly like a quiet week.

**Most of these search boxes are furniture.** Discovery asks each outlet's own
search first, because a keyword search is what bounds the crawl to articles
that might be about something. But a provincial CMS's search box very often
ignores its query and returns the latest news instead — asked for `bentrok
warga`, one outlet answered with fourteen articles about banking and the
provincial budget, every one of them recorded as found by a violence term.

That fails by returning *plenty*, so a "too few results" check never catches
it. Each outlet is therefore asked a control question first — a nonsense word
that cannot match an article — and one that answers with articles anyway is
read through its sitemap, its feed and its section page instead. In the sample
tested when this was built, three outlets in four failed that control, which
means the listing route is the normal path and the `section_path` column earns
its place.

**The tallies are filed by the newspaper's province, not the incident's.** An
incident is filed where it happened, read out of the article. A tally can only
be filed where the paper is, because the articles it counts were mostly about
nothing in particular and were never stored. So `news_articles_read` for
Jakarta means "articles read from Jakarta's two papers", not "articles about
Jakarta" — which is why the reading series are named and described separately
from the incident series rather than sharing an axis with them.

**One outlet may hold a shard for five minutes, and no longer.** A host that
hangs rather than refuses is the expensive failure: every request waits out its
timeout and is retried, and there are two dozen search terms and sixty articles
behind it. `radarlombok.co.id` did exactly this and held a shard for an hour
while six papers behind it in the list went uncollected — which reads
afterwards as a quiet day in six provinces rather than as a crawl that never
got there. Discovery now stops after two minutes and an outlet after five, a
news page is tried twice rather than five times, and whatever was found by the
deadline is what that outlet contributes.

**One outlet may contribute sixty candidates to a run.** A sitemap is an
outlet's whole archive, and many carry no `lastmod` — so an article published
this morning is indistinguishable from one published in 2019 without fetching
it to read its date. Uncapped, a single outlet with a big undated sitemap
spends the whole shard discovering that old articles are old, and lands
nothing. Entries that state a date are ordered ahead of those that do not, so
the cap falls on the oldest rather than on an arbitrary slice. It costs recall
at any outlet publishing more than sixty pieces a week.

**The gate refuses follow-up reporting.** A newspaper covers one brawl half a
dozen times: the incident, the arrests, the charges, the trial, the verdict.
Asked whether an article reports collective violence, a classifier says yes to
all six — each is true of the article — and the dataset would then count one
brawl six times, spread across the months its court case took. So the question
asks whether the article tells the incident itself, and says plainly that
statements, investigations, arrests, trials, verdicts and compensation are not
it. "Police have examined 23 witnesses" scores 0.08 where it scored 0.96
before; a real incident is unmoved at 0.96.

**The classifier chooses; it does not read.** JEV answers only between declared
alternatives, with a calibrated probability per answer. Dates, places and
casualty figures are parsed from the text, because a model that cannot emit a
number should never be asked for one.

**The second reader is called on doubt, and only on doubt.** JEV says how sure
it was of every answer, which makes "the codings it was unsure of" a named
subset rather than a suspicion. Three things put a coding in it: a gate
probability sitting within `NEWS_DEEP_BAND` of the threshold — the article it
could not decide was violence at all — a field it left `TIDAK JELAS`, and a
field it chose with less confidence than `NEWS_DEEP_MIN_CONFIDENCE`. Those go
to DeepSeek with the same vocabularies, and what comes back fills the doubtful
fields only: a confident answer from the cheap reader is never overwritten by
the expensive one, because two readers disagreeing is a fact worth keeping.

Every escalation is on the row, and on the article's own page:
`GET /v1/news/articles/{id}` carries `escalation_reason` (why it was sent),
`deepened` (which fields the second reader supplied) and `matched_categories`
(the dictionary categories that admitted it), beside the `engine` that names
both readers. The list route carries the coding's `escalation` but not the
trail — a page of a hundred articles is not where to answer why one of them was
read twice. The share of rows carrying a reason is the number to watch — a stage that
fires on everything is the cheap classifier deleted and replaced with an
expensive one, and the two thresholds are the dials.

Without `DEEPSEEK_API_KEY` the stage is skipped entirely and nothing else
changes: ambiguous codings land carrying the confidences that say so, and
asking Bronze for the rows with an `escalation_reason` and no `deepened` fields
is the queue of articles worth reading again.

**Escalation is the one column VEWS does not have.** How far an incident got —
tension, a limited beating, violence that spread, a riot — is written in
`news/profiles/violence.py` rather than mined, because there is no human list
to read it off. It is kept because a count of incidents cannot say whether they
are getting worse: a standoff that dispersed and a village burned down are both
one incident. Being machine-only, it is never compared with a human coding that
does not exist.

**Blank is zero, `-99` is unknown.** VEWS leaves a casualty cell empty when
nobody was hurt and writes `-99` when the reporting did not say. Summing them
the same way would either invent casualties or make a quiet province
indistinguishable from an unreported one. The API drops `-99` rather than
serving it, so nothing downstream can chart it.

**Machine rows are never verified.** A human VEWS row carries a `coder_id` and
a `ver_id` — two people who read the report. These carry neither, and
`machine_coded = 1`. They land in their own collection rather than beside the
human ones, so an unverified row can never be cited as VEWS.

**What is archived and what is served differ.** RAW keeps the whole page,
because a coding has to be reproducible from it after the outlet has edited,
paywalled or deleted the article. The API serves a title, a lead, the coded
fields and a link back to the publisher. The words belong to the paper.

## Adding an issue

The crawl knows nothing about violence. A profile — `news/profiles/violence.py`
— declares the search terms, the questions, and the shape of the row that comes
out. Adding economics or politics is adding a profile; discovery, fetching,
landing, extraction and serving need no change, and an article found by the
violence terms can be tagged economic too without being fetched twice.

## Known gaps

**Recall has not been measured.** `terusan news validate 2025-05` is built and
runs, but scoring a month needs that month crawled first, and the archives of
sixty-nine outlets sixteen months back are of unknown reach. Until it has been
run, nobody knows what fraction of incidents this finds. The report separates
per-outlet reach from recall for exactly this reason: an incident missed
because an archive no longer serves that month is not the same failure as one
the classifier read and rejected.

**Papua's regencies resolve to the wrong provinces.** A pre-existing fault in
`reference/geography/indonesia-regencies.csv`, not in this feature: all
forty-two regencies of the six Papua provinces are filed under `ID-91` (Papua
Barat) and `ID-92`, and none under Papua Selatan, Papua Tengah or Papua
Pegunungan, which were created in 2022. The file's own header warns those codes
were recorded from secondary knowledge rather than from a BPS publication.

It is not cosmetic. Following those parents put Nabire, Mimika, Jayawijaya,
Intan Jaya, Yalimo, Puncak, Puncak Jaya, Jayapura and Asmat in Papua Barat —
two-fifths of every coded incident, in a province that holds none of those
districts.

Two things now limit the damage, and neither is a fix:

- A province a report *names* is trusted over the one a regency's parent
  implies. Most reports from the new provinces say "Papua Tengah" or "Papua
  Pegunungan" somewhere, and those now resolve correctly.
- Where the report names none, the province is left **unresolved** rather than
  asserted. That costs a row in the provincial counts; asserting the wrong one
  puts a killing a thousand kilometres away, which nobody reading the figures
  could catch.

The fix is to assign each Papua regency to the province that actually holds it,
against BPS Kode dan Data Wilayah, and then re-cluster. `UNTRUSTED_PARENTS` in
`news/places.py` retires with it.

**The Disway network yields almost nothing.** `jateng.disway.id` lists 324
URLs in its sitemap and not one of the sixty sampled fell inside a three-day
window — they fetch cleanly and are simply old. Either its sitemap carries no
usable `lastmod`, in which case the sixty taken are an arbitrary slice of an
archive, or the recent end is somewhere the four child sitemaps followed do not
reach. The same shape shows on `palpos.disway.id` and
`bengkuluekspress.disway.id`. It needs a Disway-specific listing reader; until
then those three provinces are covered by their second paper alone.

**Two outlets refuse to be read at all** — `pikiran-rakyat.com` and
`kepri.harianhaluan.com` answer every article fetch with 403, through a
headless browser as readily as through plain HTTP. That is a decision by the
publisher, and the right response is to record the outlet as unreadable rather
than to work around it. `terusan news coverage` reports them as `read nothing`,
which the Disway sites share for a different reason.

**Some outlets find nothing**, and `terusan news coverage` reports them as
`found nothing`. That is a gap in this repository's adapters rather than in the
newspaper, and each needs looking at individually. Two causes have been found
and fixed so far:

- The skip list matched its words anywhere in a path, so an outlet serving
  `/news/<slug>/index.html` had every article rejected — the `index` in the
  filename read as the `index` of a section listing. `ajnn.net` was crawled
  daily, found thirty-five pages, and contributed none of them. The words are
  now matched as whole path segments and a trailing index file is ignored.
- An outlet with no sitemap, no feed and no section path had nothing to read
  at all. Discovery now falls back to the front page. It is the weaker source
  — a day's news of every kind, where a crime desk is already narrowed — but
  it is what a newspaper links its day from.
- Guessing at sitemap paths was spending an outlet's goodwill. `korankaltim.com`
  answers 403 to a robot, so each of the nine paths guessed at was retried
  through a headless browser; by the time the crawl asked for the section page
  it wanted, the site had stopped serving the session. The same URL that
  returns a quarter of a megabyte when asked once returned the block page when
  asked tenth. Speculative probes no longer use the browser at all.

Between them these took five of six silent outlets to yielding: `ajnn.net` 0 →
60 articles a day, `jatimpos.co` 0 → 56, `korankaltim.com` 0 → 60,
`rmollampung.id` 0 → 11, `rmolsumut.id` 0 → 10. Only `radarlombok.co.id`
remains, and its origin is down rather than blocking.

**The API reads one parser version at a time.** Re-extracting appends rather
than replaces, so a read without that filter serves every version of every row
at once. It also keeps the rows homogeneous, which matters more than it should:
the DuckDB the Go API embeds resolves a MAP subscript inconsistently across
Parquet files whose key sets differ, and an extractor that learns a new column
makes them differ. The same query returned 67 distinct newspapers in DuckDB
1.5.5 and 113 in the API — each outlet split across the parser versions it had
been read under, appearing several times on the page with a share of its own
figures. Reading one version at a time means one key set at a time.

**Kompas is paywalled.** Expect the lead only on most of its articles, which is
usually enough for the gate and rarely enough for the casualty figures.
