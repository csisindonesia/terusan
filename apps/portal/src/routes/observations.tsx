import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconCopy, IconDownload, IconExternalLink } from "@tabler/icons-react";
import { useMemo, useState } from "react";
import { z } from "zod";

import { ClampedText } from "~/components/clamped-text";
import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { StickyHeader } from "~/components/sticky-header";
import { SearchInput } from "~/components/search-input";
import { TableToolbar } from "~/components/table-toolbar";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { SaveQueryButton } from "~/components/save-query-button";
import { TablePagination } from "~/components/table-pagination";
import {
  ObservationFilterBar,
  type ObservationFilters,
} from "~/components/observation-filters";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type Indicator, type Observation } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";
import { formatCount, formatDecimal, statusLabel } from "~/lib/format";
import { indicatorLabel } from "~/lib/labels";

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
  indicator: listParam,
  geo: z.union([z.string(), z.number()]).optional(),
  geo_type: listParam,
  // Matched on the printed name, which is the commodity's identity here: the
  // reference registry resolves almost none of them, so the identifier is null
  // and the name is what the API filters on. This is what the commodities page
  // links to.
  commodity: listParam,
  q: textParam,
  period_start: period,
  period_end: period,
  page: z.number().int().min(0).optional(),
});

const PAGE_SIZE = 50;

export const Route = createFileRoute("/observations")({
  validateSearch: searchSchema,
  component: Observations,
});

