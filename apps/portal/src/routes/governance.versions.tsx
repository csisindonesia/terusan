/**
 * Which version of the code produced what is in the lake, and which documents
 * changed under the same address (program.md §17).
 *
 * Every Bronze row records the parser version that wrote it and every Silver
 * row the pipeline version, so a change to either can be traced to the rows it
 * touched. Extraction is idempotent on (document, parser version) and appends
 * rather than overwrites, which has two consequences worth watching: a
 * document not re-read since the parser changed still holds the old reading,
 * and a document that has been re-read holds every reading. This page counts
 * both, source by source. Below them are the addresses that have served more
 * than one content — an agency republishing a table under the same URL, which
 * RAW keeps as separate, content-addressed versions.
 */

import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconCopy, IconDownload, IconHistory } from "@tabler/icons-react";
import { z } from "zod";

import { ClampedText } from "~/components/clamped-text";
import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { SearchInput } from "~/components/search-input";
import { Section } from "~/components/section";
import { StatRow } from "~/components/stat-row";
import { StickyHeader } from "~/components/sticky-header";
import { TableToolbar } from "~/components/table-toolbar";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type Versions } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount, formatDate, formatRelative, formatShare } from "~/lib/format";
import { asText, textParam } from "~/lib/search-params";

const searchSchema = z.object({
  behind: z.union([z.literal("1"), z.literal(1)]).optional(),
  q: textParam,
});

export const Route = createFileRoute("/governance/versions")({
  validateSearch: searchSchema,
  component: VersionsPage,
});

type SourceRow = Versions["sources"][number];
type Republished = Versions["republished"][number];
type TableVersion = Versions["tables"][number];

function sourceColumns(
  latest: string | undefined,
  categories: Map<string, string>,
): ColumnDef<SourceRow>[] {
  return [
    {
      accessorKey: "source_id",
      header: "Source",
      cell: ({ row }) => (
        <span className="font-mono text-xs">{row.original.source_id}</span>
      ),
    },
    {
      accessorKey: "documents",
      header: "Documents",
      meta: { align: "right" },
      cell: ({ row }) => formatCount(row.original.documents),
    },
    {
      accessorKey: "behind",
      header: latest ? `Not yet read at v${latest}` : "At an older parser",
      meta: { width: "w-48" },
      cell: ({ row }) =>
        row.original.behind ? (
          <StackedCell
            primary={
              <span className="tabular-nums text-destructive">
                {formatCount(row.original.behind)}
              </span>
            }
            secondary={`${formatShare(row.original.behind, row.original.documents)} of its documents`}
          />
        ) : (
          <Badge variant="secondary">All current</Badge>
        ),
    },
    {
      id: "versions",
      header: "Parser versions held",
      cell: ({ row }) => (
        <div className="flex max-w-[18rem] flex-wrap gap-1">
          {row.original.versions.map((version) => (
            <Badge key={version} variant={version === latest ? "secondary" : "outline"}>
              v{version}
            </Badge>
          ))}
        </div>
      ),
    },
    {
      accessorKey: "superseded_rows",
      header: "Superseded rows",
      meta: { align: "right", width: "w-40" },
      cell: ({ row }) =>
        row.original.superseded_rows ? (
          <StackedCell
            primary={
              <span className="tabular-nums">
                {formatCount(row.original.superseded_rows)}
              </span>
            }
            secondary={`${formatShare(row.original.superseded_rows, row.original.rows)} of ${formatCount(row.original.rows)}`}
          />
        ) : (
          <span className="text-muted-foreground">None</span>
        ),
    },
    {
      id: "actions",
      header: "",
      enableSorting: false,
      meta: { align: "right" },
      cell: ({ row }) => (
        <SourceActions
          sourceId={row.original.source_id}
          category={categories.get(row.original.source_id)}
        />
      ),
    },
  ];
}

function SourceActions({
  sourceId,
  category,
}: {
  sourceId: string;
  category?: string;
}) {
  const navigate = useNavigate();
  return (
    <RowActions
      bare
      actions={[
        {
          label: "Extraction history",
          icon: IconHistory,
          onSelect: () => void navigate({ to: "/jobs", search: { kind: "extract" } }),
        },
        {
          label: "Copy extract command",
          icon: IconCopy,
          // A plain run is enough: extraction skips only the documents
          // already read at the current parser version, so the ones behind
          // are read again. The source's RAW subtree is under its category.
          onSelect: category
            ? () =>
                void copyToClipboard(
                  `cd pipelines && uv run terusan warehouse extract ${category} ${sourceId}`,
                )
            : undefined,
          hint: category ? undefined : "The source is not in the registry",
        },
      ]}
    />
  );
}

