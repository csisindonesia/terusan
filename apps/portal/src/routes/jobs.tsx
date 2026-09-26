/**
 * What the pipelines have been doing, across the whole warehouse
 * (program.md §39, §50).
 *
 * The question this page answers is not "what do the figures say" but "is
 * anything still fetching them". A source that stopped refreshing looks
 * identical to one whose agency has published nothing — the figures are the
 * same either way, and only the journal tells the two apart. The indicator
 * page answers it for one series; this answers it for the platform, which is
 * where a gap in a source nobody is looking at shows up.
 *
 * Two things are listed, and the difference matters. A *run* is the row a
 * pipeline writes to the journal once it has finished, and it outlives the
 * process, the server and the deployment. A *job* is a process this API
 * started and is still watching — it exists during the minutes when there is
 * no row yet, and it dies with the server. A finished job's lasting record is
 * the run it produced, so the running ones are shown above the history rather
 * than mixed into it.
 */

import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import {
  IconCopy,
  IconDownload,
  IconFileDescription,
  IconRefresh,
} from "@tabler/icons-react";
import { useState } from "react";
import { z } from "zod";

import { DataTable, StackedCell } from "~/components/data-table";
import { PageHeader } from "~/components/page-header";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { RunFilterBar, type RunFilters } from "~/components/run-filters";
import { StickyHeader } from "~/components/sticky-header";
import { TablePagination } from "~/components/table-pagination";
import { TableToolbar } from "~/components/table-toolbar";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "~/components/ui/sheet";
import { api, type Job, type PipelineRun } from "~/lib/api";
import { capabilitiesQuery } from "~/lib/session";
import { downloadCsv, toCsv } from "~/lib/csv";
import {
  formatBytes,
  formatCount,
  formatDuration,
  formatMoment,
  formatRelative,
} from "~/lib/format";
import { asTextList, listParam } from "~/lib/search-params";

// Filters live in the URL, so "every failed ingestion of BPS" is a link
// someone can paste into an issue.
const searchSchema = z.object({
  kind: listParam,
  status: listParam,
  source: listParam,
  pipeline: listParam,
  page: z.number().int().min(0).optional(),
});

const PAGE_SIZE = 50;

/** How often the running jobs are asked about. Fast enough to read as live. */
const POLL_MS = 2000;

export const Route = createFileRoute("/jobs")({
  validateSearch: searchSchema,
  component: Jobs,
});

function columnsFor(onInspect: (run: PipelineRun) => void): ColumnDef<PipelineRun>[] {
  return [
    {
      accessorKey: "started_at",
      header: "Started",
      meta: { width: "w-48" },
      cell: ({ row }) => (
        <StackedCell
          primary={formatMoment(row.original.started_at)}
          secondary={formatRelative(row.original.started_at)}
        />
      ),
    },
    {
      accessorKey: "pipeline",
      header: "Pipeline",
      cell: ({ row }) => (
        <StackedCell
          primary={<span className="font-mono text-xs">{row.original.pipeline}</span>}
          secondary={row.original.kind}
        />
      ),
    },
    {
      // What the run touched. Extraction reads the whole of RAW at once and
      // names nothing, so an em-dash here is a fact about the stage rather
      // than a gap in the record.
      id: "target",
      header: "Target",
      cell: ({ row }) => {
        const { source_id, dataset, indicator_id } = row.original;
        if (!source_id && !dataset && !indicator_id) {
          return <span className="text-muted-foreground">Whole lake</span>;
        }
        return (
          <StackedCell
            primary={source_id ?? dataset ?? "—"}
            secondary={
              indicator_id ? (
                <Link
                  to="/indicators/$indicatorId"
                  params={{ indicatorId: indicator_id }}
                  className="font-mono underline-offset-4 hover:underline"
                >
                  {indicator_id}
                </Link>
              ) : (
                (dataset ?? undefined)
              )
            }
          />
        );
      },
    },
    {
      accessorKey: "status",
      header: "Status",
      meta: { width: "w-32" },
      cell: ({ row }) => <RunStatus run={row.original} />,
    },
    {
      id: "records",
      header: "Records",
      meta: { width: "w-36" },
      cell: ({ row }) => (
        <StackedCell
          primary={formatCount(row.original.records_out)}
          secondary={`of ${formatCount(row.original.records_in)} read`}
        />
      ),
    },
    {
      accessorKey: "duration_seconds",
      header: "Took",
      meta: { width: "w-24" },
      cell: ({ row }) => (
        <span className="tabular-nums">
          {formatDuration(row.original.duration_seconds)}
        </span>
      ),
    },
    {
      accessorKey: "trigger",
      header: "Trigger",
      meta: { width: "w-28" },
      cell: ({ row }) => (
        <span className="text-muted-foreground">{row.original.trigger}</span>
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
              label: "Run details",
              icon: IconFileDescription,
              onSelect: () => onInspect(row.original),
            },
            {
              label: "Copy command",
              icon: IconCopy,
              // The argv the journal recorded, so a reader can run the same
              // thing themselves rather than reconstruct it from the name.
              onSelect: row.original.command
                ? () => void copyToClipboard(row.original.command as string)
                : undefined,
              hint: row.original.command ? undefined : "No command was recorded",
            },
          ]}
        />
      ),
    },
  ];
}

