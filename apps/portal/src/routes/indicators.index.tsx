import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconCopy, IconDownload } from "@tabler/icons-react";
import { useState } from "react";
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
import { api, type Indicator } from "~/lib/api";
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
  page: z.number().int().min(0).optional(),
});

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

function Indicators() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const query = useQuery({ queryKey: ["indicators"], queryFn: () => api.indicators() });
  const [selected, setSelected] = useState<Indicator[]>([]);

  const all = query.data?.data ?? [];

  // Filtered here rather than by the API, which returns the whole list: there
  // are a handful of indicators, and a round trip to narrow five rows is worse
  // than narrowing them in place. It moves server-side when the list does.
  const frequencies = asTextList(search.frequency);
  const units = asTextList(search.unit);
  const sources = asTextList(search.source);
  const tags = asTextList(search.tag);
  const needle = asText(search.q)?.toLowerCase() ?? "";

  const rows = all.filter(
    (indicator) =>
      (!frequencies.length || frequencies.includes(indicator.temporal_resolution)) &&
      (!units.length || (indicator.unit ? units.includes(indicator.unit) : false)) &&
      (!sources.length || indicator.sources.some((id) => sources.includes(id))) &&
      // Every tag, not any: a second tag narrows, which is what choosing one
      // after another asks for.
      (!tags.length || tags.every((tag) => (indicator.tags ?? []).includes(tag))) &&
      (!needle ||
        indicator.indicator_id.toLowerCase().includes(needle) ||
        // The key the series was declared under, which is what a maintainer
        // has in hand, and the tags, which is what everyone else does.
        (indicator.slug?.toLowerCase().includes(needle) ?? false) ||
        (indicator.tags ?? []).some((tag) => tag.includes(needle)) ||
        // The table shows the name, so the name is what a reader types. The
        // publisher's code too: someone arriving from FRED has NASDAQNQID55LMN
        // in hand, not a title.
        indicatorLabel(indicator).toLowerCase().includes(needle) ||
        (indicator.code?.toLowerCase().includes(needle) ?? false) ||
        indicator.sources.some((id) => id.toLowerCase().includes(needle))),
  );

  const allFrequencies = [...new Set(all.map((i) => i.temporal_resolution))].sort();
  const allUnits = [
    ...new Set(all.map((i) => i.unit).filter(Boolean)),
  ].sort() as string[];
  const allSources = [...new Set(all.flatMap((i) => i.sources))].sort();
  const allTags = [...new Set(all.flatMap((i) => i.tags ?? []))].sort();

  // Paged in place, like the filtering above it: the API answers with every
  // series in one call, so a page is a slice rather than a round trip. A page
  // past the end of a freshly narrowed list would render empty, so the
  // requested page is clamped rather than trusted.
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(search.page ?? 0, pageCount - 1);
  const visible = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function exportRows(chosen: Indicator[], suffix: string) {
    downloadCsv(
      `indicators-${suffix}.csv`,
      toCsv(chosen as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
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
            count={rows.length}
            isLoading={query.isLoading}
            description="What is measured, and how much of it there is. Coverage is derived from the figures themselves rather than declared, so a series cannot claim a range it does not have."
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
              {formatCount(rows.length)} indicator{rows.length === 1 ? "" : "s"}
              {rows.length !== all.length
                ? ` (${formatCount(all.length)} total)`
                : null}
              {selected.length ? ` · ${formatCount(selected.length)} selected` : null}
            </>
          ) : null
        }
      />
    </div>
  );
}
