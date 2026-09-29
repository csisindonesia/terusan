import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";

import { DataTable, StackedCell } from "~/components/data-table";
import { Section } from "~/components/section";
import { Badge } from "~/components/ui/badge";
import { api, type PipelineRun } from "~/lib/api";
import {
  formatCount,
  formatDuration,
  formatMoment,
  formatRelative,
} from "~/lib/format";
import { itemsOfKind, type Collection } from "~/lib/workspace";

/** Runs read per series. Enough to see a pattern, not a log viewer. */
const RUNS_PER_SERIES = 20;

/**
 * How many series the history is read for. One request each, and past this
 * the page would be issuing a request storm to answer "is anything broken".
 */
const MAX_SERIES = 50;

/** A run, and which of this collection's series it fed. */
type Run = PipelineRun & { series: string[] };

/**
 * What the pipelines behind this collection's series have been doing.
 *
 * The same question an indicator's Logs tab answers — is anything still
 * fetching these figures — asked of every series at once. One ingestion often
 * feeds several series here, so a run is listed once with the series it fed
 * rather than once per series, which would read as the source running eight
 * times.
 */
export function CollectionLogs({ collection }: { collection: Collection }) {
  const items = itemsOfKind(collection, "indicator");
  const read = items.slice(0, MAX_SERIES);
  const labels = new Map(items.map((item) => [item.id, item.label]));

  const runs = useQuery({
    queryKey: ["collection-runs", read.map((item) => item.id)],
    enabled: read.length > 0,
    queryFn: async () => {
      const pages = await Promise.all(
        read.map((item) =>
          api
            .indicatorRuns(item.id, { limit: RUNS_PER_SERIES })
            .then((page) => ({ id: item.id, runs: page.data })),
        ),
      );
      const byId = new Map<string, Run>();
      for (const page of pages) {
        for (const run of page.runs) {
          const held = byId.get(run.run_id);
          if (held) held.series.push(page.id);
          else byId.set(run.run_id, { ...run, series: [page.id] });
        }
      }
      return [...byId.values()].sort((a, b) =>
        b.started_at.localeCompare(a.started_at),
      );
    },
  });

  if (!items.length) {
    return (
      <p className="max-w-3xl text-sm text-muted-foreground">
        No series are filed here, so there is no pipeline to report on.
      </p>
    );
  }

  if (runs.isError) {
    return (
      <p className="max-w-3xl text-sm text-muted-foreground">
        The run history could not be read: {(runs.error as Error).message}
      </p>
    );
  }

  const rows = runs.data ?? [];
  const failures = rows.filter((run) => run.status === "failed");
  // The series whose most recent run failed: the ones worth opening first.
  const lastFailed = new Set<string>();
  const seen = new Set<string>();
  for (const run of rows) {
    for (const id of run.series) {
      if (seen.has(id)) continue;
      seen.add(id);
      if (run.status === "failed") lastFailed.add(id);
    }
  }
  const silent = read.filter((item) => !seen.has(item.id));

  const columns: ColumnDef<Run>[] = [
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
      id: "series",
      header: "Series",
      enableSorting: false,
      cell: ({ row }) => {
        const [first, ...rest] = row.original.series;
        return (
          <StackedCell
            primary={(first && labels.get(first)) ?? first ?? "—"}
            secondary={
              rest.length ? `+${rest.length} more in this collection` : undefined
            }
          />
        );
      },
    },
    {
      accessorKey: "status",
      header: "Status",
      meta: { width: "w-32" },
      cell: ({ row }) => {
        const run = row.original;
        // A dry run lands nothing on purpose, so its zero records must not
        // read as an ingestion that has stopped working.
        if (run.dry_run) return <Badge variant="outline">Dry run</Badge>;
        return run.status === "failed" ? (
          <Badge variant="destructive">Failed</Badge>
        ) : (
          <Badge variant="secondary">Succeeded</Badge>
        );
      },
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
  ];

  return (
    <div className="space-y-8">
      <Section
        title="Runs"
        description={
          <>
            Every ingestion and normalization behind the series in this collection,
            newest first, up to {RUNS_PER_SERIES} per series.
            {items.length > MAX_SERIES
              ? ` Read for the first ${MAX_SERIES} of its ${formatCount(items.length)} series; each series' own page has the rest.`
              : ""}{" "}
            Extraction reads the whole of RAW at once and belongs to no single series,
            so it is not listed here.
          </>
        }
      >
        <div className="grid gap-3 sm:grid-cols-3">
          <Fact
            label="Last run"
            value={rows[0] ? formatMoment(rows[0].started_at) : "—"}
            detail={rows[0] ? formatRelative(rows[0].started_at) : undefined}
            alarming={rows[0]?.status === "failed"}
          />
          <Fact
            label="Series whose last run failed"
            value={`${lastFailed.size} of ${read.length}`}
            detail={
              lastFailed.size
                ? [...lastFailed].map((id) => labels.get(id) ?? id).join(", ")
                : "None — every series' latest run succeeded."
            }
            alarming={lastFailed.size > 0}
          />
          <Fact
            label="No runs recorded"
            value={`${silent.length} of ${read.length}`}
            detail={
              silent.length
                ? "Nothing in the journal names these series or their source — run somewhere that does not write here, or not since runs were recorded."
                : "Every series has a recorded run."
            }
          />
        </div>

        <DataTable
          columns={columns}
          data={rows}
          isLoading={runs.isLoading}
          getRowId={(run) => run.run_id}
          emptyMessage="No runs recorded for any series in this collection."
        />
      </Section>

      {failures.length ? (
        <Section
          title="What failed"
          description="The message each failed run recorded. A scraper that broke on a layout change says so here rather than in a log file nobody opens."
        >
          <div className="space-y-3">
            {failures.map((run) => (
              <div
                key={run.run_id}
                className="rounded-lg border border-destructive/40 p-3"
              >
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-mono text-xs">{run.pipeline}</span>
                  <span className="text-muted-foreground">
                    {formatMoment(run.started_at)}
                  </span>
                  {run.trigger ? <Badge variant="outline">{run.trigger}</Badge> : null}
                </div>
                <p className="mt-2 font-mono text-xs break-words text-destructive">
                  {run.error_message ?? "No message was recorded."}
                </p>
              </div>
            ))}
          </div>
        </Section>
      ) : null}
    </div>
  );
}

/** One number about the run history, and what it means. */
function Fact({
  label,
  value,
  detail,
  alarming,
}: {
  label: string;
  value: string;
  detail?: string;
  alarming?: boolean;
}) {
  return (
    <div className="rounded-lg border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={`mt-1 text-sm font-medium ${alarming ? "text-destructive" : ""}`}>
        {value}
      </p>
      {detail ? <p className="mt-1 text-xs text-muted-foreground">{detail}</p> : null}
    </div>
  );
}