/**
 * A dry run lands nothing on purpose, so its zero records must not read as a
 * pipeline that has stopped working.
 */
function RunStatus({ run }: { run: PipelineRun }) {
  if (run.status === "failed") return <Badge variant="destructive">Failed</Badge>;
  if (run.dry_run) return <Badge variant="outline">Dry run</Badge>;
  // Succeeded and landed nothing is the quiet failure this page exists for,
  // and it reads as a success everywhere else.
  if (run.records_out === 0) {
    return (
      <Badge variant="outline" title="The run succeeded and wrote no records">
        Empty
      </Badge>
    );
  }
  return <Badge variant="secondary">Succeeded</Badge>;
}

const EXPORT_COLUMNS = [
  { key: "run_id" as const, header: "run_id" },
  { key: "started_at" as const, header: "started_at" },
  { key: "finished_at" as const, header: "finished_at" },
  { key: "pipeline" as const, header: "pipeline" },
  { key: "kind" as const, header: "kind" },
  { key: "status" as const, header: "status" },
  { key: "trigger" as const, header: "trigger" },
  { key: "source_id" as const, header: "source_id" },
  { key: "dataset" as const, header: "dataset" },
  { key: "indicator_id" as const, header: "indicator_id" },
  { key: "records_in" as const, header: "records_in" },
  { key: "records_out" as const, header: "records_out" },
  { key: "bytes_written" as const, header: "bytes_written" },
  { key: "duration_seconds" as const, header: "duration_seconds" },
  { key: "dry_run" as const, header: "dry_run" },
  { key: "error_message" as const, header: "error_message" },
  { key: "command" as const, header: "command" },
  { key: "host" as const, header: "host" },
];

