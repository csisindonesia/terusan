import { Slider } from "@base-ui/react/slider";
import {
  IconChartBar,
  IconChartDots,
  IconChartLine,
  IconDownload,
  IconMaximize,
  IconMinimize,
  IconPlayerPauseFilled,
  IconPlayerPlayFilled,
  IconShare,
  IconTable,
} from "@tabler/icons-react";
import { Link } from "@tanstack/react-router";
import { toJpeg, toPng } from "html-to-image";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import logoUrl from "~/assets/logo.png";
import { Button } from "~/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";

import { ColumnChart } from "~/components/column-chart";
import {
  Annotations,
  TimeSeriesChart,
  type Series,
} from "~/components/time-series-chart";
import type { ChartSpec, ChartStory } from "~/lib/assistant";
import { formatCompact, formatDecimal } from "~/lib/format";
import { cn } from "~/lib/utils";
import { GRID, SERIES_DARK_SLOTS, SERIES_LIGHT_SLOTS, seriesColor } from "~/lib/viz";

/**
 * The chart the assistant drew beside a reply, as a card that stands on its
 * own: a title with its span, the chart or its table, a slider to narrow the
 * span, and a footer that says where the figures come from.
 *
 * The server chose the kind and read the figures; this only draws them. Line
 * and rebased charts are the portal's own time series, a bar is its column
 * chart, and the two kinds nothing else on the site needs — two series on two
 * axes, and one plotted against the other — are drawn here in the same
 * palette, grid and type.
 *
 * The card is what a reader takes away, so it downloads as PNG or JPG exactly
 * as shown — title, chart, legend, source and link — without the controls,
 * which mean nothing on paper.
 */
