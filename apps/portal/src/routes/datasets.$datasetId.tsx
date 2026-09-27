import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { IconArrowLeft, IconCopy, IconExternalLink } from "@tabler/icons-react";
import { z } from "zod";

import type { ColumnDef } from "@tanstack/react-table";

import { CandlestickChart } from "~/components/candlestick-chart";
import { ClampedText } from "~/components/clamped-text";
import { DataTable, StackedCell } from "~/components/data-table";
import { ChoiceList, FilterChip, summarise } from "~/components/filter-chip";
import { SearchInput } from "~/components/search-input";
import { TableToolbar } from "~/components/table-toolbar";
import { TablePagination } from "~/components/table-pagination";
import { TimeSeriesChart } from "~/components/time-series-chart";
import { PageHeader } from "~/components/page-header";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { BELOW_STICKY_HEADER, StickyHeader } from "~/components/sticky-header";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { api, type Indicator } from "~/lib/api";
import { describeSchedule } from "~/lib/cron";
import { formatCount, formatDate, formatRelative } from "~/lib/format";
import { hasIntradayRange, ohlcSet, toCandles } from "~/lib/candles";
import { TagList } from "~/components/tag-list";
import { datasetLabel, indicatorLabel, titleFromId } from "~/lib/labels";
import { toggle } from "~/lib/multi";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";

const searchSchema = z.object({
  // Which series the reader is looking for. In the URL, like every other
  // narrowing here, so a dataset filtered down to one release is a link.
  q: textParam,
  // The two things that differ between series of one collection. A dataset is
  // a single subject measured several ways, so "the monthly ones" and "the
  // ones in rupiah billions" are how a reader cuts two hundred of them down —
  // the source and the tags they would use on the indicators list are the same
  // for every row here.
  frequency: listParam,
  unit: listParam,
  page: z.number().int().min(0).optional(),
});

/** Rows per page in the series table. */
const PAGE_SIZE = 25;

/**
 * The series of one dataset, as a table.
 *
 * The same shape as the indicators table, minus the columns that only make
 * sense across datasets: every row here shares this dataset's source, so a
 * source column would repeat one value six hundred times.
 */
