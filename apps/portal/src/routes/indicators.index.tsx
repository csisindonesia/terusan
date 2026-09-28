import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconCopy, IconDownload } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { z } from "zod";

import { DataTable, StackedCell } from "~/components/data-table";
import { ChoiceList, FilterChip, summarise } from "~/components/filter-chip";
import { PageHeader } from "~/components/page-header";
import { StickyHeader } from "~/components/sticky-header";
import { SearchInput } from "~/components/search-input";
import { TableToolbar } from "~/components/table-toolbar";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { TablePagination } from "~/components/table-pagination";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { ClampedText } from "~/components/clamped-text";
import { api, type Indicator, type IndicatorQuery } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { indicatorLabel } from "~/lib/labels";
import { formatCount, formatDate, formatRelative } from "~/lib/format";
import { toggle } from "~/lib/multi";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";

const searchSchema = z.object({
  frequency: listParam,
  tag: listParam,
  unit: listParam,
  source: listParam,
  q: textParam,
  // How the list is ordered, in the URL so a sorted list is a link. The values
  // are the table's own column ids, which is what a header click hands back.
  sort: z
    .enum([
      "indicator_id",
      "temporal_resolution",
      "unit",
      "source",
      "coverage",
      "last_updated",
    ])
    .optional(),
  dir: z.enum(["asc", "desc"]).optional(),
  page: z.number().int().min(0).optional(),
});

type SortColumn = NonNullable<z.infer<typeof searchSchema>["sort"]>;

// Newest first unless the reader asks otherwise: "what changed" is the
// question most visits to this page start with.
const DEFAULT_SORT: SortColumn = "last_updated";

export const Route = createFileRoute("/indicators/")({
  validateSearch: searchSchema,
  component: Indicators,
});

const PAGE_SIZE = 50;

