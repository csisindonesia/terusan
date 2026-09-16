import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";

import { DataTable } from "~/components/data-table";
import { Badge } from "~/components/ui/badge";
import { api, type Dataset } from "~/lib/api";
import { formatCount } from "~/lib/format";

export const Route = createFileRoute("/datasets")({ component: Datasets });

const columns: ColumnDef<Dataset>[] = [
  {
    accessorKey: "name",
    header: "Dataset",
    cell: ({ row }) => <span className="font-medium">{row.original.name}</span>,
  },
  {
    accessorKey: "layer",
    header: "Layer",
    cell: ({ row }) => <Badge variant="secondary">{row.original.layer}</Badge>,
  },
  { accessorKey: "slug", header: "Slug" },
  {
    accessorKey: "rows",
    header: "Rows",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.rows),
  },
];

function Datasets() {
  const query = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Datasets</h1>
        <p className="text-sm text-muted-foreground">
          What is in the lake, read from storage rather than from the catalog — so
          this cannot disagree with what is on disk.
        </p>
      </div>

      <DataTable
        columns={columns}
        data={query.data?.data ?? []}
        isLoading={query.isLoading}
        emptyMessage="The lake is empty. Run a source, then extract."
      />
    </div>
  );
}
