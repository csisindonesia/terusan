import { useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { useState } from "react";
import { z } from "zod";

import { DataTable } from "~/components/data-table";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import { api, type Observation } from "~/lib/api";
import { formatCount, formatDecimal, statusLabel } from "~/lib/format";

// Filters live in the URL so a filtered view is a link someone can send —
// which for a research portal is most of the point.
const searchSchema = z.object({
  indicator: z.string().optional(),
  geo: z.string().optional(),
  geo_type: z.string().optional(),
  period_start: z.string().optional(),
  period_end: z.string().optional(),
  page: z.number().int().min(0).optional(),
});

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
  const [geo, setGeo] = useState(search.geo ?? "");

  const page = search.page ?? 0;
  const query = useQuery({
    queryKey: ["observations", search],
    queryFn: () =>
      api.observations({
        indicator: search.indicator,
        geo: search.geo,
        geo_type: search.geo_type,
        period_start: search.period_start,
        period_end: search.period_end,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  });

  const meta = query.data?.meta;
  const total = meta?.total ?? 0;

  function setSearch(next: Partial<typeof search>) {
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

      <div className="flex flex-wrap items-center gap-2">
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setSearch({ geo: geo.trim() || undefined });
          }}
        >
          <Input
            value={geo}
            onChange={(event) => setGeo(event.target.value)}
            placeholder="Place code, e.g. IDN or ID-32"
            className="w-64"
          />
          <Button type="submit" variant="secondary">
            Filter
          </Button>
        </form>

        <Select
          value={search.geo_type ?? "all"}
          onValueChange={(value) =>
            // This Select can clear to null; the search schema wants the key
            // absent rather than nulled, so both cases collapse to undefined.
            setSearch({ geo_type: !value || value === "all" ? undefined : value })
          }
        >
          <SelectTrigger className="w-44">
            <SelectValue placeholder="All places" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All places</SelectItem>
            <SelectItem value="country">Countries</SelectItem>
            {/* Aggregates are sums of the countries beside them, so a total
                over everything is several times the truth. */}
            <SelectItem value="region">Aggregates</SelectItem>
            <SelectItem value="province">Provinces</SelectItem>
          </SelectContent>
        </Select>

        {search.geo || search.geo_type || search.period_start ? (
          <Button
            variant="ghost"
            onClick={() => {
              setGeo("");
              navigate({ search: {} });
            }}
          >
            Clear
          </Button>
        ) : null}
      </div>

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
