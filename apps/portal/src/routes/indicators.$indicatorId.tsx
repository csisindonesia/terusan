import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import {
  IconArrowLeft,
  IconCopy,
  IconDownload,
  IconExternalLink,
  IconPlayerPlay,
  IconRefresh,
} from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import { z } from "zod";

import { ClampedText } from "~/components/clamped-text";
import { CollectButton } from "~/components/collect-button";
import { DataTable, StackedCell } from "~/components/data-table";
import {
  ChoiceList,
  FilterChip,
  summarise as summariseChip,
} from "~/components/filter-chip";
import { PeriodFilter } from "~/components/period-filter";
import { SearchInput } from "~/components/search-input";
import { TablePagination } from "~/components/table-pagination";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { TableToolbar } from "~/components/table-toolbar";
import { BELOW_STICKY_HEADER, StickyHeader } from "~/components/sticky-header";
import {
  MAX_SERIES,
  TimeSeriesChart,
  type Point,
  type Series as Line,
} from "~/components/time-series-chart";
import { TagList } from "~/components/tag-list";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent } from "~/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { Skeleton } from "~/components/ui/skeleton";
import { summarise, gapsByStatus, type Figure } from "~/lib/analytics";
import {
  commodityOf,
  dimensionLabel,
  dimensionNoun,
  dimensionOfMembers,
  geoOf,
  primaryAxis,
  type Dimension,
  type Series,
} from "~/lib/series";
import { toggle } from "~/lib/multi";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";
import {
  api,
  apiBaseUrl,
  type Facet,
  type Indicator,
  type Observation,
  type ObservationQuery,
  type PipelineRun,
  type SeriesLine,
  type SeriesPoint,
  type Source,
} from "~/lib/api";
import { describeSchedule } from "~/lib/cron";
import { useIngestion, type Ingestion } from "~/lib/ingestion";
import { downloadCsv, toCsv } from "~/lib/csv";
import { datasetLabel, indicatorLabel, titleFromId } from "~/lib/labels";
import {
  formatCompact,
  formatCount,
  formatDate,
  formatDecimal,
  formatDuration,
  formatMoment,
  formatPercent,
  formatRelative,
  statusLabel,
} from "~/lib/format";

const searchSchema = z.object({
  status: listParam,
  // Which members of the indicator's dimension are on screen — cities, or
  // commodities. In the URL so a filtered view is a link someone can send.
  // Empty means all of them: the page opens showing everything it has, and
  // narrowing is something the reader chooses rather than has to do first.
  series: listParam,
  // Commodities the reader has narrowed to, where the indicator varies by
  // place *and* commodity and `series` is therefore the place. Its own
  // parameter rather than a second pass over `series`, so the two narrow
  // independently: shallots in every province, or every food in one.
  commodity: listParam,
  // Years the reader has narrowed to. Multi-select rather than a range: the
  // question here is usually "these two years" rather than "everything since",
  // and the observations page already offers a range for the other case.
  year: listParam,
  // Series muted from the chart's legend. Separate from `series`, which is the
  // chip's "show me only these": with thirty-one commodities, muting one would
  // otherwise mean listing the other thirty in the URL.
  hide: listParam,
  // A bounded stretch of time, as canonical period labels. The router parses
  // `?from=2020` as a number, so both shapes are accepted and converted where
  // a string is needed.
  from: z.union([z.string(), z.number()]).optional(),
  until: z.union([z.string(), z.number()]).optional(),
  // Which tab is open, so a link can point at the API notes or the figures
  // rather than always landing on the summary.
  tab: z.enum(["metadata", "data", "api", "logs", "settings"]).optional(),
  q: textParam,
  // How the figures table is ordered. In the URL like everything else here, so
  // a table someone sorted is a link they can send.
  //
  // The values are the table's own column ids, because the header hands those
  // back when it is clicked: an enum of its own would have to be translated in
  // both directions, and the direction that was missing left the `Place` header
  // writing a `sort` the route then refused to parse.
  sort: z.enum(["period", "bounds", "geography", "commodity", "value"]).optional(),
  dir: z.enum(["asc", "desc"]).optional(),
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/indicators/$indicatorId")({
  validateSearch: searchSchema,
  component: IndicatorDetail,
});

/** Rows per page in the figures table. The chart always shows the whole series. */
const PAGE_SIZE = 25;

/**
 * How the figures table opens: by period, newest first.
 *
 * Period rather than value, because a table of figures is read as a series
 * before it is read as a ranking, and the reader who wants the largest can
 * click the column.
 */
const DEFAULT_SORT = "period" as const;

/**
 * The column the table sorts on, as an order the API takes.
 *
 * `bounds` prints the dates a period label stands for, so it sorts the same
 * way the label does. The dimension columns sort on the printed name — see
 * `observationSorts` in the API, which orders the other dimension second so a
 * table sorted by place still keeps each place's commodities together.
 */
function orderFor(column: SortColumn, descending: boolean): string {
  const key = {
    period: "period",
    bounds: "period",
    geography: "place",
    commodity: "commodity",
    value: "value",
  }[column];
  return `${descending ? "-" : ""}${key}`;
}

type SortColumn = "period" | "bounds" | "geography" | "commodity" | "value";

/**
 * How many rows an export fetches.
 *
 * A CSV is a file someone opens in a spreadsheet, and the series behind this
 * page can be millions of rows. The cap is stated on the button rather than
 * silently applied, and a reader who wants the whole of a long series has the
 * API request on the tab beside it.
 */
const EXPORT_LIMIT = 5000;

type TabName = "metadata" | "data" | "api" | "logs" | "settings";

/**
 * The cursors the table has been handed, kept for as long as the sort and the
 * filters they belong to.
 *
 * A cursor is a bookmark for the page after one already seen, so it accumulates
 * as the reader pages forward and is worthless the moment the order changes —
 * a boundary row in one sort is in the middle of another. Held in a ref rather
 * than in the URL: it is an optimisation, and a link that carried one would
 * still have to work for a reader who opens it on page twelve.
 */
function useCursors(signature: unknown[]) {
  const held = useRef(new Map<number, string>());
  const key = JSON.stringify(signature);
  const last = useRef(key);
  if (last.current !== key) {
    held.current = new Map();
    last.current = key;
  }
  return {
    at: (page: number) => (page === 0 ? undefined : held.current.get(page)),
    remember: (page: number, cursor: string) => {
      held.current.set(page, cursor);
    },
  };
}

/**
 * A period label as the URL should carry it.
 *
 * A bare year is written as a number, because the router parses `?from=2015`
 * back as one — storing the string would make the two disagree and the router
 * would rewrite the URL to `from="2015"`, quotes and all. Anything with a month
 * or a quarter in it is a string either way.
 */
function asPeriodParam(value: string): string | number | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  return /^\d+$/.test(trimmed) ? Number(trimmed) : trimmed;
}

/** What the API tab shows as the example request. */
type ApiQuery = { key: string; value: string }[];

