import { useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconDownload } from "@tabler/icons-react";
import { useState } from "react";
import { z } from "zod";

import { DataTable, StackedCell } from "~/components/data-table";
import {
  ObservationFilterBar,
  type ObservationFilters,
} from "~/components/observation-filters";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type Observation } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount, formatDecimal, statusLabel } from "~/lib/format";

// Filters live in the URL so a filtered view is a link someone can send —
// which for a research portal is most of the point.
//
// A period is accepted as either a string or a number, and stored as whatever
// the URL held. The router parses `?period_start=2020` as the number 2020, and
// coercing it to a string here would make the stored value disagree with the
// parsed one — so the router rewrites the URL to `period_start="2020"`, quotes
// and all. Keeping the type the URL implies leaves clean links clean; the
// conversion happens where a string is actually needed.
const period = z.union([z.string(), z.number()]).optional();

const searchSchema = z.object({
  indicator: z.string().optional(),
  geo: z.union([z.string(), z.number()]).optional(),
  geo_type: z.string().optional(),
  period_start: period,
  period_end: period,
  page: z.number().int().min(0).optional(),
});

/** A search value as text, for the API and for form fields. */
function asText(value: string | number | undefined): string | undefined {
  return value === undefined ? undefined : String(value);
}

const PAGE_SIZE = 50;

export const Route = createFileRoute("/observations")({
  validateSearch: searchSchema,
  component: Observations,
});

const columns: ColumnDef<Observation>[] = [
  {
    accessorKey: "geo_name",
    header: "Place",
    cell: ({ row }) => (
      <StackedCell
        primary={row.original.geo_name ?? "—"}
        secondary={row.original.geo_id ?? "unresolved"}
      />
    ),
  },
  {
    accessorKey: "period",
    header: "Period",
    cell: ({ row }) => (
      <StackedCell
        primary={row.original.period}
        secondary={row.original.temporal_resolution}
      />
    ),
  },
  {
    accessorKey: "value",
    header: "Value",
    meta: { align: "right" },
    cell: ({ row }) => {
      const { value, unit, status, value_unambiguous } = row.original;
      if (value === null) {
        // A blank cell cannot tell "not collected" from "collected and zero".
        return <span className="text-muted-foreground">{statusLabel(status)}</span>;
      }
      return (
        <StackedCell
          primary={
            <span className="inline-flex items-center justify-end gap-1.5">
              {!value_unambiguous ? (
                // Read under an assumption that could have gone the other way.
                <Badge variant="outline" title="Read under an assumption">
                  ?
                </Badge>
              ) : null}
              {formatDecimal(value)}
            </span>
          }
          secondary={unit}
        />
      );
    },
  },
  {
    accessorKey: "source_id",
    header: "Source",
    cell: ({ row }) =>
      row.original.source_url ? (
        <a
          href={row.original.source_url}
          target="_blank"
          rel="noreferrer noopener"
          className="underline underline-offset-4 hover:text-foreground"
        >
          {row.original.source_id}
        </a>
      ) : (
        row.original.source_id
      ),
  },
];

const EXPORT_COLUMNS = [
  { key: "observation_id" as const, header: "observation_id" },
  { key: "indicator_id" as const, header: "indicator_id" },
  { key: "period" as const, header: "period" },
  { key: "period_start" as const, header: "period_start" },
  { key: "period_end" as const, header: "period_end" },
  { key: "value" as const, header: "value" },
  { key: "unit" as const, header: "unit" },
  { key: "status" as const, header: "status" },
  { key: "geo_id" as const, header: "geo_id" },
  { key: "geo_name" as const, header: "geo_name" },
  { key: "source_id" as const, header: "source_id" },
  { key: "source_url" as const, header: "source_url" },
];

function Observations() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const page = search.page ?? 0;
  const query = useQuery({
    queryKey: ["observations", search],
    queryFn: () =>
      api.observations({
        indicator: search.indicator,
        geo: asText(search.geo),
        geo_type: search.geo_type,
        period_start: asText(search.period_start),
        period_end: asText(search.period_end),
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  });

  const meta = query.data?.meta;
  const total = meta?.total ?? 0;

  // Any filter change returns to the first page: page 3 of the old result set
  // is a different set of rows under the new filters, and staying there shows
  // an arbitrary slice of them.
  function setSearch(next: ObservationFilters) {
    navigate({ search: (prev) => ({ ...prev, ...next, page: 0 }) });
  }

  const [selected, setSelected] = useState<Observation[]>([]);

  function exportRows(rows: Observation[], suffix: string) {
    downloadCsv(
      `observations-${suffix}.csv`,
      toCsv(rows as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  const rows = query.data?.data ?? [];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <h1 className="font-heading text-2xl font-semibold tracking-tight">
              Observations
            </h1>
            {/* The count beside the title rather than under it: it is the
                first thing anyone checks after applying a filter. */}
            <Badge variant="secondary" className="tabular-nums">
              {query.isLoading ? "…" : formatCount(total)}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            Statistical figures with bounded periods and resolved geography.
          </p>
        </div>

        <Button
          variant="outline"
          size="sm"
          disabled={!rows.length}
          onClick={() => exportRows(rows, `page-${page + 1}`)}
        >
          <IconDownload className="size-4" />
          Export page
        </Button>
      </div>

      <ObservationFilterBar
        value={{
          indicator: search.indicator,
          geo: asText(search.geo),
          geo_type: search.geo_type,
          period_start: asText(search.period_start),
          period_end: asText(search.period_end),
        }}
        onChange={setSearch}
        onClear={() => navigate({ search: {} })}
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
        emptyMessage="No observations match these filters."
        selectable
        // Keyed on the observation rather than the row index, so a selection
        // survives sorting and a re-fetch.
        getRowId={(row) => row.observation_id}
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

      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>
          {total > 0
            ? `${formatCount(page * PAGE_SIZE + 1)}–${formatCount(
                Math.min((page + 1) * PAGE_SIZE, total),
              )} of ${formatCount(total)}`
            : null}
          {selected.length ? ` · ${formatCount(selected.length)} selected` : null}
        </span>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page === 0}
            onClick={() => navigate({ search: (prev) => ({ ...prev, page: page - 1 }) })}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!meta?.has_more}
            onClick={() => navigate({ search: (prev) => ({ ...prev, page: page + 1 }) })}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}
