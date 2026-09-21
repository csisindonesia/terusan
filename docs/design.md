# Designing a page

The portal has one job: let someone read a figure and know where it came from.
Every pattern here follows from that, and most of them are the same pattern —
**say what is true, say what is missing, and never print something that looks
like data and is not.**

This describes the two page shapes the portal already has: a **table page**
(`/observations`, `/indicators`, `/datasets`, `/geography`) and a **detail
page** (`/indicators/$indicatorId`, `/datasets/$datasetId`). Build the next one
out of these before inventing a third.

The warehouse nests **source → dataset → indicator → observation**. A *source*
is who publishes; a *dataset* is what they release (Bank Indonesia's consumer
survey); an *indicator* is one thing it measures; an *observation* is one
figure. Datasets and indicators each get a list and a detail page, and each
detail page links to the other — a reader arriving at a series should be one
click from the rest of its release. `/v1/storage` lists the physical lake
tables, which is an operational view and deliberately not called a dataset.

Components live in
[`apps/portal/src/components/`](../apps/portal/src/components/), the logic they
share in [`apps/portal/src/lib/`](../apps/portal/src/lib/).

---

## The rules that outrank the layout

**Never invent a value.** A missing figure renders as its status —
`Not collected`, `Suppressed` — never as a blank or a zero; a blank cell cannot
tell "never collected" from "collected and zero", and they are different facts.
Where the warehouse does not record something, the page says so: the Settings
tab prints *"Not configured"* for access control rather than guessing, and the
API tab lists the filters it cannot express instead of handing over a request
that quietly returns more rows than the table showed.

**Figures are strings.** `value` arrives as a decimal string and stays one until
something formats it. Parsing to a JS number reintroduces the float drift the
decimal storage exists to prevent — 1,571,092,130,385,400 rupiah does not
survive a double. Format with [`lib/format.ts`](../apps/portal/src/lib/format.ts);
export the stored string, not the rendering.

**The URL is the state.** Filters, page, sort, open tab, muted series — all of
it lives in the query string, because a filtered view is a link someone can
send, and for a research portal that is most of the point. Parse with
[`lib/search-params.ts`](../apps/portal/src/lib/search-params.ts): the router
parses `?q=199` as a *number*, so params are typed `string | number` and
converted where a string is needed. Coercing in the schema instead makes the
router rewrite the URL to `q="199"`, quotes and all.

**Identity and name are different things.** `apbd_expenditure_realisasi` is a
key — stable, URL-safe, what the API takes as a parameter.
"APBD Expenditure Realisasi" is what a reader recognises.
[`lib/labels.ts`](../apps/portal/src/lib/labels.ts) derives the second from the
first; the key still appears wherever it is quotable (the API tab, the export).

---

## The page shell

Set in [`routes/__root.tsx`](../apps/portal/src/routes/__root.tsx).

Content is **measured, not full-bleed** — `max-w-7xl`, centred. A table stretched
across a wide monitor puts a row's first cell and its last a screen apart and
the eye loses the row between them. The breadcrumb bar shares the same width
constant so it lines up with the page title beneath it.

Every page opens with a
[`StickyHeader`](../apps/portal/src/components/sticky-header.tsx):

```tsx
<StickyHeader
  heading={<PageHeader title="Observations" count={total} description="…" />}
  filters={<TableToolbar filters={…} search={…} />}
/>
```

Title and filters ride together and stay put while the rest scrolls. Ten screens
into a table the reader has lost sight of which filters are on, and **a figure
read under a filter nobody can see is a figure read wrong.** The component draws
the rule between the two rows and the one beneath itself, so pages do not repeat
them.

It publishes its own measured height as `--page-header` on the document root.
Anything else that sticks — the metadata column on a detail page — offsets from
that with `BELOW_STICKY_HEADER`. Do not hardcode the offset: the chips wrap onto
a second row on a narrow window and the header grows.

A section's description goes **below** its title.

---

## Table pages

```
StickyHeader   title · count · actions
               ─────────────────────────
               chips ………………………… search
─────────────────────────────────────────
description
DataTable
TablePagination
```

### Filters

One row: [`TableToolbar`](../apps/portal/src/components/table-toolbar.tsx) puts
chips left and search right. They answer the same question — *which rows do I
want* — so splitting them across two lines makes the reader look in two places.

Each filter is a [`FilterChip`](../apps/portal/src/components/filter-chip.tsx)
holding a `ChoiceList`. Three rules:

- **A chip appears only where it offers a choice.** One option cannot narrow
  anything, and a row of inert controls is harder to read than a short one. A
  national series shows no Place chip; a series inside one year shows no Year
  chip.
- **Multi-select by default.** These questions are naturally plural — countries
  *and* provinces, annual *and* monthly. `summarise()` renders the chip label as
  `Beras +2`; naming every value makes the chip wider than the table.
- **Past five options the list gets a search box.** Thirty-one commodities do not
  fit on screen, so the ones past the fold are invisible rather than merely slow
  to find.

Search is committed on submit, not per keystroke: each change is a query and a
history entry, and a five-letter word should not cost five requests.

For a bounded stretch of time use
[`PeriodFilter`](../apps/portal/src/components/period-filter.tsx) — typed, not a
date picker, because the warehouse holds annual, quarterly, monthly and daily
series and a calendar would demand a day from someone reading a series that has
none. A range and a set of years are different questions and both get asked, so
both chips can sit side by side.

### The table

[`DataTable`](../apps/portal/src/components/data-table.tsx).

