import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import {
  IconCopy,
  IconDownload,
  IconExternalLink,
  IconFileDownload,
} from "@tabler/icons-react";
import { useState } from "react";
import { z } from "zod";

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
import { api, documentFileUrl, type Document, type Facet } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatBytes, formatCount, formatDate } from "~/lib/format";
import { toggle } from "~/lib/multi";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";

const PAGE_SIZE = 50;

const searchSchema = z.object({
  type: listParam,
  source_id: listParam,
  dataset_id: listParam,
  media_type: listParam,
  publisher: textParam,
  year: z.number().int().optional(),
  // A boolean, not a string: the router parses `?has_data=true` into a real
  // boolean before the schema sees it, and a string schema rejects it.
  has_data: z.boolean().optional(),
  q: textParam,
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/documents/")({
  validateSearch: searchSchema,
  component: DocumentsPage,
});

/**
 * What each kind of document is, in a reader's words.
 *
 * `data_file` is not in program.md §13's vocabulary and is the commonest thing
 * here: most of what a statistics warehouse collects is a spreadsheet, and
 * calling it a report would be the catalogue lying about its own contents.
 */
export const DOCUMENT_TYPES: Record<string, { label: string; hint: string }> = {
  publication: { label: "Publication", hint: "A titled work its publisher issues" },
  report: { label: "Report", hint: "A one-off document" },
  research: { label: "Research", hint: "A paper or study" },
  news: { label: "News", hint: "An article or clipping" },
  press_release: { label: "Press release", hint: "An announcement" },
  regulation: { label: "Regulation", hint: "A legal instrument" },
  court_decision: { label: "Court decision", hint: "A ruling" },
  clipping: { label: "Clipping", hint: "An excerpt" },
  web_page: { label: "Web page", hint: "A page as it was served" },
  data_file: { label: "Data file", hint: "A spreadsheet, CSV or API response" },
};

const DATA_STATES = [
  {
    value: "true",
    label: "Figures were read from it",
    hint: "Observations name it as their source",
  },
  {
    value: "false",
    label: "Nothing read yet",
    hint: "Collected and preserved, but no figures point at it",
  },
];

function typeLabel(value: string) {
  return DOCUMENT_TYPES[value]?.label ?? value;
}

/** Turns a facet list into the options a ChoiceList takes. */
function options(
  facets: Facet[] | undefined,
  label: (value: string) => string = (v) => v,
) {
  return (facets ?? []).map((facet) => ({
    value: facet.value,
    label: label(facet.value),
    hint: formatCount(facet.count),
  }));
}

const columns: ColumnDef<Document>[] = [
  {
    accessorKey: "title",
    header: "Document",
    cell: ({ row }) => {
      const { document_id, title, subtitle, publisher } = row.original;
      // Publisher first, then the partition: the publisher is what the eye
      // scans a column of titles for, and the partition is what tells two
      // retrievals of one collection apart once it has stopped.
      const beneath = [publisher, subtitle].filter(Boolean).join(" · ");
      return (
        <Link
          to="/documents/$documentId"
          params={{ documentId: document_id }}
          // Fixed width and wrapping, as on the regulations table: a
          // publisher's own title runs to twenty words, and the cells are
          // `whitespace-nowrap` — left alone, one title prints over the
          // columns beside it.
          className="block w-[26rem] max-w-full whitespace-normal hover:underline"
          title={title}
        >
          <StackedCell
            primary={<span className="line-clamp-2">{title}</span>}
            secondary={beneath || undefined}
          />
        </Link>
      );
    },
  },
  {
    accessorKey: "document_type",
    header: "Type",
    cell: ({ row }) => (
      <Badge variant="outline" className="text-muted-foreground">
        {typeLabel(row.original.document_type)}
      </Badge>
    ),
  },
  {
    accessorKey: "published_at",
    header: "Published",
    cell: ({ row }) => (
      <span className="tabular-nums text-muted-foreground">
        {formatDate(row.original.published_at)}
      </span>
    ),
  },
  {
    accessorKey: "size_bytes",
    header: "Size",
    meta: { align: "right" },
    cell: ({ row }) => (
      <span className="tabular-nums text-muted-foreground">
        {formatBytes(row.original.size_bytes)}
      </span>
    ),
  },
  {
    accessorKey: "observation_count",
    header: "Figures",
    meta: { align: "right" },
    enableSorting: false,
    cell: ({ row }) => {
      const { observation_count, indicator_count } = row.original;
      // Shown rather than hidden, and zero shown as plainly as any other
      // number: half of what the warehouse collects is a listing page nothing
      // was read out of, and a reader looking for sources is entitled to know
      // which half they are looking at.
      if (!observation_count) {
        return <span className="text-muted-foreground">—</span>;
      }
      return (
        <StackedCell
          primary={
            <span className="tabular-nums">{formatCount(observation_count)}</span>
          }
          secondary={`${formatCount(indicator_count)} series`}
        />
      );
    },
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
            label: "Copy document id",
            icon: IconCopy,
            onSelect: () => void copyToClipboard(row.original.document_id),
          },
          {
            label: "Download the preserved copy",
            icon: IconFileDownload,
            onSelect: () =>
              window.open(documentFileUrl(row.original.document_id), "_blank"),
          },
          ...(row.original.source_url
            ? [
                {
                  label: "Open at the publisher",
                  icon: IconExternalLink,
                  onSelect: () => window.open(row.original.source_url, "_blank"),
                },
              ]
            : []),
        ]}
      />
    ),
  },
];