export function AssistantChart({
  chart,
  className,
}: {
  chart: ChartSpec;
  className?: string;
}) {
  const cardRef = useRef<HTMLElement>(null);
  const last = chart.periods.length - 1;
  const [range, setRange] = useState<[number, number]>([0, last]);
  const [view, setView] = useState<"chart" | "table">("chart");
  const [playing, setPlaying] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // A new chart in the same slot (a regenerated reply) starts whole.
  useEffect(() => setRange([0, last]), [chart, last]);

  // Play: the end of the span runs from just past its start to the last
  // period, so the reader watches the series arrive.
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(
      () => {
        setRange(([from, to]) => {
          if (to >= last) {
            setPlaying(false);
            return [from, to];
          }
          return [from, to + 1];
        });
      },
      Math.max(30, 3000 / Math.max(1, last)),
    );
    return () => window.clearInterval(timer);
  }, [playing, last]);

  useEffect(() => {
    const changed = () => setFullscreen(document.fullscreenElement === cardRef.current);
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);

  const shown = useMemo(() => sliceChart(chart, range), [chart, range]);
  const units = new Set(shown.series.map((s) => s.unit ?? ""));
  const sharedUnit = units.size === 1 ? shown.series[0]?.unit : undefined;
  const from = shown.periods[0];
  const to = shown.periods[shown.periods.length - 1];
  const sources = [...new Set(chart.series.map((s) => s.source).filter(Boolean))];
  const page = typeof window === "undefined" ? "" : window.location.href;

  function flash(message: string) {
    setNotice(message);
    window.setTimeout(() => setNotice(null), 2000);
  }

  async function download(format: "png" | "jpeg") {
    const node = cardRef.current;
    if (!node) return;
    const background = getComputedStyle(node).backgroundColor || "#ffffff";
    // Hidden for real rather than only filtered out of the copy, so the
    // picture is as tall as what is left and not as tall as the card.
    const controls = [...node.querySelectorAll<HTMLElement>("[data-export-skip]")];
    const shownAs = controls.map((control) => control.style.display);
    controls.forEach((control) => (control.style.display = "none"));
    await new Promise((settled) => requestAnimationFrame(settled));
    const options = {
      pixelRatio: 2,
      backgroundColor: background,
      // The controls are for the page, not the picture.
      filter: (element: HTMLElement) => !element.dataset?.exportSkip,
    };
    try {
      const render = format === "png" ? toPng : toJpeg;
      let url: string;
      try {
        url = await render(node, { ...options, quality: 0.95 });
      } catch {
        // A stylesheet it cannot read (an extension's, a CDN's) stops font
        // embedding; the system fonts are an acceptable picture.
        url = await render(node, { ...options, quality: 0.95, skipFonts: true });
      }
      const link = document.createElement("a");
      link.download = `${fileName(chart.title)}.${format === "png" ? "png" : "jpg"}`;
      link.href = url;
      link.click();
    } catch {
      flash("Could not render the image.");
    } finally {
      controls.forEach((control, at) => (control.style.display = shownAs[at]!));
    }
  }

  async function share() {
    try {
      await navigator.clipboard.writeText(page);
      flash("Link copied");
    } catch {
      flash("Copy the address bar to share.");
    }
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void cardRef.current?.requestFullscreen();
  }

  const ChartIcon =
    chart.kind === "scatter"
      ? IconChartDots
      : chart.kind === "bar"
        ? IconChartBar
        : IconChartLine;

  return (
    <figure
      ref={cardRef}
      className={cn(
        "space-y-4 rounded-xl bg-card p-5 text-card-foreground sm:p-6",
        fullscreen && "overflow-auto rounded-none",
        SERIES_DARK_SLOTS,
        className,
      )}
      style={{ ...SERIES_LIGHT_SLOTS, "--viz-grid": GRID } as React.CSSProperties}
    >
      <header className="flex items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          {/* The finding as the title, and what is drawn beneath it: a reader
              takes away the sentence at the top, so it says what the chart
              shows rather than what it is of. */}
          <h3 className="font-serif text-xl leading-snug font-semibold tracking-tight sm:text-2xl">
            {chart.story?.headline ?? chart.title}
            {!chart.story && from ? (
              <span className="ml-2 font-sans text-base font-normal whitespace-nowrap text-muted-foreground">
                {from === to ? from : `${from} to ${to}`}
              </span>
            ) : null}
          </h3>
          {chart.story ? (
            <p className="text-sm font-medium text-muted-foreground">
              {chart.title}
              {from ? ` · ${from === to ? from : `${from} to ${to}`}` : ""}
            </p>
          ) : null}
          {chart.reason ? (
            <p className="text-sm text-muted-foreground">{chart.reason}</p>
          ) : null}
        </div>
        <img src={logoUrl} alt="Terusan" className="h-9 w-auto shrink-0" />
      </header>

      {chart.story?.figures.length ? (
        <StoryFigures chart={chart} story={chart.story} />
      ) : null}

      <div data-export-skip="true" className="inline-flex rounded-lg border p-0.5">
        {(
          [
            ["table", "Table", IconTable],
            [
              "chart",
              chart.kind === "scatter"
                ? "Scatter"
                : chart.kind === "bar"
                  ? "Bar"
                  : "Line",
              ChartIcon,
            ],
          ] as const
        ).map(([value, label, Icon]) => (
          <button
            key={value}
            type="button"
            onClick={() => setView(value)}
            aria-pressed={view === value}
            className={cn(
              "flex items-center gap-1.5 rounded-md px-3 py-1 text-sm text-muted-foreground transition-colors hover:text-foreground",
              view === value && "bg-muted font-medium text-foreground",
            )}
          >
            <Icon className="size-4" />
            {label}
          </button>
        ))}
      </div>

      {view === "chart" ? (
        <div className="space-y-2">
          <ChartBody chart={shown} unit={sharedUnit} />
          {/* The time series names its own lines. */}
          {shown.series.length > 1 &&
          (shown.kind === "dual_axis" || shown.kind === "bar") ? (
            <Legend chart={shown} />
          ) : null}
        </div>
      ) : (
        <FiguresTable chart={shown} />
      )}

      {last > 1 ? (
        <div data-export-skip="true" className="flex items-center gap-3 text-sm">
          <Button
            variant="secondary"
            size="icon"
            className="size-9 shrink-0"
            aria-label={playing ? "Pause" : "Play through the periods"}
            onClick={() => {
              if (playing) return setPlaying(false);
              // From the start of the span, or again from it if already whole.
              setRange(([start]) => [start, Math.min(start + 1, last)]);
              setPlaying(true);
            }}
          >
            {playing ? (
              <IconPlayerPauseFilled className="size-4" />
            ) : (
              <IconPlayerPlayFilled className="size-4" />
            )}
          </Button>
          <span className="w-16 shrink-0 text-right tabular-nums">
            {chart.periods[range[0]]}
          </span>
          <Slider.Root
            value={range}
            min={0}
            max={last}
            minStepsBetweenValues={1}
            onValueChange={(value) => {
              setPlaying(false);
              setRange(value as [number, number]);
            }}
            className="flex-1"
          >
            <Slider.Control className="flex h-6 w-full touch-none items-center select-none">
              <Slider.Track className="relative h-1 w-full rounded-full bg-muted">
                <Slider.Indicator className="rounded-full bg-muted-foreground/50" />
                {[0, 1].map((index) => (
                  <Slider.Thumb
                    key={index}
                    index={index}
                    getAriaLabel={(at) =>
                      at === 0 ? "Start of the span" : "End of the span"
                    }
                    className="size-4 rounded-full bg-muted-foreground/70 shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                ))}
              </Slider.Track>
            </Slider.Control>
          </Slider.Root>
          <span className="w-16 shrink-0 tabular-nums">{chart.periods[range[1]]}</span>
        </div>
      ) : null}

      <footer className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 text-sm">
        <div className="min-w-0 space-y-1 text-muted-foreground">
          <p>
            <span className="font-semibold text-foreground">Data source:</span>{" "}
            {sources.length ? sources.join("; ") : "Terusan catalogue"} –{" "}
            {chart.series.length === 1 ? (
              <Link
                to="/indicators/$indicatorId"
                params={{ indicatorId: chart.series[0]!.id }}
                className="text-foreground underline underline-offset-2"
              >
                Learn more about this data
              </Link>
            ) : (
              <Link
                to="/observations"
                search={{ indicator: chart.series.map((s) => s.id) }}
                className="text-foreground underline underline-offset-2"
              >
                Learn more about this data
              </Link>
            )}
          </p>
          <p className="text-xs">
            <span className="font-semibold text-foreground">Note:</span>{" "}
            {noteFor(chart, shown)}
          </p>
          <p className="text-xs">
            {page ? `${hostAndPath(page)} | ` : ""}Terusan · CSIS Indonesia
          </p>
        </div>

        <div data-export-skip="true" className="flex flex-wrap items-center gap-2">
          {notice ? (
            <span className="text-xs text-muted-foreground">{notice}</span>
          ) : null}
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button variant="secondary" size="sm" />}>
              <IconDownload className="size-4" />
              Download
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => void download("png")}>
                Image (PNG)
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => void download("jpeg")}>
                Image (JPG)
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button variant="secondary" size="sm" onClick={() => void share()}>
            <IconShare className="size-4" />
            Share
          </Button>
          <Button variant="secondary" size="sm" onClick={toggleFullscreen}>
            {fullscreen ? (
              <IconMinimize className="size-4" />
            ) : (
              <IconMaximize className="size-4" />
            )}
            {fullscreen ? "Exit full-screen" : "Enter full-screen"}
          </Button>
        </div>
      </footer>
    </figure>
  );
}

