import { useId, useState } from "react";

import { formatCompact, formatDecimal } from "~/lib/format";
import {
  CONTEXT_COLOR,
  FOCUS_COLOR,
  FOCUS_DARK_SLOT,
  FOCUS_LIGHT_SLOTS,
  MAX_SERIES,
  SERIES_DARK_SLOTS,
  SERIES_LIGHT_SLOTS,
  isIndonesia,
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
  /**
   * The line the chart is about — Indonesia, among other places. Where any
   * line is focused and any is not, the focused ones are drawn in red and the
   * rest in grey. Left unset, a line named for Indonesia is focused.
   */
  focus?: boolean;
};

const DEFAULT_WIDTH = 960;
const HEIGHT = 300;
// Gutters in viewBox units, so they scale with the chart. Wide enough that the
// axis labels and the end of the line keep clear of the card's border: at 20
// the last point sat within a few pixels of it, which reads as the series being
// cut off rather than ending.
const PADDING = { top: 16, right: 36, bottom: 28, left: 84 };

// The column the end labels sit in, right of the plot. The guide line runs out
// of the plot by `LEADER_RUN` and then bends to its label, so lines ending at
// the same height fan out to labels stacked apart rather than overprinting.
const LABEL_COLUMN = 132;
const LEADER_RUN = 10;
const LABEL_GAP = 22;
// One line of name and one of value, as the label is printed.
const LABEL_HEIGHT = 26;
const LABEL_CHARS = 18;

/** A name cut to the label column, with the cut shown. */
function clip(name: string): string {
  return name.length > LABEL_CHARS
    ? `${name.slice(0, LABEL_CHARS - 1).trimEnd()}…`
    : name;
}

/**
 * Where each end label goes: as near its line's last figure as it can be
 * without overlapping the one above, and pushed back up as a block if the
 * stack runs off the bottom of the plot.
 */
function stackLabels(wanted: number[], top: number, bottom: number): number[] {
  const order = wanted.map((y, index) => ({ y, index })).sort((a, b) => a.y - b.y);
  const placed: number[] = [];
  for (const [rank, item] of order.entries()) {
    const floor = rank === 0 ? top : placed[rank - 1]! + LABEL_HEIGHT;
    placed.push(Math.max(item.y, floor));
  }
  const overflow = (placed[placed.length - 1] ?? 0) - bottom;
  if (overflow > 0) {
    for (let rank = placed.length - 1; rank >= 0; rank--) {
      const ceiling =
        rank === placed.length - 1 ? bottom : placed[rank + 1]! - LABEL_HEIGHT;
      placed[rank] = Math.max(top, Math.min(placed[rank]!, ceiling));
    }
  }
  const result: number[] = new Array(wanted.length);
  order.forEach((item, rank) => {
    result[item.index] = placed[rank]!;
  });
  return result;
}

/**
 * How each listed line is coloured. Decided over every listed line, hidden or
 * not, so muting one never turns the chart's focus on or off underneath the
 * reader.
 */
