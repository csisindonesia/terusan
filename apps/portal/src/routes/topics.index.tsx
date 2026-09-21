import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconCopy, IconDownload } from "@tabler/icons-react";
import { z } from "zod";

import { ClampedText } from "~/components/clamped-text";
import { DataTable, StackedCell } from "~/components/data-table";
import { ChoiceList, FilterChip, summarise } from "~/components/filter-chip";
import { PageHeader } from "~/components/page-header";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { SearchInput } from "~/components/search-input";
import { StickyHeader } from "~/components/sticky-header";
import { TablePagination } from "~/components/table-pagination";
import { TableToolbar } from "~/components/table-toolbar";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount } from "~/lib/format";
import { titleFromId } from "~/lib/labels";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";
import { collectTopics, type TopicSummary } from "~/lib/topics";

const searchSchema = z.object({
  /**
   * Which kinds of tag to list. Absent means topics alone, because that is
   * what the page is called and what a reader browsing subjects wants; the
   * facets are here because they are the same vocabulary and filtering by one
   * is the same act, not because anyone browses "monthly".
   */
  kind: listParam,
  q: textParam,
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/topics/")({
  validateSearch: searchSchema,
  component: Topics,
});

const PAGE_SIZE = 50;

const KINDS = [
  {
    value: "topic",
    label: "Topics",
    hint: "What a record is about, read off its title",
  },
  {
    value: "facet",
    label: "Facets",
    hint: "Who published it, how often, in what unit",
  },
];

function kindLabel(value: string): string {
  return KINDS.find((kind) => kind.value === value)?.label ?? value;
}

const columns: ColumnDef<TopicSummary>[] = [
  {
    accessorKey: "tag",
    header: "Topic",
    cell: ({ row }) => (
      // The tag as a reader would say it, and under it the tag as the filter
      // takes it — the second is what goes in a URL or an API call, so it is
      // shown rather than prettified away.
      <Link
        to="/topics/$tag"
        params={{ tag: row.original.tag }}
        className="underline-offset-4 hover:underline"
      >
        <div className="leading-tight">
          <ClampedText className="max-w-[18rem] font-medium">
            {titleFromId(row.original.tag)}
          </ClampedText>
          <div className="font-mono text-xs text-muted-foreground">
            {row.original.tag}
          </div>
        </div>
      </Link>
    ),
  },
  {
    accessorKey: "kind",
    header: "Kind",
    cell: ({ row }) => (
      <Badge variant={row.original.kind === "topic" ? "secondary" : "outline"}>
        {row.original.kind}
      </Badge>
    ),
  },
  {
    accessorKey: "datasets",
    header: "Datasets",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.datasets),
  },
  {
    accessorKey: "indicators",
    header: "Series",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.indicators),
  },
  {
    accessorKey: "observations",
    header: "Figures",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.observations),
  },
  {
    id: "publishers",
    header: "Publishers",
    enableSorting: false,
    cell: ({ row }) => {
      const [first, ...rest] = row.original.publishers;
      if (!first) return <span className="text-muted-foreground">—</span>;
      return (
        <StackedCell
          primary={<ClampedText className="max-w-[14rem]">{first}</ClampedText>}
          secondary={rest.length ? `+${rest.length} more` : undefined}
        />
      );
    },
  },
  {
    id: "coverage",
    header: "Coverage",
    cell: ({ row }) =>
      row.original.periodStart
        ? `${row.original.periodStart} – ${row.original.periodEnd}`
        : "—",
  },
  {
    id: "actions",
    header: "",
    enableSorting: false,
    meta: { align: "right" },
    cell: ({ row }) => (
      <RowActions
        actions={[
          {
            label: "Copy tag",
            icon: IconCopy,
            onSelect: () => void copyToClipboard(row.original.tag),
          },
        ]}
      />
    ),
  },
];

const EXPORT_COLUMNS = [
  { key: "tag" as const, header: "tag" },
  { key: "kind" as const, header: "kind" },
  { key: "datasets" as const, header: "datasets" },
  { key: "indicators" as const, header: "indicators" },
  { key: "observations" as const, header: "observations" },
  { key: "publishers" as const, header: "publishers" },
  { key: "periodStart" as const, header: "period_start" },
  { key: "periodEnd" as const, header: "period_end" },
];

