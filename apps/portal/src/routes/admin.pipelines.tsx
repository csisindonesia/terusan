/**
 * Every pipeline, one row each, with how it last went (program.md §39, §50).
 *
 * The Jobs page lists runs, newest first, which is the right shape for "what
 * happened last night" and the wrong one for "which of these is broken": with
 * an ingestion per source and a normalization per series there are hundreds of
 * pipelines, and a quiet one that failed a week ago is pages down the history.
 * Here each appears once, failing ones first, and a row opens its history on
 * the Jobs page.
 */

import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconCopy, IconDownload, IconHistory, IconRefresh } from "@tabler/icons-react";
import { z } from "zod";

import { ChoiceFilter } from "~/components/choice-filter";
import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { PipelineHealthBadge } from "~/components/pipeline-health";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { SearchInput } from "~/components/search-input";
import { StickyHeader } from "~/components/sticky-header";
import { TablePagination } from "~/components/table-pagination";
import { TableToolbar } from "~/components/table-toolbar";
import { Button } from "~/components/ui/button";
import { api, type Pipeline } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import {
  formatCount,
  formatDuration,
  formatMoment,
  formatRelative,
} from "~/lib/format";
import { HEALTH_LABELS, HEALTH_ORDER, healthOf, type Health } from "~/lib/pipelines";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";

const searchSchema = z.object({
  kind: listParam,
  health: listParam,
  q: textParam,
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/admin/pipelines")({
  validateSearch: searchSchema,
  component: Pipelines,
});

const PAGE_SIZE = 50;

const KINDS = [
  { value: "ingest", label: "Ingest" },
  { value: "extract", label: "Extract" },
  { value: "normalize", label: "Normalize" },
];

const HEALTHS = (Object.keys(HEALTH_LABELS) as Health[]).map((value) => ({
  value,
  label: HEALTH_LABELS[value],
}));

const columns: ColumnDef<Pipeline>[] = [
  {
    accessorKey: "pipeline",
    header: "Pipeline",
    meta: { width: "w-80" },
    cell: ({ row }) => {
      const { pipeline, kind, source_id, dataset, indicator_id } = row.original;
      return (
        <StackedCell
          primary={
            <Link
              to="/jobs"
              search={{ pipeline }}
              className="font-mono text-xs underline-offset-4 hover:underline"
            >
              {pipeline}
            </Link>
          }
          secondary={
            // Extraction reads the whole of RAW at once and names nothing.
            [kind, source_id, dataset ?? indicator_id].filter(Boolean).join(" · ") ||
            kind
          }
        />
      );
    },
  },
  {
    id: "health",
    header: "Last run",
    meta: { width: "w-48" },
    cell: ({ row }) => (
      <div className="space-y-1">
        <PipelineHealthBadge pipeline={row.original} />
        {row.original.last_status === "failed" && row.original.last_error ? (
          <p
            className="line-clamp-2 max-w-[14rem] font-mono text-xs break-words text-destructive"
            title={row.original.last_error}
          >
            {row.original.last_error}
          </p>
        ) : null}
      </div>
    ),
  },
  {
    accessorKey: "last_started_at",
    header: "Ran",
    meta: { width: "w-44" },
    cell: ({ row }) => (
      <StackedCell
        primary={formatMoment(row.original.last_started_at)}
        secondary={`${formatRelative(row.original.last_started_at) ?? ""} · ${row.original.last_trigger}`}
      />
    ),
  },
  {
    accessorKey: "last_succeeded_at",
    header: "Last success",
    meta: { width: "w-40" },
    cell: ({ row }) =>
      row.original.last_succeeded_at ? (
        <StackedCell
          primary={formatMoment(row.original.last_succeeded_at)}
          secondary={formatRelative(row.original.last_succeeded_at)}
        />
      ) : (
        // Never, rather than a blank: a blank reads as "not recorded".
        <span className="text-muted-foreground">Never</span>
      ),
  },
  {
    accessorKey: "last_records_out",
    header: "Wrote",
    meta: { align: "right", width: "w-24" },
    cell: ({ row }) => (
      <span className="tabular-nums">{formatCount(row.original.last_records_out)}</span>
    ),
  },
  {
    accessorKey: "runs",
    header: "Runs",
    meta: { align: "right", width: "w-28" },
    cell: ({ row }) => (
      <StackedCell
        primary={<span className="tabular-nums">{formatCount(row.original.runs)}</span>}
        secondary={
          row.original.failures
            ? `${formatCount(row.original.failures)} failed`
            : "none failed"
        }
      />
    ),
  },
  {
    accessorKey: "last_duration_seconds",
    header: "Took",
    meta: { align: "right", width: "w-20" },
    cell: ({ row }) => (
      <span className="tabular-nums">
        {formatDuration(row.original.last_duration_seconds)}
      </span>
    ),
  },
  {
    id: "actions",
    header: "",
    enableSorting: false,
    meta: { align: "right" },
    cell: ({ row }) => <PipelineActions pipeline={row.original} />,
  },
];

