import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import {
  IconCopy,
  IconDownload,
  IconExternalLink,
  IconNews,
} from "@tabler/icons-react";
import { useState } from "react";
import { z } from "zod";

import { ClampedText } from "~/components/clamped-text";
import { DataTable, StackedCell } from "~/components/data-table";
import { ChoiceList, FilterChip, summarise } from "~/components/filter-chip";
import { PageHeader } from "~/components/page-header";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { SearchInput } from "~/components/search-input";
import { StickyHeader } from "~/components/sticky-header";
import { TablePagination } from "~/components/table-pagination";
import { TableToolbar } from "~/components/table-toolbar";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type NewsOutlet } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount, formatDate } from "~/lib/format";
import { placeLabel } from "~/lib/labels";
import { asText, textParam } from "~/lib/search-params";

const PAGE_SIZE = 50;

const searchSchema = z.object({
  q: textParam,
  province: textParam,
  /** Hide the titles that have been retired. */
  active: z.boolean().optional(),
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/news-outlets/")({
  validateSearch: searchSchema,
  component: NewsOutletsPage,
});

const STATES = [
  { value: "true", label: "Still publishing" },
  { value: "false", label: "Including retired" },
];

const columns: ColumnDef<NewsOutlet>[] = [
  {
    accessorKey: "outlet",
    header: "Newspaper",
    cell: ({ row }) => (
      <Link
        to="/news-outlets/$host"
        params={{ host: row.original.host }}
        className="block w-[11rem] max-w-full hover:underline"
      >
        <ClampedText className="font-medium">{row.original.outlet}</ClampedText>
      </Link>
    ),
  },
  {
    // The site, in a column of its own rather than under the title. A reader
    // scanning for a paper by its domain — which is how the outlet list is
    // actually kept — should be able to read down one column, and a domain
    // tucked under a name is read as a subtitle rather than as an address.
    accessorKey: "host",
    header: "Site",
    cell: ({ row }) => (
      // Out to the paper itself, not to its page here: the name beside it
      // already leads there, and two links to the same place in one row is a
      // choice a reader has to make for no reason.
      <a
        href={row.original.base_url}
        target="_blank"
        rel="noreferrer"
        className="inline-flex max-w-[9rem] items-center gap-1 text-muted-foreground hover:text-foreground hover:underline"
        onClick={(event) => event.stopPropagation()}
      >
        <ClampedText>{row.original.host}</ClampedText>
        <IconExternalLink className="size-3.5 shrink-0" />
      </a>
    ),
  },
  {
    accessorKey: "province",
    header: "Province",
    cell: ({ row }) => (
      <StackedCell
        // Out of the capitals the dimension stores, and clamped: `KEPULAUAN
        // BANGKA BELITUNG` otherwise sets this column's width for all
        // sixty-nine rows.
        primary={
          <ClampedText className="w-[7rem]">
            {placeLabel(row.original.province)}
          </ClampedText>
        }
        secondary={row.original.geo_id ?? undefined}
      />
    ),
  },
  {
    // The denominator, in its own column now that there is room for it. The
    // crawl reads everything a paper published and keeps almost none of it, so
    // this is the number that says whether a paper was read at all — and the
    // one that tells a quiet week from a crawl that never got there.
    accessorKey: "scanned",
    header: "Read",
    meta: { align: "right" },
    cell: ({ row }) =>
      row.original.scanned ? (
        <span className="tabular-nums">{formatCount(row.original.scanned)}</span>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
  {
    accessorKey: "recorded",
    header: "Violence",
    meta: { align: "right" },
    cell: ({ row }) => {
      const { recorded, scanned } = row.original;
      // Zero shown as plainly as any other number: a paper that published
      // nothing violent that week is a finding, not a gap. A dash means the
      // crawl has not reached this outlet yet, which is a different thing and
      // reads as one.
      if (!scanned) return <span className="text-muted-foreground">—</span>;
      const share = Math.round((recorded / scanned) * 1000) / 10;
      return (
        <StackedCell
          primary={<span className="tabular-nums">{formatCount(recorded)}</span>}
          // The share only where there is one. `0% of read` under a nought is
          // a second way of saying the same nothing, on most rows of the
          // table, and it was the widest thing in this column.
          secondary={recorded ? `${share}% of read` : undefined}
        />
      );
    },
  },
  {
    accessorKey: "last_seen",
    header: "Collected",
    cell: ({ row }) => (
      <span className="tabular-nums text-muted-foreground">
        {formatDate(row.original.last_seen)}
      </span>
    ),
  },
  {
    id: "status",
    header: "Status",
    enableSorting: false,
    cell: ({ row }) =>
      row.original.active ? (
        <Badge variant="outline" className="text-muted-foreground">
          Publishing
        </Badge>
      ) : (
        // The note says why. A retired paper stays on the list because it is
        // the reason its province's coverage thinned.
        <Badge variant="outline" title={row.original.note}>
          Retired
        </Badge>
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
            label: "Copy the host",
            icon: IconCopy,
            onSelect: () => void copyToClipboard(row.original.host),
          },
          {
            label: "See what was collected",
            icon: IconNews,
            onSelect: () => {
              window.location.href = `/news-outlets/${encodeURIComponent(row.original.host)}`;
            },
          },
          {
            label: "Open the paper",
            icon: IconExternalLink,
            onSelect: () => window.open(row.original.base_url, "_blank"),
          },
        ]}
      />
    ),
  },
];