function Jobs() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const page = search.page ?? 0;
  const [inspecting, setInspecting] = useState<PipelineRun | undefined>();

  const runs = useQuery({
    queryKey: ["runs", search],
    queryFn: () =>
      api.runs({
        kind: asTextList(search.kind),
        status: asTextList(search.status),
        source: asTextList(search.source),
        pipeline: asTextList(search.pipeline),
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  });

  const rows = runs.data?.data ?? [];
  const total = runs.data?.meta?.total ?? 0;

  // Any filter change returns to the first page: page 3 of the old history is
  // a different set of rows under the new filters.
  function setSearch(next: RunFilters) {
    navigate({ search: (prev) => ({ ...prev, ...next, page: 0 }) });
  }

  function exportRows() {
    downloadCsv(
      `runs-page-${page + 1}.csv`,
      toCsv(rows as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  const columns = columnsFor(setInspecting);

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Jobs"
            count={total}
            isLoading={runs.isLoading}
            description="Every pipeline run the journal recorded, newest first — what ran, what it landed, and what it said when it failed."
            actions={
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void runs.refetch()}
                  disabled={runs.isFetching}
                >
                  <IconRefresh className="size-4" />
                  {runs.isFetching ? "Refreshing" : "Refresh"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!rows.length}
                  onClick={exportRows}
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
              <RunFilterBar
                value={{
                  kind: asTextList(search.kind),
                  status: asTextList(search.status),
                  source: asTextList(search.source),
                  pipeline: asTextList(search.pipeline),
                }}
                onChange={setSearch}
                onClear={() => navigate({ search: {} })}
              />
            }
          />
        }
      />

      <RunningJobs />

      {runs.isError ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
          {(runs.error as Error).message}
        </p>
      ) : null}

      <DataTable
        columns={columns}
        data={rows}
        isLoading={runs.isLoading}
        loadingRows={10}
        getRowId={(run) => run.run_id}
        emptyMessage={
          // An empty journal means no rows were recorded, not that the
          // pipelines are broken — saying which is the difference between a
          // reader checking the pipeline and a reader distrusting the page.
          total === 0
            ? "Nothing has been recorded yet. Every run writes a row as it finishes, whether it succeeded or not."
            : "No runs match these filters."
        }
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
            </>
          ) : null
        }
      />

      <RunSheet run={inspecting} onClose={() => setInspecting(undefined)} />
    </div>
  );
}

/**
 * What is going right now.
 *
 * Only this API process knows: a job is a child process it started, so a run
 * begun from a terminal appears here not at all and in the history below the
 * moment it finishes. Absent entirely where the deployment does not run
 * pipelines, because an empty panel headed "Running" would read as "nothing is
 * running" on a server that could never say so.
 */
function RunningJobs() {
  const capabilities = useQuery(capabilitiesQuery);
  const available = capabilities.data?.run_pipelines ?? false;

  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.jobs(),
    enabled: available,
    // Only while something is going: a finished job never changes again.
    refetchInterval: (query) =>
      query.state.data?.data?.some((job) => job.status === "running") ? POLL_MS : false,
  });

  const rows = jobs.data?.data ?? [];
  if (!available || !rows.length) return null;

  const running = rows.filter((job) => job.status === "running");

  return (
    <section className="space-y-2">
      <div className="flex items-center gap-2">
        <h2 className="font-heading text-sm font-medium">
          {running.length ? "Running now" : "Started from this server"}
        </h2>
        <span className="text-xs text-muted-foreground">
          {running.length
            ? "A run writes its journal row when it finishes, so these are not in the history yet."
            : "Finished, and recorded in the history below."}
        </span>
      </div>
      <div className="space-y-2">
        {rows.map((job) => (
          <JobCard key={job.job_id} job={job} />
        ))}
      </div>
    </section>
  );
}

function JobCard({ job }: { job: Job }) {
  const running = job.status === "running";
  return (
    <div className="rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge
          variant={
            running ? "secondary" : job.status === "failed" ? "destructive" : "outline"
          }
        >
          {running ? "Running" : job.status === "failed" ? "Failed" : "Finished"}
        </Badge>
        <span className="font-medium">{job.target}</span>
        <span className="text-muted-foreground">{job.kind}</span>
        <span className="ml-auto text-xs text-muted-foreground tabular-nums">
          {formatDuration(job.duration_seconds)} · started{" "}
          {formatMoment(job.started_at)}
        </span>
      </div>
      <code className="mt-2 block font-mono text-xs break-words text-muted-foreground">
        {job.command}
      </code>
      {job.error ? (
        <p className="mt-2 font-mono text-xs break-words text-destructive">
          {job.error}
        </p>
      ) : null}
      {job.output.length ? (
        // The tail of what the command printed: while it runs this is the only
        // sign of progress there is.
        <pre className="mt-2 max-h-40 overflow-auto rounded bg-muted/50 p-2 font-mono text-xs whitespace-pre-wrap">
          {job.output.join("\n")}
        </pre>
      ) : null}
    </div>
  );
}

