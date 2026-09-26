import { Link } from "@tanstack/react-router";
import { useId, useState } from "react";

import { ColumnChart } from "~/components/column-chart";
import { TimeSeriesChart, type Series } from "~/components/time-series-chart";
import type { ChartSpec } from "~/lib/assistant";
import { formatCompact, formatDecimal } from "~/lib/format";
import { cn } from "~/lib/utils";
import { GRID, SERIES_DARK_SLOTS, SERIES_LIGHT_SLOTS, seriesColor } from "~/lib/viz";

/**
 * The chart the assistant drew beside a reply.
 *
 * The server chose the kind and read the figures; this only draws them. Line
 * and rebased charts are the portal's own time series, a bar is its column
 * chart, and the two kinds nothing else on the site needs — two series on two
 * axes, and one plotted against the other — are drawn here in the same
 * palette, grid and type, so a chart in a reply reads like one on a series'
 * own page.
 *
 * Every series is named under the chart with a link to its page, and the whole
 * selection opens in the Data Explorer: a chart in a conversation is where a
 * reader starts, not where the figures end.
 */
export function AssistantChart({
  chart,
  className,
}: {
  chart: ChartSpec;
  className?: string;
}) {
  const units = new Set(chart.series.map((s) => s.unit ?? ""));
  const sharedUnit = units.size === 1 ? chart.series[0]?.unit : undefined;

  return (
    <figure
      className={cn("space-y-3", SERIES_DARK_SLOTS, className)}
      style={{ ...SERIES_LIGHT_SLOTS, "--viz-grid": GRID } as React.CSSProperties}
    >
      <header className="space-y-0.5">
        <p className="text-sm font-medium">{chart.title}</p>
        {chart.reason ? (
          <p className="text-xs text-muted-foreground">{chart.reason}</p>
        ) : null}
      </header>

      <ChartBody chart={chart} unit={sharedUnit} />

      <footer className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {chart.series.map((series, slot) => (
          <span key={series.id} className="flex min-w-0 items-center gap-1.5">
            {/* A scatter has one colour of dot and no line per series. */}
            {chart.series.length > 1 && chart.kind !== "scatter" ? (
              <span
                aria-hidden
                className="h-0.5 w-4 shrink-0 rounded-full"
                style={{ background: seriesColor(slot) }}
              />
            ) : null}
            <Link
              to="/indicators/$indicatorId"
              params={{ indicatorId: series.id }}
              className="truncate underline-offset-2 hover:text-foreground hover:underline"
            >
              {series.label}
            </Link>
            {series.member || series.unit ? (
              <span className="shrink-0">
                ({[series.member, series.unit].filter(Boolean).join("; ")})
              </span>
            ) : null}
          </span>
        ))}
        {chart.correlation !== undefined ? (
          <span
            className="tabular-nums"
            title="Pearson correlation over the periods both have"
          >
            r = {chart.correlation.toFixed(2)} · {chart.overlap} periods
          </span>
        ) : null}
        <Link
          to="/observations"
          search={{ indicator: chart.series.map((s) => s.id) }}
          className="ml-auto font-medium text-foreground underline-offset-2 hover:underline"
        >
          Open in Data Explorer
        </Link>
      </footer>
    </figure>
  );
}

function seriesName(series: ChartSpec["series"][number]): string {
  return series.member && series.member !== "Indonesia"
    ? `${series.label} — ${series.member}`
    : series.label;
}

function ChartBody({ chart, unit }: { chart: ChartSpec; unit?: string }) {
  switch (chart.kind) {
    case "dual_axis":
      if (chart.series.length === 2) return <DualAxisChart chart={chart} />;
      break;
    case "scatter":
      if (chart.series.length === 2) return <ScatterChart chart={chart} />;
      break;
    case "indexed":
      return <IndexedChart chart={chart} />;
    case "bar": {
      const series = chart.series[0]!;
      const columns = chart.periods
        .map((period, index) => ({
          label: period,
          value: series.values[index] ?? null,
        }))
        .filter(
          (column): column is { label: string; value: number } => column.value !== null,
        );
      // Columns grow from zero; a negative figure needs a line.
      if (columns.every((column) => column.value >= 0)) {
        return (
          <ColumnChart
            columns={columns}
            unit={series.unit}
            format={(value) => formatDecimal(String(round(value)))}
          />
        );
      }
      break;
    }
  }
  return <TimeSeriesChart series={toSeries(chart)} unit={unit} />;
}