- **Gutters of 16px** on the first and last cell — not padding on the container,
  so the header stripe and row rules still run edge to edge while the content
  keeps clear of the border.
- **Separators between header cells**, not sort icons on every column.
- **`StackedCell`** for a value with its qualifier: the place over its
  identifier, the figure over its unit, the period over its bounds.
- **Numerics right-aligned and `tabular-nums`**, so digits line up down the
  column.
- **Selection is keyed on the row's own id** (`getRowId`), never the index, so a
  selection survives sorting and a re-fetch.
- **Row actions are a three-dot menu** ([`RowActions`](../apps/portal/src/components/row-actions.tsx)).
  An action that cannot be offered is listed as *unavailable with a reason*
  rather than hidden — "The serving layer is read-only — run it from a terminal"
  tells the reader more than an absent button.
- **Export takes what is showing**, and says which: `Export page` versus
  `Export selection`.

### Pagination

[`TablePagination`](../apps/portal/src/components/table-pagination.tsx), reading
its slots from [`lib/pagination.ts`](../apps/portal/src/lib/pagination.ts):

```
«  ‹  1  2  …  7  8  ›  »
```

The window is a fixed width whichever page you are on, so the control does not
reflow under the pointer. The summary beside it reads
`1–25 of 186 filtered from 2,988` — the reader needs the narrowed count *and*
what it was narrowed from.

---

## The detail page

[`routes/indicators.$indicatorId.tsx`](../apps/portal/src/routes/indicators.$indicatorId.tsx).
Filters live **above** the tabs, because they govern all of them: they narrow
the chart on one, the table on another, and they shape the request shown on a
third. Inside the data tab they would hide the reason the other two changed.

| Tab | Holds |
|---|---|
| **Metadata** | The facts list, the analytics tiles, the chart |
| **Data** | The figures table, with the row count on the tab |
| **API** | How to fetch this without the portal |
| **Settings** | How the series is collected and kept |

### Dimensions

An indicator is rarely one line.
[`lib/series.ts`](../apps/portal/src/lib/series.ts) works out what it varies
along — `geography`, `commodity`, or `none` — and the page adapts: the column
heads itself "Place" or "Commodity" and disappears entirely for a national
series rather than printing a column of em-dashes.

**An unresolved dimension is still a dimension.** Seventeen of Bank Indonesia's
eighteen survey cities are not in the geography registry, so their identifier is
null and the printed name is all that distinguishes them. Anything that groups,
filters or searches falls back to that name; blanking it reported eighteen
cities as one unnamed place.

### Analytics

Six tiles. They describe **one** series: averaged across rice and chilli they
would describe nothing, so when several are in view they step aside and point at
the filter.

A figure too long for its tile is shown compactly with the exact one on hover.
`1,571,092,130,385,400` clipped to `1,571,092,130,38` is a truncated number that
still looks like a number, which is the worst way for this to fail.

### The chart

[`TimeSeriesChart`](../apps/portal/src/components/time-series-chart.tsx). Read
the `dataviz` skill for the method; what this codebase settles:

- **Colours come from the validated categorical order, assigned by position and
  never cycled.** Run the validator rather than eyeballing it.
- **Colour follows the entity, not its rank.** A series keeps the slot it was
  listed in whether or not it is drawn — muting one must not repaint the
  survivors, because the reader is comparing lines across clicks.
- **Eight lines maximum.** A ninth would have to repeat a hue, so the chart
  refuses and the caption says so: *"Showing 8 of 31 commodity series."*
- **A legend whenever there is more than one line** — identity is never carried
  by colour alone — and the legend is a *control*: click to mute, click again to
  restore, muted entries staying listed or there would be no way back. It writes
  to the URL, and the table follows it.
- **The line breaks at a gap.** Drawing through a missing period asserts a value
  nobody recorded.
- **Never past zero** on an axis for a quantity that cannot be negative: a GDP
  axis labelled −109B invites the reader to believe otherwise.
- Gutters wide enough that labels and the end of the line keep clear of the
  card border — inside the SVG, not on the container, or the tooltip's
  percentage positioning drifts off the point it labels.

### The API tab

Shows the request that returns **what is on screen**, rewritten live as filters
change, in curl, Python, R, JavaScript and DuckDB. Every example converts the
value explicitly and says what that costs.

The response sample is **a real row from the series being viewed**. A fabricated
one once showed a Jakarta `geo_name` on a national budget series — exactly the
confusion the tab exists to prevent.

### The Settings tab

Reads the source registry, published into the lake by
`terusan silver dimensions` and served from `/v1/sources`. Cron is rendered in
words — `0 7 * * *` → *"07:00 every day"* — with the raw expression beside it,
and anything outside the handful of known shapes is handed back as written: a
schedule described wrongly is worse than one left in its own notation.

Nothing here is editable. The serving layer is read-only by design, and a switch
that looked like it saved and did not would be worse than no switch.

---

## Checking the work

Typecheck and lint catch neither of the two things that actually break these
pages: a layout that reflows and a value that renders wrong.

- `make smoke` asks whether the client JS loads and the table rendered rows
  rather than skeletons. It exists because the portal once rendered perfectly
  server-side and then sat there — every other check passed.
- **Open it and look.** The clipped analytics tiles, the chart running into its
  card border, the `Places 2` beside a description saying eighteen: none of
  those show up in a test. Measure the thing you changed — computed style,
  bounding boxes, contrast ratio — rather than judging it from a screenshot.
