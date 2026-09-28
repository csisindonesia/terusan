import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconArrowRight, IconCopy, IconDownload } from "@tabler/icons-react";
import { useState } from "react";
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
import { toggle } from "~/lib/multi";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";
import { collectTopics, type TopicSummary } from "~/lib/topics";

const searchSchema = z.object({
  /**
   * Which kinds of tag to list. Absent means no filter, so both: topics and
   * facets are one vocabulary, and filtering by either is the same act.
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
    id: "name",
    header: "Topic",
    cell: ({ row }) => (
      <Link
        to="/topics/$tag"
        params={{ tag: row.original.tag }}
        className="underline-offset-4 hover:underline"
      >
        <ClampedText className="max-w-[18rem] font-medium">
          {titleFromId(row.original.tag)}
        </ClampedText>
      </Link>
    ),
  },
  {
    // The tag as the filter takes it — what goes in a URL or an API call — so
    // it is shown rather than prettified away. Clamped, like the publisher:
    // facet tags carry a whole publisher's name.
    accessorKey: "tag",
    header: "Slug",
    cell: ({ row }) => (
      <ClampedText className="max-w-[8rem] font-mono text-xs text-muted-foreground">
        {row.original.tag}
      </ClampedText>
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
          primary={<ClampedText className="max-w-[12rem]">{first}</ClampedText>}
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
            label: "Open",
            icon: IconArrowRight,
            onSelect: () => {
              window.location.href = `/topics/${encodeURIComponent(row.original.tag)}`;
            },
          },
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
  const [selected, setSelected] = useState<TopicSummary[]>([]);

  // The same query keys the datasets and indicators pages use, so arriving
  // here after either is served from the cache rather than re-fetched.
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });
  const indicators = useQuery({
    queryKey: ["indicators", "folded"],
    // A price's four series as one, as every list counts them.
    queryFn: () => api.indicators({ fold: "ohlc" }),
  });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.sources() });

  const isLoading = datasets.isLoading || indicators.isLoading || sources.isLoading;

  const all = collectTopics({
    datasets: datasets.data?.data ?? [],
    indicators: indicators.data?.data ?? [],
    sources: sources.data?.data ?? [],
  });

  const kinds = asTextList(search.kind);
  // Nothing ticked is no filter, as on every other chip.
  const shownKinds = kinds.length ? kinds : KINDS.map((kind) => kind.value);
  const needle = asText(search.q)?.toLowerCase() ?? "";

  const rows = all.filter(
    (topic) =>
      shownKinds.includes(topic.kind) &&
      (!needle ||
        topic.tag.includes(needle) ||
        titleFromId(topic.tag).toLowerCase().includes(needle) ||
        topic.publishers.some((name) => name.toLowerCase().includes(needle))),
  );

  function chooseKind(value: string) {
    navigate({
      search: (prev) => ({ ...prev, kind: toggle(kinds, value), page: 0 }),
    });
  }

  // Paged in place, like every other catalogue table here: the vocabulary is
  // assembled from data already in hand, so a page is a slice.
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(search.page ?? 0, pageCount - 1);
  const visible = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function exportRows(chosen: TopicSummary[], suffix: string) {
    downloadCsv(
      `topics-${suffix}.csv`,
      toCsv(chosen as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

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
                onClick={() => exportRows(rows, "all")}
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
                  value={summarise(kinds, kindLabel)}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, kind: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={KINDS}
                    selected={kinds}
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
        selectable
        getRowId={(row) => row.tag}
        onSelectionChange={setSelected}
        renderSelectionActions={(chosen) => (
          <Button
            variant="outline"
            size="sm"
            onClick={() => exportRows(chosen, "selection")}
          >
            <IconDownload className="size-4" />
            Export {formatCount(chosen.length)}
          </Button>
        )}
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
              {formatCount(rows.length)} tag{rows.length === 1 ? "" : "s"}
              {rows.length !== all.length
                ? ` (${formatCount(all.length)} tags in all)`
                : null}
              {selected.length ? ` · ${formatCount(selected.length)} selected` : null}
            </>
          ) : null
        }
      />
    </div>
  );
}
