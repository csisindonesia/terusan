import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconCopy, IconDownload } from "@tabler/icons-react";
import { useState } from "react";
import { z } from "zod";

import { DataTable, StackedCell } from "~/components/data-table";
import { ChoiceList, FilterChip, summarise } from "~/components/filter-chip";
import { ClampedText } from "~/components/clamped-text";
import { PageHeader } from "~/components/page-header";
import { StickyHeader } from "~/components/sticky-header";
import { SearchInput } from "~/components/search-input";
import { TableToolbar } from "~/components/table-toolbar";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { TablePagination } from "~/components/table-pagination";
import { Button } from "~/components/ui/button";
import { api, type Dataset } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount, formatDate, formatRelative } from "~/lib/format";
import { datasetLabel } from "~/lib/labels";
import { toggle } from "~/lib/multi";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";

const searchSchema = z.object({
  tag: listParam,
  organization: listParam,
  q: textParam,
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/datasets/")({
  validateSearch: searchSchema,
  component: Datasets,
});

const PAGE_SIZE = 50;

const columns: ColumnDef<Dataset>[] = [
  {
    accessorKey: "dataset_id",
    header: "Dataset",
    meta: { width: "w-96" },
    cell: ({ row }) => (
      // The title, and under it the name the collection is known by in the
      // pipeline. The identifier itself is a code and says nothing, which is
      // the point of it — it is in the URL, not in the row.
      <div className="space-y-1">
        <Link
          to="/datasets/$datasetId"
          params={{ datasetId: row.original.dataset_id }}
          className="font-medium underline-offset-4 hover:underline"
        >
          {/* Clamped: titles run to a sentence, and one sets the column's
              width for every row. The whole of it is a hover away. */}
          <ClampedText className="max-w-[24rem]">
            {datasetLabel(row.original)}
          </ClampedText>
        </Link>
        {row.original.slug && row.original.slug !== row.original.dataset_id ? (
          <div className="text-xs text-muted-foreground">{row.original.slug}</div>
        ) : null}
      </div>
    ),
  },
  {
    accessorKey: "organization",
    header: "Published by",
    // Clamped, like the dataset title: publisher names here run to
    // "Kementerian Keuangan Direktorat Jenderal Perimbangan Keuangan", and one
    // of those sets the width of the column for every row — pushing the
    // coverage and the update date off the right edge. The whole name is a
    // hover away.
    meta: { width: "w-56" },
    cell: ({ row }) => (
      <StackedCell
        primary={
          row.original.organization ? (
            <ClampedText className="max-w-[14rem]">
              {row.original.organization}
            </ClampedText>
          ) : (
            "—"
          )
        }
        secondary={row.original.source_id}
      />
    ),
  },
  {
    id: "indicators",
    header: "Series",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.indicators.length),
  },
  {
    accessorKey: "observations",
    header: "Figures",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.observations),
  },
  {
    id: "coverage",
    header: "Coverage",
    cell: ({ row }) => `${row.original.period_start} – ${row.original.period_end}`,
  },
  {
    accessorKey: "last_updated",
    header: "Last updated",
    cell: ({ row }) => (
      <StackedCell
        primary={formatDate(row.original.last_updated)}
        secondary={formatRelative(row.original.last_updated)}
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
            label: "Copy dataset id",
            icon: IconCopy,
            onSelect: () => void copyToClipboard(row.original.dataset_id),
          },
          {
            label: "Copy ingest command",
            icon: IconCopy,
            onSelect: () =>
              void copyToClipboard(
                `cd pipelines && uv run terusan sources run ${row.original.source_id}`,
              ),
          },
        ]}
      />
    ),
  },
];

const EXPORT_COLUMNS = [
  { key: "dataset_id" as const, header: "dataset_id" },
  { key: "slug" as const, header: "slug" },
  { key: "title" as const, header: "title" },
  { key: "tags" as const, header: "tags" },
  { key: "source_id" as const, header: "source_id" },
  { key: "organization" as const, header: "organization" },
  { key: "license" as const, header: "license" },
  { key: "observations" as const, header: "observations" },
  { key: "period_start" as const, header: "period_start" },
  { key: "period_end" as const, header: "period_end" },
  { key: "last_updated" as const, header: "last_updated" },
];

