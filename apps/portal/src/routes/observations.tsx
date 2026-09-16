import { useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { z } from "zod";

import { DataTable } from "~/components/data-table";
import {
  ObservationFilterBar,
  type ObservationFilters,
} from "~/components/observation-filters";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type Observation } from "~/lib/api";
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
      <div>
        <div className="font-medium">{row.original.geo_name ?? "—"}</div>
        <div className="text-xs text-muted-foreground">
          {row.original.geo_id ?? "unresolved"}
        </div>
      </div>
    ),
  },
  { accessorKey: "period", header: "Period" },
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
        <span className="inline-flex items-center justify-end gap-1.5">
          {!value_unambiguous ? (
            // The figure was read under an assumption that could have gone the
            // other way. Flagged here rather than silently trusted.
            <Badge variant="outline" title="Read under an assumption">
              ?
            </Badge>
          ) : null}
          <span>{formatDecimal(value)}</span>
          {unit ? <span className="text-xs text-muted-foreground">{unit}</span> : null}
        </span>
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

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Observations</h1>
        <p className="text-sm text-muted-foreground">
          {query.isLoading ? "Loading…" : `${formatCount(total)} figures`}
        </p>
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
        data={query.data?.data ?? []}
        isLoading={query.isLoading}
        loadingRows={10}
        emptyMessage="No observations match these filters."
      />

      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>
          {total > 0
            ? `${formatCount(page * PAGE_SIZE + 1)}–${formatCount(
                Math.min((page + 1) * PAGE_SIZE, total),
              )} of ${formatCount(total)}`
            : null}
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