const EXPORT_COLUMNS = [
  { key: "outlet" as const, header: "outlet" },
  { key: "host" as const, header: "host" },
  { key: "province" as const, header: "province" },
  { key: "geo_id" as const, header: "geo_id" },
  { key: "scanned" as const, header: "scanned" },
  { key: "matched" as const, header: "matched" },
  { key: "recorded" as const, header: "recorded" },
  { key: "articles" as const, header: "articles_stored" },
  { key: "last_seen" as const, header: "last_seen" },
  { key: "active" as const, header: "active" },
  { key: "note" as const, header: "note" },
];

function NewsOutletsPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [selected, setSelected] = useState<NewsOutlet[]>([]);
  const page = search.page ?? 0;

  const list = useQuery({
    queryKey: ["news-outlets", search],
    queryFn: () =>
      api.newsOutlets({
        q: asText(search.q),
        province: asText(search.province),
        active: search.active === undefined ? undefined : String(search.active),
        order: "province",
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  });

  // The province options come from the list itself rather than a facets
  // endpoint: there are thirty-eight of them and they do not change, so a
  // second round trip would buy nothing.
  const all = useQuery({
    queryKey: ["news-outlets", "all"],
    queryFn: () => api.newsOutlets({ limit: 200 }),
  });

  const rows = list.data?.data ?? [];
  const total = list.data?.meta?.total ?? 0;
  const province = asText(search.province);
  const active = search.active === undefined ? [] : [String(search.active)];

  const provinces = Array.from(
    new Map(
      (all.data?.data ?? []).map((outlet) => [outlet.province, outlet] as const),
    ).values(),
  )
    .sort((a, b) => a.province.localeCompare(b.province))
    // The value is what the API filters by and stays as stored; only the
    // label is calmed.
    .map((outlet) => ({ value: outlet.province, label: placeLabel(outlet.province) }));

  const scanned = rows.reduce((sum, row) => sum + row.scanned, 0);
  const recorded = rows.reduce((sum, row) => sum + row.recorded, 0);

  function exportRows(chosen: NewsOutlet[], suffix: string) {
    downloadCsv(
      `news-outlets-${suffix}.csv`,
      toCsv(chosen as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="News outlets"
            count={total}
            isLoading={list.isLoading}
            description={`Two newspapers per province plus two national papers, read every day. Everything each one publishes is read and counted; only what is collective violence is kept. ${scanned ? `${formatCount(scanned)} articles read on this page, ${formatCount(recorded)} recorded. A dash means the crawl has not reached that paper yet.` : "Retired titles stay on the list: a paper that stopped publishing is why its province's coverage thinned."}`}
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
              <>
                <FilterChip
                  label="Province"
                  value={province}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, province: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={provinces}
                    selected={province ? [province] : []}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          province: province === value ? undefined : value,
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, province: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                <FilterChip
                  label="Status"
                  value={summarise(
                    active,
                    (v) => STATES.find((s) => s.value === v)?.label ?? v,
                  )}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, active: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={STATES}
                    selected={active}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          active: active[0] === value ? undefined : value === "true",
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, active: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                {province || active.length || search.q ? (
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
                placeholder="Search papers, hosts and provinces"
                onSearch={(q) =>
                  navigate({ search: (prev) => ({ ...prev, q, page: 0 }) })
                }
              />
            }
          />
        }
      />

      <DataTable
        columns={columns}
        data={rows}
        isLoading={list.isLoading}
        loadingRows={10}
        emptyMessage="The outlet list has not been published yet. Run `terusan silver dimensions`."
        selectable
        getRowId={(row) => row.host}
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
