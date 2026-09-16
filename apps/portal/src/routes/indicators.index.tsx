import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconCopy, IconDownload } from "@tabler/icons-react";
import { useState } from "react";
import { z } from "zod";

import { DataTable, StackedCell } from "~/components/data-table";
import { ChoiceList, FilterChip, summarise } from "~/components/filter-chip";
import { PageHeader } from "~/components/page-header";
import { SearchInput } from "~/components/search-input";
import { TableToolbar } from "~/components/table-toolbar";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { TablePagination } from "~/components/table-pagination";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type Indicator } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount, formatDate, formatRelative } from "~/lib/format";
import { asList, toggle } from "~/lib/multi";

const list = z.union([z.string(), z.array(z.string())]).optional();

const searchSchema = z.object({
  frequency: list,
  unit: list,
  source: list,
  q: z.string().optional(),
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
    cell: ({ row }) => (
      // To the series itself rather than to a filtered table: the next
      // question after reading a row here is about this one indicator.
      <Link
        to="/indicators/$indicatorId"
        params={{ indicatorId: row.original.indicator_id }}
        className="font-medium underline-offset-4 hover:underline"
      >
        {row.original.indicator_id}
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
    cell: ({ row }) => row.original.unit ?? "—",
  },
  {
    id: "source",
    header: "Source",
    enableSorting: false,
    cell: ({ row }) => {
      const [first, ...rest] = row.original.sources;
      if (!first) return <span className="text-muted-foreground">—</span>;
      return (
        <StackedCell
          primary={first}
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
  const frequencies = asList(search.frequency);
  const units = asList(search.unit);
  const sources = asList(search.source);
  const needle = (search.q ?? "").toLowerCase();

  const rows = all.filter(
    (indicator) =>
      (!frequencies.length || frequencies.includes(indicator.temporal_resolution)) &&
      (!units.length || (indicator.unit ? units.includes(indicator.unit) : false)) &&
      (!sources.length || indicator.sources.some((id) => sources.includes(id))) &&
      (!needle ||
        indicator.indicator_id.toLowerCase().includes(needle) ||
        indicator.sources.some((id) => id.toLowerCase().includes(needle))),
  );

  const allFrequencies = [...new Set(all.map((i) => i.temporal_resolution))].sort();
  const allUnits = [
    ...new Set(all.map((i) => i.unit).filter(Boolean)),
  ].sort() as string[];
  const allSources = [...new Set(all.flatMap((i) => i.sources))].sort();

  function exportRows(chosen: Indicator[], suffix: string) {
    downloadCsv(
      `indicators-${suffix}.csv`,
      toCsv(chosen as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  return (
    <div className="space-y-5">
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

      <TableToolbar
        filters={
          <>
            <FilterChip
              label="Frequency"
              value={summarise(frequencies)}
              onClear={() =>
                navigate({ search: (prev) => ({ ...prev, frequency: undefined }) })
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
                    }),
                  })
                }
                onClear={() =>
                  navigate({ search: (prev) => ({ ...prev, frequency: undefined }) })
                }
              />
            </FilterChip>

            <FilterChip
              label="Unit"
              value={summarise(units)}
              onClear={() =>
                navigate({ search: (prev) => ({ ...prev, unit: undefined }) })
              }
            >
              <ChoiceList
                options={allUnits.map((value) => ({ value, label: value }))}
                selected={units}
                onToggle={(value) =>
                  navigate({
                    search: (prev) => ({ ...prev, unit: toggle(units, value) }),
                  })
                }
                onClear={() =>
                  navigate({ search: (prev) => ({ ...prev, unit: undefined }) })
                }
              />
            </FilterChip>

            <FilterChip
              label="Source"
              value={summarise(sources)}
              onClear={() =>
                navigate({ search: (prev) => ({ ...prev, source: undefined }) })
              }
            >
              <ChoiceList
                options={allSources.map((value) => ({ value, label: value }))}
                selected={sources}
                onToggle={(value) =>
                  navigate({
                    search: (prev) => ({ ...prev, source: toggle(sources, value) }),
                  })
                }
                onClear={() =>
                  navigate({ search: (prev) => ({ ...prev, source: undefined }) })
                }
              />
            </FilterChip>

            {frequencies.length || units.length || sources.length || search.q ? (
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
            value={search.q}
            placeholder="Search indicators and sources"
            onSearch={(q) => navigate({ search: (prev) => ({ ...prev, q }) })}
          />
        }
      />

      <DataTable
        columns={columns}
        data={rows}
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
        page={0}
        total={rows.length}
        pageSize={PAGE_SIZE}
        onPage={() => undefined}
        summary={
          rows.length ? (
            <>
              {formatCount(rows.length)} indicator{rows.length === 1 ? "" : "s"}
              {rows.length !== all.length ? ` of ${formatCount(all.length)}` : null}
              {selected.length ? ` · ${formatCount(selected.length)} selected` : null}
            </>
          ) : null
        }
      />
    </div>
  );
}
