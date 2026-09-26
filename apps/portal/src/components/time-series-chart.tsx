import { useId, useState } from "react";

import { formatCompact, formatDecimal } from "~/lib/format";
import {
  MAX_SERIES,
  SERIES_DARK_SLOTS,
  SERIES_LIGHT_SLOTS,
  seriesColor,
} from "~/lib/viz";

export { MAX_SERIES };

/**
 * One or more series over time.
 *
 * A line, because the data's job here is change over time. Drawn as SVG rather
 * than through a charting library: the marks are a handful of shapes, and a
 * library would bring its own opinions about every one of them.
 *
 * Colour is the validated categorical order, assigned by position and never
 * cycled. The palette clears the CVD and normal-vision floors on the adjacent
 * pairlist that lines use; its contrast warning is relieved by the legend and
 * the table of figures below the chart, so identity is never carried by colour
 * alone. Past `MAX_SERIES` the hues would have to repeat, so the caller is
 * asked to narrow instead.
 */

export type Point = {
  label: string;
  /** Null where the figure is absent — the line breaks rather than bridging. */
  value: number | null;
  status?: string;
};

export type Series = {
  name: string;
  points: Point[];
  /** Muted from the legend. Still listed, so it can be brought back. */
  hidden?: boolean;
};

const DEFAULT_WIDTH = 960;
const HEIGHT = 300;
// Gutters in viewBox units, so they scale with the chart. Wide enough that the
// axis labels and the end of the line keep clear of the card's border: at 20
// the last point sat within a few pixels of it, which reads as the series being
// cut off rather than ending.
const PADDING = { top: 16, right: 36, bottom: 28, left: 84 };

/**
 * Which periods get a label along the bottom.
 *
 * First and last always, then evenly spaced between — dropping any that would
 * land close enough to an end to overlap it. A tick printed on top of another
 * is worse than one fewer tick.
 */
function labelIndices(count: number): number[] {
  if (count <= 2) return count === 1 ? [0] : [0, 1];

  const step = Math.ceil(count / 8);

  // A full step of clearance from either end. Anything closer prints on top of
  // the first or last label, which are the two that always appear.
  const indices = [0];
  for (let index = step; index <= count - 1 - step; index += step) {
    indices.push(index);
  }
  indices.push(count - 1);
  return indices;
}