const seriesColumns: ColumnDef<Indicator>[] = [
  {
    accessorKey: "indicator_id",
    header: "Series",
    cell: ({ row }) => (
      <Link
        to="/indicators/$indicatorId"
        params={{ indicatorId: row.original.indicator_id }}
        className="underline-offset-4 hover:underline"
      >
        <div className="leading-tight">
          <ClampedText className="max-w-[26rem] font-medium">
            {indicatorLabel(row.original)}
          </ClampedText>
          {row.original.code ? (
            <div className="text-xs text-muted-foreground">{row.original.code}</div>
          ) : null}
        </div>
      </Link>
    ),
  },
  {
    accessorKey: "temporal_resolution",
    header: "Frequency",
    cell: ({ row }) => (
      <Badge variant="secondary">{row.original.temporal_resolution}</Badge>
    ),
  },
  {
    accessorKey: "unit",
    header: "Unit",
    cell: ({ row }) =>
      row.original.unit ? (
        <ClampedText className="max-w-[11rem]">{row.original.unit}</ClampedText>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
  {
    id: "coverage",
    header: "Coverage",
    accessorFn: (row) => row.period_start,
    cell: ({ row }) => (
      <StackedCell
        primary={`${row.original.period_start}–${row.original.period_end}`}
        secondary={`${formatCount(row.original.observations)} figures`}
      />
    ),
  },
];

export const Route = createFileRoute("/datasets/$datasetId")({
  validateSearch: searchSchema,
  component: DatasetDetail,
});

/**
 * How many sessions the candle chart shows.
 *
 * Six months of trading. Wide enough to read a trend, narrow enough that a
 * single day is still a mark you can point at.
 */
const SESSIONS = 120;

/**
 * One collection, and the series inside it.
 *
 * The page a reader lands on when they know the publication but not yet which
 * of its numbers they want — "the consumer survey" rather than
 * `consumer_confidence_index`. It answers who publishes it, under what terms,
 * how often, and what is in it; each series then has its own page for the
 * figures themselves.
 */
function DatasetDetail() {
  const { datasetId } = Route.useParams();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });

  const dataset = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => api.dataset(datasetId),
  });
  // This collection's series, with the coverage and frequency the list
  // already computes. Asked for by dataset rather than picked out of every
  // series in the warehouse, which runs to tens of thousands of rows; the
  // largest collections hold a few thousand, well inside one page.
  const indicators = useQuery({
    queryKey: ["indicators", { dataset: datasetId, limit: 10000 }],
    queryFn: () => api.indicators({ dataset: datasetId, limit: 10000 }),
  });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.sources() });
  // How much material this collection was built from. Asked for one row
  // rather than the list itself: the number is all this page shows, and the
  // documents page is where they are read.
  const documents = useQuery({
    queryKey: ["dataset-documents", datasetId],
    queryFn: () => api.documents({ dataset_id: [datasetId], limit: 1 }),
  });

  const meta = dataset.data?.data;
  const documentCount = documents.data?.meta?.total ?? 0;
  const source = sources.data?.data?.find(
    (entry) => entry.source_id === meta?.source_id,
  );
  const series = indicators.data?.data ?? [];

  // Narrowed in place: the dataset's series are already in hand, and six
  // hundred of them is a list nobody scrolls. The publisher's code is matched
  // as well as the name — someone arriving from FRED has NASDAQNQID55LMN in
  // hand, not a title.
  const needle = asText(search.q)?.toLowerCase() ?? "";
  const frequencies = asTextList(search.frequency);
  const units = asTextList(search.unit);
  const shown = series.filter(
    (indicator) =>
      (!frequencies.length || frequencies.includes(indicator.temporal_resolution)) &&
      // A series with no unit is excluded by a unit filter rather than kept:
      // asking for rupiah billions is asking for the ones measured that way,
      // and an unmeasured one is not among them.
      (!units.length || (indicator.unit ? units.includes(indicator.unit) : false)) &&
      (!needle ||
        indicatorLabel(indicator).toLowerCase().includes(needle) ||
        (indicator.code?.toLowerCase().includes(needle) ?? false) ||
        indicator.indicator_id.toLowerCase().includes(needle)),
  );

  // Offered from this dataset's own series, not the warehouse's: a unit no
  // series here carries would filter the table down to nothing, which is a
  // control that can only be used wrongly.
  const allFrequencies = [...new Set(series.map((i) => i.temporal_resolution))].sort();
  const allUnits = [
    ...new Set(series.map((i) => i.unit).filter(Boolean)),
  ].sort() as string[];
  // Both chips are shown whether or not this collection varies along them. A
  // control that appears on one dataset and not the next makes the reader look
  // for it; one that is always in the same place, and says "Monthly" is the
  // only frequency on offer, has told them something about the data.

  const loading = dataset.isLoading || indicators.isLoading;
  // Paged in place: the series are already in hand, so a page is a slice rather
  // than a round trip. A page past the end of a freshly narrowed list would
  // render empty, so the requested page is clamped rather than trusted.
  const pageCount = Math.max(1, Math.ceil(shown.length / PAGE_SIZE));
  const page = Math.min(search.page ?? 0, pageCount - 1);
  const paged = shown.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  // A dataset carrying open, high, low and close is one a candle can be read
  // from. Detected rather than declared: nothing in the warehouse marks a
  // series as a price, and four series named this way are what a candle is.
  //
  // Read off the series rather than off the dataset's identifiers: what a
  // published identifier says is `ml03my8u`, and the open is only recognisable
  // by the key it was declared under (program.md §10).
  const ohlc = ohlcSet(series);
  const bars = useQuery({
    queryKey: ["candles", ohlc?.prefix],
    enabled: Boolean(ohlc),
    queryFn: () =>
      api.observations({
        indicator: (ohlc as NonNullable<typeof ohlc>).ids,
        // Newest first, so the limit takes the most recent sessions rather
        // than the oldest. `toCandles` sorts them back into order.
        order: "-period",
        // A few sessions of slack: the row limit can fall part-way through a
        // period, leaving that day with two of its four figures. The extra is
        // trimmed by the slice below, so the window is always whole bars.
        limit: 4 * (SESSIONS + 8),
      }),
  });

  // The most recent sessions, newest last. Five years is twelve hundred bars
  // and a bar would be under a pixel wide — unreadable, and an unreadable chart
  // is worse than a shorter one. The Data tab holds the whole series.
  const candles = ohlc ? toCandles(bars.data?.data ?? [], ohlc).slice(-SESSIONS) : [];
  const closed = candles.filter((candle) => candle.open === null).length;
  // The form follows the data: a contract that settles rather than trades has
  // no range to draw, so its closes are a line.
  const tradeable = hasIntradayRange(candles);

  if (dataset.isError) {
    return (
      <div className="space-y-4">
        <BackLink />
        <p className="rounded-sm border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
          {(dataset.error as Error).message}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <StickyHeader
        heading={
          <div className="space-y-2">
            <BackLink />
            <PageHeader
              // The catalogue's title, not the identifier: the identifier is a
              // derived code and reads as nothing (program.md §10).
              title={meta ? datasetLabel(meta) : titleFromId(datasetId)}
              description={
                meta
                  ? [
                      meta.description,
                      `${formatCount(meta.observations)} figures in ${formatCount(meta.indicators.length)} series, covering ${meta.period_start} to ${meta.period_end}, published by ${meta.organization ?? meta.source_id}.`,
                    ]
                      .filter(Boolean)
                      .join(" ")
                  : undefined
              }
              actions={
                <RowActions
                  label="Dataset actions"
                  actions={[
                    {
                      label: "Copy dataset id",
                      icon: IconCopy,
                      onSelect: () => void copyToClipboard(datasetId),
                    },
                    {
                      label: "Copy ingest command",
                      icon: IconCopy,
                      onSelect: meta
                        ? () =>
                            void copyToClipboard(
                              `cd pipelines && uv run terusan sources run ${meta.source_id}`,
                            )
                        : undefined,
                      hint: meta ? undefined : "Still loading",
                    },
                    {
                      label: "Open the publisher",
                      icon: IconExternalLink,
                      onSelect: source?.base_url
                        ? () => window.open(source.base_url, "_blank", "noopener")
                        : undefined,
                      hint: source?.base_url
                        ? undefined
                        : "No public URL is recorded for this source",
                    },
                  ]}
                />
              }
            />
          </div>
        }
      />

      <div className="grid gap-8 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <aside className={`lg:sticky lg:self-start ${BELOW_STICKY_HEADER}`}>
          <h2 className="font-heading text-sm font-semibold tracking-tight">About</h2>
          <dl className="mt-3 space-y-3">
            <Fact label="Publisher" value={meta?.organization} />
            <Fact label="Source" value={source?.name ?? meta?.source_id} />
            <Fact
              label="Refresh"
              value={describeSchedule(source?.schedule) ?? "Manual only"}
              hint={source?.schedule}
            />
            <Fact label="Cadence" value={source?.update_frequency} />
            {/* Said here rather than left to be inferred from an empty chart: a
                source that has stopped is a fact about the data, not a fault. */}
            <Fact
              label="Collection"
              value={source ? (source.active ? "Scheduled" : "Paused") : undefined}
              hint={source && !source.active ? "no longer collected" : undefined}
            />
            <Fact
              label="Licence"
              value={meta?.license ?? source?.license ?? "Not recorded"}
            />
            <Fact
              label="Coverage"
              value={meta ? `${meta.period_start} – ${meta.period_end}` : undefined}
            />
            <Fact
              label="Figures"
              value={meta ? formatCount(meta.observations) : undefined}
            />
            <Fact
              label="Last updated"
              value={meta ? formatDate(meta.last_updated) : undefined}
              hint={meta ? formatRelative(meta.last_updated) : undefined}
            />
            {/* The material this collection was built from. A count and a
                link rather than a list: a collection can be hundreds of
                retrievals, and the documents page is already the place that
                lists and filters them. */}
            {documentCount ? (
              <div>
                <dt className="text-xs font-medium text-muted-foreground">
                  Source documents
                </dt>
                <dd className="mt-0.5 text-sm">
                  <Link
                    to="/documents"
                    search={{ dataset_id: [datasetId] }}
                    className="underline underline-offset-4 hover:text-foreground"
                  >
                    {formatCount(documentCount)}
                    {documentCount === 1 ? " document" : " documents"}
                  </Link>
                </dd>
              </div>
            ) : null}
            {/* The words this collection is found by. Clickable, back to the
                list filtered by the one clicked: a tag a reader cannot act on
                is decoration. */}
            {meta?.tags?.length ? (
              <div>
                <dt className="text-xs font-medium text-muted-foreground">Tags</dt>
                <dd className="mt-1">
                  <TagList
                    tags={meta.tags}
                    max={meta.tags.length}
                    onSelect={(tag) =>
                      void navigate({ to: "/datasets", search: { tag: [tag] } })
                    }
                  />
                </dd>
              </div>
            ) : null}
          </dl>
        </aside>

        <div className="min-w-0 space-y-8">
          {ohlc ? (
            <section className="space-y-3">
              <h2 className="font-heading text-lg font-semibold tracking-tight">
                Price
              </h2>
              <p className="max-w-2xl text-sm text-muted-foreground">
                The last {formatCount(SESSIONS)} sessions.{" "}
                {tradeable ? (
                  <>
                    Each bar is one trading day: the wick spans the low to the high, the
                    body spans the open to the close.
                  </>
                ) : (
                  <>
                    Drawn as a line, not candles: this contract settles once a day
                    rather than trading, so its open, high, low and close are one figure
                    repeated and there is no range for a bar to show.
                  </>
                )}{" "}
                Five years are held — the whole series is on each indicator&rsquo;s own
                page.
              </p>
              {bars.isLoading || indicators.isLoading ? (
                <Skeleton className="h-[340px] w-full rounded-sm" />
              ) : tradeable ? (
                <CandlestickChart
                  candles={candles}
                  unit={series[0]?.unit}
                  caption={
                    candles.length
                      ? [
                          `${candles[0]?.label} to ${candles[candles.length - 1]?.label}.`,
                          // Yahoo lists a market holiday as a dated row with no
                          // prices. The gap is the exchange being shut, not a
                          // figure we failed to collect, and saying so stops it
                          // reading as a hole in the data.
                          closed
                            ? `${formatCount(closed)} of these sessions have no prices — the exchange was closed.`
                            : null,
                        ]
                          .filter(Boolean)
                          .join(" ")
                      : undefined
                  }
                />
              ) : (
                <TimeSeriesChart
                  series={[
                    {
                      name: "Close",
                      points: candles.map((candle) => ({
                        label: candle.label,
                        value: candle.close,
                        status: candle.close === null ? "no price" : undefined,
                      })),
                    },
                  ]}
                  unit={series[0]?.unit}
                  caption={
                    candles.length
                      ? `${candles[0]?.label} to ${candles[candles.length - 1]?.label}.${
                          closed
                            ? ` ${formatCount(closed)} of these sessions carry no price.`
                            : ""
                        }`
                      : undefined
                  }
                />
              )}
            </section>
          ) : null}

          <section className="space-y-3">
            <div className="space-y-1">
              <h2 className="font-heading text-lg font-semibold tracking-tight">
                The series in it
              </h2>
              <p className="max-w-2xl text-sm text-muted-foreground">
                A dataset is what the agency releases; these are the things it measures.
                Each carries its own unit and frequency, so they are listed rather than
                summed.
              </p>
            </div>

            {/* Directly above the rows they narrow, rather than beside the
                heading: what a control does to a table is read from how close
                it sits to it. On every collection, not only the long ones — a
                reader who learned where these sit on one dataset should not
                have to look for them on the next. Withheld only where there is
                no table under them to narrow. */}
            {loading || series.length ? (
              <TableToolbar
                filters={
                  <>
                    <FilterChip
                      label="Frequency"
                      value={summarise(frequencies)}
                      onClear={() =>
                        navigate({
                          search: (prev) => ({
                            ...prev,
                            frequency: undefined,
                            page: 0,
                          }),
                        })
                      }
                    >
                      <ChoiceList
                        options={allFrequencies.map((value) => ({
                          value,
                          label: value,
                        }))}
                        selected={frequencies}
                        onToggle={(value) =>
                          navigate({
                            search: (prev) => ({
                              ...prev,
                              frequency: toggle(frequencies, value),
                              page: 0,
                            }),
                          })
                        }
                        onClear={() =>
                          navigate({
                            search: (prev) => ({
                              ...prev,
                              frequency: undefined,
                              page: 0,
                            }),
                          })
                        }
                      />
                    </FilterChip>

                    <FilterChip
                      label="Unit"
                      value={summarise(units)}
                      onClear={() =>
                        navigate({
                          search: (prev) => ({ ...prev, unit: undefined, page: 0 }),
                        })
                      }
                    >
                      <ChoiceList
                        options={allUnits.map((value) => ({ value, label: value }))}
                        selected={units}
                        searchPlaceholder="Search units"
                        onToggle={(value) =>
                          navigate({
                            search: (prev) => ({
                              ...prev,
                              unit: toggle(units, value),
                              page: 0,
                            }),
                          })
                        }
                        onClear={() =>
                          navigate({
                            search: (prev) => ({
                              ...prev,
                              unit: undefined,
                              page: 0,
                            }),
                          })
                        }
                      />
                    </FilterChip>

                    {frequencies.length || units.length || search.q ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8"
                        onClick={() => navigate({ search: {} })}
                      >
                        Clear all
                      </Button>
                    ) : null}
                  </>
                }
                search={
                  <SearchInput
                    value={asText(search.q)}
                    placeholder="Search series"
                    onSearch={(q) =>
                      navigate({ search: (prev) => ({ ...prev, q, page: 0 }) })
                    }
                  />
                }
              />
            ) : null}

            {!loading && !series.length ? (
              <p className="rounded-sm border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
                No series are published from this dataset yet. Its documents may be in
                Bronze without a normalization mapping.
              </p>
            ) : (
              <div className="space-y-4">
                <DataTable
                  columns={seriesColumns}
                  data={paged}
                  isLoading={loading}
                  loadingRows={8}
                  emptyMessage={
                    needle && !frequencies.length && !units.length
                      ? `No series here match “${asText(search.q) ?? ""}”.`
                      : "No series here match these filters."
                  }
                />

                <TablePagination
                  page={page}
                  total={shown.length}
                  pageSize={PAGE_SIZE}
                  onPage={(next) =>
                    navigate({ search: (prev) => ({ ...prev, page: next }) })
                  }
                  summary={
                    shown.length ? (
                      <>
                        {formatCount(page * PAGE_SIZE + 1)}–
                        {formatCount(Math.min((page + 1) * PAGE_SIZE, shown.length))} of{" "}
                        {formatCount(shown.length)}
                        {shown.length !== series.length
                          ? ` filtered from ${formatCount(series.length)}`
                          : null}
                      </>
                    ) : null
                  }
                />
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/datasets"
      className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
    >
      <IconArrowLeft className="size-4" />
      All datasets
    </Link>
  );
}

function Fact({
  label,
  value,
  hint,
}: {
  label: string;
  value?: string;
  hint?: string;
}) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm">
        {value ?? <span className="text-muted-foreground">—</span>}
        {hint ? (
          <span className="ml-1.5 text-xs text-muted-foreground">{hint}</span>
        ) : null}
      </dd>
    </div>
  );
}
