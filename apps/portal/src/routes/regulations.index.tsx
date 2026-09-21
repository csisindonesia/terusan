import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconCopy, IconDownload, IconExternalLink } from "@tabler/icons-react";
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
import { api, type Facet, type Regulation } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount } from "~/lib/format";
import { toggle } from "~/lib/multi";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";

const PAGE_SIZE = 50;

const searchSchema = z.object({
  track: listParam,
  instrument: listParam,
  region_type: listParam,
  category: listParam,
  region: textParam,
  year: z.number().int().optional(),
  // A boolean, not a string: the router parses `?has_text=true` into a
  // real boolean before the schema sees it, and a string schema rejects it.
  has_text: z.boolean().optional(),
  q: textParam,
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/regulations/")({
  validateSearch: searchSchema,
  component: RegulationsPage,
});

/**
 * What the corpus calls the two halves, and what a reader calls them.
 *
 * `pkd` rather than `perkada`: the data uses the short form, and a filter
 * spelled the long way matches nothing at all.
 */
const TRACKS = [
  { value: "perda", label: "Perda", hint: "Passed with the regional legislature" },
  { value: "pkd", label: "Perkada", hint: "Issued by the head of the region alone" },
];

const TEXT_STATES = [
  {
    value: "true",
    label: "Readable here",
    hint: "Converted and segmented into articles",
  },
  { value: "false", label: "Catalogue only", hint: "No usable text — link out to BPK" },
];

/** Turns a facet list into the options a ChoiceList takes. */
function options(facets: Facet[] | undefined) {
  return (facets ?? []).map((facet) => ({
    value: facet.value,
    label: facet.value,
    hint: formatCount(facet.count),
  }));
}

const columns: ColumnDef<Regulation>[] = [
  {
    accessorKey: "title",
    header: "Regulation",
    cell: ({ row }) => {
      const { instrument, number, year, key, title } = row.original;
      // The instrument, number and year are what a lawyer cites it by, and the
      // title is what it is about. Both, stacked, because either alone leaves
      // the reader guessing.
      const citation = [instrument ?? "Peraturan", number && `No ${number}`, year]
        .filter(Boolean)
        .join(" ");
      return (
        <Link
          to="/regulations/$key"
          params={{ key }}
          // Fixed width and wrapping, because a regulation's formal title runs
          // to twenty words and the table cells are `whitespace-nowrap`: left
          // alone, one title pushes the row wider than the window and prints
          // over the columns beside it. Two lines, then an ellipsis — the
          // citation beneath identifies it, and the detail page has it whole.
          className="block w-[24rem] max-w-full whitespace-normal hover:underline"
          title={title}
        >
          <StackedCell
            primary={<span className="line-clamp-2">{title}</span>}
            secondary={citation}
          />
        </Link>
      );
    },
  },
  {
    accessorKey: "region_name",
    header: "Region",
    cell: ({ row }) => (
      <StackedCell
        primary={
          <span className="block w-40 max-w-full whitespace-normal">
            {row.original.region_name ?? "—"}
          </span>
        }
        secondary={row.original.region_type ?? undefined}
      />
    ),
  },
  {
    accessorKey: "year",
    header: "Year",
    meta: { align: "right" },
    cell: ({ row }) => <span className="tabular-nums">{row.original.year ?? "—"}</span>,
  },
  {
    accessorKey: "subject",
    header: "Subject",
    cell: ({ row }) => (
      <span className="line-clamp-2 block w-56 max-w-full whitespace-normal text-muted-foreground">
        {row.original.subject ?? "—"}
      </span>
    ),
  },
  {
    id: "text",
    header: "Text",
    enableSorting: false,
    cell: ({ row }) => {
      const { parse_status, pasal } = row.original;
      // Shown rather than hidden: 20,757 records were catalogued but never
      // converted, and a reader who clicks one deserves to know before they do
      // rather than after.
      if (parse_status !== "ok") {
        return (
          <Badge variant="outline" className="text-muted-foreground">
            {parse_status ?? "no text"}
          </Badge>
        );
      }
      return (
        <span className="text-muted-foreground tabular-nums">
          {pasal ? `${formatCount(pasal)} pasal` : "parsed"}
        </span>
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
            label: "Copy key",
            icon: IconCopy,
            onSelect: () => void copyToClipboard(row.original.key),
          },
          ...(row.original.pdf_url
            ? [
                {
                  label: "Open the PDF at BPK",
                  icon: IconExternalLink,
                  onSelect: () => window.open(row.original.pdf_url, "_blank"),
                },
              ]
            : []),
          ...(row.original.detail_url
            ? [
                {
                  label: "Open the record at BPK",
                  icon: IconExternalLink,
                  onSelect: () => window.open(row.original.detail_url, "_blank"),
                },
              ]
            : []),
        ]}
      />
    ),
  },
];

