import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import {
  IconArrowLeft,
  IconChartBar,
  IconCopy,
  IconDatabase,
} from "@tabler/icons-react";
import { z } from "zod";

import { ClampedText } from "~/components/clamped-text";
import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { SearchInput } from "~/components/search-input";
import { BELOW_STICKY_HEADER, StickyHeader } from "~/components/sticky-header";
import { TablePagination } from "~/components/table-pagination";
import { TagList } from "~/components/tag-list";
import { Badge } from "~/components/ui/badge";
import { api, type Dataset, type Indicator } from "~/lib/api";
import { formatCount, formatDate, formatRelative } from "~/lib/format";
import { datasetLabel, indicatorLabel, titleFromId } from "~/lib/labels";
import { asText, textParam } from "~/lib/search-params";
import { facetVocabulary, relatedTags, tagKind, topicRecords } from "~/lib/topics";

const searchSchema = z.object({
  /** Which series the reader is looking for, in the URL like every other
   *  narrowing here — a topic filtered down to one release is a link. */
  q: textParam,
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/topics/$tag")({
  validateSearch: searchSchema,
  component: TopicDetail,
});

/** Rows per page in the series table. */
const PAGE_SIZE = 25;

/**
 * The collections filed under a topic.
 *
 * Narrower than the datasets table: every row here shares the topic, so the
 * tag column that page would carry would repeat one value down the page.
 */
const datasetColumns: ColumnDef<Dataset>[] = [
  {
    accessorKey: "dataset_id",
    header: "Dataset",
    cell: ({ row }) => (
      <Link
        to="/datasets/$datasetId"
        params={{ datasetId: row.original.dataset_id }}
        className="underline-offset-4 hover:underline"
      >
        <ClampedText className="max-w-[20rem] font-medium">
          {datasetLabel(row.original)}
        </ClampedText>
      </Link>
    ),
  },
  {
    accessorKey: "organization",
    header: "Published by",
    meta: { width: "w-56" },
    cell: ({ row }) => (
      <StackedCell
        primary={
          row.original.organization ? (
            <ClampedText className="max-w-[14rem]">
              {row.original.organization}
            </ClampedText>
          ) : (
            "—"
          )
        }
        secondary={row.original.source_id}
      />
    ),
  },
  {
    id: "indicators",
    header: "Series",
    meta: { align: "right" },
    cell: ({ row }) =>
      formatCount(row.original.series ?? row.original.indicators.length),
  },
  {
    accessorKey: "observations",
    header: "Figures",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.observations),
  },
  {
    id: "coverage",
    header: "Coverage",
    cell: ({ row }) => `${row.original.period_start} – ${row.original.period_end}`,
  },
];