export function TimeSeriesChart({
  series,
  unit,
  caption,
  onToggle,
  width = DEFAULT_WIDTH,
  dashed = false,
  markers = false,
  framed = true,
}: {
  series: Series[];
  unit?: string;
  caption?: string;
  /** The viewBox width: narrower where the chart sits in a narrow column, so
   * its 11px labels are not drawn at eight. */
  width?: number;
  /** Dashed gridlines, for a chart that is read off the grid less than it is
   * looked at. */
  dashed?: boolean;
  /** A dot on every figure, where there are few enough to tell apart. */
  markers?: boolean;
  /** Its own border and surface; off where a card around it has them. */
  framed?: boolean;
  /** Given when the legend is a control rather than a key. */
  onToggle?: (name: string) => void;
}) {
  const WIDTH = width;
  const clipId = useId();
  const [hover, setHover] = useState<number | null>(null);

  // Sliced before anything else, so a series' colour is its position in this
  // list and nothing later can change it. Muting one must not repaint the
  // survivors: the reader is comparing lines across clicks.
  const listed = series.slice(0, MAX_SERIES);
  // Each carries the slot it was listed in, because that is what picks its
  // colour — `shown`'s own index shifts as soon as anything is muted.
  const shown = listed
    .map((entry, slot) => ({ ...entry, slot }))
    .filter((entry) => !entry.hidden);

  if (listed.length > 0 && shown.length === 0) {
    return (
      <div className="space-y-2">
        <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
          Every series is hidden. Click one below to bring it back.
        </p>
        <Legend listed={listed} onToggle={onToggle} />
      </div>
    );
  }
  // Every series is plotted against one set of periods, so they have to agree
  // on what those are. Taking the longest rather than the first: a series that
  // starts late must not truncate the axis for one that does not.
  const axis = shown.reduce<Point[]>(
    (longest, entry) => (entry.points.length > longest.length ? entry.points : longest),
    [],
  );

  const values = shown
    .flatMap((entry) => entry.points)
    .map((point) => point.value)
    .filter((value): value is number => value !== null);

  if (values.length < 2) {
    return (
      <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
        Not enough figures to plot — a line needs at least two.
      </p>
    );
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  // A flat series would divide by zero; give it a band to sit in.
  const span = max - min || Math.abs(max) || 1;
  // Padding below the minimum, but never past zero for a series that never
  // goes negative: a GDP axis labelled -109B invites the reader to believe the
  // quantity can be negative.
  const padded = min - span * 0.08;
  const low = min >= 0 ? Math.max(0, padded) : padded;
  const high = max + span * 0.08;

  const plotWidth = WIDTH - PADDING.left - PADDING.right;
  const plotHeight = HEIGHT - PADDING.top - PADDING.bottom;

  const x = (index: number) =>
    PADDING.left +
    (axis.length === 1 ? plotWidth / 2 : (index / (axis.length - 1)) * plotWidth);
  const y = (value: number) =>
    PADDING.top + plotHeight - ((value - low) / (high - low)) * plotHeight;

  // Breaks at gaps rather than joining across them: a line drawn through a
  // missing year asserts a value nobody recorded.
  const runsOf = (points: Point[]) => {
    const segments: { index: number; value: number }[][] = [];
    let run: { index: number; value: number }[] = [];
    points.forEach((point, index) => {
      if (point.value === null) {
        if (run.length) segments.push(run);
        run = [];
      } else {
        run.push({ index, value: point.value });
      }
    });
    if (run.length) segments.push(run);
    return segments;
  };

  const path = (run: { index: number; value: number }[]) =>
    run.map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.index)} ${y(p.value)}`).join(" ");

  const areaPath = (run: { index: number; value: number }[]) =>
    `${path(run)} L ${x(run[run.length - 1]!.index)} ${PADDING.top + plotHeight} ` +
    `L ${x(run[0]!.index)} ${PADDING.top + plotHeight} Z`;

  // Four gridlines is enough to read a value off; more is noise.
  const ticks = [0, 1, 2, 3, 4].map((step) => low + ((high - low) * step) / 4);

  const only = shown.length === 1 ? shown[0]! : undefined;
  // Marks only where there is one line. Peaks from eight overlaid series are
  // eight dots with nothing to say which belongs to which.
  const lastIndex = only ? only.points.findLastIndex((p) => p.value !== null) : -1;
  const present = only ? only.points.filter((p) => p.value !== null) : [];
  const peakIndex =
    present.length > 0
      ? only!.points.indexOf(
          present.reduce((a, b) => ((b.value as number) > (a.value as number) ? b : a)),
        )
      : -1;

  return (
    <figure
      className={`viz-root space-y-2 ${SERIES_DARK_SLOTS}`}
      style={
        {
          ...SERIES_LIGHT_SLOTS,
          "--viz-grid": "color-mix(in oklab, currentColor 12%, transparent)",
        } as React.CSSProperties
      }
    >
      <div
        className={
          framed ? "relative overflow-hidden rounded-lg border bg-card" : "relative"
        }
      >
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="h-[300px] w-full"
          role="img"
          aria-label={caption ?? "Time series"}
          onMouseLeave={() => setHover(null)}
        >
          <defs>
            <clipPath id={clipId}>
              <rect
                x={PADDING.left}
                y={PADDING.top}
                width={plotWidth}
                height={plotHeight}
              />
            </clipPath>
          </defs>

          {/* Recessive: hairline, solid, one step off the surface. */}
          {ticks.map((value) => (
            <g key={value}>
              <line
                x1={PADDING.left}
                x2={WIDTH - PADDING.right}
                y1={y(value)}
                y2={y(value)}
                stroke="var(--viz-grid)"
                strokeWidth={1}
                strokeDasharray={dashed ? "4 4" : undefined}
              />
              <text
                x={PADDING.left - 10}
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

          <g clipPath={`url(#${clipId})`}>
            {shown.map((entry) => (
              <g key={`series-${entry.name}`}>
                {/* The wash belongs to a single series. Eight overlaid washes
                    stack into a muddy block that hides the lines it is meant
                    to support. */}
                {shown.length === 1
                  ? runsOf(entry.points).map((run, runIndex) => (
                      <path
                        key={`area-${runIndex}`}
                        d={areaPath(run)}
                        fill={seriesColor(entry.slot)}
                        fillOpacity={0.1}
                      />
                    ))
                  : null}
                {runsOf(entry.points).map((run, runIndex) => (
                  <path
                    key={`line-${runIndex}`}
                    d={path(run)}
                    fill="none"
                    stroke={seriesColor(entry.slot)}
                    strokeWidth={2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                ))}
                {markers && entry.points.length <= 120
                  ? entry.points.map((point, index) =>
                      point.value === null ? null : (
                        <circle
                          key={`dot-${index}`}
                          cx={x(index)}
                          cy={y(point.value)}
                          r={2.5}
                          fill={seriesColor(entry.slot)}
                        />
                      ),
                    )
                  : null}
              </g>
            ))}
          </g>

          {/* Period labels: first, last, and evenly spaced between — one per
              point would collide at sixty-six years, and so would a tick that
              landed next to an end. */}
          {labelIndices(axis.length).map((index) => (
            <text
              key={`tick-${axis[index]!.label}`}
              x={x(index)}
              y={HEIGHT - 8}
              textAnchor={
                index === 0 ? "start" : index === axis.length - 1 ? "end" : "middle"
              }
              fill="var(--muted-foreground)"
              fontSize={11}
            >
              {axis[index]!.label}
            </text>
          ))}

          {/* Labelled selectively: the peak and the latest, not every point. */}
          {only
            ? // Deduplicated: when the peak *is* the latest figure both are the
              // same index, and two marks keyed alike is a React key collision.
              [...new Set([peakIndex, lastIndex])].map((index) => {
                const point = only.points[index];
                if (!point || point.value === null) return null;
                return (
                  <circle
                    key={`mark-${index}`}
                    cx={x(index)}
                    cy={y(point.value)}
                    r={4}
                    fill={seriesColor(only.slot)}
                    stroke="var(--color-card, #fff)"
                    strokeWidth={2}
                  />
                );
              })
            : null}

          {hover !== null ? (
            <g>
              <line
                x1={x(hover)}
                x2={x(hover)}
                y1={PADDING.top}
                y2={PADDING.top + plotHeight}
                stroke="var(--viz-grid)"
                strokeWidth={1}
              />
              {shown.map((entry) => {
                const point = entry.points[hover];
                if (!point || point.value === null) return null;
                return (
                  <circle
                    key={`hover-${entry.name}`}
                    cx={x(hover)}
                    cy={y(point.value)}
                    r={5}
                    fill={seriesColor(entry.slot)}
                    stroke="var(--color-card, #fff)"
                    strokeWidth={2}
                  />
                );
              })}
            </g>
          ) : null}

          {/* Hit targets wider than the marks, so a 4px dot is still catchable. */}
          {axis.map((point, index) => (
            <rect
              key={`hit-${point.label}`}
              x={x(index) - plotWidth / axis.length / 2}
              y={PADDING.top}
              width={plotWidth / axis.length}
              height={plotHeight}
              fill="transparent"
              onMouseEnter={() => setHover(index)}
            />
          ))}
        </svg>

        {hover !== null && axis[hover] ? (
          <div
            className="pointer-events-none absolute top-3 rounded-lg border bg-popover px-2.5 py-1.5 text-xs shadow-sm"
            style={{
              left: `calc(${(x(hover) / WIDTH) * 100}% + 8px)`,
              transform: hover > axis.length / 2 ? "translateX(-110%)" : undefined,
            }}
          >
            <div className="font-medium">{axis[hover]!.label}</div>
            {shown.map((entry) => {
              const point = entry.points[hover];
              return (
                <div
                  key={entry.name}
                  className="flex items-center gap-1.5 tabular-nums"
                >
                  {shown.length > 1 ? (
                    <span
                      aria-hidden
                      className="size-2 shrink-0 rounded-full"
                      style={{ background: seriesColor(entry.slot) }}
                    />
                  ) : null}
                  {shown.length > 1 ? (
                    <span className="text-muted-foreground">{entry.name}</span>
                  ) : null}
                  <span className="ml-auto">
                    {!point || point.value === null
                      ? (point?.status ?? "no value")
                      : `${formatDecimal(String(point.value))}${unit ? ` ${unit}` : ""}`}
                  </span>
                </div>
              );
            })}
          </div>
        ) : null}
      </div>

      {listed.length > 1 ? <Legend listed={listed} onToggle={onToggle} /> : null}

      {caption ? (
        <figcaption className="text-xs text-muted-foreground">{caption}</figcaption>
      ) : null}
    </figure>
  );
}