const republishedColumns: ColumnDef<Republished>[] = [
  {
    accessorKey: "source_url",
    header: "Address",
    meta: { width: "w-[28rem]" },
    cell: ({ row }) => (
      <StackedCell
        primary={
          <a
            href={row.original.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="underline-offset-4 hover:underline"
          >
            <ClampedText className="max-w-[26rem]">
              {row.original.source_url}
            </ClampedText>
          </a>
        }
        secondary={<span className="font-mono">{row.original.source_id}</span>}
      />
    ),
  },
  {
    accessorKey: "versions",
    header: "Contents",
    meta: { align: "right" },
    cell: ({ row }) => formatCount(row.original.versions),
  },
  {
    accessorKey: "first_retrieved",
    header: "First fetched",
    cell: ({ row }) => formatDate(row.original.first_retrieved),
  },
  {
    accessorKey: "last_retrieved",
    header: "Last fetched",
    cell: ({ row }) => (
      <StackedCell
        primary={formatDate(row.original.last_retrieved)}
        secondary={formatRelative(row.original.last_retrieved)}
      />
    ),
  },
];

/** One table's versions, as a stacked bar: how much of it each version wrote. */
function TableVersions({
  entries,
  latest,
}: {
  entries: TableVersion[];
  latest?: string;
}) {
  const first = entries[0];
  if (!first) return null;
  const total = entries.reduce((sum, entry) => sum + entry.rows, 0);
  return (
    <div className="space-y-2 rounded-lg border p-4">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-mono text-xs">
          {first.layer}/{first.dataset}.{first.column}
        </span>
        <span className="text-xs text-muted-foreground tabular-nums">
          {formatCount(total)} rows
        </span>
      </div>
      <ul className="space-y-1">
        {entries.map((entry) => (
          <li
            key={entry.version ?? "none"}
            className="relative rounded px-2 py-1 text-xs"
          >
            <div
              aria-hidden
              className="absolute inset-y-0 left-0 rounded bg-muted"
              style={{ width: `${Math.max(0.5, (entry.rows / total) * 100)}%` }}
            />
            <div className="relative flex justify-between gap-3">
              <span className="font-mono">
                {entry.version ? `v${entry.version}` : "Not recorded"}
                {entry.column === "parser_version" && entry.version === latest ? (
                  <span className="ml-2 text-muted-foreground">newest</span>
                ) : null}
              </span>
              <span className="tabular-nums">
                {formatCount(entry.rows)}{" "}
                <span className="text-muted-foreground">
                  {formatShare(entry.rows, total)}
                </span>
              </span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function VersionsPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const query = useQuery({ queryKey: ["versions"], queryFn: () => api.versions() });
  const registry = useQuery({ queryKey: ["sources"], queryFn: () => api.sources() });
  const categories = new Map(
    (registry.data?.data ?? []).map((source) => [source.source_id, source.category]),
  );

  const data = query.data?.data;
  const latest = data?.latest_parser;
  const behindOnly = search.behind !== undefined;
  const needle = asText(search.q)?.toLowerCase() ?? "";

  const sources = (data?.sources ?? []).filter(
    (source) =>
      (!behindOnly || source.behind > 0) &&
      (!needle || source.source_id.toLowerCase().includes(needle)),
  );
  const republished = (data?.republished ?? []).filter(
    (entry) =>
      !needle ||
      entry.source_url.toLowerCase().includes(needle) ||
      (entry.source_id ?? "").toLowerCase().includes(needle),
  );

  // One card per table and column, in the order the API listed them.
  const grouped = new Map<string, TableVersion[]>();
  for (const entry of data?.tables ?? []) {
    const key = `${entry.layer}/${entry.dataset}.${entry.column}`;
    grouped.set(key, [...(grouped.get(key) ?? []), entry]);
  }

  const all = data?.sources ?? [];
  const documents = all.reduce((sum, source) => sum + source.documents, 0);
  const behind = all.reduce((sum, source) => sum + source.behind, 0);
  const rows = all.reduce((sum, source) => sum + source.rows, 0);
  const superseded = all.reduce((sum, source) => sum + source.superseded_rows, 0);

  function set(next: Partial<z.infer<typeof searchSchema>>) {
    navigate({ search: (prev) => ({ ...prev, ...next }) });
  }

  return (
    <div className="space-y-8">
      <StickyHeader
        heading={
          <PageHeader
            title="Versions"
            isLoading={query.isLoading}
            description={
              <>
                Which parser and pipeline versions wrote each layer, which documents
                were last read by an older parser, and which addresses have served more
                than one content.
                {latest ? ` The newest parser version in Bronze is v${latest}.` : null}
              </>
            }
            actions={
              <Button
                variant="outline"
                size="sm"
                disabled={!sources.length}
                onClick={() =>
                  downloadCsv(
                    "parser-versions.csv",
                    toCsv(
                      sources.map((source) => ({
                        ...source,
                        versions: source.versions.join(" "),
                      })),
                      [
                        { key: "source_id", header: "source_id" },
                        { key: "documents", header: "documents" },
                        { key: "current", header: "current" },
                        { key: "behind", header: "behind" },
                        { key: "versions", header: "versions" },
                        { key: "rows", header: "rows" },
                        { key: "superseded_rows", header: "superseded_rows" },
                      ],
                    ),
                  )
                }
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
              <Button
                variant={behindOnly ? "secondary" : "outline"}
                size="sm"
                className="h-8 rounded-full"
                onClick={() => set({ behind: behindOnly ? undefined : "1" })}
              >
                Behind the newest parser only
              </Button>
            }
            search={
              <SearchInput
                value={asText(search.q)}
                placeholder="Search sources and addresses"
                onSearch={(q) => set({ q })}
              />
            }
          />
        }
      />

      <StatRow
        isLoading={query.isLoading}
        stats={[
          { label: "Newest parser", value: latest ? `v${latest}` : "—" },
          {
            label: "Documents behind it",
            value: formatCount(behind),
            note: `${formatShare(behind, documents)} of ${formatCount(documents)}`,
            attention: behind > 0,
            onSelect: () => set({ behind: "1" }),
          },
          {
            label: "Superseded Bronze rows",
            value: formatCount(superseded),
            note: `${formatShare(superseded, rows)} of Bronze records`,
          },
          {
            label: "Republished addresses",
            value: formatCount(data?.republished_total ?? 0),
            note: "one URL, more than one content",
          },
        ]}
      />

      {query.isError ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
          {(query.error as Error).message}
        </p>
      ) : null}

      <Section
        title="By source"
        description={
          <>
            A document is behind when its newest reading predates the newest parser.
            Extraction skips only the documents already read at the current version, so
            these are read again the next time extraction runs over the source.
            Superseded rows are older readings of documents that have since been read
            again; they stay in Bronze so the two readings can be compared.
          </>
        }
      >
        <DataTable
          columns={sourceColumns(latest, categories)}
          data={sources}
          isLoading={query.isLoading}
          getRowId={(source) => source.source_id}
          emptyMessage={
            all.length ? "No source matches." : "Bronze holds no records yet."
          }
        />
      </Section>

      <Section
        title="Versions in each table"
        description="How many rows each recorded version wrote. Silver records the pipeline version; Bronze records both."
      >
        <div className="grid items-start gap-4 md:grid-cols-2">
          {[...grouped.entries()].map(([key, entries]) => (
            <TableVersions key={key} entries={entries} latest={latest} />
          ))}
        </div>
      </Section>

      <Section
        title="Republished documents"
        description={
          data && data.republished_total > data.republished.length
            ? `The ${formatCount(data.republished.length)} most recently fetched of ${formatCount(data.republished_total)} addresses that have served more than one content. RAW keeps each as its own version.`
            : "Addresses that have served more than one content, most recently fetched first. RAW keeps each as its own version."
        }
      >
        <DataTable
          columns={republishedColumns}
          data={republished}
          isLoading={query.isLoading}
          getRowId={(entry) => entry.source_url}
          emptyMessage="No address has served more than one content."
        />
        <p className="text-xs text-muted-foreground">
          A revised figure shows on its series' page:{" "}
          <Link to="/indicators" className="underline underline-offset-4">
            find the series
          </Link>{" "}
          a document feeds from its dataset's lineage.
        </p>
      </Section>
    </div>
  );
}
