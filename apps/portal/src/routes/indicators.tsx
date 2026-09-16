import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";

import { DataTable } from "~/components/data-table";
import { Badge } from "~/components/ui/badge";
import { api, type Indicator } from "~/lib/api";
import { formatCount } from "~/lib/format";

export const Route = createFileRoute("/indicators")({ component: Indicators });

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
    cell: ({ row }) => <Badge variant="secondary">{row.original.temporal_resolution}</Badge>,
  },
  {
    accessorKey: "unit",
    header: "Unit",
    cell: ({ row }) => row.original.unit ?? "—",
  },
  {
    id: "coverage",
    header: "Coverage",
    cell: ({ row }) => `${row.original.period_start}–${row.original.period_end}`,
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

function Indicators() {
  const query = useQuery({ queryKey: ["indicators"], queryFn: () => api.indicators() });

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Indicators</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">
          What is measured, and how much of it there is. Coverage is derived from
          the figures themselves rather than declared, so a series cannot claim a
          range it does not have.
        </p>
      </div>

      <DataTable
        columns={columns}
        data={query.data?.data ?? []}
        isLoading={query.isLoading}
        emptyMessage="No indicators yet. Normalize a Bronze dataset into Silver first."
      />
    </div>
  );
}
