import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconDownload } from "@tabler/icons-react";
import { useState } from "react";

import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { TablePagination } from "~/components/table-pagination";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type Dataset } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount } from "~/lib/format";

export const Route = createFileRoute("/datasets")({ component: Datasets });

const PAGE_SIZE = 50;

const columns: ColumnDef<Dataset>[] = [
  {
    accessorKey: "name",
    header: "Dataset",
    cell: ({ row }) => (
      <StackedCell primary={row.original.name} secondary={row.original.slug} />
    ),
  },
  {
    accessorKey: "layer",
    header: "Layer",
    cell: ({ row }) => <Badge variant="secondary">{row.original.layer}</Badge>,
  },
  {
    accessorKey: "rows",
    header: "Rows",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.rows),
  },
];

const EXPORT_COLUMNS = [
  { key: "slug" as const, header: "slug" },
  { key: "name" as const, header: "name" },
  { key: "layer" as const, header: "layer" },
  { key: "rows" as const, header: "rows" },
];

function Datasets() {
  const query = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });
  const [selected, setSelected] = useState<Dataset[]>([]);

  const rows = query.data?.data ?? [];

  function exportRows(chosen: Dataset[], suffix: string) {
    downloadCsv(
      `datasets-${suffix}.csv`,
      toCsv(chosen as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Datasets"
        count={rows.length}
        isLoading={query.isLoading}
        description="What is in the lake, read from storage rather than from the catalog — so this cannot disagree with what is on disk."
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
        emptyMessage="The lake is empty. Run a source, then extract."
        selectable
        getRowId={(row) => row.slug}
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
              {formatCount(rows.length)} datasets
              {selected.length ? ` · ${formatCount(selected.length)} selected` : null}
            </>
          ) : null
        }
      />
    </div>
  );
}