/** The chart narrowed to the periods between two indices, inclusive. */
function sliceChart(chart: ChartSpec, [from, to]: [number, number]): ChartSpec {
  if (from === 0 && to === chart.periods.length - 1) return chart;
  const series = chart.series.map((s) => ({
    ...s,
    values: s.values.slice(from, to + 1),
  }));
  const sliced: ChartSpec = {
    ...chart,
    periods: chart.periods.slice(from, to + 1),
    series,
  };
  // The marks move with the span, and those outside it go.
  if (chart.story?.annotations) {
    sliced.story = {
      ...chart.story,
      annotations: chart.story.annotations
        .filter((mark) => mark.index >= from && mark.index <= to)
        .map((mark) => ({ ...mark, index: mark.index - from })),
    };
  }
  if (series.length === 2) {
    const [r, n] = pearson(series[0]!.values, series[1]!.values);
    sliced.correlation = n >= 3 ? r : undefined;
    sliced.overlap = n >= 3 ? n : undefined;
  }
  return sliced;
}

function pearson(a: (number | null)[], b: (number | null)[]): [number, number] {
  const pairs = a.flatMap((x, i) =>
    x != null && b[i] != null ? [[x, b[i]!] as const] : [],
  );
  const n = pairs.length;
  if (n < 2) return [0, n];
  const mx = pairs.reduce((sum, [x]) => sum + x, 0) / n;
  const my = pairs.reduce((sum, [, y]) => sum + y, 0) / n;
  let sxy = 0;
  let sxx = 0;
  let syy = 0;
  for (const [x, y] of pairs) {
    sxy += (x - mx) * (y - my);
    sxx += (x - mx) ** 2;
    syy += (y - my) ** 2;
  }
  return sxx && syy ? [sxy / Math.sqrt(sxx * syy), n] : [0, 0];
}