/**
 * The subjects the catalogue covers, from the tags its records carry.
 *
 * There is no topic table in the warehouse and there should not be one: a tag
 * is derived with the record it describes, so the vocabulary is whatever the
 * catalogue currently says it is. This page assembles it from the three
 * catalogue calls the portal already makes, which is why it needs no endpoint
 * of its own and cannot fall out of step with the records it summarises.
 */
function Topics() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });

  // The same query keys the datasets and indicators pages use, so arriving
  // here after either is served from the cache rather than re-fetched.
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });
  const indicators = useQuery({
    queryKey: ["indicators"],
    queryFn: () => api.indicators(),
  });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.sources() });

  const isLoading = datasets.isLoading || indicators.isLoading || sources.isLoading;

  const all = collectTopics({
    datasets: datasets.data?.data ?? [],
    indicators: indicators.data?.data ?? [],
    sources: sources.data?.data ?? [],
  });

  const kinds = asTextList(search.kind);
  const shownKinds = kinds.length ? kinds : ["topic"];
  const needle = asText(search.q)?.toLowerCase() ?? "";

  const rows = all.filter(
    (topic) =>
      shownKinds.includes(topic.kind) &&
      (!needle ||
        topic.tag.includes(needle) ||
        titleFromId(topic.tag).toLowerCase().includes(needle) ||
        topic.publishers.some((name) => name.toLowerCase().includes(needle))),
  );

  /**
   * Turning one kind on or off.
   *
   * Unticking the only kind that is on leaves the other rather than nothing:
   * an empty selection reads as the default here, so "not topics" has to mean
   * facets or the chip would undo itself. The default is kept out of the URL.
   */
  function chooseKind(value: string) {
    const next = shownKinds.includes(value)
      ? shownKinds.filter((kind) => kind !== value)
      : [...shownKinds, value];
    const resolved = next.length
      ? next
      : KINDS.map((kind) => kind.value).filter((kind) => kind !== value);
    const isDefault = resolved.length === 1 && resolved[0] === "topic";
    navigate({
      search: (prev) => ({ ...prev, kind: isDefault ? undefined : resolved, page: 0 }),
    });
  }

  // Paged in place, like every other catalogue table here: the vocabulary is
  // assembled from data already in hand, so a page is a slice.
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(search.page ?? 0, pageCount - 1);
  const visible = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Topics"
            count={rows.length}
            isLoading={isLoading}
            description="What the catalogue is about, from the tags its datasets and series carry. Tags are derived with the record they describe rather than typed by hand, so this vocabulary is what the warehouse currently holds — not a list someone maintains beside it."
            actions={
              <Button
                variant="outline"
                size="sm"
                disabled={!rows.length}
                onClick={() =>
                  downloadCsv(
                    "topics.csv",
                    toCsv(rows as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
                  )
                }
              >
                <IconDownload className="size-4" />
                Export
              </Button>
            }
          />
        }
        filters={
          <TableToolbar
            filters={
              <>
                <FilterChip
                  label="Kind"
                  // What is on rather than what the URL carries: the default
                  // is a filter too, and a quiet chip over a filtered table
                  // is the thing the sticky header exists to prevent.
                  value={summarise(shownKinds, kindLabel)}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, kind: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={KINDS}
                    selected={shownKinds}
                    onToggle={chooseKind}
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, kind: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                {kinds.length || search.q ? (
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
                placeholder="Search topics and publishers"
                onSearch={(q) =>
                  navigate({ search: (prev) => ({ ...prev, q, page: 0 }) })
                }
              />
            }
          />
        }
      />

      <DataTable
        columns={columns}
        data={visible}
        isLoading={isLoading}
        emptyMessage="No topics match this filter."
        getRowId={(row) => row.tag}
      />

      <TablePagination
        page={page}
        total={rows.length}
        pageSize={PAGE_SIZE}
        onPage={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        summary={
          rows.length ? (
            <>
              {formatCount(page * PAGE_SIZE + 1)}–
              {formatCount(Math.min((page + 1) * PAGE_SIZE, rows.length))} of{" "}
              {formatCount(rows.length)} topic{rows.length === 1 ? "" : "s"}
              {rows.length !== all.length
                ? ` (${formatCount(all.length)} tags in all)`
                : null}
            </>
          ) : null
        }
      />
    </div>
  );
}