function toSeries(
  chart: ChartSpec,
  values = chart.series.map((s) => s.values),
): Series[] {
  return chart.series.map((series, index) => ({
    name: seriesName(series),
    points: chart.periods.map((label, at) => ({
      label,
      value: values[index]![at] ?? null,
    })),
  }));
}

/**
 * Lines in different units, each rebased to 100 at the first period where
 * every one has a figure — or at its own first, where they never share one.
 */
function IndexedChart({ chart }: { chart: ChartSpec }) {
  const common = chart.periods.findIndex((_, at) =>
    chart.series.every((series) => series.values[at] != null),
  );
  const rebased = chart.series.map((series) => {
    const baseAt = common >= 0 ? common : series.values.findIndex((v) => v != null);
    const base = series.values[baseAt];
    return series.values.map((value) =>
      value == null || !base ? null : round((value / base) * 100),
    );
  });
  const baseLabel = common >= 0 ? chart.periods[common] : "each series' first period";
  return (
    <TimeSeriesChart series={toSeries(chart, rebased)} unit={`(${baseLabel} = 100)`} />
  );
}

// ---- shared SVG furniture --------------------------------------------------

// Narrower than a series page's 960: a reply column is around 700px wide, and
// at 960 the 11px labels would be drawn at 8.
const WIDTH = 720;
const HEIGHT = 300;

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

/** An axis padded around its figures, and the four gridlines along it. */
function scaleOf(values: number[]) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || Math.abs(max) || 1;
  const padded = min - span * 0.08;
  const low = min >= 0 ? Math.max(0, padded) : padded;
  const high = max + span * 0.08;
  return {
    low,
    high,
    ticks: [0, 1, 2, 3, 4].map((step) => low + ((high - low) * step) / 4),
  };
}

function present(values: (number | null)[]): number[] {
  return values.filter((value): value is number => value != null);
}

function tickIndices(count: number): number[] {
  if (count <= 2) return count === 1 ? [0] : [0, 1];
  const step = Math.ceil(count / 8);
  const indices = [0];
  for (let index = step; index <= count - 1 - step; index += step) indices.push(index);
  indices.push(count - 1);
  return indices;
}

function Frame({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div
      className="viz-root relative overflow-hidden rounded-lg border bg-card"
      role="group"
      aria-label={label}
    >
      {children}
    </div>
  );
}

function Tooltip({
  x,
  flip,
  children,
}: {
  x: number;
  flip: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className="pointer-events-none absolute top-3 rounded-lg border bg-popover px-2.5 py-1.5 text-xs shadow-sm"
      style={{
        left: `calc(${(x / WIDTH) * 100}% + 8px)`,
        transform: flip ? "translateX(-110%)" : undefined,
      }}
    >
      {children}
    </div>
  );
}

// ---- two units, two axes ---------------------------------------------------

/**
 * Two series in different units over the same periods, each against its own
 * axis. The axis labels take the colour of their line, so which scale a line
 * is read off is never a guess — and the tooltip gives both figures in their
 * own units, which is what a reader comparing them wants.
 */