/** What the reader should know before reading the figures off. */
function noteFor(chart: ChartSpec, shown: ChartSpec): string {
  const frequency = { month: "Monthly", quarter: "Quarterly", year: "Annual" }[
    chart.granularity
  ];
  const parts = [
    `${frequency} figures; where a source publishes more often, the average of each period.`,
  ];
  if (chart.kind === "indexed")
    parts.push("Each line is rebased to 100 at the first common period.");
  const places = [...new Set(chart.series.map((s) => s.member).filter(Boolean))];
  if (places.length) parts.push(`Drawn for ${places.join(", ")}.`);
  if (shown.correlation !== undefined) {
    parts.push(
      `Correlation r = ${shown.correlation.toFixed(2)} over ${shown.overlap} periods — it does not show that one causes the other.`,
    );
  }
  return parts.join(" ");
}

function hostAndPath(href: string): string {
  try {
    const url = new URL(href);
    return `${url.host}${url.pathname}`;
  } catch {
    return href;
  }
}

function fileName(title: string): string {
  return (
    title
      .normalize("NFKD")
      .replace(/[^\w\s-]/g, "")
      .trim()
      .replace(/\s+/g, "-")
      .toLowerCase()
      .slice(0, 80) || "terusan-chart"
  );
}

/** Which colour is which series. A scatter has one colour of dot, so it names
 * its axes instead. */
function Legend({ chart }: { chart: ChartSpec }) {
  if (chart.kind === "scatter") return null;
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {chart.series.map((series, slot) => (
        <li key={series.id} className="flex min-w-0 items-center gap-1.5">
          <span
            aria-hidden
            className="h-0.5 w-4 shrink-0 rounded-full"
            style={{ background: seriesColor(slot) }}
          />
          <Link
            to="/indicators/$indicatorId"
            params={{ indicatorId: series.id }}
            className="truncate underline-offset-2 hover:text-foreground hover:underline"
          >
            {seriesName(series)}
          </Link>
          {series.unit ? <span className="shrink-0">({series.unit})</span> : null}
        </li>
      ))}
    </ul>
  );
}