/** The series under a topic, in the same shape the indicators table uses. */
const seriesColumns: ColumnDef<Indicator>[] = [
  {
    accessorKey: "indicator_id",
    header: "Series",
    meta: { width: "w-72" },
    cell: ({ row }) => (
      <Link
        to="/indicators/$indicatorId"
        params={{ indicatorId: row.original.indicator_id }}
        className="underline-offset-4 hover:underline"
      >
        <div className="leading-tight">
          <ClampedText className="max-w-[17rem] font-medium">
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
    meta: { width: "w-44" },
    cell: ({ row }) =>
      row.original.unit ? (
        <ClampedText className="max-w-[11rem]">{row.original.unit}</ClampedText>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
  {
    id: "source",
    header: "Source",
    enableSorting: false,
    meta: { width: "w-44" },
    cell: ({ row }) => {
      const [first, ...rest] = row.original.sources;
      if (!first) return <span className="text-muted-foreground">—</span>;
      return (
        <StackedCell
          primary={<ClampedText className="max-w-[11rem]">{first}</ClampedText>}
          secondary={rest.length ? `+${rest.length} more` : undefined}
        />
      );
    },
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

/**
 * One topic, and what is filed under it.
 *
 * The page a reader lands on when they know the subject but not the
 * publication — "inflation" rather than a dataset name. It answers who
 * publishes on it, over what period, in how many series, and what else the
 * same records are filed under; each series then has its own page for the
 * figures themselves.
 *
 * A series reaches this page either by carrying the tag itself or by belonging
 * to a dataset that does. The pipeline only hangs a tag on a dataset when its
 * series agree on it or when the fact is the dataset's own, so the inheritance
 * cannot pull in a series the topic does not describe.
 */
function TopicDetail() {
  const { tag } = Route.useParams();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });

  // The catalogue in full, from the same query keys the list pages use: a
  // topic is a slice of it rather than a record of its own, so there is
  // nothing to fetch by id and arriving from either list costs no request.
  const datasetQuery = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.datasets(),
  });
  const indicatorQuery = useQuery({
    queryKey: ["indicators", "folded"],
    // A price's four series as one, as every list counts them.
    queryFn: () => api.indicators({ fold: "ohlc" }),
  });
  const sourceQuery = useQuery({ queryKey: ["sources"], queryFn: () => api.sources() });

  const loading = datasetQuery.isLoading || indicatorQuery.isLoading;

  const datasets = datasetQuery.data?.data ?? [];
  const indicators = indicatorQuery.data?.data ?? [];
  const records = topicRecords(tag, datasets, indicators);

  const facets = facetVocabulary({
    datasets,
    indicators,
    sources: sourceQuery.data?.data ?? [],
  });
  const kind = tagKind(tag, facets);
  // A topic's neighbours are other topics: nearly every record is `annual` and
  // `statistics` too, and listing those as related would be listing the
  // catalogue. A facet's neighbours are whatever it was filed beside.
  const related = relatedTags(tag, records, {
    exclude: kind === "topic" ? facets : undefined,
  });

  const figures = records.indicators.reduce(
    (total, indicator) => total + indicator.observations,
    0,
  );
  // Lexicographic, which is what these period strings are ordered by anyway.
  const starts = [
    ...records.indicators.map((indicator) => indicator.period_start),
    ...records.datasets.map((dataset) => dataset.period_start),
  ]
    .filter(Boolean)
    .sort();
  const ends = [
    ...records.indicators.map((indicator) => indicator.period_end),
    ...records.datasets.map((dataset) => dataset.period_end),
  ]
    .filter(Boolean)
    .sort();
  const updated = [
    ...records.indicators.map((indicator) => indicator.last_updated),
    ...records.datasets.map((dataset) => dataset.last_updated),
  ]
    .filter(Boolean)
    .sort()
    .pop();
  const publishers = [
    ...new Set(
      [
        ...records.datasets.map((dataset) => dataset.organization),
        ...records.indicators.map(
          (indicator) => indicator.publisher ?? indicator.sources[0],
        ),
      ].filter(Boolean) as string[],
    ),
  ].sort();

  // Narrowed in place: the series are already in hand, and six hundred of them
  // is a list nobody scrolls. The publisher's code is matched as well as the
  // name — someone arriving from FRED has NASDAQNQID55LMN in hand, not a title.
  const needle = asText(search.q)?.toLowerCase() ?? "";
  const shown = needle
    ? records.indicators.filter(
        (indicator) =>
          indicatorLabel(indicator).toLowerCase().includes(needle) ||
          (indicator.code?.toLowerCase().includes(needle) ?? false) ||
          indicator.indicator_id.toLowerCase().includes(needle),
      )
    : records.indicators;

  // Paged in place, like the filtering above it. A page past the end of a
  // freshly narrowed list would render empty, so it is clamped rather than
  // trusted.
  const pageCount = Math.max(1, Math.ceil(shown.length / PAGE_SIZE));
  const page = Math.min(search.page ?? 0, pageCount - 1);
  const paged = shown.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const empty = !loading && !records.datasets.length && !records.indicators.length;

  return (
    <div className="space-y-6">
      <StickyHeader
        heading={
          <div className="space-y-2">
            <BackLink />
            <PageHeader
              // The tag as a reader would say it. The tag as the filter takes
              // it is in the sidebar, where anyone who needs to quote it will
              // look; a heading reading `external-sector` is a database row.
              title={titleFromId(tag)}
              description={
                loading
                  ? undefined
                  : [
                      kind === "topic"
                        ? "A subject, read off the titles of the records that carry it."
                        : "A fact about the records rather than a subject — a publisher, a cadence or a unit.",
                      `${formatCount(figures)} figures in ${formatCount(records.indicators.length)} series across ${formatCount(records.datasets.length)} dataset${records.datasets.length === 1 ? "" : "s"}${
                        starts.length
                          ? `, covering ${starts[0]} to ${ends[ends.length - 1]}`
                          : ""
                      }.`,
                    ].join(" ")
              }
              actions={
                <RowActions
                  label="Topic actions"
                  actions={[
                    {
                      label: "Copy tag",
                      icon: IconCopy,
                      onSelect: () => void copyToClipboard(tag),
                    },
                    {
                      // The same filter, on the pages that page, sort and
                      // export it. This page is the overview; those are the
                      // tools.
                      label: "Filter datasets by this tag",
                      icon: IconDatabase,
                      onSelect: () =>
                        void navigate({ to: "/datasets", search: { tag: [tag] } }),
                    },
                    {
                      label: "Filter indicators by this tag",
                      icon: IconChartBar,
                      onSelect: () =>
                        void navigate({ to: "/indicators", search: { tag: [tag] } }),
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
            <Fact
              label="Kind"
              value={kind === "topic" ? "Topic" : "Facet"}
              hint={kind === "topic" ? "from the titles" : "from the columns"}
            />
            {/* What the API and the URL take. Shown rather than prettified
                away: it is the only form of this word that filters. */}
            <Fact label="Tag" value={tag} mono />
            <Fact
              label="Datasets"
              value={loading ? undefined : formatCount(records.datasets.length)}
            />
            <Fact
              label="Series"
              value={loading ? undefined : formatCount(records.indicators.length)}
              hint={loading ? undefined : "incl. inherited"}
            />
            <Fact label="Figures" value={loading ? undefined : formatCount(figures)} />
            <Fact
              label="Coverage"
              value={
                starts.length ? `${starts[0]} – ${ends[ends.length - 1]}` : undefined
              }
            />
            <Fact
              label="Publishers"
              // Named while they fit, counted once they do not: three names
              // say who is behind the topic, eleven say nothing.
              value={
                publishers.length
                  ? publishers.length <= 3
                    ? publishers.join(", ")
                    : `${formatCount(publishers.length)} publishers`
                  : undefined
              }
              hint={
                publishers.length > 3 ? publishers.slice(0, 2).join(", ") : undefined
              }
            />
            <Fact
              label="Last updated"
              value={updated ? formatDate(updated) : undefined}
              hint={updated ? formatRelative(updated) : undefined}
            />
            {/* Where else the same figures live — the way out of a topic that
                turned out to be the wrong one. Clickable, like the tags on a
                dataset: a tag a reader cannot act on is decoration. */}
            {related.length ? (
              <div>
                <dt className="text-xs font-medium text-muted-foreground">Alongside</dt>
                <dd className="mt-1">
                  <TagList
                    tags={related}
                    max={related.length}
                    onSelect={(other) =>
                      void navigate({ to: "/topics/$tag", params: { tag: other } })
                    }
                  />
                </dd>
              </div>
            ) : null}
          </dl>
        </aside>

        <div className="min-w-0 space-y-8">
          {empty ? (
            <p className="rounded-sm border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
              No record carries this tag. Tags are derived with the record they describe
              and rebuilt whenever a source is re-normalized, so one that was linked
              earlier can stop existing.
            </p>
          ) : null}

          {/* While loading too, so the section arrives as a skeleton rather
              than popping in under the reader once the catalogue lands. */}
          {loading || records.datasets.length ? (
            <section className="space-y-3">
              <div className="space-y-1">
                <h2 className="font-heading text-lg font-semibold tracking-tight">
                  The datasets filed under it
                </h2>
                <p className="max-w-2xl text-sm text-muted-foreground">
                  Collections as their publishers issue them. A dataset carries this tag
                  when the series inside it agree on it, or when the fact is the
                  collection&rsquo;s own.
                </p>
              </div>
              <DataTable
                columns={datasetColumns}
                data={records.datasets}
                isLoading={loading}
                loadingRows={3}
                emptyMessage="No dataset carries this tag."
                getRowId={(row) => row.dataset_id}
              />
            </section>
          ) : null}

          <section className="space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="space-y-1">
                <h2 className="font-heading text-lg font-semibold tracking-tight">
                  The series under it
                </h2>
                <p className="max-w-2xl text-sm text-muted-foreground">
                  What is measured on this subject. Each carries its own unit and
                  frequency, so they are listed rather than summed.
                </p>
              </div>
              {/* Only where the list is long enough to need it: a topic with
                  four series is read, not searched. */}
              {records.indicators.length > 10 ? (
                <div className="w-full sm:w-64">
                  <SearchInput
                    value={asText(search.q)}
                    placeholder="Search series"
                    onSearch={(q) => navigate({ search: { q, page: 0 } })}
                  />
                </div>
              ) : null}
            </div>

            {!loading && !records.indicators.length ? (
              <p className="rounded-sm border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
                No series carries this tag. The datasets above do, but none of their
                series has been published yet.
              </p>
            ) : (
              <div className="space-y-4">
                <DataTable
                  columns={seriesColumns}
                  data={paged}
                  isLoading={loading}
                  loadingRows={8}
                  emptyMessage={`No series here match “${asText(search.q) ?? ""}”.`}
                  getRowId={(row) => row.indicator_id}
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
                        {shown.length !== records.indicators.length
                          ? ` filtered from ${formatCount(records.indicators.length)}`
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
      to="/topics"
      className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
    >
      <IconArrowLeft className="size-4" />
      All topics
    </Link>
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
  /** For the tag itself, which is read character by character. */
  mono?: boolean;
}) {
  return (
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