function DualAxisChart({ chart }: { chart: ChartSpec }) {
  const clipId = useId();
  const [hover, setHover] = useState<number | null>(null);
  const [left, right] = chart.series as [
    ChartSpec["series"][0],
    ChartSpec["series"][0],
  ];
  const padding = { top: 28, right: 64, bottom: 28, left: 64 };
  const plotWidth = WIDTH - padding.left - padding.right;
  const plotHeight = HEIGHT - padding.top - padding.bottom;
  const count = chart.periods.length;

  const scales = [scaleOf(present(left.values)), scaleOf(present(right.values))];
  const x = (index: number) =>
    padding.left + (count === 1 ? plotWidth / 2 : (index / (count - 1)) * plotWidth);
  const y = (axis: number, value: number) => {
    const { low, high } = scales[axis]!;
    return padding.top + plotHeight - ((value - low) / (high - low)) * plotHeight;
  };
  const paths = (axis: number, values: (number | null)[]) => {
    const runs: string[] = [];
    let run = "";
    values.forEach((value, index) => {
      if (value == null) {
        if (run) runs.push(run);
        run = "";
      } else {
        run += `${run ? "L" : "M"} ${x(index)} ${y(axis, value)} `;
      }
    });
    if (run) runs.push(run);
    return runs;
  };

  return (
    <Frame label={chart.title}>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-[300px] w-full"
        role="img"
        aria-label={`${left.label} and ${right.label}`}
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <clipPath id={clipId}>
            <rect
              x={padding.left}
              y={padding.top}
              width={plotWidth}
              height={plotHeight}
            />
          </clipPath>
        </defs>

        {/* One set of gridlines, from the left axis: two would cross. */}
        {scales[0]!.ticks.map((_, step) => {
          const at = padding.top + plotHeight - (plotHeight * step) / 4;
          return (
            <g key={step}>
              <line
                x1={padding.left}
                x2={WIDTH - padding.right}
                y1={at}
                y2={at}
                stroke="var(--viz-grid)"
              />
              {[0, 1].map((axis) => (
                <text
                  key={axis}
                  x={axis === 0 ? padding.left - 10 : WIDTH - padding.right + 10}
                  y={at}
                  textAnchor={axis === 0 ? "end" : "start"}
                  dominantBaseline="middle"
                  className="text-[11px]"
                  fill={seriesColor(axis)}
                >
                  {formatCompact(String(scales[axis]!.ticks[step]))}
                </text>
              ))}
            </g>
          );
        })}

        {/* Each axis says what it measures, above its labels. */}
        {[left, right].map((series, axis) => (
          <text
            key={series.id}
            x={axis === 0 ? padding.left - 10 : WIDTH - padding.right + 10}
            y={12}
            textAnchor={axis === 0 ? "end" : "start"}
            className="text-[11px] font-medium"
            fill={seriesColor(axis)}
          >
            {series.unit || (axis === 0 ? "left" : "right")}
          </text>
        ))}

        <g clipPath={`url(#${clipId})`}>
          {[left, right].map((series, axis) =>
            paths(axis, series.values).map((d, run) => (
              <path
                key={`${series.id}-${run}`}
                d={d}
                fill="none"
                stroke={seriesColor(axis)}
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            )),
          )}
        </g>

        {tickIndices(count).map((index) => (
          <text
            key={`tick-${chart.periods[index]}`}
            x={x(index)}
            y={HEIGHT - 8}
            textAnchor={index === 0 ? "start" : index === count - 1 ? "end" : "middle"}
            className="fill-muted-foreground text-[11px]"
          >
            {chart.periods[index]}
          </text>
        ))}

        {hover !== null ? (
          <g>
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1={padding.top}
              y2={padding.top + plotHeight}
              stroke="var(--viz-grid)"
            />
            {[left, right].map((series, axis) => {
              const value = series.values[hover];
              return value == null ? null : (
                <circle
                  key={series.id}
                  cx={x(hover)}
                  cy={y(axis, value)}
                  r={5}
                  fill={seriesColor(axis)}
                  stroke="var(--color-card, #fff)"
                  strokeWidth={2}
                />
              );
            })}
          </g>
        ) : null}

        {chart.periods.map((period, index) => (
          <rect
            key={`hit-${period}`}
            x={x(index) - plotWidth / count / 2}
            y={padding.top}
            width={plotWidth / count}
            height={plotHeight}
            fill="transparent"
            onMouseEnter={() => setHover(index)}
          />
        ))}
      </svg>

      {hover !== null ? (
        <Tooltip x={x(hover)} flip={hover > count / 2}>
          <div className="font-medium">{chart.periods[hover]}</div>
          {[left, right].map((series, axis) => {
            const value = series.values[hover];
            return (
              <div key={series.id} className="flex items-center gap-1.5 tabular-nums">
                <span
                  aria-hidden
                  className="size-2 shrink-0 rounded-full"
                  style={{ background: seriesColor(axis) }}
                />
                <span className="max-w-56 truncate text-muted-foreground">
                  {seriesName(series)}
                </span>
                <span className="ml-auto pl-2">
                  {value == null
                    ? "no value"
                    : `${formatDecimal(String(round(value)))}${series.unit ? ` ${series.unit}` : ""}`}
                </span>
              </div>
            );
          })}
        </Tooltip>
      ) : null}
    </Frame>
  );
}

// ---- one against the other -------------------------------------------------

/**
 * One dot per period, the first series along the bottom and the second up the
 * side, with the least-squares line through them. Older periods are fainter,
 * so the drift of the cloud over time is there to be seen without a legend.
 */