/** The figures behind the chart, a row per period. */
function FiguresTable({ chart }: { chart: ChartSpec }) {
  return (
    <div className="max-h-[340px] overflow-auto rounded-lg border">
      <table className="w-full text-sm tabular-nums">
        <thead className="sticky top-0 bg-muted text-left text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Period</th>
            {chart.series.map((series) => (
              <th key={series.id} className="px-3 py-2 text-right font-medium">
                {seriesName(series)}
                {series.unit ? ` (${series.unit})` : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {chart.periods.map((period, at) => (
            <tr key={period} className="border-t">
              <td className="px-3 py-1.5">{period}</td>
              {chart.series.map((series) => {
                const value = series.values[at];
                return (
                  <td key={series.id} className="px-3 py-1.5 text-right">
                    {value == null ? "—" : formatDecimal(String(round(value)))}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
  return (
    <TimeSeriesChart
      series={toSeries(chart)}
      unit={unit}
      annotations={chart.story?.annotations}
      {...LOOK}
    />
  );
}

// How the time series is drawn inside the card: at the card's width, with the
// dashed grid and a dot per figure, and no frame of its own.
const LOOK = { width: 720, dashed: true, markers: true, framed: false } as const;

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
    <TimeSeriesChart
      series={toSeries(chart, rebased)}
      unit={`(${baseLabel} = 100)`}
      annotations={chart.story?.annotations}
      {...LOOK}
    />
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
    <div className="viz-root relative" role="group" aria-label={label}>
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
                strokeDasharray="4 4"
              />
              {[0, 1].map((axis) => (
                <text
                  key={axis}
                  x={axis === 0 ? padding.left - 10 : WIDTH - padding.right + 10}
                  y={at}
                  textAnchor={axis === 0 ? "end" : "start"}
                  dominantBaseline="middle"
                  fontSize={11}
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
            fontSize={11}
            fontWeight={500}
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
          {/* A dot on every figure, where there are few enough to tell apart. */}
          {count <= 120
            ? [left, right].map((series, axis) =>
                series.values.map((value, index) =>
                  value == null ? null : (
                    <circle
                      key={`${series.id}-dot-${index}`}
                      cx={x(index)}
                      cy={y(axis, value)}
                      r={2.5}
                      fill={seriesColor(axis)}
                    />
                  ),
                ),
              )
            : null}
        </g>

        {chart.story?.annotations?.length ? (
          <Annotations
            marks={chart.story.annotations.flatMap((mark) => {
              const value = [left, right][mark.series]?.values[mark.index];
              if (mark.series > 1 || value == null) return [];
              return [
                {
                  key: `${mark.series}-${mark.index}`,
                  x: x(mark.index),
                  y: y(mark.series, value),
                  color: seriesColor(mark.series),
                  label: mark.label,
                },
              ];
            })}
            width={WIDTH}
            top={padding.top}
          />
        ) : null}

        {tickIndices(count).map((index) => (
          <text
            key={`tick-${chart.periods[index]}`}
            x={x(index)}
            y={HEIGHT - 8}
            textAnchor={index === 0 ? "start" : index === count - 1 ? "end" : "middle"}
            fill="var(--muted-foreground)"
            fontSize={11}
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
              strokeDasharray="4 4"
            />
            <text
              x={padding.left - 10}
              y={y(value)}
              textAnchor="end"
              dominantBaseline="middle"
              fill="var(--muted-foreground)"
              fontSize={11}
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
            fill="var(--muted-foreground)"
            fontSize={11}
          >
            {formatCompact(String(value))}
          </text>
        ))}

        {/* What each axis measures. */}
        <text
          x={WIDTH - padding.right}
          y={HEIGHT - 6}
          textAnchor="end"
          fill="var(--muted-foreground)"
          fontSize={11}
          fontWeight={500}
        >
          → {across.label}
          {across.unit ? ` (${across.unit})` : ""}
        </text>
        <text
          x={padding.left}
          y={14}
          fill="var(--muted-foreground)"
          fontSize={11}
          fontWeight={500}
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

/**
 * The key figures at a glance: each series' latest value and how far it
 * moved over the span, written by the server as the headline writes them —
 * 17.844 in Indonesian, 17,844 in English, rounded the same way.
 */
function StoryFigures({ chart, story }: { chart: ChartSpec; story: ChartStory }) {
  const since = story.language === "id" ? "sejak" : "since";
  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {story.figures.slice(0, 4).map((figure) => {
        const series = chart.series[figure.series];
        if (!series) return null;
        const flat = Math.abs(figure.change) < 0.05;
        return (
          <div key={series.id} className="min-w-0 rounded-lg bg-muted/60 px-3 py-2">
            <dt className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
              {chart.series.length > 1 ? (
                <span
                  aria-hidden
                  className="size-2 shrink-0 rounded-full"
                  style={{ background: seriesColor(figure.series) }}
                />
              ) : null}
              <span className="truncate" title={seriesName(series)}>
                {series.short ?? seriesName(series)}
              </span>
            </dt>
            <dd className="mt-0.5 font-heading text-lg font-semibold tabular-nums">
              {figure.last_text}
            </dd>
            <dd className="text-xs text-muted-foreground tabular-nums">
              {flat ? "=" : figure.change > 0 ? "▲" : "▼"} {figure.change_text} {since}{" "}
              {figure.from}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