// The series a row belongs to is looked up rather than carried on the row:
// the observations endpoint returns `indicator_id` and nothing readable, and
// the indicator list is already in the cache for the filter bar.
function columnsFor(indicators: Map<string, Indicator>): ColumnDef<Observation>[] {
  return [
    {
      // This table spans every series in the warehouse, and a place, a period
      // and a bare number read the same whichever series they came from. Without
      // the series named on the row, the reader cannot attribute a single figure
      // — 3.2 is an inflation rate or a tonnage or a price, and nothing here
      // says which.
      accessorKey: "indicator_id",
      header: "Indicator",
      cell: ({ row }) => {
        const id = row.original.indicator_id;
        const indicator = indicators.get(id);
        return (
          <Link
            to="/indicators/$indicatorId"
            params={{ indicatorId: id }}
            className="underline-offset-4 hover:underline"
          >
            <div className="leading-tight">
              {/* Titles run long; the whole of one is a hover away. */}
              <ClampedText className="max-w-[18rem] font-medium">
                {indicatorLabel(indicator ?? { indicator_id: id })}
              </ClampedText>
              {/* The publisher's own code where there is one, and otherwise the
                identifier the filters and the CSV are keyed on. */}
              <div className="text-xs text-muted-foreground">
                {indicator?.code ?? id}
              </div>
            </div>
          </Link>
        );
      },
    },
    {
      // This table spans every indicator, and they do not all vary along the same
      // dimension: a provincial figure is identified by its place, a food price
      // by its commodity. One column headed "Place" would print an em-dash for
      // every one of Bank Indonesia's thirty-one commodities and leave the reader
      // with thirty-one rows they cannot tell apart.
      id: "member",
      header: "Place or commodity",
      cell: ({ row }) => {
        const { geo_name, geo_id, commodity_name, commodity_id } = row.original;
        if (geo_name || geo_id) {
          return (
            <StackedCell primary={geo_name ?? "—"} secondary={geo_id ?? "unresolved"} />
          );
        }
        if (commodity_name || commodity_id) {
          return (
            <StackedCell
              primary={commodity_name ?? "—"}
              secondary={commodity_id ?? "commodity"}
            />
          );
        }
        // Neither: a national series, which is a fact about the row rather than
        // a gap in it.
        return <span className="text-muted-foreground">National</span>;
      },
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
    {
      id: "actions",
      header: "",
      enableSorting: false,
      meta: { align: "right" },
      cell: ({ row }) => (
        <RowActions
          actions={[
            {
              label: "Copy value",
              icon: IconCopy,
              // The stored decimal, not the formatted one: a figure lifted out
              // of here should be the figure, not its rendering.
              onSelect: row.original.value
                ? () => void copyToClipboard(row.original.value as string)
                : undefined,
              hint: row.original.value ? undefined : "This figure has no value",
            },
            {
              label: "Open source",
              icon: IconExternalLink,
              onSelect: row.original.source_url
                ? () => window.open(row.original.source_url, "_blank", "noopener")
                : undefined,
              hint: row.original.source_url ? undefined : "No source URL recorded",
            },
          ]}
        />
      ),
    },
  ];
}

const EXPORT_COLUMNS = [
  { key: "observation_id" as const, header: "observation_id" },
  { key: "indicator_id" as const, header: "indicator_id" },
  { key: "indicator_name" as const, header: "indicator_name" },
  { key: "period" as const, header: "period" },
  { key: "period_start" as const, header: "period_start" },
  { key: "period_end" as const, header: "period_end" },
  { key: "value" as const, header: "value" },
  { key: "unit" as const, header: "unit" },
  { key: "status" as const, header: "status" },
  { key: "geo_id" as const, header: "geo_id" },
  { key: "geo_name" as const, header: "geo_name" },
  { key: "commodity_id" as const, header: "commodity_id" },
  { key: "commodity_name" as const, header: "commodity_name" },
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
        indicator: asTextList(search.indicator),
        geo: asText(search.geo),
        geo_type: asTextList(search.geo_type),
        commodity: asTextList(search.commodity),
        q: asText(search.q),
        period_start: asText(search.period_start),
        period_end: asText(search.period_end),
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  });

  // Same query key as the filter bar's, so the two share one fetch.
  const indicators = useQuery({
    queryKey: ["indicators"],
    queryFn: () => api.indicators(),
  });
  const indicatorsById = useMemo(
    () => new Map((indicators.data?.data ?? []).map((i) => [i.indicator_id, i])),
    [indicators.data],
  );
  const columns = useMemo(() => columnsFor(indicatorsById), [indicatorsById]);

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
    // The identifier alone leaves a downloaded file unreadable, so the series
    // name rides along with it.
    const named = rows.map((row) => ({
      ...row,
      indicator_name: indicatorLabel(
        indicatorsById.get(row.indicator_id) ?? { indicator_id: row.indicator_id },
      ),
    }));
    downloadCsv(
      `observations-${suffix}.csv`,
      toCsv(named as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  const rows = query.data?.data ?? [];

  return (
    <div className="space-y-5">
      {/* Title and filters ride together: ten screens into a table the
          reader has lost sight of which filters are on, and a figure read
          under a filter nobody can see is a figure read wrong. */}
      <StickyHeader
        heading={
          <PageHeader
            title="Observations"
            count={total}
            isLoading={query.isLoading}
            description="Statistical figures with bounded periods and resolved geography."
            actions={
              <>
                {/* Kept as filters rather than as rows: a question asked of
                    this warehouse is worth re-asking next month, and the
                    combiner on the saved-queries page can line it up against
                    another one. */}
                <SaveQueryButton
                  kind="observations"
                  path="/observations"
                  search={{ ...search, page: undefined }}
                  suggestion={asText(search.q)}
                />
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!rows.length}
                  onClick={() => exportRows(rows, `page-${page + 1}`)}
                >
                  <IconDownload className="size-4" />
                  Export page
                </Button>
              </>
            }
          />
        }
        filters={
          <TableToolbar
            filters={
              <>
                <ObservationFilterBar
                  value={{
                    indicator: asTextList(search.indicator),
                    geo: asText(search.geo),
                    geo_type: asTextList(search.geo_type),
                    commodity: asTextList(search.commodity),
                    period_start: asText(search.period_start),
                    period_end: asText(search.period_end),
                  }}
                  onChange={setSearch}
                  onClear={() => navigate({ search: {} })}
                />
              </>
            }
            search={
              <SearchInput
                value={asText(search.q)}
                placeholder="Search places, commodities and indicators"
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
