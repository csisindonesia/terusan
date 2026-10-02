/**
 * The datasets as whoever keeps them sees them (program.md §9, §39).
 *
 * The public Datasets page answers "what is here"; this answers "is each of
 * them still being kept". A dataset is fed by one source's ingestion and
 * produced by one normalization per series, and either can break while the
 * figures already published go on looking fine. So each row carries both: the
 * ingestion of its source, and how many of its normalizations are failing.
 * Below them are the physical tables of the lake, which is where a dataset
 * that has gone missing from the catalogue can still be found.
 */

import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import {
  IconCopy,
  IconDatabase,
  IconDownload,
  IconHistory,
  IconRefresh,
} from "@tabler/icons-react";
import { z } from "zod";

import { ChoiceFilter } from "~/components/choice-filter";
import { ClampedText } from "~/components/clamped-text";
import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { PipelineHealthBadge } from "~/components/pipeline-health";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { RunSourceButton } from "~/components/run-source-button";
import { SearchInput } from "~/components/search-input";
import { StickyHeader } from "~/components/sticky-header";
import { TablePagination } from "~/components/table-pagination";
import { TableToolbar } from "~/components/table-toolbar";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { api, type Dataset, type LakeTable, type Pipeline } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount, formatDate, formatRelative } from "~/lib/format";
import { datasetLabel, titleFromId } from "~/lib/labels";
import { layerNote } from "~/lib/layers";
import { byDataset, bySource, healthOf } from "~/lib/pipelines";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";

const searchSchema = z.object({
  problem: listParam,
  q: textParam,
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/admin/datasets")({
  validateSearch: searchSchema,
  component: ManageDatasets,
});

const PAGE_SIZE = 50;

/** A dataset with the pipelines that keep it. */
type Row = Dataset & {
  ingest?: Pipeline;
  normalizations: Pipeline[];
  failingNormalizations: number;
};

type Problem = "normalize" | "ingest" | "untitled" | "unrecorded";

const PROBLEMS: { value: Problem; label: string }[] = [
  { value: "normalize", label: "Normalization failing" },
  { value: "ingest", label: "Ingestion failing" },
  // A dataset absent from `datasets.py` still serves, under a name made up
  // from its slug; worth finding, because that name is what readers see.
  { value: "untitled", label: "No declared title" },
  { value: "unrecorded", label: "No runs recorded" },
];

function problemsOf(row: Row): Problem[] {
  const out: Problem[] = [];
  if (row.failingNormalizations > 0) out.push("normalize");
  if (row.ingest && healthOf(row.ingest) === "failing") out.push("ingest");
  if (!row.title?.trim()) out.push("untitled");
  if (!row.ingest && !row.normalizations.length) out.push("unrecorded");
  return out;
}

const columns: ColumnDef<Row>[] = [
  {
    accessorKey: "dataset_id",
    header: "Dataset",
    meta: { width: "w-72" },
    cell: ({ row }) => (
      <div className="space-y-1">
        <Link
          to="/datasets/$datasetId"
          params={{ datasetId: row.original.dataset_id }}
          className="font-medium underline-offset-4 hover:underline"
        >
          <ClampedText className="max-w-[17rem]">
            {datasetLabel(row.original)}
          </ClampedText>
        </Link>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="font-mono">
            {row.original.slug ?? row.original.dataset_id}
          </span>
          {row.original.title?.trim() ? null : (
            <Badge variant="outline" title="Not declared in datasets.py">
              No declared title
            </Badge>
          )}
        </div>
      </div>
    ),
  },
  {
    id: "contents",
    header: "Holds",
    meta: { align: "right", width: "w-32" },
    cell: ({ row }) => (
      <StackedCell
        primary={
          <span className="tabular-nums">
            {formatCount(row.original.observations)} figures
          </span>
        }
        secondary={`${formatCount(row.original.series ?? row.original.indicators.length)} series · ${row.original.period_start} – ${row.original.period_end}`}
      />
    ),
  },
  {
    accessorKey: "last_updated",
    header: "Last updated",
    meta: { width: "w-36" },
    cell: ({ row }) => (
      <StackedCell
        primary={formatDate(row.original.last_updated)}
        secondary={formatRelative(row.original.last_updated)}
      />
    ),
  },
  {
    id: "ingest",
    header: "Ingestion",
    meta: { width: "w-48" },
    cell: ({ row }) => {
      const ingest = row.original.ingest;
      return (
        <div className="space-y-1">
          <div className="font-mono text-xs text-muted-foreground">
            {row.original.source_id}
          </div>
          {ingest ? (
            <div className="flex items-center gap-2">
              <PipelineHealthBadge pipeline={ingest} />
              <span className="text-xs text-muted-foreground">
                {formatRelative(ingest.last_started_at)}
              </span>
            </div>
          ) : (
            <span className="text-xs text-muted-foreground">Never run</span>
          )}
        </div>
      );
    },
  },
  {
    id: "normalize",
    header: "Normalization",
    meta: { width: "w-44" },
    cell: ({ row }) => {
      const { normalizations, failingNormalizations } = row.original;
      if (!normalizations.length) {
        return <span className="text-xs text-muted-foreground">No runs recorded</span>;
      }
      return failingNormalizations ? (
        <Link
          to="/admin/pipelines"
          search={{ q: row.original.slug, health: "failing" }}
          title="Open the failing pipelines"
        >
          <Badge variant="destructive">
            {formatCount(failingNormalizations)} of {formatCount(normalizations.length)}{" "}
            failing
          </Badge>
        </Link>
      ) : (
        <Badge variant="secondary">
          {formatCount(normalizations.length)} pipeline
          {normalizations.length === 1 ? "" : "s"} succeeded
        </Badge>
      );
    },
  },
  {
    id: "run",
    header: "",
    enableSorting: false,
    meta: { align: "right", width: "w-28" },
    cell: ({ row }) => <RunSourceButton sourceId={row.original.source_id} />,
  },
  {
    id: "actions",
    header: "",
    enableSorting: false,
    meta: { align: "right" },
    cell: ({ row }) => <DatasetActions dataset={row.original} />,
  },
];

