import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconChartArea, IconCopy, IconDownload, IconRuler } from "@tabler/icons-react";
import { useState } from "react";
import { z } from "zod";

import { DataTable, StackedCell } from "~/components/data-table";
import { FilterChip } from "~/components/filter-chip";
import { PageHeader } from "~/components/page-header";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { TablePagination } from "~/components/table-pagination";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type Indicator } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount } from "~/lib/format";

const searchSchema = z.object({
  frequency: z.string().optional(),
  unit: z.string().optional(),
});

export const Route = createFileRoute("/indicators")({
  validateSearch: searchSchema,
  component: Indicators,
});

const PAGE_SIZE = 50;

const columns: ColumnDef<Indicator>[] = [
  {
    accessorKey: "indicator_id",
    header: "Indicator",
    cell: ({ row }) => (
      // Straight to the figures, filtered — the only question anyone has after
      // reading a row here.
      <Link
        to="/observations"
        search={{ indicator: row.original.indicator_id }}
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
      <StackedCell
        primary={<Badge variant="secondary">{row.original.temporal_resolution}</Badge>}
        secondary={row.original.unit ?? undefined}
      />
    ),
  },
  {
    id: "coverage",
    header: "Coverage",
    cell: ({ row }) => (
      <StackedCell
        primary={`${row.original.period_start}–${row.original.period_end}`}
        secondary={`${formatCount(row.original.sources)} source${row.original.sources === 1 ? "" : "s"}`}
      />
    ),
  },
  {
    accessorKey: "geographies",
    header: "Places",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.geographies),
  },
  {
    accessorKey: "observations",
    header: "Figures",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.observations),
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
  const rows = all.filter(
    (indicator) =>
      (!search.frequency || indicator.temporal_resolution === search.frequency) &&
      (!search.unit || indicator.unit === search.unit),
  );

  const frequencies = [...new Set(all.map((i) => i.temporal_resolution))].sort();
  const units = [...new Set(all.map((i) => i.unit).filter(Boolean))].sort() as string[];

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

      <div className="flex flex-wrap items-center gap-2">
        <FilterChip
          icon={IconChartArea}
          label="Frequency"
          value={search.frequency}
          onClear={() => navigate({ search: (prev) => ({ ...prev, frequency: undefined }) })}
        >
          <ChoiceList
            options={frequencies}
            selected={search.frequency}
            onSelect={(frequency) =>
              navigate({ search: (prev) => ({ ...prev, frequency }) })
            }
          />
        </FilterChip>

        <FilterChip
          icon={IconRuler}
          label="Unit"
          value={search.unit}
          onClear={() => navigate({ search: (prev) => ({ ...prev, unit: undefined }) })}
        >
          <ChoiceList
            options={units}
            selected={search.unit}
            onSelect={(unit) => navigate({ search: (prev) => ({ ...prev, unit }) })}
          />
        </FilterChip>

        {search.frequency || search.unit ? (
          <Button variant="ghost" size="sm" className="h-8" onClick={() => navigate({ search: {} })}>
            Clear all
          </Button>
        ) : null}
      </div>

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

/** A short list of values to pick from, for a filter with few options. */
function ChoiceList({
  options,
  selected,
  onSelect,
}: {
  options: string[];
  selected?: string;
  onSelect: (value: string) => void;
}) {
  if (!options.length) {
    return <p className="px-2 py-3 text-sm text-muted-foreground">Nothing to choose from.</p>;
  }
  return (
    <div className="grid gap-0.5">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => onSelect(option)}
          className={`rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted ${
            option === selected ? "bg-muted font-medium" : ""
          }`}
        >
          {option}
        </button>
      ))}
    </div>
  );
}