function ScatterChart({ chart }: { chart: ChartSpec }) {
  const [hover, setHover] = useState<number | null>(null);
  const [across, up] = chart.series as [ChartSpec["series"][0], ChartSpec["series"][0]];
  const padding = { top: 28, right: 20, bottom: 44, left: 64 };
  const plotWidth = WIDTH - padding.left - padding.right;
  const plotHeight = HEIGHT - padding.top - padding.bottom;

  const dots = chart.periods
    .map((period, index) => ({
      period,
      index,
      a: across.values[index] ?? null,
      b: up.values[index] ?? null,
    }))
    .filter(
      (dot): dot is { period: string; index: number; a: number; b: number } =>
        dot.a !== null && dot.b !== null,
    );

  if (dots.length < 2) {
    return (
      <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
        The two series share too few periods to plot against each other.
      </p>
    );
  }

  const sx = scaleOf(dots.map((d) => d.a));
  const sy = scaleOf(dots.map((d) => d.b));
  const x = (value: number) =>
    padding.left + ((value - sx.low) / (sx.high - sx.low)) * plotWidth;
  const y = (value: number) =>
    padding.top + plotHeight - ((value - sy.low) / (sy.high - sy.low)) * plotHeight;

  // Least squares, drawn across the span of the dots only.
  const n = dots.length;
  const meanA = dots.reduce((sum, d) => sum + d.a, 0) / n;
  const meanB = dots.reduce((sum, d) => sum + d.b, 0) / n;
  const sxx = dots.reduce((sum, d) => sum + (d.a - meanA) ** 2, 0);
  const slope = sxx
    ? dots.reduce((sum, d) => sum + (d.a - meanA) * (d.b - meanB), 0) / sxx
    : 0;
  const lowA = Math.min(...dots.map((d) => d.a));
  const highA = Math.max(...dots.map((d) => d.a));
  const fit = (a: number) => meanB + slope * (a - meanA);

  const shown = hover !== null ? dots[hover] : undefined;

  return (
    <Frame label={chart.title}>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-[300px] w-full"
        role="img"
        aria-label={`${up.label} against ${across.label}`}
        onMouseLeave={() => setHover(null)}
      >
        {sy.ticks.map((value) => (
          <g key={`y-${value}`}>
            <line
              x1={padding.left}
              x2={WIDTH - padding.right}
              y1={y(value)}
              y2={y(value)}
              stroke="var(--viz-grid)"
            />
            <text
              x={padding.left - 10}
              y={y(value)}
              textAnchor="end"
              dominantBaseline="middle"
              className="fill-muted-foreground text-[11px]"
            >
              {formatCompact(String(value))}
            </text>
          </g>
        ))}
        {sx.ticks.map((value, step) => (
          <text
            key={`x-${value}`}
            x={x(value)}
            y={padding.top + plotHeight + 16}
            textAnchor={step === 0 ? "start" : step === 4 ? "end" : "middle"}
            className="fill-muted-foreground text-[11px]"
          >
            {formatCompact(String(value))}
          </text>
        ))}

        {/* What each axis measures. */}
        <text
          x={WIDTH - padding.right}
          y={HEIGHT - 6}
          textAnchor="end"
          className="fill-muted-foreground text-[11px] font-medium"
        >
          → {across.label}
          {across.unit ? ` (${across.unit})` : ""}
        </text>
        <text
          x={padding.left}
          y={14}
          className="fill-muted-foreground text-[11px] font-medium"
        >
          ↑ {up.label}
          {up.unit ? ` (${up.unit})` : ""}
        </text>

        <line
          x1={x(lowA)}
          x2={x(highA)}
          y1={y(fit(lowA))}
          y2={y(fit(highA))}
          stroke={seriesColor(1)}
          strokeWidth={1.5}
          strokeDasharray="6 4"
        />

        {dots.map((dot, order) => (
          <circle
            key={dot.period}
            cx={x(dot.a)}
            cy={y(dot.b)}
            r={hover === order ? 6 : 4}
            fill={seriesColor(0)}
            fillOpacity={0.25 + 0.65 * (n === 1 ? 1 : order / (n - 1))}
            stroke={hover === order ? "var(--color-card, #fff)" : "none"}
            strokeWidth={2}
            onMouseEnter={() => setHover(order)}
          />
        ))}
      </svg>

      {shown ? (
        <Tooltip x={x(shown.a)} flip={x(shown.a) > WIDTH / 2}>
          <div className="font-medium">{shown.period}</div>
          {[
            { series: across, value: shown.a },
            { series: up, value: shown.b },
          ].map(({ series, value }) => (
            <div key={series.id} className="flex gap-1.5 tabular-nums">
              <span className="max-w-56 truncate text-muted-foreground">
                {seriesName(series)}
              </span>
              <span className="ml-auto pl-2">
                {formatDecimal(String(round(value)))}
                {series.unit ? ` ${series.unit}` : ""}
              </span>
            </div>
          ))}
        </Tooltip>
      ) : null}
    </Frame>
  );
}