function DatasetActions({ dataset }: { dataset: Row }) {
  const navigate = useNavigate();
  return (
    <RowActions
      bare
      actions={[
        {
          label: "Open dataset",
          icon: IconDatabase,
          onSelect: () =>
            void navigate({
              to: "/datasets/$datasetId",
              params: { datasetId: dataset.dataset_id },
            }),
        },
        {
          label: "Run history",
          icon: IconHistory,
          onSelect: () =>
            void navigate({ to: "/jobs", search: { source: dataset.source_id } }),
        },
        {
          label: "Copy dataset id",
          icon: IconCopy,
          onSelect: () => void copyToClipboard(dataset.dataset_id),
        },
        {
          label: "Copy ingest command",
          icon: IconCopy,
          onSelect: () =>
            void copyToClipboard(
              `cd pipelines && uv run terusan sources run ${dataset.source_id}`,
            ),
        },
      ]}
    />
  );
}

const EXPORT_COLUMNS = [
  { key: "dataset_id" as const, header: "dataset_id" },
  { key: "slug" as const, header: "slug" },
  { key: "title" as const, header: "title" },
  { key: "source_id" as const, header: "source_id" },
  { key: "observations" as const, header: "observations" },
  { key: "period_start" as const, header: "period_start" },
  { key: "period_end" as const, header: "period_end" },
  { key: "last_updated" as const, header: "last_updated" },
  { key: "ingest_status" as const, header: "ingest_status" },
  { key: "normalize_pipelines" as const, header: "normalize_pipelines" },
  { key: "normalize_failing" as const, header: "normalize_failing" },
];

