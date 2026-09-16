import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";

import { DataTable } from "~/components/data-table";
import { Badge } from "~/components/ui/badge";
import { api, type Geography } from "~/lib/api";
import { formatCount } from "~/lib/format";

export const Route = createFileRoute("/geography")({ component: GeographyPage });

const columns: ColumnDef<Geography>[] = [
  {
    accessorKey: "name",
    header: "Name",
    cell: ({ row }) => <span className="font-medium">{row.original.name}</span>,
  },
  { accessorKey: "geo_id", header: "Identifier" },
  {
    accessorKey: "geo_type",
    header: "Type",
    cell: ({ row }) => (
      <Badge variant={row.original.geo_type === "region" ? "outline" : "secondary"}>
        {row.original.geo_type}
      </Badge>
    ),
  },
  {
    accessorKey: "bps_code",
    header: "BPS",
    cell: ({ row }) => row.original.bps_code ?? "—",
  },
  {
    accessorKey: "valid_from",
    header: "Valid from",
    cell: ({ row }) => row.original.valid_from ?? "—",
  },
];

function GeographyPage() {
  const query = useQuery({
    queryKey: ["geography"],
    queryFn: () => api.geography({ limit: 500 }),
  });

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Geography</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">
          {query.data?.meta?.total
            ? `${formatCount(query.data.meta.total)} places. `
            : null}
          Aggregates are marked as regions: they are real published figures, but
          every country sits inside several of them, so a total over everything
          counts most places more than once.
        </p>
      </div>

      <DataTable
        columns={columns}
        data={query.data?.data ?? []}
        isLoading={query.isLoading}
        emptyMessage="The geography dimension has not been published yet."
      />
    </div>
  );
}
