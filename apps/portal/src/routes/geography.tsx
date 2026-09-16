import { useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconChartArea, IconCopy, IconDownload } from "@tabler/icons-react";
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
import { api, type Geography } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount } from "~/lib/format";
import { toggle } from "~/lib/multi";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";

const PAGE_SIZE = 50;

const searchSchema = z.object({
  geo_type: listParam,
  q: textParam,
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/geography")({
  validateSearch: searchSchema,
  component: GeographyPage,
});

const PLACE_TYPES = [
  { value: "country", label: "Countries" },
  { value: "province", label: "Provinces" },
  // Aggregates are sums of the countries beside them, so a total over
  // everything counts most places more than once.
  { value: "region", label: "Aggregates" },
];

const columns: ColumnDef<Geography>[] = [
  {
    accessorKey: "name",
    header: "Name",
    cell: ({ row }) => (
      <StackedCell primary={row.original.name} secondary={row.original.geo_id} />
    ),
  },
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
    accessorKey: "parent_geo_id",
    header: "Within",
    cell: ({ row }) => row.original.parent_geo_id ?? "—",
  },
  {
    accessorKey: "bps_code",
    header: "Codes",
    cell: ({ row }) => (
      <StackedCell
        primary={row.original.bps_code ? `BPS ${row.original.bps_code}` : "—"}
        secondary={row.original.iso_code ? `ISO ${row.original.iso_code}` : undefined}
      />
    ),
  },
  {
    accessorKey: "valid_from",
    header: "Valid from",
    // Administrative changes must not silently overwrite earlier definitions
    // (program.md §11), so when a place came into being is worth a column.
    cell: ({ row }) => row.original.valid_from ?? "—",
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
            onSelect: () => void copyToClipboard(row.original.geo_id),
          },
          {
            label: "View figures",
            icon: IconChartArea,
            onSelect: () => {
              window.location.href = `/observations?geo=${encodeURIComponent(row.original.geo_id)}`;
            },
          },
        ]}
      />
    ),
  },
];

const EXPORT_COLUMNS = [
  { key: "geo_id" as const, header: "geo_id" },
  { key: "name" as const, header: "name" },
  { key: "geo_type" as const, header: "geo_type" },
  { key: "parent_geo_id" as const, header: "parent_geo_id" },
  { key: "bps_code" as const, header: "bps_code" },
  { key: "iso_code" as const, header: "iso_code" },
  { key: "valid_from" as const, header: "valid_from" },
];

function GeographyPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [selected, setSelected] = useState<Geography[]>([]);

  const page = search.page ?? 0;
  const query = useQuery({
    queryKey: ["geography", search],
    queryFn: () =>
      api.geography({
        geo_type: asTextList(search.geo_type),
        q: asText(search.q),
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  });

  const rows = query.data?.data ?? [];
  const total = query.data?.meta?.total ?? 0;
  const chosen = asTextList(search.geo_type);

  function exportRows(chosen: Geography[], suffix: string) {
    downloadCsv(
      `geography-${suffix}.csv`,
      toCsv(chosen as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Geography"
        count={total}
        isLoading={query.isLoading}
        description="The places the figures refer to, plus Indonesia's provinces. Aggregates are marked as regions: they are real published figures, but every country sits inside several of them, so a total over everything counts most places more than once."
        actions={
          <Button
            variant="outline"
            size="sm"
            disabled={!rows.length}
            onClick={() => exportRows(rows, `page-${page + 1}`)}
          >
            <IconDownload className="size-4" />
            Export page
          </Button>
        }
      />

      <TableToolbar
        filters={
          <>
            <FilterChip
              label="Type"
              value={summarise(
                chosen,
                (v) => PLACE_TYPES.find((t) => t.value === v)?.label ?? v,
              )}
              onClear={() =>
                navigate({
                  search: (prev) => ({ ...prev, geo_type: undefined, page: 0 }),
                })
              }
            >
              <ChoiceList
                options={PLACE_TYPES}
                selected={chosen}
                onToggle={(type) =>
                  navigate({
                    search: (prev) => ({
                      ...prev,
                      geo_type: toggle(chosen, type),
                      page: 0,
                    }),
                  })
                }
                onClear={() =>
                  navigate({
                    search: (prev) => ({ ...prev, geo_type: undefined, page: 0 }),
                  })
                }
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
            placeholder="Search places and codes"
            onSearch={(q) => navigate({ search: (prev) => ({ ...prev, q, page: 0 }) })}
          />
        }
      />

      <DataTable
        columns={columns}
        data={rows}
        isLoading={query.isLoading}
        loadingRows={10}
        emptyMessage="The geography dimension has not been published yet."
        selectable
        getRowId={(row) => row.geo_id}
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
        total={total}
        pageSize={PAGE_SIZE}
        onPage={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        summary={
          total > 0 ? (
            <>
              {formatCount(page * PAGE_SIZE + 1)}–
              {formatCount(Math.min((page + 1) * PAGE_SIZE, total))} of{" "}
              {formatCount(total)}
              {selected.length ? ` · ${formatCount(selected.length)} selected` : null}
            </>
          ) : null
        }
      />
    </div>
  );
}