/**
 * The key, and — where the caller offers one — the control.
 *
 * A legend already names every line; letting it mute one costs no extra
 * furniture and answers the question a reader has while looking at it, which is
 * "what would this look like without that one". Muted entries stay listed, or
 * there would be no way back.
 *
 * Every listed series keeps its slot whether or not it is drawn, so muting one
 * never repaints the rest.
 */
function Legend({
  listed,
  onToggle,
}: {
  listed: Series[];
  onToggle?: (name: string) => void;
}) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1">
      {listed.map((entry, slot) => {
        const swatch = (
          <>
            <span
              aria-hidden
              className="h-0.5 w-4 shrink-0 rounded-full"
              style={{
                background: seriesColor(slot),
                // Muted rather than recoloured: the colour is the series'
                // identity and stays attached to it.
                opacity: entry.hidden ? 0.3 : 1,
              }}
            />
            <span className={entry.hidden ? "line-through opacity-60" : undefined}>
              {entry.name}
            </span>
          </>
        );

        return (
          <li key={entry.name} className="flex items-center text-xs">
            {onToggle ? (
              <button
                type="button"
                onClick={() => onToggle(entry.name)}
                aria-pressed={!entry.hidden}
                title={entry.hidden ? `Show ${entry.name}` : `Hide ${entry.name}`}
                className="flex cursor-pointer items-center gap-1.5 rounded px-1 py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                {swatch}
              </button>
            ) : (
              <span className="flex items-center gap-1.5 px-1 py-0.5 text-muted-foreground">
                {swatch}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
