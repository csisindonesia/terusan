import { useId, useState } from "react";

import { formatCompact, formatDecimal } from "~/lib/format";

/**
 * One series over time.
 *
 * A line, because the data's job here is change over time. One series, so no
 * legend — the heading names it. Drawn as SVG rather than through a charting
 * library: the marks are a handful of shapes, and a library would bring its own
 * opinions about every one of them.
 *
 * Colour is the validated categorical slot 1; there is no second series to tell
 * it apart from, so the only check that bites is contrast against the surface,
 * which it passes in both modes.
 */

export type Point = {
  label: string;
  /** Null where the figure is absent — the line breaks rather than bridging. */
  value: number | null;
  status?: string;
};

const WIDTH = 960;
const HEIGHT = 300;
const PADDING = { top: 16, right: 20, bottom: 28, left: 64 };

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
  points,
  unit,
  caption,
}: {
  points: Point[];
  unit?: string;
  caption?: string;
}) {
  const clipId = useId();
  const [hover, setHover] = useState<number | null>(null);

  const present = points.filter((point) => point.value !== null);
  if (present.length < 2) {
    return (
      <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
        Not enough figures to plot — a line needs at least two.
      </p>
    );
  }

  const values = present.map((point) => point.value as number);
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
    (points.length === 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth);
  const y = (value: number) =>
    PADDING.top + plotHeight - ((value - low) / (high - low)) * plotHeight;

  // Breaks at gaps rather than joining across them: a line drawn through a
  // missing year asserts a value nobody recorded.
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

  const path = (run: { index: number; value: number }[]) =>
    run.map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.index)} ${y(p.value)}`).join(" ");

  const areaPath = (run: { index: number; value: number }[]) =>
    `${path(run)} L ${x(run[run.length - 1]!.index)} ${PADDING.top + plotHeight} ` +
    `L ${x(run[0]!.index)} ${PADDING.top + plotHeight} Z`;

  // Four gridlines is enough to read a value off; more is noise.
  const ticks = [0, 1, 2, 3, 4].map((step) => low + ((high - low) * step) / 4);

  const lastIndex = points.findLastIndex((point) => point.value !== null);
  const peak = present.reduce((a, b) =>
    (b.value as number) > (a.value as number) ? b : a,
  );
  const peakIndex = points.indexOf(peak);

  const active = hover !== null ? points[hover] : undefined;

  return (
    <figure
      className="viz-root space-y-2"
      style={
        {
          "--series-1": "#2a78d6",
          "--viz-grid": "color-mix(in oklab, currentColor 12%, transparent)",
        } as React.CSSProperties
      }
    >
      <div className="relative overflow-hidden rounded-lg border bg-card">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="h-[300px] w-full dark:[--series-1:#3987e5]"
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
              />
              <text
                x={PADDING.left - 10}
                y={y(value)}
                textAnchor="end"
                dominantBaseline="middle"
                className="fill-muted-foreground text-[11px]"
              >
                {formatCompact(String(value))}
              </text>
            </g>
          ))}

          <g clipPath={`url(#${clipId})`}>
            {segments.map((run, index) => (
              <path
                key={`area-${index}`}
                d={areaPath(run)}
                fill="var(--series-1)"
                // A wash, never a saturated block.
                fillOpacity={0.1}
              />
            ))}
            {segments.map((run, index) => (
              <path
                key={`line-${index}`}
                d={path(run)}
                fill="none"
                stroke="var(--series-1)"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            ))}
          </g>

          {/* Period labels: first, last, and evenly spaced between — one per
              point would collide at sixty-six years, and so would a tick that
              landed next to an end. */}
          {labelIndices(points.length).map((index) => (
            <text
              key={`tick-${points[index]!.label}`}
              x={x(index)}
              y={HEIGHT - 8}
              textAnchor={
                index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"
              }
              className="fill-muted-foreground text-[11px]"
            >
              {points[index]!.label}
            </text>
          ))}

          {/* Labelled selectively: the peak and the latest, not every point. */}
          {[peakIndex, lastIndex].map((index) => {
            const point = points[index];
            if (!point || point.value === null) return null;
            return (
              <circle
                key={`mark-${index}`}
                cx={x(index)}
                cy={y(point.value)}
                r={4}
                fill="var(--series-1)"
                stroke="var(--color-card, #fff)"
                strokeWidth={2}
              />
            );
          })}

          {hover !== null && points[hover]?.value !== null ? (
            <g>
              <line
                x1={x(hover)}
                x2={x(hover)}
                y1={PADDING.top}
                y2={PADDING.top + plotHeight}
                stroke="var(--viz-grid)"
                strokeWidth={1}
              />
              <circle
                cx={x(hover)}
                cy={y(points[hover]!.value as number)}
                r={5}
                fill="var(--series-1)"
                stroke="var(--color-card, #fff)"
                strokeWidth={2}
              />
            </g>
          ) : null}

          {/* Hit targets wider than the marks, so a 4px dot is still catchable. */}
          {points.map((point, index) => (
            <rect
              key={`hit-${point.label}`}
              x={x(index) - plotWidth / points.length / 2}
              y={PADDING.top}
              width={plotWidth / points.length}
              height={plotHeight}
              fill="transparent"
              onMouseEnter={() => setHover(index)}
            />
          ))}
        </svg>

        {active ? (
          <div
            className="pointer-events-none absolute top-3 rounded-lg border bg-popover px-2.5 py-1.5 text-xs shadow-sm"
            style={{
              left: `calc(${(x(hover as number) / WIDTH) * 100}% + 8px)`,
              transform:
                (hover as number) > points.length * 0.7
                  ? "translateX(-110%)"
                  : undefined,
            }}
          >
            <div className="font-medium">{active.label}</div>
            <div className="tabular-nums">
              {active.value === null
                ? (active.status ?? "no value")
                : `${formatDecimal(String(active.value))}${unit ? ` ${unit}` : ""}`}
            </div>
          </div>
        ) : null}
      </div>

      {caption ? (
        <figcaption className="text-xs text-muted-foreground">{caption}</figcaption>
      ) : null}
    </figure>
  );
}