function paletteFor(listed: Series[]): (slot: number) => string {
  const focused = listed.map((entry) => entry.focus ?? isIndonesia(entry.name));
  const contrasted = focused.some(Boolean) && focused.some((f) => !f);
  return (slot) =>
    contrasted ? (focused[slot] ? FOCUS_COLOR : CONTEXT_COLOR) : seriesColor(slot);
}

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
  annotations = [],
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
  /**
   * Points to mark and label, by series and period index: a peak, a low, a
   * sharp move. Where a caller passes these, they replace the default marks.
   */
  annotations?: { series: number; index: number; label: string }[];
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

  const colorOf = paletteFor(listed);
  const isFocus = (slot: number) => colorOf(slot) === FOCUS_COLOR;
  // Every line named at its end where there is more than one: the eye stays
  // on the chart instead of travelling to a key and back.
  const labelled = shown.length > 1;

  if (listed.length > 0 && shown.length === 0) {
    return (
      <div className="space-y-2">
        <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
          Every series is hidden. Click one below to bring it back.
        </p>
        <Legend listed={listed} onToggle={onToggle} colorOf={colorOf} />
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

  const pad = labelled ? { ...PADDING, right: LABEL_COLUMN + LEADER_RUN + 8 } : PADDING;
  const plotWidth = WIDTH - pad.left - pad.right;
  const plotHeight = HEIGHT - pad.top - pad.bottom;

  const x = (index: number) =>
    pad.left +
    (axis.length === 1 ? plotWidth / 2 : (index / (axis.length - 1)) * plotWidth);
  const y = (value: number) =>
    pad.top + plotHeight - ((value - low) / (high - low)) * plotHeight;

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
    `${path(run)} L ${x(run[run.length - 1]!.index)} ${pad.top + plotHeight} ` +
    `L ${x(run[0]!.index)} ${pad.top + plotHeight} Z`;

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
      className={`viz-root space-y-2 ${SERIES_DARK_SLOTS} ${FOCUS_DARK_SLOT}`}
      style={
        {
          ...SERIES_LIGHT_SLOTS,
          ...FOCUS_LIGHT_SLOTS,
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
              <rect x={pad.left} y={pad.top} width={plotWidth} height={plotHeight} />
            </clipPath>
          </defs>

          {/* Recessive: hairline, solid, one step off the surface. */}
          {ticks.map((value) => (
            <g key={value}>
              <line
                x1={pad.left}
                x2={WIDTH - pad.right}
                y1={y(value)}
                y2={y(value)}
                stroke="var(--viz-grid)"
                strokeWidth={1}
                strokeDasharray={dashed ? "4 4" : undefined}
              />
              <text
                x={pad.left - 10}
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
            {[...shown]
              .sort((a, b) => Number(isFocus(a.slot)) - Number(isFocus(b.slot)))
              .map((entry) => (
                <g key={`series-${entry.name}`}>
                  {/* The wash belongs to a single series. Eight overlaid washes
                    stack into a muddy block that hides the lines it is meant
                    to support. */}
                  {shown.length === 1
                    ? runsOf(entry.points).map((run, runIndex) => (
                        <path
                          key={`area-${runIndex}`}
                          d={areaPath(run)}
                          fill={colorOf(entry.slot)}
                          fillOpacity={0.1}
                        />
                      ))
                    : null}
                  {runsOf(entry.points).map((run, runIndex) => (
                    <path
                      key={`line-${runIndex}`}
                      d={path(run)}
                      fill="none"
                      stroke={colorOf(entry.slot)}
                      strokeWidth={isFocus(entry.slot) ? 2.75 : 2}
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
                            fill={colorOf(entry.slot)}
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

          {annotations.length ? (
            <Annotations
              marks={annotations.flatMap((mark) => {
                const entry = listed[mark.series];
                const point = entry?.points[mark.index];
                if (!entry || entry.hidden || !point || point.value === null) return [];
                return [
                  {
                    key: `${mark.series}-${mark.index}`,
                    x: x(mark.index),
                    y: y(point.value),
                    color: colorOf(mark.series),
                    label: mark.label,
                  },
                ];
              })}
              width={WIDTH}
              top={pad.top}
            />
          ) : null}

          {/* Labelled selectively: the peak and the latest, not every point. */}
          {only && !annotations.length
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
                    fill={colorOf(only.slot)}
                    stroke="var(--color-card, #fff)"
                    strokeWidth={2}
                  />
                );
              })
            : null}

          {labelled ? (
            <EndLabels
              lines={shown.flatMap((entry) => {
                const index = entry.points.findLastIndex((p) => p.value !== null);
                const point = entry.points[index];
                if (!point || point.value === null) return [];
                return [
                  {
                    key: entry.name,
                    name: entry.name,
                    value: point.value,
                    x: x(index),
                    y: y(point.value),
                    color: colorOf(entry.slot),
                    focus: isFocus(entry.slot),
                  },
                ];
              })}
              plotRight={WIDTH - pad.right}
              top={pad.top}
              bottom={pad.top + plotHeight}
            />
          ) : null}

          {hover !== null ? (
            <g>
              <line
                x1={x(hover)}
                x2={x(hover)}
                y1={pad.top}
                y2={pad.top + plotHeight}
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
                    fill={colorOf(entry.slot)}
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
              y={pad.top}
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
                      style={{ background: colorOf(entry.slot) }}
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

      {/* Named at their ends already, so the key stays only where it is also
          the control that mutes a line and brings it back. */}
      {listed.length > 1 && (onToggle || !labelled) ? (
        <Legend listed={listed} onToggle={onToggle} colorOf={colorOf} />
      ) : null}

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
  colorOf,
}: {
  listed: Series[];
  onToggle?: (name: string) => void;
  colorOf: (slot: number) => string;
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
                background: colorOf(slot),
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

/**
 * Each line's name and latest figure, at its end.
 *
 * Stacked as a title over a value, in the line's colour, in a column right of
 * the plot — and tied to the line's last point by a guide that runs out of the
 * plot and bends to the label. The bend is what lets lines finishing at the
 * same height keep labels that do not overprint: the stack spreads them apart,
 * and each guide still says which is whose.
 */
function EndLabels({
  lines,
  plotRight,
  top,
  bottom,
}: {
  lines: {
    key: string;
    name: string;
    value: number;
    x: number;
    y: number;
    color: string;
    focus: boolean;
  }[];
  plotRight: number;
  top: number;
  bottom: number;
}) {
  // Centred on the line where there is room: the label's middle, not its top,
  // is what lines up with the figure.
  const tops = stackLabels(
    lines.map((line) => line.y - LABEL_HEIGHT / 2),
    top - 4,
    bottom - LABEL_HEIGHT + 6,
  );
  const elbow = plotRight + LEADER_RUN;
  const textX = elbow + LABEL_GAP;
  return (
    <g>
      {lines.map((line, index) => {
        const labelTop = tops[index]!;
        const anchorY = labelTop + LABEL_HEIGHT / 2 - 3;
        // The grey that recedes as a line is too faint to read as type, so a
        // context label is set in the page's muted ink instead.
        const ink =
          line.color === CONTEXT_COLOR ? "var(--muted-foreground)" : line.color;
        return (
          <g key={line.key}>
            <title>{`${line.name}: ${formatDecimal(String(line.value))}`}</title>
            <path
              d={`M ${line.x} ${line.y} L ${elbow} ${line.y} L ${textX - 5} ${anchorY}`}
              fill="none"
              stroke={line.color}
              strokeWidth={1}
              strokeOpacity={line.focus ? 0.9 : 0.6}
            />
            <circle
              cx={line.x}
              cy={line.y}
              r={line.focus ? 4.5 : 3.5}
              fill={line.color}
              stroke="var(--card, #fff)"
              strokeWidth={1.5}
            />
            <text
              x={textX}
              y={labelTop + 10}
              fontSize={11}
              fontWeight={line.focus ? 700 : 600}
              fill={ink}
            >
              {clip(line.name)}
            </text>
            <text
              x={textX}
              y={labelTop + 23}
              fontSize={11}
              fontWeight={line.focus ? 700 : 600}
              fill={ink}
              className="tabular-nums"
            >
              {formatCompact(String(line.value))}
            </text>
          </g>
        );
      })}
    </g>
  );
}

/**
 * Marked points with their labels: a dot on the line and a few words above
 * it, on a halo in the card's colour so a label crossing a line stays
 * legible. Labels near an edge are anchored to stay inside it, and one near
 * the top goes below its point instead.
 */
export function Annotations({
  marks,
  width,
  top,
}: {
  marks: { key: string; x: number; y: number; color: string; label: string }[];
  width: number;
  top: number;
}) {
  // Placed one by one, each label moved clear of those already placed: two
  // series peaking in the same period otherwise print one label over the
  // other. Widths are estimated from the text, which at 11px is close enough.
  const placed: { left: number; right: number; y: number }[] = [];
  const layout = marks.map((mark) => {
    const anchor: "start" | "end" | "middle" =
      mark.x < 90 ? "start" : mark.x > width - 90 ? "end" : "middle";
    const textWidth = mark.label.length * 6.2;
    const left =
      anchor === "start"
        ? mark.x
        : anchor === "end"
          ? mark.x - textWidth
          : mark.x - textWidth / 2;
    let y = mark.y - 12 < top + 8 ? mark.y + 18 : mark.y - 10;
    for (let tries = 0; tries < 6; tries++) {
      const clash = placed.some(
        (box) =>
          left < box.right && left + textWidth > box.left && Math.abs(box.y - y) < 14,
      );
      if (!clash) break;
      y += y < mark.y ? -14 : 14;
    }
    placed.push({ left, right: left + textWidth, y });
    return { ...mark, anchor, labelY: Math.max(top + 4, y) };
  });
  return (
    <g>
      {layout.map((mark) => {
        const anchor = mark.anchor;
        return (
          <g key={mark.key}>
            <circle
              cx={mark.x}
              cy={mark.y}
              r={4.5}
              fill={mark.color}
              stroke="var(--card, #fff)"
              strokeWidth={2}
            />
            <text
              x={mark.x}
              y={mark.labelY}
              textAnchor={anchor}
              fontSize={11}
              fontWeight={600}
              fill="var(--foreground)"
              stroke="var(--card, #fff)"
              strokeWidth={3}
              paintOrder="stroke"
            >
              {mark.label}
            </text>
          </g>
        );
      })}
    </g>
  );
}
