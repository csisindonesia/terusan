import { useQueries, useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { IconArrowLeft, IconChartArea, IconCopy } from "@tabler/icons-react";
import { useState } from "react";
import { z } from "zod";

import { ChartCard } from "~/components/chart-card";
import { PageHeader } from "~/components/page-header";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { BELOW_STICKY_HEADER, StickyHeader } from "~/components/sticky-header";
import {
  MAX_SERIES,
  TimeSeriesChart,
  type Series as Line,
} from "~/components/time-series-chart";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { api, type Indicator, type SeriesLine } from "~/lib/api";
import { formatCount, formatDate, formatRelative } from "~/lib/format";
import { indicatorLabel } from "~/lib/labels";

// A commodity is rarely in more than a handful of series; past this the list
// is a catalogue, and the observations page is the place to read it.
const MAX_LISTED = 40;

const searchSchema = z.object({
  series: z.string().optional(),
});

export const Route = createFileRoute("/commodities/$name")({
  validateSearch: searchSchema,
  component: CommodityPage,
});

/**
 * One commodity: what the registry knows of it, which series carry it, and
 * the figures of one of them drawn.
 *
 * Found by name because the name is the identity (see `Commodity`): most of
 * these have no registry identifier to put in a URL.
 */
function CommodityPage() {
  const { name } = Route.useParams();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [hidden, setHidden] = useState<string[]>([]);

  // The list endpoint answers a search by substring, so the exact name is
  // picked out of what it returns: "Beras" also finds "Beras Kualitas Medium".
  const summary = useQuery({
    queryKey: ["commodity", name],
    queryFn: () => api.commodities({ q: name, limit: 200 }),
  });
  const commodity = summary.data?.data?.find((row) => row.name === name);
  const ids = commodity?.indicator_ids ?? [];

  const series = useQueries({
    queries: ids.slice(0, MAX_LISTED).map((id) => ({
      queryKey: ["indicator", id],
      queryFn: () => api.indicator(id),
    })),
  });
  const indicators = series
    .map((query) => query.data?.data)
    .filter((row): row is Indicator => Boolean(row));

  const selectedId =
    search.series && ids.includes(search.series) ? search.series : ids[0];
  const selected = indicators.find((row) => row.indicator_id === selectedId);

  // The commodity filter matters even with one series chosen: a price table
  // carries every commodity it prices, and this page is about one of them.
  const chart = useQuery({
    queryKey: ["observations", "series", "commodity", name, selectedId],
    enabled: Boolean(selectedId),
    queryFn: () =>
      api.observationSeries({
        indicator: [selectedId!],
        commodity: [name],
        members: MAX_SERIES,
      }),
  });
  const drawn = chart.data?.data;
  const periods = periodsOf(drawn?.series ?? []);
  const lines = linesFrom(drawn?.series ?? [], periods, hidden, name);
  const heading = selected ? indicatorLabel(selected) : name;

  const facts = commodity
    ? [
        commodity.category,
        commodity.hs_code ? `HS ${commodity.hs_code}` : null,
        commodity.commodity_id ? null : "not yet in the registry",
      ].filter(Boolean)
    : [];

  return (
    <div className="space-y-5">
      <Link
        to="/commodities"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <IconArrowLeft className="size-4" />
        All commodities
      </Link>

      <StickyHeader
        heading={
          <PageHeader
            title={name}
            count={commodity?.observations}
            isLoading={summary.isLoading}
            description={
              commodity
                ? `${facts.length ? `${facts.join(" · ")}. ` : ""}Figures from ${commodity.sources.join(", ")}, ${commodity.period_start} to ${commodity.period_end}.`
                : undefined
            }
            actions={
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  render={
                    <Link
                      to="/observations"
                      search={{ commodity: [name] }}
                    />
                  }
                >
                  <IconChartArea className="size-4" />
                  View figures
                </Button>
                <RowActions
                  actions={[
                    {
                      label: "Copy name",
                      icon: IconCopy,
                      onSelect: () => void copyToClipboard(name),
                    },
                  ]}
                />
              </div>
            }
          />
        }
      />

      {summary.isError ? (
        <p className="rounded-lg bg-destructive/5 px-4 py-3 text-sm">
          {(summary.error as Error).message}
        </p>
      ) : null}
      {summary.isSuccess && !commodity ? (
        <p className="rounded-lg bg-muted/60 px-4 py-3 text-sm">
          No figures are published for a commodity named “{name}”.
        </p>
      ) : null}

      <div className="grid gap-8 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <aside className={`lg:sticky lg:self-start ${BELOW_STICKY_HEADER}`}>
          <h2 className="font-heading text-sm font-semibold tracking-tight">About</h2>
          <dl className="mt-3 space-y-3">
            <Fact
              label="Registry ID"
              value={commodity ? (commodity.commodity_id ?? "Unregistered") : undefined}
            />
            <Fact label="Category" value={commodity?.category} hint={commodity?.subcategory} />
            <Fact label="HS code" value={commodity?.hs_code} />
            <Fact
              label="Units"
              value={commodity?.units.length ? commodity.units.join(", ") : undefined}
            />
            <Fact
              label="Coverage"
              value={
                commodity
                  ? `${commodity.period_start} – ${commodity.period_end}`
                  : undefined
              }
            />
            <Fact
              label="Figures"
              value={commodity ? formatCount(commodity.observations) : undefined}
            />
            <Fact
              label="Series"
              value={commodity ? formatCount(commodity.indicators) : undefined}
            />
            <Fact
              label="Places"
              value={
                commodity
                  ? commodity.geographies
                    ? formatCount(commodity.geographies)
                    : "None — priced without a place"
                  : undefined
              }
            />
            <Fact
              label="Sources"
              value={commodity?.sources.length ? commodity.sources.join(", ") : undefined}
            />
            <Fact
              label="Last updated"
              value={commodity ? formatDate(commodity.last_updated) : undefined}
              hint={commodity ? formatRelative(commodity.last_updated) : undefined}
            />
          </dl>
        </aside>

        <div className="min-w-0 space-y-8">
          <ChartCard
            plain
            title={heading}
            span={
              periods.length
                ? `${periods[0]} to ${periods[periods.length - 1]}`
                : undefined
            }
            reason={
              drawn && drawn.members > lines.length
                ? `The ${formatCount(lines.length)} largest of ${formatCount(drawn.members)} lines.`
                : undefined
            }
            sources={selected?.publisher ? [selected.publisher] : (commodity?.sources ?? [])}
            link={
              selectedId ? (
                <Link
                  to="/indicators/$indicatorId"
                  params={{ indicatorId: selectedId }}
                  className="text-foreground underline underline-offset-2"
                >
                  The whole series
                </Link>
              ) : undefined
            }
            note={granularityNote(drawn?.granularity, chart.data?.meta?.total ?? 0)}
          >
            {chart.isLoading || summary.isLoading ? (
              <Skeleton className="h-[300px] w-full" />
            ) : lines.length ? (
              <TimeSeriesChart
                series={lines}
                unit={selected?.unit}
                framed={false}
                dashed
                onToggle={(member) =>
                  setHidden((current) =>
                    current.includes(member)
                      ? current.filter((entry) => entry !== member)
                      : [...current, member],
                  )
                }
              />
            ) : (
              <p className="py-12 text-center text-sm text-muted-foreground">
                Nothing to draw for this series.
              </p>
            )}
          </ChartCard>

          <section className="space-y-3">
            <h2 className="font-heading text-sm font-semibold tracking-tight">
              Series carrying {name}
            </h2>
            <ul className="divide-y">
              {ids.slice(0, MAX_LISTED).map((id, index) => {
                const row = series[index]?.data?.data;
                const active = id === selectedId;
                return (
                  <li key={id} className="flex items-center gap-3 py-2.5">
                    <button
                      type="button"
                      onClick={() => {
                        setHidden([]);
                        navigate({
                          search: (prev) => ({ ...prev, series: id }),
                          replace: true,
                        });
                      }}
                      className="min-w-0 flex-1 text-left"
                      aria-pressed={active}
                    >
                      <div className="flex items-center gap-2">
                        <span
                          className={`truncate text-sm ${active ? "font-semibold" : "font-medium"}`}
                        >
                          {row ? indicatorLabel(row) : id}
                        </span>
                        {active ? <Badge variant="secondary">Charted</Badge> : null}
                      </div>
                      <div className="truncate text-xs text-muted-foreground">
                        {row
                          ? [
                              row.publisher ?? row.sources.join(", "),
                              row.temporal_resolution,
                              row.unit,
                              `${row.period_start} – ${row.period_end}`,
                            ]
                              .filter(Boolean)
                              .join(" · ")
                          : "Loading…"}
                      </div>
                    </button>
                    <Link
                      to="/indicators/$indicatorId"
                      params={{ indicatorId: id }}
                      className="shrink-0 text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                    >
                      Open
                    </Link>
                  </li>
                );
              })}
            </ul>
            {ids.length > MAX_LISTED ? (
              <p className="text-sm text-muted-foreground">
                {formatCount(ids.length - MAX_LISTED)} more — every figure is on the{" "}
                <Link
                  to="/observations"
                  search={{ commodity: [name] }}
                  className="text-foreground underline underline-offset-2"
                >
                  observations page
                </Link>
                .
              </p>
            ) : null}
          </section>
        </div>
      </div>
    </div>
  );
}

/** Every bucket any line has a point for, so the lines share one axis. */
function periodsOf(series: SeriesLine[]): string[] {
  return [...new Set(series.flatMap((line) => line.points.map((p) => p.period)))].sort();
}

function linesFrom(
  series: SeriesLine[],
  periods: string[],
  hidden: string[],
  fallbackName: string,
): Line[] {
  return series.map((line) => {
    const byPeriod = new Map(line.points.map((point) => [point.period, point]));
    return {
      name: line.member || fallbackName,
      hidden: hidden.includes(line.member || fallbackName),
      points: periods.map((period) => {
        const point = byPeriod.get(period);
        return {
          label: period,
          value: point?.value == null ? null : Number(point.value),
          status:
            point && point.count > 1
              ? `mean of ${formatCount(point.count - point.missing)} figures`
              : undefined,
        };
      }),
    };
  });
}

function granularityNote(granularity: string | undefined, rows: number): string | undefined {
  const bucket = { month: "Monthly", quarter: "Quarterly", year: "Annual" }[
    granularity ?? ""
  ];
  if (!bucket) return undefined;
  return `${bucket} means of ${formatCount(rows)} figures — the series is longer than a chart can draw point by point.`;
}

function Fact({ label, value, hint }: { label: string; value?: string; hint?: string }) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm">
        {value ?? <span className="text-muted-foreground">—</span>}
        {hint ? <span className="ml-1.5 text-xs text-muted-foreground">{hint}</span> : null}
      </dd>
    </div>
  );
}