function Datasets() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const query = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });
  const [selected, setSelected] = useState<Dataset[]>([]);

  const all = query.data?.data ?? [];

  // Filtered in place: there are a handful of datasets, and a round trip to
  // narrow them is worse than not.
  const organizations = [
    ...new Set(all.map((dataset) => dataset.organization).filter(Boolean)),
  ].sort() as string[];
  const chosen = asTextList(search.organization);
  const tags = asTextList(search.tag);
  const needle = asText(search.q)?.toLowerCase() ?? "";

  // Every tag, not any: two tags chosen together narrow rather than widen,
  // which is what a reader picking a second one is asking for.
  const allTags = [...new Set(all.flatMap((dataset) => dataset.tags ?? []))].sort();

  const rows = all.filter(
    (dataset) =>
      (!chosen.length || chosen.includes(dataset.organization ?? "")) &&
      (!tags.length || tags.every((tag) => (dataset.tags ?? []).includes(tag))) &&
      (!needle ||
        dataset.dataset_id.toLowerCase().includes(needle) ||
        datasetLabel(dataset).toLowerCase().includes(needle) ||
        (dataset.slug ?? "").toLowerCase().includes(needle) ||
        (dataset.organization ?? "").toLowerCase().includes(needle) ||
        (dataset.tags ?? []).some((tag) => tag.includes(needle)) ||
        // The series inside it, so searching for an indicator finds the
        // collection it belongs to.
        dataset.indicators.some((id) => id.toLowerCase().includes(needle))),
  )
    // Freshest first: what changed is what a returning reader looks for.
    // ISO timestamps sort as text; a collection never updated goes last.
    .sort((a, b) => (b.last_updated ?? "").localeCompare(a.last_updated ?? ""));

  // Paged in place, like the filtering above it: the API answers with every
  // collection in one call, so a page is a slice rather than a round trip. A
  // page past the end of a freshly narrowed list would render empty, so the
  // requested page is clamped rather than trusted.
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(search.page ?? 0, pageCount - 1);
  const visible = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function exportRows(chosen: Dataset[], suffix: string) {
    downloadCsv(
      `datasets-${suffix}.csv`,
      toCsv(chosen as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Datasets"
            count={rows.length}
            isLoading={query.isLoading}
            description="Collections as their publishers issue them. A dataset is what an agency releases; the series inside it are what you plot."
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
        }
        filters={
          <TableToolbar
            filters={
              <>
                {organizations.length > 1 ? (
                  <FilterChip
                    label="Published by"
                    value={summarise(chosen)}
                    onClear={() =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          organization: undefined,
                          page: 0,
                        }),
                      })
                    }
                  >
                    <ChoiceList
                      options={organizations.map((name) => ({
                        value: name,
                        label: name,
                      }))}
                      selected={chosen}
                      searchPlaceholder="Search publishers"
                      onToggle={(name) =>
                        navigate({
                          search: (prev) => ({
                            ...prev,
                            organization: toggle(chosen, name),
                            page: 0,
                          }),
                        })
                      }
                      onClear={() =>
                        navigate({
                          search: (prev) => ({
                            ...prev,
                            organization: undefined,
                            page: 0,
                          }),
                        })
                      }
                    />
                  </FilterChip>
                ) : null}

                <FilterChip
                  label="Tag"
                  value={summarise(tags)}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, tag: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={allTags.map((value) => ({ value, label: value }))}
                    selected={tags}
                    searchPlaceholder="Search tags"
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          tag: toggle(tags, value),
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, tag: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                {chosen.length || tags.length || search.q ? (
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
                placeholder="Search datasets, publishers and series"
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
        data={visible}
        isLoading={query.isLoading}
        emptyMessage="No datasets match this filter."
        selectable
        getRowId={(row) => row.dataset_id}
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
        total={rows.length}
        pageSize={PAGE_SIZE}
        onPage={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
        summary={
          rows.length ? (
            <>
              {formatCount(page * PAGE_SIZE + 1)}–
              {formatCount(Math.min((page + 1) * PAGE_SIZE, rows.length))} of{" "}
              {formatCount(rows.length)} dataset{rows.length === 1 ? "" : "s"}
              {rows.length !== all.length
                ? ` (${formatCount(all.length)} total)`
                : null}
              {selected.length ? ` · ${formatCount(selected.length)} selected` : null}
            </>
          ) : null
        }
      />
    </div>
  );
}
