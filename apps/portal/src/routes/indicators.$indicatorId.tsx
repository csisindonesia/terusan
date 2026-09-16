import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconArrowLeft, IconDownload, IconExternalLink } from "@tabler/icons-react";

import { DataTable, StackedCell } from "~/components/data-table";
import { TimeSeriesChart } from "~/components/time-series-chart";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent } from "~/components/ui/card";
import { Skeleton } from "~/components/ui/skeleton";
import { summarise, gapsByStatus, type Figure } from "~/lib/analytics";
import { api, type Observation } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import {
  formatCount,
  formatDate,
  formatDecimal,
  formatPercent,
  formatRelative,
  statusLabel,
} from "~/lib/format";

export const Route = createFileRoute("/indicators/$indicatorId")({
  component: IndicatorDetail,
});

/** Enough to hold a long annual series in one request; paging a chart is worse. */
const MAX_FIGURES = 5000;

const columns: ColumnDef<Observation>[] = [
  {
    accessorKey: "period",
    header: "Period",
    cell: ({ row }) => (
      <StackedCell
        primary={row.original.period}
        secondary={`${row.original.period_start} – ${row.original.period_end}`}
      />
    ),
  },
  {
    accessorKey: "geo_name",
    header: "Place",
    cell: ({ row }) => (
      <StackedCell
        primary={row.original.geo_name ?? "—"}
        secondary={row.original.geo_id ?? "unresolved"}
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
        return <span className="text-muted-foreground">{statusLabel(status)}</span>;
      }
      return (
        <StackedCell
          primary={
            <span className="inline-flex items-center justify-end gap-1.5">
              {!value_unambiguous ? (
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
];

const EXPORT_COLUMNS = [
  { key: "period" as const, header: "period" },
  { key: "period_start" as const, header: "period_start" },
  { key: "period_end" as const, header: "period_end" },
  { key: "geo_id" as const, header: "geo_id" },
  { key: "geo_name" as const, header: "geo_name" },
  { key: "value" as const, header: "value" },
  { key: "unit" as const, header: "unit" },
  { key: "status" as const, header: "status" },
  { key: "source_id" as const, header: "source_id" },
  { key: "source_url" as const, header: "source_url" },
];

function IndicatorDetail() {
  const { indicatorId } = Route.useParams();

  const indicator = useQuery({
    queryKey: ["indicator", indicatorId],
    queryFn: () => api.indicator(indicatorId),
  });

  const observations = useQuery({
    queryKey: ["observations", "series", indicatorId],
    queryFn: () =>
      api.observations({
        indicator: [indicatorId],
        order: "period",
        limit: MAX_FIGURES,
      }),
  });

  const meta = indicator.data?.data;
  const rows = observations.data?.data ?? [];

  const figures: Figure[] = rows.map((row) => ({
    period: row.period,
    value: row.value === null ? null : Number(row.value),
    status: row.status,
  }));
  const stats = summarise(figures);
  const gaps = gapsByStatus(figures);

  if (indicator.isError) {
    return (
      <div className="space-y-4">
        <BackLink />
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
          {(indicator.error as Error).message}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <BackLink />

      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="font-heading text-2xl font-semibold tracking-tight">
            {indicatorId}
          </h1>
          {meta ? <Badge variant="secondary">{meta.temporal_resolution}</Badge> : null}
          {meta?.unit ? <Badge variant="outline">{meta.unit}</Badge> : null}
        </div>
        <p className="max-w-3xl text-sm text-muted-foreground">
          {meta ? (
            <>
              {formatCount(meta.observations)} figures covering {meta.period_start} to{" "}
              {meta.period_end}, across {formatCount(meta.geographies)} place
              {meta.geographies === 1 ? "" : "s"}, from{" "}
              {meta.sources.join(", ") || "an unrecorded source"}. Every figure keeps
              the document it came from, so a number here can be traced back to what
              published it.
            </>
          ) : (
            <Skeleton className="h-4 w-96" />
          )}
        </p>
      </div>

      <Section title="Metadata">
        <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
          <Fact label="Frequency" value={meta?.temporal_resolution} />
          <Fact label="Unit" value={meta?.unit} />
          <Fact
            label="Coverage"
            value={meta ? `${meta.period_start} – ${meta.period_end}` : undefined}
          />
          <Fact
            label="Places"
            value={meta ? formatCount(meta.geographies) : undefined}
          />
          <Fact
            label="Figures"
            value={meta ? formatCount(meta.observations) : undefined}
          />
          <Fact label="Sources" value={meta?.sources.join(", ")} />
          <Fact
            label="Last updated"
            value={meta ? formatDate(meta.last_updated) : undefined}
            hint={meta ? formatRelative(meta.last_updated) : undefined}
          />
          <Fact label="Layer" value="silver" hint="Normalized, typed observations" />
          <Fact
            label="Access"
            value="Internal"
            hint="Widening access is a decision, not a default"
          />
        </dl>
      </Section>

      <Section title="Analytics">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Latest"
            value={stats.latest ? formatDecimal(String(stats.latest.value)) : undefined}
            hint={stats.latest?.period}
            loading={observations.isLoading}
          />
          <Stat
            label="Change over the series"
            value={formatPercent(stats.totalChange)}
            hint={
              stats.first && stats.latest
                ? `${stats.first.period} → ${stats.latest.period}`
                : undefined
            }
            loading={observations.isLoading}
          />
          <Stat
            label="Compound annual growth"
            value={formatPercent(stats.cagr, 2)}
            // Meaningless where the base is zero or negative, and left unstated
            // rather than rendered as a number nobody can act on.
            hint={stats.cagr === null ? "not meaningful for this series" : "per year"}
            loading={observations.isLoading}
          />
          <Stat
            label="Recorded"
            value={`${formatCount(stats.present)} of ${formatCount(stats.count)}`}
            hint={
              gaps.length
                ? gaps
                    .map(
                      (gap) => `${gap.count} ${statusLabel(gap.status).toLowerCase()}`,
                    )
                    .join(", ")
                : "no gaps"
            }
            loading={observations.isLoading}
          />
          <Stat
            label="Highest"
            value={stats.max ? formatDecimal(String(stats.max.value)) : undefined}
            hint={stats.max?.period}
            loading={observations.isLoading}
          />
          <Stat
            label="Lowest"
            value={stats.min ? formatDecimal(String(stats.min.value)) : undefined}
            hint={stats.min?.period}
            loading={observations.isLoading}
          />
        </div>
      </Section>

      <Section title="Over time">
        {observations.isLoading ? (
          <Skeleton className="h-[300px] w-full rounded-lg" />
        ) : (
          <TimeSeriesChart
            points={figures.map((figure) => ({
              label: figure.period,
              value: figure.value,
              status: statusLabel(figure.status),
            }))}
            unit={meta?.unit}
            caption={
              gaps.length
                ? `The line breaks where a figure is absent — joining across a gap would assert a value nobody recorded. ${formatCount(stats.missing)} of ${formatCount(stats.count)} periods have none.`
                : undefined
            }
          />
        )}
      </Section>

      <Section
        title="The figures"
        action={
          <Button
            variant="outline"
            size="sm"
            disabled={!rows.length}
            onClick={() =>
              downloadCsv(
                `${indicatorId}.csv`,
                toCsv(rows as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
              )
            }
          >
            <IconDownload className="size-4" />
            Export series
          </Button>
        }
      >
        <DataTable
          columns={columns}
          data={rows}
          isLoading={observations.isLoading}
          loadingRows={8}
          emptyMessage="This indicator has no figures."
        />
      </Section>

      {rows[0]?.source_url ? (
        <Card>
          <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4 text-sm">
            <span className="text-muted-foreground">
              Every figure records where it was published, so a number lifted from here
              can be checked against the original.
            </span>
            <Button
              variant="outline"
              size="sm"
              render={
                <a
                  href={rows[0].source_url}
                  target="_blank"
                  rel="noreferrer noopener"
                />
              }
            >
              <IconExternalLink className="size-4" />
              Open the source
            </Button>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/indicators"
      className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
    >
      <IconArrowLeft className="size-4" />
      All indicators
    </Link>
  );
}

function Section({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-heading text-lg font-semibold tracking-tight">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function Fact({
  label,
  value,
  hint,
}: {
  label: string;
  value?: string;
  hint?: string;
}) {
  return (
    <div className="border-b pb-2">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm">
        {value ?? <span className="text-muted-foreground">—</span>}
        {hint ? (
          <span className="ml-1.5 text-xs text-muted-foreground">{hint}</span>
        ) : null}
      </dd>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
  loading,
}: {
  label: string;
  value?: string;
  hint?: string;
  loading?: boolean;
}) {
  return (
    <Card>
      <CardContent className="space-y-1 py-4">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        {loading ? (
          <Skeleton className="h-7 w-24" />
        ) : (
          <p className="font-heading text-xl font-semibold tabular-nums">
            {value ?? "—"}
          </p>
        )}
        {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      </CardContent>
    </Card>
  );
}