const EXPORT_COLUMNS = [
  { key: "key" as const, header: "key" },
  { key: "track" as const, header: "track" },
  { key: "instrument" as const, header: "instrument" },
  { key: "number" as const, header: "number" },
  { key: "year" as const, header: "year" },
  { key: "title" as const, header: "title" },
  { key: "region_name" as const, header: "region_name" },
  { key: "region_type" as const, header: "region_type" },
  { key: "subject" as const, header: "subject" },
  { key: "parse_status" as const, header: "parse_status" },
  { key: "detail_url" as const, header: "detail_url" },
  { key: "pdf_url" as const, header: "pdf_url" },
];

function RegulationsPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [selected, setSelected] = useState<Regulation[]>([]);

  const page = search.page ?? 0;

  const query = {
    track: asTextList(search.track),
    instrument: asTextList(search.instrument),
    region_type: asTextList(search.region_type),
    category: asTextList(search.category),
    region: asText(search.region),
    year: search.year,
    // The API takes the word, and String() of a boolean is exactly it.
    has_text: search.has_text === undefined ? undefined : String(search.has_text),
    q: asText(search.q),
  };

  const list = useQuery({
    queryKey: ["regulations", search],
    queryFn: () =>
      api.regulations({ ...query, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
  });

  // The facets answer to the same filters as the list, so an option that would
  // return nothing is never offered. The page is left out: which page you are
  // on does not change what the filters could be.
  const facets = useQuery({
    queryKey: ["regulation-facets", query],
    queryFn: () => api.regulationFacets(query),
  });

  const rows = list.data?.data ?? [];
  const total = list.data?.meta?.total ?? 0;
  const available = facets.data?.data;

  const tracks = asTextList(search.track);
  const instruments = asTextList(search.instrument);
  const regionTypes = asTextList(search.region_type);
  const categories = asTextList(search.category);
  const region = asText(search.region);
  const hasText = search.has_text === undefined ? undefined : String(search.has_text);

  const filtered =
    tracks.length ||
    instruments.length ||
    regionTypes.length ||
    categories.length ||
    region ||
    search.has_text !== undefined ||
    search.year !== undefined ||
    search.q;

  function exportRows(chosen: Regulation[], suffix: string) {
    downloadCsv(
      `regulations-${suffix}.csv`,
      toCsv(chosen as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Regulations"
            count={total}
            isLoading={list.isLoading}
            description="Perda and peraturan kepala daerah from BPK's legal documentation portal, 1952 onwards. Most carry their full text, segmented into bab and pasal; the rest are catalogued with the reason their text is missing, and link out to BPK."
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
                  value={summarise(
                    tracks,
                    (v) => TRACKS.find((t) => t.value === v)?.label ?? v,
                  )}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, track: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={TRACKS}
                    selected={tracks}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          track: toggle(tracks, value),
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, track: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                <FilterChip
                  label="Instrument"
                  value={summarise(instruments, (v) => v)}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, instrument: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={options(available?.instruments)}
                    selected={instruments}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          instrument: toggle(instruments, value),
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, instrument: undefined, page: 0 }),
                      })
                    }
                  />
                </FilterChip>

                <FilterChip
                  label="Level"
                  value={summarise(regionTypes, (v) => v)}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, region_type: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={options(available?.region_types)}
                    selected={regionTypes}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          region_type: toggle(regionTypes, value),
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          region_type: undefined,
                          page: 0,
                        }),
                      })
                    }
                  />
                </FilterChip>

                {/* One region at a time rather than several: there are 563 of
                    them, and a multi-select over that many is a list nobody
                    finishes reading. */}
                <FilterChip
                  label="Region"
                  value={region}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, region: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={options(available?.regions)}
                    selected={region ? [region] : []}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          region: prev.region === value ? undefined : value,
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, region: undefined, page: 0 }),
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
                  label="Text"
                  value={TEXT_STATES.find((t) => t.value === hasText)?.label}
                  onClear={() =>
                    navigate({
                      search: (prev) => ({ ...prev, has_text: undefined, page: 0 }),
                    })
                  }
                >
                  <ChoiceList
                    options={TEXT_STATES}
                    selected={hasText ? [hasText] : []}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          has_text:
                            prev.has_text === (value === "true")
                              ? undefined
                              : value === "true",
                          page: 0,
                        }),
                      })
                    }
                    onClear={() =>
                      navigate({
                        search: (prev) => ({ ...prev, has_text: undefined, page: 0 }),
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
                placeholder="Search titles and subjects"
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
        emptyMessage="No regulation matches these filters."
        selectable
        getRowId={(row) => row.key}
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