const EXPORT_COLUMNS = [
  { key: "document_id" as const, header: "document_id" },
  { key: "document_type" as const, header: "document_type" },
  { key: "title" as const, header: "title" },
  { key: "subtitle" as const, header: "subtitle" },
  { key: "publisher" as const, header: "publisher" },
  { key: "published_at" as const, header: "published_at" },
  { key: "media_type" as const, header: "media_type" },
  { key: "size_bytes" as const, header: "size_bytes" },
  { key: "indicator_count" as const, header: "indicator_count" },
  { key: "observation_count" as const, header: "observation_count" },
  { key: "dataset_slug" as const, header: "dataset_slug" },
  { key: "source_id" as const, header: "source_id" },
  { key: "source_url" as const, header: "source_url" },
  { key: "content_hash" as const, header: "content_hash" },
];

function DocumentsPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [selected, setSelected] = useState<Document[]>([]);

  const page = search.page ?? 0;

  const query = {
    type: asTextList(search.type),
    source_id: asTextList(search.source_id),
    dataset_id: asTextList(search.dataset_id),
    media_type: asTextList(search.media_type),
    publisher: asText(search.publisher),
    year: search.year,
    // The API takes the word, and String() of a boolean is exactly it.
    has_data: search.has_data === undefined ? undefined : String(search.has_data),
    q: asText(search.q),
  };

  const list = useQuery({
    queryKey: ["documents", search],
    queryFn: () =>
      api.documents({ ...query, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
  });

  // The facets answer to the same filters as the list, so an option that would
  // return nothing is never offered. The page is left out: which page you are
  // on does not change what the filters could be.
  const facets = useQuery({
    queryKey: ["document-facets", query],
    queryFn: () => api.documentFacets(query),
  });
  // A facet carries an identifier and a count and has nowhere to put a title,
  // so the collection names come from the dataset catalogue. Cached under the
  // key the datasets page uses, so it is usually already loaded.
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });

  const rows = list.data?.data ?? [];
  const total = list.data?.meta?.total ?? 0;
  const available = facets.data?.data;

  const types = asTextList(search.type);
  const sources = asTextList(search.source_id);
  const datasetIds = asTextList(search.dataset_id);
  const mediaTypes = asTextList(search.media_type);
  const publisher = asText(search.publisher);
  const hasData = search.has_data === undefined ? undefined : String(search.has_data);

  /** A collection's title, falling back to its identifier until the list loads. */
  function datasetTitle(datasetId: string) {
    const entry = datasets.data?.data?.find((row) => row.dataset_id === datasetId);
    return entry?.title ?? entry?.slug ?? datasetId;
  }

  const filtered =
    types.length ||
    sources.length ||
    datasetIds.length ||
    mediaTypes.length ||
    publisher ||
    search.has_data !== undefined ||
    search.year !== undefined ||
    search.q;

  function exportRows(chosen: Document[], suffix: string) {
    downloadCsv(
      `documents-${suffix}.csv`,
      toCsv(chosen as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Documents"
            count={total}
            isLoading={list.isLoading}
            description="Publications the warehouse collected — handbooks, reports, papers. Each is kept as it was published, readable here, with the figures read out of it linked by name."
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
                  label="Type"
                  value={summarise(types, typeLabel)}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, type: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={(available?.types ?? []).map((facet) => ({
                      value: facet.value,
                      label: typeLabel(facet.value),
                      hint:
                        DOCUMENT_TYPES[facet.value]?.hint ?? formatCount(facet.count),
                    }))}
                    selected={types}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          type: toggle(types, value),
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, type: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                <FilterChip
                  label="Source"
                  value={summarise(sources, (v) => v)}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, source_id: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={options(available?.sources)}
                    selected={sources}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          source_id: toggle(sources, value),
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, source_id: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                {/* One publisher at a time rather than several: the list is
                    long, and a reader who has picked one is not usually
                    comparing it against another. */}
                <FilterChip
                  label="Publisher"
                  value={publisher}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, publisher: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={options(available?.publishers)}
                    selected={publisher ? [publisher] : []}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          publisher: prev.publisher === value ? undefined : value,
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, publisher: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                <FilterChip
                  label="Collection"
                  value={summarise(datasetIds, datasetTitle)}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, dataset_id: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={options(available?.datasets, datasetTitle)}
                    selected={datasetIds}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          dataset_id: toggle(datasetIds, value),
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, dataset_id: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                <FilterChip
                  label="Format"
                  value={summarise(mediaTypes, (v) => v)}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, media_type: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={options(available?.media_types)}
                    selected={mediaTypes}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          media_type: toggle(mediaTypes, value),
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, media_type: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                <FilterChip
                  label="Year"
                  value={search.year ? String(search.year) : undefined}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, year: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={options(available?.years)}
                    selected={search.year ? [String(search.year)] : []}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          year: prev.year === Number(value) ? undefined : Number(value),
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, year: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                <FilterChip
                  label="Figures"
                  value={DATA_STATES.find((s) => s.value === hasData)?.label}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, has_data: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={DATA_STATES}
                    selected={hasData ? [hasData] : []}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          has_data:
                            prev.has_data === (value === "true")
                              ? undefined
                              : value === "true",
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, has_data: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                {filtered ? (
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
                placeholder="Search titles, publishers and filenames"
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
        emptyMessage="No document matches these filters."
        selectable
        getRowId={(row) => row.document_id}
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
