import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import {
  IconChartArea,
  IconCopy,
  IconDownload,
  IconExternalLink,
} from "@tabler/icons-react";
import { useState } from "react";
import { z } from "zod";

import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { StickyHeader } from "~/components/sticky-header";
import { SearchInput } from "~/components/search-input";
import { TableToolbar } from "~/components/table-toolbar";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { TablePagination } from "~/components/table-pagination";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type Commodity } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount } from "~/lib/format";
import { asText, textParam } from "~/lib/search-params";

const PAGE_SIZE = 50;

const searchSchema = z.object({
  q: textParam,
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/commodities/")({
  validateSearch: searchSchema,
  component: CommoditiesPage,
});

const columns: ColumnDef<Commodity>[] = [
  {
    accessorKey: "name",
    header: "Commodity",
    cell: ({ row }) => (
      <StackedCell
        primary={
          <Link
            to="/commodities/$name"
            params={{ name: row.original.name }}
            className="underline-offset-4 hover:underline"
          >
            {row.original.name}
          </Link>
        }
        // The identifier where the registry resolved the commodity, and
        // otherwise a plain statement that it did not: a blank line here
        // reads as a rendering fault rather than as the gap it is.
        secondary={row.original.commodity_id ?? "unregistered"}
      />
    ),
  },
  {
    accessorKey: "category",
    header: "Category",
    cell: ({ row }) =>
      row.original.category ? (
        <StackedCell
          primary={row.original.category}
          secondary={row.original.subcategory}
        />
      ) : (
        "—"
      ),
  },
  {
    accessorKey: "hs_code",
    header: "HS code",
    cell: ({ row }) => row.original.hs_code ?? "—",
  },
  {
    accessorKey: "units",
    header: "Unit",
    cell: ({ row }) =>
      row.original.units.length ? (
        <div className="flex flex-wrap gap-1">
          {row.original.units.map((unit) => (
            <Badge key={unit} variant="secondary">
              {unit}
            </Badge>
          ))}
        </div>
      ) : (
        "—"
      ),
  },
  {
    accessorKey: "observations",
    header: "Figures",
    meta: { align: "right" },
    cell: ({ row }) => (
      <StackedCell
        primary={formatCount(row.original.observations)}
        secondary={`${formatCount(row.original.indicators)} series`}
      />
    ),
  },
  {
    accessorKey: "period_start",
    header: "Covers",
    cell: ({ row }) => (
      <StackedCell
        primary={
          row.original.period_start === row.original.period_end
            ? row.original.period_start
            : `${row.original.period_start} – ${row.original.period_end}`
        }
        secondary={row.original.sources.join(", ")}
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
            label: "Open",
            icon: IconExternalLink,
            onSelect: () => {
              window.location.href = `/commodities/${encodeURIComponent(
                row.original.name,
              )}`;
            },
          },
          {
            label: "Copy name",
            icon: IconCopy,
            onSelect: () => void copyToClipboard(row.original.name),
          },
          {
            label: "View figures",
            icon: IconChartArea,
            onSelect: () => {
              window.location.href = `/observations?commodity=${encodeURIComponent(
                row.original.name,
              )}`;
            },
          },
        ]}
      />
    ),
  },
];

const EXPORT_COLUMNS = [
  { key: "name" as const, header: "name" },
  { key: "commodity_id" as const, header: "commodity_id" },
  { key: "category" as const, header: "category" },
  { key: "subcategory" as const, header: "subcategory" },
  { key: "hs_code" as const, header: "hs_code" },
  { key: "units" as const, header: "units" },
  { key: "observations" as const, header: "observations" },
  { key: "indicators" as const, header: "indicators" },
  { key: "period_start" as const, header: "period_start" },
  { key: "period_end" as const, header: "period_end" },
  { key: "sources" as const, header: "sources" },
];

function CommoditiesPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [selected, setSelected] = useState<Commodity[]>([]);

  const page = search.page ?? 0;
  const query = useQuery({
    queryKey: ["commodities", search],
    queryFn: () =>
      api.commodities({
        q: asText(search.q),
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  });

  const rows = query.data?.data ?? [];
  const total = query.data?.meta?.total ?? 0;

  function exportRows(chosen: Commodity[], suffix: string) {
    downloadCsv(
      `commodities-${suffix}.csv`,
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
            title="Commodities"
            count={total}
            isLoading={query.isLoading}
            description="The goods the figures are about — whether the commodity is one row of a price table or a traded contract with a series of its own. Read off the figures rather than off the registry, so a commodity nothing has resolved yet is still listed under the name its source printed, and one the registry knows carries its category and HS code."
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
        }
        filters={
          <TableToolbar
            filters={
              search.q ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8"
                  onClick={() => navigate({ search: {} })}
                >
                  Clear all
                </Button>
              ) : null
            }
            search={
              <SearchInput
                value={asText(search.q)}
                placeholder="Search commodities and codes"
                onSearch={(q) =>
                  navigate({ search: (prev) => ({ ...prev, q, page: 0 }) })
                }
              />
            }
          />
        }
      />

      {query.isError ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
          {(query.error as Error).message}
        </p>
      ) : null}

      <DataTable
        columns={columns}
        data={rows}
        isLoading={query.isLoading}
        loadingRows={10}
        emptyMessage="No commodity figures have been published yet."
        selectable
        getRowId={(row) => row.name}
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