function PipelineActions({ pipeline }: { pipeline: Pipeline }) {
  const navigate = useNavigate();
  return (
    <RowActions
      bare
      actions={[
        {
          label: "Run history",
          icon: IconHistory,
          onSelect: () =>
            void navigate({ to: "/jobs", search: { pipeline: pipeline.pipeline } }),
        },
        {
          label: "Copy pipeline name",
          icon: IconCopy,
          onSelect: () => void copyToClipboard(pipeline.pipeline),
        },
        {
          label: "Copy last error",
          icon: IconCopy,
          onSelect: pipeline.last_error
            ? () => void copyToClipboard(pipeline.last_error as string)
            : undefined,
          hint: pipeline.last_error ? undefined : "It has never failed",
        },
      ]}
    />
  );
}

const EXPORT_COLUMNS = [
  { key: "pipeline" as const, header: "pipeline" },
  { key: "kind" as const, header: "kind" },
  { key: "source_id" as const, header: "source_id" },
  { key: "dataset" as const, header: "dataset" },
  { key: "indicator_id" as const, header: "indicator_id" },
  { key: "last_status" as const, header: "last_status" },
  { key: "last_started_at" as const, header: "last_started_at" },
  { key: "last_succeeded_at" as const, header: "last_succeeded_at" },
  { key: "last_records_out" as const, header: "last_records_out" },
  { key: "runs" as const, header: "runs" },
  { key: "failures" as const, header: "failures" },
  { key: "failing_streak" as const, header: "failing_streak" },
  { key: "last_error" as const, header: "last_error" },
];

function Pipelines() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const query = useQuery({ queryKey: ["pipelines"], queryFn: () => api.pipelines() });

  const all = query.data?.data ?? [];
  const kinds = asTextList(search.kind);
  const healths = asTextList(search.health);
  const needle = asText(search.q)?.toLowerCase() ?? "";

  const rows = all
    .filter(
      (pipeline) =>
        (!kinds.length || kinds.includes(pipeline.kind)) &&
        (!healths.length || healths.includes(healthOf(pipeline))) &&
        (!needle ||
          [
            pipeline.pipeline,
            pipeline.source_id,
            pipeline.dataset,
            pipeline.indicator_id,
          ]
            .filter(Boolean)
            .some((text) => (text as string).toLowerCase().includes(needle))),
    )
    // Broken first, and the longest broken first among those; then the API's
    // own order, most recently run first.
    .map((pipeline, index) => ({ pipeline, index }))
    .sort(
      (a, b) =>
        HEALTH_ORDER[healthOf(a.pipeline)] - HEALTH_ORDER[healthOf(b.pipeline)] ||
        b.pipeline.failing_streak - a.pipeline.failing_streak ||
        a.index - b.index,
    )
    .map(({ pipeline }) => pipeline);

  const failing = all.filter((pipeline) => healthOf(pipeline) === "failing").length;

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(search.page ?? 0, pageCount - 1);
  const visible = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function set(next: Partial<z.infer<typeof searchSchema>>) {
    navigate({ search: (prev) => ({ ...prev, ...next, page: 0 }) });
  }

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Pipelines"
            count={rows.length}
            isLoading={query.isLoading}
            description={
              query.isLoading
                ? "Every pipeline the journal has heard of, once each, with how it last went."
                : `Every pipeline the journal has heard of, once each, with how it last went. ${formatCount(failing)} failing now.`
            }
            actions={
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void query.refetch()}
                  disabled={query.isFetching}
                >
                  <IconRefresh className="size-4" />
                  {query.isFetching ? "Refreshing" : "Refresh"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!rows.length}
                  onClick={() =>
                    downloadCsv(
                      "pipelines.csv",
                      toCsv(
                        rows as unknown as Record<string, unknown>[],
                        EXPORT_COLUMNS,
                      ),
                    )
                  }
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
                  label="Stage"
                  options={KINDS}
                  selected={kinds}
                  onChange={(kind) => set({ kind })}
                />
                <ChoiceFilter
                  label="Last run"
                  options={HEALTHS}
                  selected={healths}
                  onChange={(health) => set({ health })}
                />
                {kinds.length || healths.length || search.q ? (
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
                placeholder="Search pipelines, sources and datasets"
                onSearch={(q) => set({ q })}
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
        data={visible}
        isLoading={query.isLoading}
        loadingRows={10}
        getRowId={(pipeline) => `${pipeline.kind}:${pipeline.pipeline}`}
        emptyMessage={
          all.length === 0
            ? "Nothing has been recorded yet. Every run writes a row to the journal as it finishes."
            : "No pipelines match these filters."
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
    </div>
  );
}