/**
 * One run, in full.
 *
 * The table carries what distinguishes a row from its neighbours; everything
 * else — the argv, the host, the stage's own counters, the message a failure
 * recorded — is what someone opens a row to read, and there is no room for it
 * in a column.
 */
function RunSheet({ run, onClose }: { run?: PipelineRun; onClose: () => void }) {
  return (
    <Sheet open={Boolean(run)} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-lg">
        {run ? (
          <>
            <SheetHeader>
              <SheetTitle className="font-mono text-sm break-words">
                {run.pipeline}
              </SheetTitle>
              <SheetDescription>
                {formatMoment(run.started_at)} · {formatDuration(run.duration_seconds)}{" "}
                · {run.trigger}
              </SheetDescription>
            </SheetHeader>

            <div className="space-y-4 px-4 pb-6">
              <div className="flex flex-wrap items-center gap-2">
                <RunStatus run={run} />
                <Badge variant="outline">{run.kind}</Badge>
                {run.pipeline_version ? (
                  <Badge variant="outline">pipeline v{run.pipeline_version}</Badge>
                ) : null}
                {run.parser_version ? (
                  <Badge variant="outline">parser v{run.parser_version}</Badge>
                ) : null}
              </div>

              {run.error_message ? (
                <div className="rounded-lg border border-destructive/40 p-3">
                  <div className="text-xs text-muted-foreground">What it said</div>
                  <p className="mt-1 font-mono text-xs break-words text-destructive">
                    {run.error_message}
                  </p>
                </div>
              ) : null}

              <dl className="divide-y text-sm">
                <Row
                  label="Records"
                  value={`${formatCount(run.records_out)} written of ${formatCount(run.records_in)} read`}
                />
                <Row label="Bytes" value={formatBytes(run.bytes_written)} />
                <Row label="Finished" value={formatMoment(run.finished_at)} />
                <Row label="Source" value={run.source_id} />
                <Row label="Dataset" value={run.dataset} />
                <Row
                  label="Indicator"
                  value={
                    run.indicator_id ? (
                      <Link
                        to="/indicators/$indicatorId"
                        params={{ indicatorId: run.indicator_id }}
                        className="font-mono underline underline-offset-4"
                      >
                        {run.indicator_id}
                      </Link>
                    ) : undefined
                  }
                />
                <Row label="Host" value={run.host} />
                <Row
                  label="Run"
                  value={<span className="font-mono text-xs">{run.run_id}</span>}
                />
              </dl>

              {run.command ? (
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">Command</span>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void copyToClipboard(run.command as string)}
                    >
                      <IconCopy className="size-4" />
                      Copy
                    </Button>
                  </div>
                  <code className="block rounded bg-muted/50 p-2 font-mono text-xs break-words">
                    {run.command}
                  </code>
                </div>
              ) : null}

              {run.detail ? (
                <div>
                  <div className="mb-1 text-xs text-muted-foreground">
                    Counters, as the stage recorded them
                  </div>
                  <pre className="overflow-x-auto rounded bg-muted/50 p-2 font-mono text-xs whitespace-pre-wrap">
                    {prettyDetail(run.detail)}
                  </pre>
                </div>
              ) : null}
            </div>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

function Row({ label, value }: { label: string; value?: React.ReactNode }) {
  if (value === undefined || value === null || value === "") return null;
  return (
    <div className="flex gap-4 py-2">
      <dt className="w-28 shrink-0 text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  );
}

/**
 * The stage's counters are JSON text, because each stage counts different
 * things. Printed as it was recorded where it will not parse — a detail that
 * is not JSON is still worth reading.
 */
function prettyDetail(detail: string): string {
  try {
    return JSON.stringify(JSON.parse(detail), null, 2);
  } catch {
    return detail;
  }
}