const columns: ColumnDef<Indicator>[] = [
  {
    accessorKey: "indicator_id",
    header: "Indicator",
    // Narrower than the title needs: the columns beside it — frequency, unit,
    // source, coverage — are what a reader scans a list of six hundred series
    // by, and a title column wide enough for the longest FRED title pushes
    // them off the edge. The whole title is a hover away.
    meta: { width: "w-72" },
    cell: ({ row }) => (
      // To the series itself rather than to a filtered table: the next
      // question after reading a row here is about this one indicator.
      //
      // The name, and under it the publisher's own code where there is one:
      // `NASDAQNQID55LMN` is what a reader takes back to FRED, and it is the
      // only thing telling two rows apart when a publisher gives forty series
      // titles that differ in their last two words.
      <Link
        to="/indicators/$indicatorId"
        params={{ indicatorId: row.original.indicator_id }}
        className="underline-offset-4 hover:underline"
      >
        {/* The whole title is a hover away, and the row is still a link to it. */}
        <div className="leading-tight">
          <ClampedText className="max-w-[17rem] font-medium">
            {indicatorLabel(row.original)}
          </ClampedText>
          {row.original.code || row.original.slug ? (
            <div className="text-xs text-muted-foreground">
              {row.original.code ?? row.original.slug}
            </div>
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
    // Clamped like the name, and for the same reason: "2005 International
    // Dollars per Person Counted in Total Employment" is a real unit here, and
    // one of them sets the width of the column for all six hundred rows.
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
    // Clamped like the name and the unit beside it: a source id such as
    // `kemenkeu-djpk-transfer-daerah` is as long as the title it sits next to,
    // and left to itself it widens the column for all six hundred rows.
    meta: { width: "w-44" },
    cell: ({ row }) => {
      const [first, ...rest] = row.original.sources;
      if (!first) return <span className="text-muted-foreground">—</span>;
      return (
        <StackedCell
          primary={<ClampedText className="max-w-[11rem]">{first}</ClampedText>}
          // Naming them all would outgrow the column; the count carries the
          // rest, and the figures themselves say which row came from where.
          secondary={rest.length ? `+${rest.length} more` : undefined}
        />
      );
    },
  },
  {
    id: "coverage",
    header: "Coverage",
    cell: ({ row }) => (
      <StackedCell
        primary={`${row.original.period_start}–${row.original.period_end}`}
        secondary={`${formatCount(row.original.observations)} figures`}
      />
    ),
  },
  {
    accessorKey: "last_updated",
    header: "Latest update",
    cell: ({ row }) => (
      <StackedCell
        primary={formatDate(row.original.last_updated)}
        // When the pipeline last ran, which is a different question from how
        // recent the figures are — that is what Coverage says.
        secondary={formatRelative(row.original.last_updated)}
      />
    ),
  },
  {
    id: "actions",
    header: "",
    enableSorting: false,
    meta: { align: "right" },
    cell: ({ row }) => (
      <RowActions
        collect={[
          {
            kind: "indicator",
            id: row.original.indicator_id,
            label: indicatorLabel(row.original),
          },
        ]}
        actions={[
          {
            label: "Copy identifier",
            icon: IconCopy,
            onSelect: () => void copyToClipboard(row.original.indicator_id),
          },
        ]}
      />
    ),
  },
];

const EXPORT_COLUMNS = [
  { key: "indicator_id" as const, header: "indicator_id" },
  { key: "slug" as const, header: "slug" },
  { key: "name" as const, header: "name" },
  { key: "tags" as const, header: "tags" },
  { key: "temporal_resolution" as const, header: "temporal_resolution" },
  { key: "unit" as const, header: "unit" },
  { key: "period_start" as const, header: "period_start" },
  { key: "period_end" as const, header: "period_end" },
  { key: "geographies" as const, header: "geographies" },
  { key: "observations" as const, header: "observations" },
  { key: "sources" as const, header: "sources" },
  { key: "last_updated" as const, header: "last_updated" },
];

/**
 * The most rows the API hands back in one call. "Export all" walks the list in
 * pages this size, so a filter matching more than this still exports whole.
 */
const EXPORT_PAGE = 10000;

function Indicators() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [selected, setSelected] = useState<Indicator[]>([]);
  const [exporting, setExporting] = useState(false);

  // Filtered, sorted and paged by the API: the catalogue holds tens of
  // thousands of series, and downloading all of them to show fifty was most of
  // what this page used to cost. The URL is unchanged, so links still work.
  const frequencies = asTextList(search.frequency);
  const units = asTextList(search.unit);
  const sources = asTextList(search.source);
  const tags = asTextList(search.tag);
  const needle = asText(search.q)?.trim() ?? "";

  const sortColumn = search.sort ?? DEFAULT_SORT;
  // Sent explicitly whichever way it was arrived at, so the page and the API
  // cannot disagree about which way an unsorted list runs.
  const dir = search.dir ?? (search.sort ? "asc" : "desc");
  const descending = dir === "desc";
  const requestedPage = search.page ?? 0;

  const filters: IndicatorQuery = {
    q: needle || undefined,
    frequency: frequencies,
    unit: units,
    source: sources,
    tag: tags,
    sort: sortColumn,
    dir,
    // A price's four series are one row; its page offers the other three.
    fold: "ohlc" as const,
  };

  const query = useQuery({
    queryKey: ["indicators", { ...filters, page: requestedPage }],
    queryFn: () =>
      api.indicators({
        ...filters,
        limit: PAGE_SIZE,
        offset: requestedPage * PAGE_SIZE,
      }),
    // The old page stays on screen while the next one loads, rather than the
    // table collapsing to a skeleton on every click through the pages.
    placeholderData: keepPreviousData,
  });

  // The choices each chip offers, over every series rather than the ones on
  // this page — and the same whatever is filtered, so choosing one value never
  // hides the others from the list.
  const facets = useQuery({
    queryKey: ["indicator-facets"],
    queryFn: () => api.indicatorFacets(),
  });

  // How many series there are unfiltered, for the "(N total)" beside a
  // narrowed count. The home page asks for the same numbers, so this is
  // usually already in hand.
  const stats = useQuery({ queryKey: ["stats"], queryFn: () => api.stats() });

  const visible = query.data?.data ?? [];
  const matching = query.data?.meta?.total ?? visible.length;
  const catalogueSize = stats.data?.data.series;

  const allFrequencies = facets.data?.data.frequencies ?? [];
  const allUnits = facets.data?.data.units ?? [];
  const allSources = facets.data?.data.sources ?? [];
  const allTags = facets.data?.data.tags ?? [];

  const pageCount = Math.max(1, Math.ceil(matching / PAGE_SIZE));
  const page = Math.min(requestedPage, pageCount - 1);

  // A page past the end of a freshly narrowed list comes back empty, so once
  // the count says so the URL is moved to the last page that has rows rather
  // than leaving the reader on a blank one.
  useEffect(() => {
    if (query.isPlaceholderData || !query.data) return;
    if (requestedPage > page) {
      navigate({ search: (prev) => ({ ...prev, page }), replace: true });
    }
  }, [navigate, page, query.data, query.isPlaceholderData, requestedPage]);

  function exportRows(chosen: Indicator[], suffix: string) {
    downloadCsv(
      `indicators-${suffix}.csv`,
      toCsv(chosen as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  // Every matching series, not the page on screen: asked for in the same order
  // the table shows, in pages as large as the API allows.
  async function exportAll() {
    setExporting(true);
    try {
      const rows: Indicator[] = [];
      for (let offset = 0; ; offset += EXPORT_PAGE) {
        const next = await api.indicators({ ...filters, limit: EXPORT_PAGE, offset });
        rows.push(...next.data);
        if (!next.meta?.has_more || !next.data.length) break;
      }
      exportRows(rows, "all");
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="space-y-5">
      {/* Title and filters ride together: ten screens into a table the
          reader has lost sight of which filters are on, and a figure read
          under a filter nobody can see is a figure read wrong. */}
      <StickyHeader
        heading={
          <PageHeader
            title="Indicators"
            count={matching}
            isLoading={query.isLoading}
            description="What is measured, and how much of it there is. Coverage is derived from the figures themselves rather than declared, so a series cannot claim a range it does not have."
            actions={
              <Button
                variant="outline"
                size="sm"
                disabled={!matching || exporting}
                onClick={() => void exportAll()}
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
                  label="Frequency"
                  value={summarise(frequencies)}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, frequency: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={allFrequencies.map((value) => ({ value, label: value }))}
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
                        search: (prev) => ({ ...prev, frequency: undefined, page: 0 }),
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
                        search: (prev) => ({ ...prev, unit: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                <FilterChip
                  label="Source"
                  value={summarise(sources)}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, source: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={allSources.map((value) => ({ value, label: value }))}
                    selected={sources}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          source: toggle(sources, value),
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, source: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                <FilterChip
                  label="Tag"
                  value={summarise(tags)}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, tag: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={allTags.map((value) => ({ value, label: value }))}
                    selected={tags}
                    searchPlaceholder="Search tags"
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          tag: toggle(tags, value),
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, tag: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                {frequencies.length ||
                units.length ||
                sources.length ||
                tags.length ||
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
            search={
              <SearchInput
                value={asText(search.q)}
                placeholder="Search indicators, tags and sources"
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
        isLoading={query.isLoading}
        emptyMessage="No indicators match these filters."
        selectable
        getRowId={(row) => row.indicator_id}
        onSelectionChange={setSelected}
        sorting={[{ id: sortColumn, desc: descending }]}
        onSortingChange={(next) => {
          const [first] = next;
          navigate({
            search: (prev) => ({
              ...prev,
              sort: first?.id as SortColumn | undefined,
              dir: first ? (first.desc ? "desc" : "asc") : undefined,
              page: 0,
            }),
          });
        }}
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
        total={matching}
        pageSize={PAGE_SIZE}
        onPage={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        summary={
          matching ? (
            <>
              {formatCount(page * PAGE_SIZE + 1)}–
              {formatCount(Math.min((page + 1) * PAGE_SIZE, matching))} of{" "}
              {formatCount(matching)} indicator{matching === 1 ? "" : "s"}
              {catalogueSize !== undefined && matching !== catalogueSize
                ? ` (${formatCount(catalogueSize)} total)`
                : null}
              {selected.length ? ` · ${formatCount(selected.length)} selected` : null}
            </>
          ) : null
        }
      />
    </div>
  );
}
