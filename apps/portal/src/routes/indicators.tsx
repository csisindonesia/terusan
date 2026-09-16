import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconDownload } from "@tabler/icons-react";
import { useState } from "react";

import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { TablePagination } from "~/components/table-pagination";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type Indicator } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount } from "~/lib/format";

export const Route = createFileRoute("/indicators")({ component: Indicators });

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
  const query = useQuery({ queryKey: ["indicators"], queryFn: () => api.indicators() });
  const [selected, setSelected] = useState<Indicator[]>([]);

  const rows = query.data?.data ?? [];

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

      <DataTable
        columns={columns}
        data={rows}
        isLoading={query.isLoading}
        emptyMessage="No indicators yet. Normalize a Bronze dataset into Silver first."
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
              {selected.length ? ` · ${formatCount(selected.length)} selected` : null}
            </>
          ) : null
        }
      />
    </div>
  );
}