function columnsFor(dimension: Dimension): ColumnDef<Observation>[] {
  const period: ColumnDef<Observation> = {
    accessorKey: "period",
    header: "Period",
    // Wide enough for the longest label a period takes — `2026-09-17` — and no
    // wider: this column holds one short string per row.
    meta: { width: "w-32" },
    cell: ({ row }) => <span className="font-medium">{row.original.period}</span>,
  };

  // The bounds the label stands for, in a column of their own. Both are
  // inclusive, and they are what makes a period comparable: `1992` cannot be
  // filtered or joined on, 1992-01-01 to 1992-12-31 can. Under the label they
  // read as a second fact about the row rather than as the same one written
  // out.
  const bounds: ColumnDef<Observation> = {
    id: "bounds",
    accessorFn: (row) => row.period_start,
    header: "Covers",
    // Two ISO dates and a dash, in tabular figures, which is a fixed width.
    meta: { width: "w-52" },
    cell: ({ row }) => (
      <span className="tabular-nums text-muted-foreground">
        {row.original.period_start} – {row.original.period_end}
      </span>
    ),
  };

  // One column per dimension the indicator varies along, so a row says what it
  // is about. A series priced across places *and* commodities needs both: with
  // only one, every row on the page reads alike and the reader is left asking
  // which commodity they are looking at.
  //
  // A national series has no dimension column at all: a column of em-dashes
  // asks the reader to check, every row, whether they have missed something.
  const memberColumn = (which: "geography" | "commodity"): ColumnDef<Observation> => ({
    id: which,
    // An accessor as well as a cell: without one the column is a display
    // column, and a display column offers no sort control.
    accessorFn: (row) => (which === "geography" ? geoOf(row) : commodityOf(row)) ?? "",
    header: dimensionLabel(which),
    // Room for the longest member names that turn up — "Nusa Tenggara Timur",
    // "Rice — medium grade I" — and no more. Sized like the rest rather than
    // left to take the slack: on a national series this column holds the word
    // "Indonesia" on every row, and unsized it took a third of the table.
    meta: { width: "w-56" },
    cell: ({ row }) => (
      <StackedCell
        primary={
          (which === "geography" ? geoOf(row.original) : commodityOf(row.original)) ??
          "—"
        }
        secondary={
          which === "geography"
            ? (row.original.geo_id ?? "unresolved")
            : (row.original.commodity_id ?? "unresolved")
        }
      />
    ),
  });

  const member: ColumnDef<Observation>[] =
    dimension === "none"
      ? []
      : dimension === "both"
        ? [memberColumn("geography"), memberColumn("commodity")]
        : [memberColumn(dimension)];

  return [
    period,
    bounds,
    ...member,
    {
      accessorKey: "value",
      header: "Value",
      // Figures and a sort control, right-aligned. Left to itself it took a
      // quarter of the table to hold eight characters.
      meta: { align: "right", width: "w-40" },
      cell: ({ row }) => {
        const { value, status, value_unambiguous } = row.original;
        if (value === null) {
          return <span className="text-muted-foreground">{statusLabel(status)}</span>;
        }
        return (
          <span className="inline-flex items-center justify-end gap-1.5 font-medium">
            {!value_unambiguous ? (
              <Badge variant="outline" title="Read under an assumption">
                ?
              </Badge>
            ) : null}
            {formatDecimal(value)}
          </span>
        );
      },
    },
    {
      // A column rather than a line under each figure. The unit is part of the
      // fact — a number read in the wrong one is wrong by orders of magnitude —
      // but it is the same words on every row, and under the value it stretched
      // that column to the width of "US$ m., constant 2024 prices and exchange
      // rates" and repeated it fifty-two times.
      accessorKey: "unit",
      header: "Unit",
      // Sized rather than left to the table: as the last column it would
      // otherwise take every pixel the others did not, and a clamped unit would
      // sit in a column three times its own width.
      meta: { width: "w-48" },
      cell: ({ row }) =>
        row.original.unit ? (
          <ClampedText className="max-w-[11rem]">{row.original.unit}</ClampedText>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
  ];
}

/**
 * Every bucket any line has a point for, in order.
 *
 * Shared across the lines so they are plotted against one axis: a line drawn
 * against only the periods it has figures for would shift left and misdate
 * everything after its first gap.
 */
function periodsOf(series: SeriesLine[]): string[] {
  return [...new Set(series.flatMap((line) => line.points.map((p) => p.period)))].sort();
}

/**
 * What a bucket stands for, in the words a tooltip can carry.
 *
 * A point that is one published figure says what that figure's status was. A
 * point that is a mean says so and says how many figures it is the mean of,
 * because a monthly mean of three daily prices and one of thirty are different
 * claims and the chart cannot show the difference.
 */
function pointStatus(point: SeriesPoint): string {
  const present = point.count - point.missing;
  if (point.value === null) return statusLabel("missing");
  if (point.count === 1) return statusLabel("ok");
  if (point.missing) {
    return `mean of ${formatCount(present)} of ${formatCount(point.count)} figures`;
  }
  return `mean of ${formatCount(point.count)} figures`;
}

/** The served lines, padded onto the shared axis and muted where the legend says. */
function linesFrom(
  series: SeriesLine[],
  periods: string[],
  hidden: string[],
  fallbackName: string,
): Line[] {
  return series.map((line) => {
    const byPeriod = new Map(line.points.map((point) => [point.period, point]));
    return {
      name: line.member || fallbackName,
      hidden: hidden.includes(line.member),
      points: periods.map((period): Point => {
        const point = byPeriod.get(period);
        if (!point || point.value === null) {
          return { label: period, value: null, status: statusLabel("missing") };
        }
        return { label: period, value: Number(point.value), status: pointStatus(point) };
      }),
    };
  });
}

/** How a chart says what its points are, where they are not figures as published. */
function granularityNote(granularity: string, rows: number): string | null {
  const bucket = { month: "Monthly", quarter: "Quarterly", year: "Annual" }[granularity];
  if (!bucket) return null;
  return `${bucket} means of ${formatCount(rows)} figures — the series is longer than a chart can draw point by point. Narrow to a shorter span for the figures as published.`;
}

/** A facet list as the narrowing controls take it. */
function optionsOf(facets: Facet[] | undefined): Series[] {
  return (facets ?? []).map((facet) => ({
    key: facet.value,
    label: facet.value,
    count: facet.count,
  }));
}

/**
 * The upper bound a period label stands for, as a label.
 *
 * Periods are compared as text, here and in the warehouse, and a coarse bound
 * compared that way excludes what sits inside it: `2015` sorts before
 * `2015-06`, so a reader asking for everything until 2015 would lose every
 * month of it but January. Padding the bound to the end of the year or the
 * month it names is what makes "until 2015" mean the whole of 2015.
 */
function periodCeiling(label: string): string {
  if (/^\d{4}$/.test(label)) return `${label}-12-31`;
  if (/^\d{4}-\d{2}$/.test(label)) return `${label}-31`;
  return label;
}

const EXPORT_COLUMNS = [
  { key: "period" as const, header: "period" },
  { key: "period_start" as const, header: "period_start" },
  { key: "period_end" as const, header: "period_end" },
  { key: "geo_id" as const, header: "geo_id" },
  { key: "geo_name" as const, header: "geo_name" },
  { key: "commodity_id" as const, header: "commodity_id" },
  { key: "commodity_name" as const, header: "commodity_name" },
  { key: "value" as const, header: "value" },
  { key: "unit" as const, header: "unit" },
  { key: "status" as const, header: "status" },
  { key: "source_id" as const, header: "source_id" },
  { key: "source_url" as const, header: "source_url" },
];

function IndicatorDetail() {
  const { indicatorId } = Route.useParams();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });

  const indicator = useQuery({
    queryKey: ["indicator", indicatorId],
    queryFn: () => api.indicator(indicatorId),
  });

  // What the filters can offer, counted in the warehouse. Asked without the
  // filters that are on, unlike the documents page: which control a chip
  // belongs to depends on the dimension the series varies along, and a `Place`
  // list narrowed to the one place already chosen is a list a reader cannot add
  // a second place from.
  const facets = useQuery({
    queryKey: ["observation-facets", indicatorId],
    queryFn: () => api.observationFacets({ indicator: [indicatorId] }),
  });

  // The registry is a handful of rows and every indicator page wants one of
  // them, so it is fetched whole and cached under one key rather than per
  // series.
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.sources() });
  // Which collection this series belongs to. Asked of the dataset list rather
  // than carried on the observation, because a series belongs to exactly one
  // and the list is already cached for the datasets page.
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });
  // What the figures were actually read out of. Almost every series here has
  // exactly one such document; the few with more are collections a publisher
  // issues in instalments.
  const documents = useQuery({
    queryKey: ["indicator-documents", indicatorId],
    queryFn: () => api.indicatorDocuments(indicatorId),
  });

  const meta = indicator.data?.data;
  // The published name where the series has one, derived from the identifier
  // where it does not. It arrives with the indicator query, so the page reads
  // as the identifier for as long as that is in flight rather than flashing an
  // empty heading.
  const heading = meta ? indicatorLabel(meta) : titleFromId(indicatorId);
  const source = meta?.sources[0];
  const sourceRecord = sources.data?.data?.find((entry) => entry.source_id === source);
  const dataset = datasets.data?.data?.find((entry) =>
    entry.indicators.includes(indicatorId),
  );
  const documentRows = documents.data?.data ?? [];

  // Whether this serving layer will run the scraper behind the series, and
  // what it is doing if it already is. Held here rather than in the logs tab
  // because the menu that starts a run lives in the header: one job, watched
  // from both.
  const ingestion = useIngestion(source, indicatorId);

  // An indicator is rarely one line. `food_price_traditional` is thirty-one
  // commodities across 34 provinces and `consumer_confidence_by_city` is
  // eighteen cities. The page opens on all of them — the reader came to see
  // what is here, not to guess a commodity before being shown anything — and
  // narrowing is a filter beside the others rather than a gate in front of the
  // page.
  //
  // Read off the facet counts rather than the rows, because the rows are a page
  // of the series and not the series: 2.6 million daily food prices reach the
  // browser twenty-five at a time.
  const available = facets.data?.data;
  const dimension = dimensionOfMembers(
    available?.places.length ?? 0,
    available?.commodities.length ?? 0,
  );

  // The narrowing controls work one axis at a time. `series` holds the primary
  // axis — the place, where there is one — and `commodity` the other, so a
  // reader can ask for shallots without naming a province.
  const axis = primaryAxis(dimension);
  const axisOptions = optionsOf(
    axis === "commodity" ? available?.commodities : available?.places,
  );
  const commodityOptions =
    dimension === "both" ? optionsOf(available?.commodities) : [];
  const statuses = (available?.statuses ?? []).map((facet) => facet.value);
  const years = optionsOf(available?.years);

  const chosenSeries = asTextList(search.series);
  const chosenCommodities = asTextList(search.commodity);
  const chosenStatuses = asTextList(search.status);
  const chosenYears = asTextList(search.year);
  const hidden = asTextList(search.hide);
  const from = asText(search.from);
  const until = asText(search.until);

  const tab: TabName = search.tab ?? "metadata";
  const page = search.page ?? 0;

  // Newest first unless the reader has said otherwise. A series is stored in
  // period order, which is right for the chart — a line has to be drawn left to
  // right — and wrong for the table: a daily series since 2017 would open on
  // January 2017 and the figure someone came for would be on page 40,000.
  const sortColumn = search.sort ?? DEFAULT_SORT;
  // Only the default carries its own direction. Once the reader has picked a
  // column, the table's header supplies `dir` on every click, so an absent one
  // there means a hand-written URL and ascending is the least surprising
  // reading of it.
  const descending = search.sort ? search.dir === "desc" : true;

  // Every filter the reader has set, as the API takes them.
  //
  // One object, used by both queries and printed on the API tab, because the
  // three have to agree: a table paged in the warehouse under one filter and
  // charted in the browser under another is two answers to one question.
  const filters: ObservationQuery = {
    indicator: [indicatorId],
    // The primary axis chip narrows by place for most series and by commodity
    // for the ones that vary only that way. Places go by the name they were
    // published under rather than by `geo`, whose identifier most survey cities
    // do not have.
    ...(axis === "commodity"
      ? { commodity: chosenSeries }
      : { geo_name: chosenSeries }),
    ...(dimension === "both" ? { commodity: chosenCommodities } : {}),
    status: chosenStatuses,
    year: chosenYears,
    period_start: from,
    // Padded so a coarse bound includes what sits inside it: compared as text,
    // `2015` would exclude `2015-06`, which sorts after it.
    period_end: until ? periodCeiling(until) : undefined,
    q: asText(search.q),
  };

  // One page of figures, ordered and narrowed where the rows are rather than
  // where they are read. Sorting a page in the browser orders twenty-five rows
  // against two million others' positions, which reads as a table that has
  // quietly lost rows.
  //
  // Paged by cursor where there is one for the page being asked for — which is
  // the page after one already seen. The warehouse can seek to a named boundary
  // row; an offset makes it produce and discard every row ahead of it, which at
  // the end of a daily series is 2.4s against 0.1s.
  const order = orderFor(sortColumn, descending);
  const cursors = useCursors([filters, order]);
  const observations = useQuery({
    queryKey: ["observations", "page", filters, order, page],
    queryFn: () =>
      api.observations({
        ...filters,
        order,
        limit: PAGE_SIZE,
        ...(cursors.at(page) !== undefined
          ? { after: cursors.at(page) }
          : { offset: page * PAGE_SIZE }),
      }),
  });

  // What the chart draws, aggregated where the figures are. A decade of daily
  // prices across 34 provinces is 2.6 million rows and a chart a thousand
  // pixels wide; the warehouse returns a line per member at a granularity the
  // span can be drawn at, and says which.
  const chart = useQuery({
    queryKey: ["observations", "series", filters, dimension],
    queryFn: () =>
      api.observationSeries({ ...filters, dimension, members: MAX_SERIES }),
  });

  const paged = observations.data?.data ?? [];
  const total = observations.data?.meta?.total ?? 0;
  useEffect(() => {
    const next = observations.data?.meta?.next_cursor;
    if (next) cursors.remember(page + 1, next);
  }, [cursors, page, observations.data?.meta?.next_cursor]);

  const drawn = chart.data?.data;
  const granularity = drawn?.granularity ?? "native";
  // How many figures the chart stands for, and how many members there are
  // against the eight a palette can tell apart.
  const charted = chart.data?.meta?.total ?? 0;
  const memberCount = drawn?.members ?? 0;
  const periods = periodsOf(drawn?.series ?? []);
  const lines = linesFrom(drawn?.series ?? [], periods, hidden, heading);

  // Analytics describe one series. Averaged across rice and chilli they would
  // describe nothing, so they follow the chart only when a single series is in
  // view and step aside otherwise.
  const visibleLines = lines.filter((line) => !line.hidden);
  const single = visibleLines.length === 1 ? visibleLines[0]! : undefined;
  const figures: Figure[] = (single?.points ?? []).map((point) => ({
    period: point.label,
    value: point.value,
    status: point.status ?? "ok",
  }));
  const stats = summarise(figures);
  const gaps = gapsByStatus(figures);

  // A row to show the shape of the response with, and to carry the source link
  // at the foot of the page.
  const sample = paged[0];

  // Fetched when it is asked for rather than held against the chance it will
  // be: the rows behind this page are the warehouse's, and the browser has one
  // screen of them.
  const [exporting, setExporting] = useState(false);
  async function exportFigures() {
    setExporting(true);
    try {
      const rows = await api.observations({ ...filters, order, limit: EXPORT_LIMIT });
      downloadCsv(
        `${indicatorId}.csv`,
        toCsv(rows.data as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
      );
    } finally {
      setExporting(false);
    }
  }

  // The request that would return what is on screen — the same filters, in the
  // same order, so a reader can copy it rather than translating a filter into
  // query parameters by hand.
  const apiQuery: ApiQuery = [
    ...Object.entries(filters).flatMap(([key, value]) =>
      value === undefined || value === "" || (Array.isArray(value) && !value.length)
        ? []
        : Array.isArray(value)
          ? value.map((entry) => ({ key, value: entry }))
          : [{ key, value: String(value) }],
    ),
    { key: "order", value: order },
    { key: "limit", value: String(PAGE_SIZE) },
    ...(page ? [{ key: "offset", value: String(page * PAGE_SIZE) }] : []),
  ];

  // The filters live on the data tab now, but they still narrow the chart and
  // the analytics on this one. A figure read under a filter nobody can see is a
  // figure read wrong, so the metadata tab says when any are on.
  const narrowedBy =
    chosenStatuses.length +
    chosenSeries.length +
    chosenCommodities.length +
    chosenYears.length +
    hidden.length +
    (from ? 1 : 0) +
    (until ? 1 : 0) +
    (search.q ? 1 : 0);

  const searchBox = (
    <SearchInput
      value={asText(search.q)}
      placeholder={
        dimension === "none"
          ? "Search periods"
          : `Search periods and ${dimensionLabel(dimension).toLowerCase()}`
      }
      onSearch={(q) => navigate({ search: (prev) => ({ ...prev, q, page: 0 }) })}
    />
  );

  if (indicator.isError) {
    return (
      <div className="space-y-4">
        <BackLink />
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
          {(indicator.error as Error).message}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <BackLink />

      {/* The tabs ride in the header with the title, and what narrows the page
          sits under them — next to the tab it is narrowing rather than above
          the choice of tab. Both stay pinned: ten screens into a table the
          reader has lost sight of which filters are on, and a figure read under
          a filter nobody can see is a figure read wrong. */}
      <Tabs
        value={tab}
        onValueChange={(next) =>
          navigate({ search: (prev) => ({ ...prev, tab: next as TabName }) })
        }
      >
        <StickyHeader
          divided={false}
          heading={
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="font-heading text-2xl font-semibold tracking-tight">
                  {heading}
                </h1>
                <div className="ml-auto flex items-center gap-2">
                  {/* Filing a series is something a reader does while reading
                      it — by the time they are on the collections page, the
                      series they wanted is behind them. */}
                  <CollectButton
                    items={[{ kind: "indicator", id: indicatorId, label: heading }]}
                  />
                  <RowActions
                    label="Indicator actions"
                    actions={[
                      {
                        label: "Copy ingest command",
                        icon: IconCopy,
                        // The pipeline runs from a terminal, so the useful thing the
                        // browser can do is hand over the exact line to paste.
                        onSelect: source
                          ? () =>
                              void copyToClipboard(
                                `cd pipelines && uv run terusan sources run ${source}`,
                              )
                          : undefined,
                        hint: source ? undefined : "No source recorded for this series",
                      },
                      {
                        label: "Copy re-extract command",
                        icon: IconCopy,
                        // Only the step that can be stated in full. This one is
                        // complete and correct; the normalization that follows it
                        // is not — see below.
                        onSelect: () =>
                          void copyToClipboard(
                            "cd pipelines && uv run terusan warehouse extract",
                          ),
                      },
                      {
                        // The serving layer starts the scraper; it does not
                        // write a figure itself. What lands still lands the way
                        // everything here lands — RAW, then Bronze, then Silver
                        // — and the run is journalled either way, so a figure
                        // that arrived from this click is no less traceable
                        // than one that arrived from a terminal at 05:00.
                        label: ingestion.running
                          ? "Ingestion running…"
                          : "Run ingestion now",
                        icon: ingestion.running ? IconRefresh : IconPlayerPlay,
                        onSelect:
                          ingestion.available && source && !ingestion.running
                            ? () => {
                                ingestion.start();
                                // Straight to the tab that shows what it is
                                // doing. A button that starts a ten-minute job
                                // and then looks like it did nothing is worse
                                // than no button.
                                navigate({
                                  search: (prev) => ({ ...prev, tab: "logs" }),
                                });
                              }
                            : undefined,
                        hint: ingestion.unavailable,
                      },
                    ]}
                    unavailable={[
                      {
                        label: "Rebuild from RAW",
                        icon: IconRefresh,
                        hint: "The serving layer is read-only — run it from a terminal",
                      },
                      {
                        label: "Copy normalize command",
                        icon: IconCopy,
                        // `terusan silver normalize <id>` on its own fails with
                        // "declares neither a period/value pair nor value
                        // columns": the column mapping is passed as flags and is
                        // not recorded anywhere this page can read.
                        hint: "This series' column mapping is not stored, so the command cannot be written out",
                      },
                    ]}
                  />
                </div>
              </div>
              {/* The skeleton is a sibling of the paragraph rather than a child of
                it: it renders a <div>, which is not allowed inside a <p>, and the
                browser closes the paragraph early — so the server tree and the
                client tree disagree and hydration throws. */}
              {meta ? (
                <p className="max-w-3xl text-sm text-muted-foreground">
                  {formatCount(meta.observations)} figures covering {meta.period_start}{" "}
                  to {meta.period_end}
                  {/* A commodity series resolves no geography at all, and saying
                    "across 0 places" of one reads as a gap in the data rather
                    than as the shape of it. */}
                  {dimension === "none" ? null : (
                    <>
                      , across {formatCount(memberCount)}{" "}
                      {dimensionNoun(dimension, memberCount)}
                    </>
                  )}
                  , from {meta.sources.join(", ") || "an unrecorded source"}. Every
                  figure keeps the document it came from, so a number here can be traced
                  back to what published it.
                </p>
              ) : (
                <Skeleton className="h-4 w-96" />
              )}
            </div>
          }
          filters={
            <TabsList>
              <TabsTrigger value="metadata">Metadata</TabsTrigger>
              <TabsTrigger value="data">Data</TabsTrigger>
              <TabsTrigger value="api">API</TabsTrigger>
              <TabsTrigger value="logs">Logs</TabsTrigger>
              <TabsTrigger value="settings">Settings</TabsTrigger>
            </TabsList>
          }
        />

        <TabsContent value="metadata" className="space-y-4 pt-2">
          {narrowedBy ? (
            <p className="rounded-lg border border-dashed px-3 py-2 text-sm text-muted-foreground">
              The chart and the analytics below are narrowed by{" "}
              {narrowedBy === 1 ? "a filter" : `${narrowedBy} filters`} set on the data
              tab.{" "}
              <button
                type="button"
                onClick={() =>
                  navigate({ search: (prev) => ({ ...prev, tab: "data" }) })
                }
                className="underline underline-offset-4 hover:text-foreground"
              >
                Show them
              </button>{" "}
              ·{" "}
              <button
                type="button"
                onClick={() => navigate({ search: { tab: "metadata" } })}
                className="underline underline-offset-4 hover:text-foreground"
              >
                Clear all
              </button>
            </p>
          ) : null}

          <div className="grid gap-8 lg:grid-cols-[16rem_minmax(0,1fr)]">
            {/* Metadata on the left: it is what a reader checks *while*
                reading a figure, and the left edge is where the eye starts.
                No card around it — a border here fences off the one thing
                that should read as part of the page. */}
            <aside className={`lg:sticky lg:self-start ${BELOW_STICKY_HEADER}`}>
              <h2 className="font-heading text-sm font-semibold tracking-tight">
                Metadata
              </h2>
              <dl className="mt-3 space-y-3">
                {/* The publisher's own identifier for the series. Ours is in
                    the URL and in the API tab; this is the one a reader quotes
                    back to the publisher to find the same figures there. */}
                <Fact label="Code" value={meta?.code} mono />
                {/* The key the series was declared under, which is what a
                    maintainer greps the pipeline for. Not the identifier: that
                    is the code in the URL. */}
                <Fact label="Key" value={meta?.slug} mono />
                <Fact label="Frequency" value={meta?.temporal_resolution} />
                <Fact label="Unit" value={meta?.unit} />
                {/* Who produced the figures, which is not who we collected
                    them from: FRED carries the Treasury's and the OECD's
                    series, and crediting FRED for a Treasury figure would be
                    wrong. */}
                <Fact label="Published by" value={meta?.publisher} />
                <Fact label="Release" value={meta?.release} />
                <Fact
                  label="Coverage"
                  value={meta ? `${meta.period_start} – ${meta.period_end}` : undefined}
                />
                {/* Omitted for a national series: it varies along no
                    dimension at all, and "Places 0" reads as a gap in the data
                    rather than as the shape of it. */}
                {dimension === "none" ? null : (
                  <Fact
                    label={
                      dimension === "commodity"
                        ? "Commodities"
                        : dimension === "both"
                          ? "Series"
                          : "Places"
                    }
                    // Counted from the figures, like the sentence above it.
                    // `meta.geographies` counts distinct `geo_id`, and
                    // seventeen of the eighteen survey cities have none — so it
                    // reported two places on a series the description called
                    // eighteen.
                    value={formatCount(memberCount)}
                    hint={
                      dimension === "commodity"
                        ? "This series varies by commodity, not by place"
                        : dimension === "both"
                          ? "This series varies by place and by commodity, so a series is one of each"
                          : undefined
                    }
                  />
                )}
                <Fact
                  label="Figures"
                  value={meta ? formatCount(meta.observations) : undefined}
                />
                {/* Up to the collection this came from: a reader who wants the
                    rest of the release should not have to search for it. */}
                <div>
                  <dt className="text-xs font-medium text-muted-foreground">Dataset</dt>
                  <dd className="mt-0.5 text-sm">
                    {dataset ? (
                      <Link
                        to="/datasets/$datasetId"
                        params={{ datasetId: dataset.dataset_id }}
                        className="underline underline-offset-4 hover:text-foreground"
                      >
                        {datasetLabel(dataset)}
                      </Link>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </dd>
                </div>
                {/* The material the figures were read from. Beside the
                    dataset because they are the same kind of fact — where
                    this sits — and because a reader checking a number wants
                    the document far more often than they want the code. */}
                {documentRows.length ? (
                  <div>
                    <dt className="text-xs font-medium text-muted-foreground">
                      Read from
                    </dt>
                    <dd className="mt-0.5 space-y-1 text-sm">
                      {documentRows.map((document) => (
                        <div key={document.document_id}>
                          <Link
                            to="/documents/$documentId"
                            params={{ documentId: document.document_id }}
                            className="underline underline-offset-4 hover:text-foreground"
                          >
                            {document.title}
                          </Link>
                          {document.subtitle ? (
                            <span className="ml-1.5 text-xs text-muted-foreground">
                              {document.subtitle}
                            </span>
                          ) : null}
                        </div>
                      ))}
                    </dd>
                  </div>
                ) : null}
                <Fact label="Sources" value={meta?.sources.join(", ")} />
                {meta?.tags?.length ? (
                  <div>
                    <dt className="text-xs font-medium text-muted-foreground">Tags</dt>
                    <dd className="mt-1">
                      <TagList
                        tags={meta.tags}
                        max={meta.tags.length}
                        onSelect={(tag) =>
                          void navigate({ to: "/indicators", search: { tag: [tag] } })
                        }
                      />
                    </dd>
                  </div>
                ) : null}
                <Fact
                  label="Last updated"
                  value={meta ? formatDate(meta.last_updated) : undefined}
                  hint={meta ? formatRelative(meta.last_updated) : undefined}
                />
                <Fact
                  label="Layer"
                  value="silver"
                  hint="Normalized, typed observations"
                />
                <Fact
                  label="Access"
                  value="Internal"
                  hint="Widening access is a decision, not a default"
                />
              </dl>
            </aside>

            <div className="min-w-0 space-y-6">
              {/* What the series counts, in the words of whoever publishes it.
                  It comes first because every figure below is read under it: a
                  net-transactions series and a stock series look identical in a
                  chart and mean different things. Absent for a series whose
                  publisher wrote no notes, rather than filled with our own
                  description of it. */}
              {meta?.description ? (
                <Section
                  title="About this series"
                  // Not "as <publisher> describes it": three agencies produce
                  // one of these series often enough, and their names run
                  // longer than the sentence they sit in. Metadata names them.
                  description="In the publisher's own words."
                >
                  {/* Wider than the prose elsewhere on the page: these notes
                      are three or four paragraphs of definition, and a narrow
                      measure turns them into a column of ten-word lines. */}
                  <p className="max-w-5xl whitespace-pre-line text-sm leading-relaxed">
                    {meta.description}
                  </p>
                </Section>
              ) : null}

              <Section
                title="Analytics"
                description={
                  <>
                    Derived from the figures themselves rather than declared, so these
                    follow the filters above and change when you narrow them. A figure
                    that was never collected is counted as a gap, not as a zero.
                  </>
                }
              >
                {single ? null : (
                  // Averaged across rice and chilli these would describe nothing.
                  <p className="rounded-lg border border-dashed px-4 py-3 text-sm text-muted-foreground">
                    {formatCount(lines.length)} series are in view. Choose one in the{" "}
                    {dimensionLabel(dimension).toLowerCase()} filter below to see its
                    latest figure, its change over time and its gaps — averaged across
                    series these would describe nothing.
                  </p>
                )}
                <div
                  className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3"
                  hidden={!single}
                >
                  <Stat
                    label="Latest"
                    raw={stats.latest ? String(stats.latest.value) : null}
                    value={
                      stats.latest
                        ? formatDecimal(String(stats.latest.value))
                        : undefined
                    }
                    hint={stats.latest?.period}
                    loading={chart.isLoading}
                  />
                  <Stat
                    label="Change over the series"
                    value={formatPercent(stats.totalChange)}
                    hint={
                      stats.first && stats.latest
                        ? `${stats.first.period} → ${stats.latest.period}`
                        : undefined
                    }
                    loading={chart.isLoading}
                  />
                  <Stat
                    label="Compound annual growth"
                    value={formatPercent(stats.cagr, 2)}
                    // Meaningless where the base is zero or negative, and left
                    // unstated rather than rendered as a number nobody can act on.
                    hint={
                      stats.cagr === null
                        ? "not meaningful for this series"
                        : "per year"
                    }
                    loading={chart.isLoading}
                  />
                  <Stat
                    label="Recorded"
                    value={`${formatCount(stats.present)} of ${formatCount(stats.count)}`}
                    hint={
                      gaps.length
                        ? gaps
                            .map(
                              (gap) =>
                                `${gap.count} ${statusLabel(gap.status).toLowerCase()}`,
                            )
                            .join(", ")
                        : "no gaps"
                    }
                    loading={chart.isLoading}
                  />
                  <Stat
                    label="Highest"
                    raw={stats.max ? String(stats.max.value) : null}
                    value={
                      stats.max ? formatDecimal(String(stats.max.value)) : undefined
                    }
                    hint={stats.max?.period}
                    loading={chart.isLoading}
                  />
                  <Stat
                    label="Lowest"
                    raw={stats.min ? String(stats.min.value) : null}
                    value={
                      stats.min ? formatDecimal(String(stats.min.value)) : undefined
                    }
                    hint={stats.min?.period}
                    loading={chart.isLoading}
                  />
                </div>
              </Section>

              <Section
                title="Over time"
                description={
                  <>
                    One line per series, over the same periods as the table below. The
                    line breaks at a missing figure rather than joining across it — a
                    line drawn through a gap asserts a value nobody recorded.
                  </>
                }
              >
                {chart.isLoading ? (
                  <Skeleton className="h-[300px] w-full rounded-lg" />
                ) : (
                  <TimeSeriesChart
                    series={lines}
                    unit={meta?.unit}
                    // The legend mutes a line, and the table follows: it is a
                    // filter that happens to be drawn as a key.
                    onToggle={
                      dimension === "none"
                        ? undefined
                        : (name) =>
                            navigate({
                              search: (prev) => ({
                                ...prev,
                                hide: toggle(hidden, name),
                                page: 0,
                              }),
                            })
                    }
                    caption={
                      [
                        // Said plainly rather than left to be inferred from a
                        // legend that stops at eight.
                        lines.length < memberCount
                          ? `Showing ${formatCount(lines.length)} of ${formatCount(memberCount)} ${dimensionLabel(dimension).toLowerCase()} series — a ninth line would have to repeat a colour. Narrow with the ${dimensionLabel(dimension).toLowerCase()} filter below.`
                          : null,
                        single && gaps.length
                          ? `The line breaks where a figure is absent — joining across a gap would assert a value nobody recorded. ${formatCount(stats.missing)} of ${formatCount(stats.count)} periods have none.`
                          : null,
                        // A mean is not a published figure, and a chart that
                        // quietly drew one would be a chart of something the
                        // warehouse does not hold.
                        granularityNote(granularity, charted),
                      ]
                        .filter(Boolean)
                        .join(" ") || undefined
                    }
                  />
                )}
              </Section>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="data" className="pt-2">
          <Section
            title="The figures"
            description={
              <>
                The figures as published, narrowed by the same filters as the chart
                and paged in the warehouse. Each keeps the document it was published
                in, so any figure here can be checked against its source. The export
                fetches up to {formatCount(EXPORT_LIMIT)} rows in the order shown —
                not just the page on screen, and not the whole of a series longer than
                that.
              </>
            }
            action={
              <Button
                variant="outline"
                size="sm"
                disabled={!total || exporting}
                onClick={() => void exportFigures()}
              >
                <IconDownload className="size-4" />
                {exporting
                  ? "Exporting…"
                  : `Export ${total > EXPORT_LIMIT ? formatCount(EXPORT_LIMIT) : "figures"}`}
              </Button>
            }
          >
            <div className="space-y-4">
              {/* Above the table they narrow. They shape the chart and the
                  analytics on the metadata tab too, which is why that tab says
                  so when any of them are on — a figure read under a filter
                  nobody can see is a figure read wrong. */}
              <TableToolbar
                filters={
                  <>
                    {/* Every chip here appears only where it offers a choice:
                    a filter with one option cannot narrow anything, and a
                    row of inert controls is harder to read than a short
                    one. */}
                    {statuses.length > 1 ? (
                      <FilterChip
                        label="Status"
                        value={summariseChip(chosenStatuses, statusLabel)}
                        onClear={() =>
                          navigate({
                            search: (prev) => ({ ...prev, status: undefined, page: 0 }),
                          })
                        }
                      >
                        <ChoiceList
                          options={statuses.map((value) => ({
                            value,
                            label: statusLabel(value),
                          }))}
                          selected={chosenStatuses}
                          onToggle={(value) =>
                            navigate({
                              search: (prev) => ({
                                ...prev,
                                status: toggle(chosenStatuses, value),
                                page: 0,
                              }),
                            })
                          }
                          onClear={() =>
                            navigate({
                              search: (prev) => ({
                                ...prev,
                                status: undefined,
                                page: 0,
                              }),
                            })
                          }
                        />
                      </FilterChip>
                    ) : null}

                    {/* Beside Status rather than above the page: narrowing to
                    one commodity is the same kind of act as narrowing to
                    one status, and the page shows everything until asked. */}
                    {axis && axisOptions.length > 1 ? (
                      <FilterChip
                        label={dimensionLabel(axis)}
                        value={summariseChip(chosenSeries)}
                        onClear={() =>
                          navigate({
                            search: (prev) => ({
                              ...prev,
                              series: undefined,
                              page: 0,
                            }),
                          })
                        }
                      >
                        <ChoiceList
                          options={axisOptions.map((entry) => ({
                            value: entry.key,
                            label: entry.label,
                            // What a reader needs to choose between members is
                            // how much of each there is.
                            hint: `${formatCount(entry.count)}`,
                          }))}
                          selected={chosenSeries}
                          searchPlaceholder={`Search ${dimensionLabel(axis).toLowerCase()}`}
                          onToggle={(value) =>
                            navigate({
                              search: (prev) => ({
                                ...prev,
                                series: toggle(chosenSeries, value),
                                page: 0,
                              }),
                            })
                          }
                          onClear={() =>
                            navigate({
                              search: (prev) => ({
                                ...prev,
                                series: undefined,
                                page: 0,
                              }),
                            })
                          }
                        />
                      </FilterChip>
                    ) : null}

                    {/* The other axis, where there is one. Thirty-one foods
                    priced in every province: without this the only way to ask
                    for shallots is to pick them out of a list of every
                    province-and-commodity pairing there is. */}
                    {commodityOptions.length > 1 ? (
                      <FilterChip
                        label="Commodity"
                        value={summariseChip(chosenCommodities)}
                        onClear={() =>
                          navigate({
                            search: (prev) => ({
                              ...prev,
                              commodity: undefined,
                              page: 0,
                            }),
                          })
                        }
                      >
                        <ChoiceList
                          options={commodityOptions.map((entry) => ({
                            value: entry.key,
                            label: entry.label,
                            hint: `${formatCount(entry.count)}`,
                          }))}
                          selected={chosenCommodities}
                          searchPlaceholder="Search commodity"
                          onToggle={(value) =>
                            navigate({
                              search: (prev) => ({
                                ...prev,
                                commodity: toggle(chosenCommodities, value),
                                page: 0,
                              }),
                            })
                          }
                          onClear={() =>
                            navigate({
                              search: (prev) => ({
                                ...prev,
                                commodity: undefined,
                                page: 0,
                              }),
                            })
                          }
                        />
                      </FilterChip>
                    ) : null}

                    {/* Only where there is a choice to make: a series that
                    sits in one year offers nothing to narrow. */}
                    {years.length > 1 ? (
                      <FilterChip
                        label="Year"
                        value={summariseChip(chosenYears)}
                        onClear={() =>
                          navigate({
                            search: (prev) => ({ ...prev, year: undefined, page: 0 }),
                          })
                        }
                      >
                        <ChoiceList
                          options={years.map((entry) => ({
                            value: entry.key,
                            label: entry.label,
                            hint: `${formatCount(entry.count)}`,
                          }))}
                          selected={chosenYears}
                          searchPlaceholder="Search year"
                          onToggle={(value) =>
                            navigate({
                              search: (prev) => ({
                                ...prev,
                                year: toggle(chosenYears, value),
                                page: 0,
                              }),
                            })
                          }
                          onClear={() =>
                            navigate({
                              search: (prev) => ({ ...prev, year: undefined, page: 0 }),
                            })
                          }
                        />
                      </FilterChip>
                    ) : null}

                    {/* A range, where Year is a set: "every month from 2015 to
                    mid-2018" is not a list of years, and a list of years is not
                    a range. Both are offered because both get asked. */}
                    <FilterChip
                      label="Period"
                      value={
                        from && until
                          ? `${from}–${until}`
                          : (from ?? until ?? undefined)
                      }
                      onClear={() =>
                        navigate({
                          search: (prev) => ({
                            ...prev,
                            from: undefined,
                            until: undefined,
                            page: 0,
                          }),
                        })
                      }
                    >
                      <PeriodFilter
                        from={from ?? ""}
                        until={until ?? ""}
                        onApply={(start, end) =>
                          navigate({
                            search: (prev) => ({
                              ...prev,
                              from: asPeriodParam(start),
                              until: asPeriodParam(end),
                              page: 0,
                            }),
                          })
                        }
                      />
                    </FilterChip>

                    {chosenStatuses.length ||
                    chosenSeries.length ||
                    chosenCommodities.length ||
                    chosenYears.length ||
                    hidden.length ||
                    from ||
                    until ||
                    search.q ? (
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
                search={searchBox}
              />

              <DataTable
                columns={columnsFor(dimension)}
                data={paged}
                isLoading={observations.isLoading}
                loadingRows={8}
                emptyMessage="No figures match these filters."
                // Ticking rows is how a reader takes a handful of figures out
                // of four hundred: the export below takes everything showing,
                // and this takes what they picked.
                selectable
                getRowId={(row) => row.observation_id}
                renderSelectionActions={(chosen) => (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      downloadCsv(
                        `${indicatorId}-selection.csv`,
                        toCsv(
                          chosen as unknown as Record<string, unknown>[],
                          EXPORT_COLUMNS,
                        ),
                      )
                    }
                  >
                    <IconDownload className="size-4" />
                    Export {formatCount(chosen.length)} selected
                  </Button>
                )}
                // Held here rather than in the table: the table is handed one
                // page, and sorting has to order the whole series.
                // The effective order, not just what the URL names: a table
                // sorted newest-first has to show the arrow saying so, or the
                // reader's first click "sorts" it into the order it was
                // already in.
                sorting={[{ id: sortColumn, desc: descending }]}
                onSortingChange={(next) => {
                  const [first] = next;
                  navigate({
                    search: (prev) => ({
                      ...prev,
                      sort: first?.id as typeof search.sort,
                      dir: first ? (first.desc ? "desc" : "asc") : undefined,
                      // Back to the first page: row 300 of the old order is a
                      // different row in the new one.
                      page: 0,
                    }),
                  });
                }}
              />

              <TablePagination
                page={page}
                total={total}
                pageSize={PAGE_SIZE}
                onPage={(next) =>
                  navigate({ search: (prev) => ({ ...prev, page: next }) })
                }
                summary={
                  total ? (
                    <>
                      {formatCount(page * PAGE_SIZE + 1)}–
                      {formatCount(Math.min((page + 1) * PAGE_SIZE, total))} of{" "}
                      {formatCount(total)}
                      {/* Counted in the warehouse under the filters that are on,
                          against what the series holds in full. */}
                      {meta && total !== meta.observations
                        ? ` filtered from ${formatCount(meta.observations)}`
                        : null}
                    </>
                  ) : null
                }
              />
            </div>
          </Section>
        </TabsContent>

        <TabsContent value="api" className="pt-2">
          <ApiTab
            indicatorId={indicatorId}
            query={apiQuery}
            sample={sample}
            dimension={dimension}
            hiddenCount={hidden.length}
          />
        </TabsContent>

        <TabsContent value="logs" className="pt-2">
          <LogsTab
            indicatorId={indicatorId}
            source={sourceRecord}
            ingestion={ingestion}
          />
        </TabsContent>

        <TabsContent value="settings" className="pt-2">
          <SettingsTab
            source={sourceRecord}
            indicator={meta}
            dimension={dimension}
            seriesCount={memberCount}
          />
        </TabsContent>
      </Tabs>

      {sample?.source_url ? (
        <Card>
          <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4 text-sm">
            <span className="text-muted-foreground">
              Every figure records where it was published, so a number lifted from here
              can be checked against the original.
            </span>
            <Button
              variant="outline"
              size="sm"
              nativeButton={false}
              render={
                <a
                  href={sample.source_url}
                  target="_blank"
                  rel="noreferrer noopener"
                />
              }
            >
              <IconExternalLink className="size-4" />
              Open the source
            </Button>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/indicators"
      className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
    >
      <IconArrowLeft className="size-4" />
      All indicators
    </Link>
  );
}

function Section({
  title,
  description,
  action,
  children,
}: {
  title: string;
  /** What the section is showing, and what it is not. */
  description?: React.ReactNode;
  action?: React.ReactNode;
  /** Optional: a section whose description says it all needs nothing below. */
  children?: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="font-heading text-lg font-semibold tracking-tight">{title}</h2>
          {description ? (
            <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function Fact({
  label,
  value,
  hint,
  mono,
}: {
  label: string;
  value?: string;
  hint?: string;
  /** For a publisher's code, which is read character by character. */
  mono?: boolean;
}) {
  return (
    // No rule under each fact: seven of them stack into a ladder of lines that
    // reads as a table the list is not. The spacing separates them.
    <div>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className={`mt-0.5 text-sm${mono ? " font-mono text-xs" : ""}`}>
        {value ?? <span className="text-muted-foreground">—</span>}
        {hint ? (
          <span className="ml-1.5 text-xs text-muted-foreground">{hint}</span>
        ) : null}
      </dd>
    </div>
  );
}

/**
 * Past this many characters the figure is wider than its tile and the end of it
 * is simply cut off — 1,571,092,130,385,400 rupiah rendered as "1,571,092,130,38".
 */
const STAT_FITS = 15;

function Stat({
  label,
  value,
  raw,
  hint,
  loading,
}: {
  label: string;
  value?: string;
  /**
   * The figure before formatting. Given for quantities that can run long, so
   * an oversized one can be shown compactly instead of clipped.
   */
  raw?: string | null;
  hint?: string;
  loading?: boolean;
}) {
  const overlong = Boolean(value && raw && value.length > STAT_FITS);
  const shown = overlong ? formatCompact(raw as string) : value;

  return (
    <Card>
      <CardContent className="space-y-1 py-4">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        {loading ? (
          <Skeleton className="h-7 w-24" />
        ) : (
          // The exact figure stays available rather than being lost to the
          // rounding: this is a warehouse, and a reader who wants the digits
          // wants all of them.
          <p
            className="font-heading text-xl font-semibold tabular-nums"
            title={overlong ? value : undefined}
          >
            {shown ?? "—"}
          </p>
        )}
        {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      </CardContent>
    </Card>
  );
}

/**
 * The same request, in the tools this warehouse's readers actually use.
 *
 * Every one of them converts `value` explicitly. The API sends it as a string
 * because published statistics are decimal quantities, and a language that
 * parses JSON numbers into floats would quietly round a figure like Indonesia's
 * 2025 APBD revenue — 1,571,092,130,385,400 rupiah — before the reader ever
 * saw it.
 */
type Language = "curl" | "python" | "r" | "javascript" | "duckdb";

/** Shown only before the series has loaded, so the shape is never blank. */
const PLACEHOLDER_ROW = {
  observation_id: "obs_…",
  indicator_id: "…",
  period: "2025",
  period_start: "2025-01-01",
  period_end: "2025-12-31",
  temporal_resolution: "annual",
  value: "1571092130385400.000000000",
  unit: "IDR",
  status: "ok",
  value_unambiguous: true,
  geo_id: null,
  commodity_id: null,
  source_id: "…",
};

const LANGUAGES: {
  id: Language;
  label: string;
  code: (url: string, indicatorId: string) => string;
  note?: string;
}[] = [
  {
    id: "curl",
    label: "curl",
    code: (url) => `curl -s '${url}' | jq '.data[0]'`,
  },
  {
    id: "python",
    label: "Python",
    code: (url) =>
      [
        "from decimal import Decimal",
        "",
        "import pandas as pd",
        "import requests",
        "",
        `response = requests.get(`,
        `    "${url}",`,
        "    timeout=30,",
        ")",
        "response.raise_for_status()",
        "payload = response.json()",
        "",
        'figures = pd.DataFrame(payload["data"])',
        "# A string on the wire, on purpose — see the note below.",
        'figures["value"] = figures["value"].apply(',
        "    lambda raw: Decimal(raw) if raw is not None else None",
        ")",
        "",
        'print(payload["meta"]["total"], "figures")',
      ].join("\n"),
    note: "Use `pd.to_numeric` instead where float precision is enough — for an index or a percentage it is. For rupiah totals it is not.",
  },
  {
    id: "r",
    label: "R",
    code: (url) =>
      [
        "library(httr2)",
        "library(dplyr)",
        "",
        `payload <- request("${url}") |>`,
        "  req_perform() |>",
        "  resp_body_json(simplifyVector = TRUE)",
        "",
        "figures <- payload$data |>",
        "  mutate(",
        "    value = as.numeric(value),",
        "    period_start = as.Date(period_start)",
        "  )",
        "",
        "nrow(figures)",
      ].join("\n"),
    note: "R's numeric is a double, so very large rupiah figures lose their last digits. Keep `value` as character where the exact figure matters.",
  },
  {
    id: "javascript",
    label: "JavaScript",
    code: (url) =>
      [
        `const response = await fetch(`,
        `  "${url}",`,
        ");",
        "if (!response.ok) throw new Error(await response.text());",
        "const { data, meta } = await response.json();",
        "",
        "// `Number` is a double: fine for an index, lossy for a rupiah total.",
        "const figures = data.map((row) => ({",
        "  ...row,",
        "  value: row.value === null ? null : Number(row.value),",
        "}));",
      ].join("\n"),
  },
  {
    id: "duckdb",
    label: "DuckDB",
    code: (url) =>
      [
        "-- Straight from the API, no download step.",
        "INSTALL json; LOAD json;",
        "",
        "SELECT",
        "  t.row.period       AS period,",
        "  CAST(t.row.value AS DECIMAL(38, 9)) AS value,",
        "  t.row.unit         AS unit",
        `FROM read_json('${url}')`,
        "CROSS JOIN UNNEST(data) AS t(row)",
        "ORDER BY period;",
      ].join("\n"),
    note: "DECIMAL rather than DOUBLE, so the figures survive the cast. This is also how the warehouse itself stores them.",
  },
];

/**
 * How to fetch this indicator without the portal.
 *
 * Here because the portal is one reader of the warehouse and not the point of
 * it: the figures are meant to end up in someone's notebook or model. Showing
 * the request that matches what is on screen — rather than a generic example —
 * means the reader can narrow here and copy the result, instead of translating
 * a filter UI into query parameters by hand.
 */
function ApiTab({
  indicatorId,
  query,
  sample,
  dimension,
  hiddenCount,
}: {
  indicatorId: string;
  query: ApiQuery;
  /** A real row from this series — see the note where it is rendered. */
  sample?: Observation;
  dimension: Dimension;
  /** Series muted from the legend — a browser-side filter with no API twin. */
  hiddenCount: number;
}) {
  const [language, setLanguage] = useState<Language>("python");

  const search = query
    .map(({ key, value }) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join("&");
  const url = `${apiBaseUrl}/v1/observations?${search}`;

  // One filter on this page does not survive the trip. Said plainly rather than
  // letting a reader discover that their copied request returns more rows than
  // the table showed.
  const caveats = [
    hiddenCount > 0
      ? `${hiddenCount === 1 ? "One series is" : `${hiddenCount} series are`} muted from the chart's legend. That is a browser-side filter with no API equivalent, so the response will include ${hiddenCount === 1 ? "it" : "them"}.`
      : null,
  ].filter(Boolean) as string[];

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <div className="min-w-0 space-y-6">
        <Section
          title="The request"
          description={
            <>
              The call that returns what is on screen. Narrowing with the filters above
              rewrites it, so a reader can select what they want here and copy the
              request for it rather than translating a filter into query parameters by
              hand.
            </>
          }
        >
          <div className="space-y-3">
            <Snippet label="Endpoint" value={url} />

            {/* One language at a time. A page that stacks five snippets makes
                the reader scroll past four they cannot use to reach the one
                they can. */}
            <Tabs
              value={language}
              onValueChange={(next) => setLanguage(next as Language)}
            >
              <TabsList variant="line">
                {LANGUAGES.map((entry) => (
                  <TabsTrigger key={entry.id} value={entry.id}>
                    {entry.label}
                  </TabsTrigger>
                ))}
              </TabsList>
              {LANGUAGES.map((entry) => (
                <TabsContent key={entry.id} value={entry.id} className="pt-2">
                  <Snippet label={entry.label} value={entry.code(url, indicatorId)} />
                  {entry.note ? (
                    <p className="pt-2 text-xs text-muted-foreground">{entry.note}</p>
                  ) : null}
                </TabsContent>
              ))}
            </Tabs>
          </div>
        </Section>

        {caveats.length ? (
          <Section title="What this request does not carry">
            <ul className="space-y-2">
              {caveats.map((caveat) => (
                <li
                  key={caveat}
                  className="rounded-lg border border-dashed px-4 py-3 text-sm text-muted-foreground"
                >
                  {caveat}
                </li>
              ))}
            </ul>
          </Section>
        ) : null}

        <Section
          title="What comes back"
          description={
            <>
              Every response uses the same envelope: <code>data</code> holds the rows,{" "}
              <code>meta</code> holds the count and the paging state. A figure&rsquo;s{" "}
              <code>value</code> is a string, not a number — these are decimal
              quantities, and parsing them to a float here would reintroduce the drift
              the decimal storage exists to avoid.
            </>
          }
        >
          <div className="space-y-3">
            {/* A row from this very series rather than an invented one: a
                fabricated sample once showed a Jakarta geo_name on a national
                budget series, which is exactly the confusion this tab exists
                to prevent. */}
            <Snippet
              label={sample ? "One row, from this series" : "One row"}
              value={JSON.stringify(sample ?? PLACEHOLDER_ROW, null, 2)}
            />
          </div>
        </Section>

        <Section
          title="Paging"
          description={
            <>
              <code>limit</code> and <code>offset</code> page the result;{" "}
              <code>meta.total</code> is the count before paging and{" "}
              <code>meta.has_more</code> says whether another page exists. Ask for what
              you need in one request where you can — a series of a few thousand figures
              is one response, and paging it costs a round trip per page.
            </>
          }
        />
      </div>

      <aside className={`space-y-4 lg:sticky lg:self-start ${BELOW_STICKY_HEADER}`}>
        <h2 className="font-heading text-sm font-semibold tracking-tight">
          Related endpoints
        </h2>
        <dl className="space-y-3">
          <Endpoint
            path={`/v1/indicators/${indicatorId}`}
            description="This indicator's record — coverage, unit, sources."
          />
          <Endpoint path="/v1/indicators" description="Every indicator held." />
          <Endpoint path="/v1/geography" description="The places figures resolve to." />
          <Endpoint path="/v1/datasets" description="What each layer holds." />
        </dl>
      </aside>
    </div>
  );
}

/** A block of code with a way to take it. */
function Snippet({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <Button
          variant="ghost"
          size="sm"
          className="h-7"
          onClick={() => void copyToClipboard(value)}
        >
          <IconCopy className="size-3.5" />
          Copy
        </Button>
      </div>
      {/* Scrolls rather than wraps: a broken URL is a URL that will not paste.
          A code block reads as something to be copied and run rather than
          something to be read, and the surface says so before the text does.
          The steps are fixed rather than themed, because this block sets its
          own ground and the pairing has to hold on its own.

          It darkens against a light page and *lifts* against a dark one: the
          dark theme's ground is already near-black, so a darker block would
          differ from it by a contrast ratio of 1.00 — invisible. */}
      <pre className="overflow-x-auto rounded-lg bg-neutral-900 px-3 py-2.5 text-xs leading-relaxed text-neutral-100 dark:border dark:border-border dark:bg-neutral-800">
        <code>{value}</code>
      </pre>
    </div>
  );
}

function Endpoint({ path, description }: { path: string; description: string }) {
  return (
    <div className="space-y-0.5">
      <dt>
        <code className="text-xs">{path}</code>
      </dt>
      <dd className="text-xs text-muted-foreground">{description}</dd>
    </div>
  );
}

/**
 * How this series is kept: the settings its figures are collected under.
 *
 * Read from the source registry, which is the authority and lives in code —
 * `terusan silver dimensions` publishes it into the lake, and this reads it
 * back. Nothing here is editable from the browser, because the serving layer is
 * read-only by design: a switch that looked like it saved and did not would be
 * worse than no switch.
 */
/**
 * What the pipelines behind this series have been doing (program.md §39).
 *
 * The question this answers is not "what do the figures say" but "is anything
 * still fetching them". A series that stopped refreshing looks identical to one
 * whose publisher has not issued anything — the figures are the same either
 * way, and only the run history tells the two apart.
 */
function LogsTab({
  indicatorId,
  source,
  ingestion,
}: {
  indicatorId: string;
  source?: Source;
  /** Started from the header menu, watched here. */
  ingestion: Ingestion;
}) {
  const runs = useQuery({
    queryKey: ["indicator-runs", indicatorId],
    queryFn: () => api.indicatorRuns(indicatorId, { limit: RUN_LIMIT }),
  });

  const rows = runs.data?.data ?? [];
  const failures = rows.filter((run) => run.status === "failed");
  const lastSuccess = rows.find((run) => run.status === "succeeded");
  const schedule = describeSchedule(source?.schedule);

  if (runs.isError) {
    return (
      <p className="max-w-3xl text-sm text-muted-foreground">
        The run history could not be read: {(runs.error as Error).message}
      </p>
    );
  }

  // An empty history is the one result that needs explaining: it means the
  // journal has no rows for this series, not that the series is broken. Saying
  // which is the difference between a reader checking the pipeline and a
  // reader distrusting the page.
  if (!runs.isLoading && !rows.length) {
    return (
      <div className="max-w-3xl space-y-3">
        <Section
          title="No runs recorded"
          description={
            <>
              Nothing in the journal names this series or the source behind it. Either
              the pipelines have not run since runs began being recorded, or they are
              being run somewhere that does not write to this lake.
            </>
          }
        />
        <IngestionPanel ingestion={ingestion} source={source} />
        <div className="rounded-lg border bg-muted/40 p-3">
          <p className="text-xs text-muted-foreground">
            Every run writes a row as it finishes, whether it succeeded or not. Start
            one and it appears here:
          </p>
          <code className="mt-2 block font-mono text-xs">
            terusan sources run {source?.source_id ?? "<source>"}
          </code>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <IngestionPanel ingestion={ingestion} source={source} />

      <Section
        title="Runs"
        action={<RunNowButton ingestion={ingestion} />}
        description={
          <>
            Every ingestion of {source ? source.name : "the source behind this series"}{" "}
            and every normalization that produced it, newest first.{" "}
            {schedule
              ? `The source is scheduled ${schedule.toLowerCase()}, so a gap wider than that is worth looking at.`
              : "The source has no schedule, so it runs only when someone runs it."}{" "}
            Extraction reads the whole of RAW at once and belongs to no single series,
            so it is not listed here.
          </>
        }
      >
        <div className="grid gap-3 sm:grid-cols-3">
          <RunFact
            label="Last run"
            value={rows[0] ? formatMoment(rows[0].started_at) : "—"}
            detail={
              rows[0]
                ? `${rows[0].status === "failed" ? "Failed" : "Succeeded"}${
                    formatRelative(rows[0].started_at)
                      ? `, ${formatRelative(rows[0].started_at)}`
                      : ""
                  }`
                : undefined
            }
            alarming={rows[0]?.status === "failed"}
          />
          <RunFact
            label="Last success"
            value={lastSuccess ? formatMoment(lastSuccess.started_at) : "None recorded"}
            detail={
              lastSuccess
                ? `${formatCount(lastSuccess.records_out)} record${
                    lastSuccess.records_out === 1 ? "" : "s"
                  } written`
                : "Every recorded run of this series failed."
            }
            alarming={!lastSuccess}
          />
          <RunFact
            label="Failures"
            value={`${failures.length} of ${rows.length}`}
            detail={`In the last ${rows.length} recorded run${rows.length === 1 ? "" : "s"}.`}
            alarming={failures.length > 0}
          />
        </div>

        <DataTable
          columns={runColumns}
          data={rows}
          isLoading={runs.isLoading}
          getRowId={(run) => run.run_id}
          emptyMessage="No runs recorded."
        />
      </Section>

      {failures.length ? (
        <Section
          title="What failed"
          description="The message each failed run recorded. A scraper that broke on a layout change says so here rather than in a log file nobody opens."
        >
          <div className="space-y-3">
            {failures.map((run) => (
              <div
                key={run.run_id}
                className="rounded-lg border border-destructive/40 p-3"
              >
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-mono text-xs">{run.pipeline}</span>
                  <span className="text-muted-foreground">
                    {formatMoment(run.started_at)}
                  </span>
                  {run.trigger ? <Badge variant="outline">{run.trigger}</Badge> : null}
                </div>
                <p className="mt-2 font-mono text-xs break-words text-destructive">
                  {run.error_message ?? "No message was recorded."}
                </p>
                {run.detail ? (
                  <p className="mt-2 font-mono text-xs break-words text-muted-foreground">
                    {run.detail}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        </Section>
      ) : null}
    </div>
  );
}

/**
 * Start the scraper behind this series, from the page that shows its figures.
 *
 * Beside the run history rather than only in the header menu: the reader who
 * has just noticed the last run failed three days ago is looking at this
 * table, and making them find a menu to act on what they just read is a small
 * cruelty.
 */
function RunNowButton({ ingestion }: { ingestion: Ingestion }) {
  if (!ingestion.available) return null;

  return (
    <Button
      variant="outline"
      size="sm"
      disabled={ingestion.running || ingestion.starting}
      title={ingestion.unavailable}
      onClick={() => ingestion.start()}
    >
      {ingestion.running ? (
        <IconRefresh className="size-4 animate-spin" />
      ) : (
        <IconPlayerPlay className="size-4" />
      )}
      {ingestion.running ? "Ingesting…" : "Run ingestion now"}
    </Button>
  );
}

/**
 * What the ingestion started from this page is doing.
 *
 * Shown only once something has been started, and it stays after it finishes:
 * a run that failed in eleven seconds would otherwise vanish before the reader
 * looked up, leaving a table that never gained a row and no reason why.
 *
 * The job and the run below it are two different records of one thing, and the
 * distinction is worth keeping straight. This is the process, live, and it dies
 * with the server that started it. The row that appears in the table is what
 * the pipeline itself wrote to the journal when it finished, and that is the
 * one that is still there next month.
 */
function IngestionPanel({
  ingestion,
  source,
}: {
  ingestion: Ingestion;
  source?: Source;
}) {
  const { job, error } = ingestion;

  if (error) {
    return (
      <div className="rounded-lg border border-destructive/40 p-3">
        <p className="text-sm font-medium text-destructive">
          The run could not be started
        </p>
        <p className="mt-1 text-xs break-words text-muted-foreground">{error}</p>
      </div>
    );
  }

  if (!job) return null;

  const running = job.status === "running";

  return (
    <div
      className={`rounded-lg border p-3 ${
        job.status === "failed" ? "border-destructive/40" : ""
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        {running ? (
          <IconRefresh className="size-4 animate-spin text-muted-foreground" />
        ) : null}
        <p className="text-sm font-medium">
          {running
            ? `Ingesting ${source?.name ?? job.target}…`
            : job.status === "succeeded"
              ? `Ingestion of ${source?.name ?? job.target} finished`
              : `Ingestion of ${source?.name ?? job.target} failed`}
        </p>
        <Badge variant="outline">{formatDuration(job.duration_seconds)}</Badge>
        {job.exit_code !== undefined && job.exit_code !== 0 ? (
          <Badge variant="outline">exit {job.exit_code}</Badge>
        ) : null}
        <span className="ml-auto text-xs text-muted-foreground">
          started {formatMoment(job.started_at)}
        </span>
      </div>

      {/* The command, because the honest answer to "what did that button do"
          is the line it ran, and because a run that dies here is one a
          maintainer will want to repeat in a terminal where they can poke it. */}
      <code className="mt-2 block font-mono text-xs break-all text-muted-foreground">
        {job.command}
      </code>

      {job.error ? (
        <p className="mt-2 font-mono text-xs break-words text-destructive">
          {job.error}
        </p>
      ) : null}

      <JobOutput output={job.output} running={running} />

      {!running ? (
        <p className="mt-2 text-xs text-muted-foreground">
          What the run recorded is in the table below. A run that succeeded and wrote no
          records landed nothing — the publisher had nothing new, or the scraper stopped
          finding it.
        </p>
      ) : null}
    </div>
  );
}

/** How many lines of a running job are on screen before it scrolls. */
const OUTPUT_ROWS = 14;

/**
 * The tail of what the pipeline printed.
 *
 * Follows the end while the run is going, and stops following the moment the
 * reader scrolls up — a log that yanks itself back to the bottom while someone
 * is reading the traceback they scrolled to is unusable.
 */
function JobOutput({ output, running }: { output: string[]; running: boolean }) {
  const [following, setFollowing] = useState(true);
  const box = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (!following || !box.current) return;
    box.current.scrollTop = box.current.scrollHeight;
  }, [following, output]);

  if (!output.length) {
    return (
      <p className="mt-2 text-xs text-muted-foreground">
        {running ? "Waiting for the first output…" : "The run printed nothing."}
      </p>
    );
  }

  return (
    <pre
      ref={box}
      onScroll={(event) => {
        const el = event.currentTarget;
        // A couple of pixels of slack: a scroll position is fractional at some
        // zoom levels and an exact comparison would read as "not at the bottom"
        // on a box nobody has touched.
        setFollowing(el.scrollHeight - el.scrollTop - el.clientHeight < 4);
      }}
      className="mt-2 max-h-72 overflow-auto rounded-md bg-muted/60 p-2 font-mono text-xs leading-relaxed whitespace-pre-wrap"
      style={{ minHeight: `${OUTPUT_ROWS}rem` }}
    >
      {output.join("\n")}
    </pre>
  );
}

/** One number about the run history, and what it means. */
function RunFact({
  label,
  value,
  detail,
  alarming,
}: {
  label: string;
  value: string;
  detail?: string;
  /** Worth a reader's attention: a failure, or a series with no success at all. */
  alarming?: boolean;
}) {
  return (
    <div className="rounded-lg border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={`mt-1 text-sm font-medium ${alarming ? "text-destructive" : ""}`}>
        {value}
      </p>
      {detail ? <p className="mt-1 text-xs text-muted-foreground">{detail}</p> : null}
    </div>
  );
}

/** How many runs the tab reads. Enough to see a pattern, not a log viewer. */
const RUN_LIMIT = 50;

const runColumns: ColumnDef<PipelineRun>[] = [
  {
    accessorKey: "started_at",
    header: "Started",
    meta: { width: "w-48" },
    cell: ({ row }) => (
      <StackedCell
        primary={formatMoment(row.original.started_at)}
        secondary={formatRelative(row.original.started_at)}
      />
    ),
  },
  {
    accessorKey: "pipeline",
    header: "Pipeline",
    cell: ({ row }) => (
      <StackedCell
        primary={<span className="font-mono text-xs">{row.original.pipeline}</span>}
        secondary={row.original.kind}
      />
    ),
  },
  {
    accessorKey: "status",
    header: "Status",
    meta: { width: "w-32" },
    cell: ({ row }) => {
      const run = row.original;
      // A dry run lands nothing on purpose, so its zero records must not read
      // as an ingestion that has stopped working.
      if (run.dry_run) return <Badge variant="outline">Dry run</Badge>;
      return run.status === "failed" ? (
        <Badge variant="destructive">Failed</Badge>
      ) : (
        <Badge variant="secondary">Succeeded</Badge>
      );
    },
  },
  {
    id: "records",
    header: "Records",
    meta: { width: "w-36" },
    cell: ({ row }) => (
      <StackedCell
        primary={formatCount(row.original.records_out)}
        secondary={`of ${formatCount(row.original.records_in)} read`}
      />
    ),
  },
  {
    accessorKey: "duration_seconds",
    header: "Took",
    meta: { width: "w-24" },
    cell: ({ row }) => (
      <span className="tabular-nums">
        {formatDuration(row.original.duration_seconds)}
      </span>
    ),
  },
  {
    accessorKey: "trigger",
    header: "Trigger",
    meta: { width: "w-28" },
    cell: ({ row }) => (
      <span className="text-muted-foreground">{row.original.trigger}</span>
    ),
  },
];

function SettingsTab({
  source,
  indicator,
  dimension,
  seriesCount,
}: {
  source?: Source;
  indicator?: Indicator;
  dimension: Dimension;
  seriesCount: number;
}) {
  const schedule = describeSchedule(source?.schedule);

  return (
    <div className="max-w-3xl space-y-8">
      <Section
        title="Collection"
        description="When this series is fetched, and how hard its server may be asked. These are the source's own settings — changing them means changing the registry the pipeline runs from."
      >
        <dl className="divide-y">
          <Setting
            label="Refresh schedule"
            value={schedule ?? "Manual only"}
            detail={
              source?.schedule
                ? `Cron ${source.schedule}, in the server's timezone. The scraper runs then; whether the agency has published anything new by that point is the agency's business.`
                : "No schedule is recorded, so this source is collected only when someone runs it."
            }
          />
          <Setting
            label="Expected cadence"
            value={source?.update_frequency ?? indicator?.temporal_resolution}
            detail="How often the publisher issues new figures. Distinct from the refresh schedule: a monthly release checked daily is normal, because the release date moves."
          />
          <Setting
            label="Collection"
            value={source ? (source.active ? "Scheduled" : "Paused") : undefined}
            detail={
              source?.active
                ? "The scraper runs on its schedule."
                : "The scraper is registered but will not run until it is re-enabled."
            }
          />
          <Setting
            label="Request rate"
            value={
              source
                ? `${source.max_requests_per_second} request${
                    source.max_requests_per_second === 1 ? "" : "s"
                  } per second`
                : undefined
            }
            detail="The source's own ceiling. The runner also limits per host, so several sources sharing one government server cannot add up to a hammering."
          />
          <Setting
            label="How it is collected"
            value={source ? source.collection_method.replace(/_/g, " ") : undefined}
            detail={
              source
                ? `Published as ${source.source_type.replace(/_/g, " ")}. The method decides what lands in RAW — a file, an API response, or a scraped page.`
                : undefined
            }
          />
        </dl>
      </Section>

      <Section
        title="Access and licence"
        description="What may be done with these figures, and where they came from. The licence travels with the data: a figure lifted out of here carries the terms its publisher set."
      >
        <dl className="divide-y">
          <Setting
            label="Licence"
            value={source?.license ?? "Not recorded"}
            detail={
              source?.license
                ? "As the publisher states it. Check it before redistributing — these terms are the publisher's, not this warehouse's."
                : "No licence is recorded for this source, which means redistribution terms are unknown rather than permissive."
            }
          />
          <Setting
            label="Published openly"
            value={source?.base_url ? "Yes, by the agency" : "Unknown"}
            detail={
              source?.base_url
                ? "The figures are published on a public government portal, so anyone can obtain them at the source. What this warehouse adds is that they are parsed, typed and comparable."
                : undefined
            }
            link={source?.base_url}
          />
          <Setting
            label="Access to this copy"
            value="Not configured"
            detail="Who may read the warehouse is not part of the source registry, so this page cannot state it. Until it is modelled, treat access as an operational question rather than something the data answers."
          />
          <Setting
            label="Coverage"
            value={source?.country === "ID" ? "Indonesia" : source?.country}
            detail={
              dimension === "none"
                ? "A single national series."
                : `Broken down by ${dimensionLabel(dimension).toLowerCase()} — ${formatCount(seriesCount)} of them.`
            }
          />
        </dl>
      </Section>

      <Section
        title="Storage"
        description="Where these figures sit in the warehouse, and what that layer promises about them."
      >
        <dl className="divide-y">
          <Setting
            label="Layer"
            value="Silver"
            detail="Normalized and typed: periods are bounded, places are resolved, and values are decimals rather than the strings the publisher wrote. RAW keeps the original bytes, so any of this can be rebuilt."
          />
          <Setting
            label="Format"
            value="Parquet, partitioned by indicator and frequency"
            detail="So a query for one series reads one partition instead of the whole lake."
          />
          <Setting
            label="Figures held"
            value={indicator ? `${formatCount(indicator.observations)}` : undefined}
            detail={
              indicator
                ? `Covering ${indicator.period_start} to ${indicator.period_end}. Re-running normalization replaces them rather than appending, so a corrected mapping leaves nothing stale behind.`
                : undefined
            }
          />
        </dl>
      </Section>
    </div>
  );
}

/** One setting: what it is set to, and what that means. */
function Setting({
  label,
  value,
  detail,
  link,
}: {
  label: string;
  value?: string;
  detail?: string;
  link?: string;
}) {
  return (
    <div className="grid gap-1 py-3 sm:grid-cols-[12rem_minmax(0,1fr)] sm:gap-4">
      <dt className="text-sm font-medium">{label}</dt>
      <dd className="space-y-1">
        <p className="text-sm">
          {link ? (
            <a
              href={link}
              target="_blank"
              rel="noreferrer noopener"
              className="underline underline-offset-4 hover:text-foreground"
            >
              {value ?? "—"}
            </a>
          ) : (
            (value ?? <span className="text-muted-foreground">—</span>)
          )}
        </p>
        {detail ? <p className="text-xs text-muted-foreground">{detail}</p> : null}
      </dd>
    </div>
  );
}