function ManageDatasets() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => api.pipelines(),
  });

  const journal = pipelines.data?.data ?? [];
  const ingests = bySource(journal.filter((pipeline) => pipeline.kind === "ingest"));
  const normalizations = byDataset(
    journal.filter((pipeline) => pipeline.kind === "normalize"),
  );

  const all: Row[] = (datasets.data?.data ?? []).map((dataset) => {
    const own = (dataset.slug && normalizations.get(dataset.slug)) || [];
    return {
      ...dataset,
      ingest: ingests.get(dataset.source_id),
      normalizations: own,
      failingNormalizations: own.filter((pipeline) => healthOf(pipeline) === "failing")
        .length,
    };
  });

  const problems = asTextList(search.problem);
  const needle = asText(search.q)?.toLowerCase() ?? "";

  const rows = all
    .filter(
      (row) =>
        (!problems.length ||
          problemsOf(row).some((problem) => problems.includes(problem))) &&
        (!needle ||
          [row.dataset_id, row.slug, datasetLabel(row), row.source_id, row.organization]
            .filter(Boolean)
            .some((text) => (text as string).toLowerCase().includes(needle))),
    )
    // Rows with something wrong first, the most wrong first; then freshest.
    .sort(
      (a, b) =>
        problemsOf(b).length - problemsOf(a).length ||
        b.failingNormalizations - a.failingNormalizations ||
        (b.last_updated ?? "").localeCompare(a.last_updated ?? ""),
    );

  const troubled = all.filter(
    (row) => row.failingNormalizations > 0 || problemsOf(row).includes("ingest"),
  ).length;

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(search.page ?? 0, pageCount - 1);
  const visible = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const isLoading = datasets.isLoading || pipelines.isLoading;
  const error = datasets.error ?? pipelines.error;

  function set(next: Partial<z.infer<typeof searchSchema>>) {
    navigate({ search: (prev) => ({ ...prev, ...next, page: 0 }) });
  }

  function exportRows() {
    const flat = rows.map((row) => ({
      ...row,
      ingest_status: row.ingest?.last_status,
      normalize_pipelines: row.normalizations.length,
      normalize_failing: row.failingNormalizations,
    }));
    downloadCsv(
      "datasets-maintenance.csv",
      toCsv(flat as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Manage datasets"
            count={rows.length}
            isLoading={isLoading}
            description={
              isLoading
                ? "Each dataset with the ingestion that feeds it and the normalizations that produce it."
                : `Each dataset with the ingestion that feeds it and the normalizations that produce it. ${formatCount(troubled)} with a failing pipeline.`
            }
            actions={
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    void datasets.refetch();
                    void pipelines.refetch();
                  }}
                  disabled={datasets.isFetching || pipelines.isFetching}
                >
                  <IconRefresh className="size-4" />
                  {datasets.isFetching || pipelines.isFetching
                    ? "Refreshing"
                    : "Refresh"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!rows.length}
                  onClick={exportRows}
                >
                  <IconDownload className="size-4" />
                  Export
                </Button>
              </>
            }
          />
        }
        filters={
          <TableToolbar
            filters={
              <>
                <ChoiceFilter
                  label="Needs attention"
                  options={PROBLEMS}
                  selected={problems}
                  onChange={(problem) => set({ problem })}
                />
                {problems.length || search.q ? (
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
                placeholder="Search datasets and sources"
                onSearch={(q) => set({ q })}
              />
            }
          />
        }
      />

      {error ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
          {(error as Error).message}
        </p>
      ) : null}

      <DataTable
        columns={columns}
        data={visible}
        isLoading={isLoading}
        loadingRows={10}
        getRowId={(row) => row.dataset_id}
        emptyMessage={
          all.length === 0
            ? "No datasets are in Silver yet. Normalize a series and it appears here."
            : "No datasets match these filters."
        }
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
              {formatCount(rows.length)}
              {rows.length !== all.length
                ? ` (${formatCount(all.length)} total)`
                : null}
            </>
          ) : null
        }
      />

      <LakeTables />
    </div>
  );
}

/**
 * The physical tables, by layer.
 *
 * The catalogue above is derived from Silver observations, so a table that
 * holds rows but no figures — the registry, the document catalogue, a
 * dimension — is not in it. This is where those are counted.
 */
function LakeTables() {
  const storage = useQuery({ queryKey: ["storage"], queryFn: () => api.storage() });
  const tables = storage.data?.data ?? [];

  const tableColumns: ColumnDef<LakeTable>[] = [
    {
      accessorKey: "name",
      header: "Table",
      cell: ({ row }) => (
        <StackedCell
          primary={row.original.name}
          secondary={<span className="font-mono">{row.original.slug}</span>}
        />
      ),
    },
    {
      accessorKey: "layer",
      header: "Layer",
      meta: { width: "w-32" },
      cell: ({ row }) => (
        <Badge variant="outline" title={layerNote(row.original.layer)}>
          {titleFromId(row.original.layer)}
        </Badge>
      ),
    },
    {
      accessorKey: "rows",
      header: "Rows",
      meta: { align: "right", width: "w-32" },
      cell: ({ row }) => (
        <span className="tabular-nums">{formatCount(row.original.rows)}</span>
      ),
    },
  ];

  return (
    <section className="space-y-2 pt-4">
      <div className="flex items-baseline gap-2">
        <h2 className="font-heading text-sm font-medium">Lake tables</h2>
        <span className="text-xs text-muted-foreground">
          Every physical table, including the ones that hold no figures.
        </span>
      </div>
      <DataTable
        columns={tableColumns}
        data={tables}
        isLoading={storage.isLoading}
        loadingRows={6}
        getRowId={(table) => table.slug}
        emptyMessage="The lake holds no tables yet."
      />
    </section>
  );
}
