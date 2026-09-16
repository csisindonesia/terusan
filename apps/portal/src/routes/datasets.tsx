import { useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
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
import { api, type Dataset } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount } from "~/lib/format";
import { toggle } from "~/lib/multi";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";

const searchSchema = z.object({
  layer: listParam,
  q: textParam,
});

export const Route = createFileRoute("/datasets")({
  validateSearch: searchSchema,
  component: Datasets,
});

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
  {
    id: "actions",
    header: "",
    enableSorting: false,
    meta: { align: "right" },
    cell: ({ row }) => (
      <RowActions
        actions={[
          {
            label: "Copy slug",
            icon: IconCopy,
            onSelect: () => void copyToClipboard(row.original.slug),
          },
        ]}
      />
    ),
  },
];

const EXPORT_COLUMNS = [
  { key: "slug" as const, header: "slug" },
  { key: "name" as const, header: "name" },
  { key: "layer" as const, header: "layer" },
  { key: "rows" as const, header: "rows" },
];

function Datasets() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const query = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });
  const [selected, setSelected] = useState<Dataset[]>([]);

  const all = query.data?.data ?? [];

  // Filtered in place: the endpoint returns every dataset in the lake, which
  // is a handful, and a round trip to narrow them is worse than not.
  const layers = [...new Set(all.map((dataset) => dataset.layer))].sort();
  const chosen = asTextList(search.layer);
  const needle = asText(search.q)?.toLowerCase() ?? "";

  const rows = all.filter(
    (dataset) =>
      (!chosen.length || chosen.includes(dataset.layer)) &&
      (!needle ||
        dataset.name.toLowerCase().includes(needle) ||
        dataset.slug.toLowerCase().includes(needle)),
  );

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

      <TableToolbar
        filters={
          <>
            <FilterChip
              label="Layer"
              value={summarise(chosen)}
              onClear={() =>
                navigate({ search: (prev) => ({ ...prev, layer: undefined }) })
              }
            >
              <ChoiceList
                options={layers.map((layer) => ({ value: layer, label: layer }))}
                selected={chosen}
                onToggle={(layer) =>
                  navigate({
                    search: (prev) => ({ ...prev, layer: toggle(chosen, layer) }),
                  })
                }
                onClear={() =>
                  navigate({ search: (prev) => ({ ...prev, layer: undefined }) })
                }
                empty="Nothing in the lake yet."
              />
            </FilterChip>

            {chosen.length || search.q ? (
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
            placeholder="Search datasets"
            onSearch={(q) => navigate({ search: (prev) => ({ ...prev, q }) })}
          />
        }
      />

      <DataTable
        columns={columns}
        data={rows}
        isLoading={query.isLoading}
        emptyMessage="No datasets match this filter."
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
              {formatCount(rows.length)} dataset{rows.length === 1 ? "" : "s"}
              {rows.length !== all.length ? ` of ${formatCount(all.length)}` : null}
              {selected.length ? ` · ${formatCount(selected.length)} selected` : null}
            </>
          ) : null
        }
      />
    </div>
  );
}
