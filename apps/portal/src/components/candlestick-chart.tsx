import { useId, useState } from "react";

import { formatCompact, formatDecimal } from "~/lib/format";

/**
 * Open, high, low and close, one bar per period.
 *
 * A candle carries four figures where a line carries one, which is the whole
 * reason to use it: the range within a session is the thing a line throws away.
 *
 * Direction is shown twice. The body is **hollow when the close is above the
 * open and filled when it is below** — the convention that predates colour —
 * and colour repeats it. That redundancy is not decoration: the rise/fall pair
 * separates at CVD ΔE 6.9, which the palette rules permit only alongside a
 * secondary encoding. The conventional green/red is worse, not better; it
 * measures 4.1 and fails outright.
 */

export type Candle = {
  label: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
};

const WIDTH = 960;
const HEIGHT = 340;
const PADDING = { top: 16, right: 36, bottom: 28, left: 84 };

/** The narrowest a body may draw, so an unchanged session is still a mark. */
const MIN_BODY = 1;
/** Surface gap between adjacent bodies, in viewBox units. */
const GAP = 2;

const RISE = "#1baf7a";
const FALL = "#e34948";
const RISE_DARK = "#199e70";
const FALL_DARK = "#e66767";

function labelIndices(count: number): number[] {
  if (count <= 2) return count === 1 ? [0] : [0, 1];
  const step = Math.ceil(count / 8);
  const indices = [0];
  for (let index = step; index <= count - 1 - step; index += step) indices.push(index);
  indices.push(count - 1);
  return indices;
}

export function CandlestickChart({
  candles,
  unit,
  caption,
}: {
  candles: Candle[];
  unit?: string;
  caption?: string;
}) {
  const clipId = useId();
  const [hover, setHover] = useState<number | null>(null);

  const complete = candles.filter(
    (candle) =>
      candle.open !== null &&
      candle.high !== null &&
      candle.low !== null &&
      candle.close !== null,
  );

  if (complete.length < 2) {
    return (
      <p className="rounded-sm border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
        Not enough complete sessions to plot — a candle needs an open, a high, a low and
        a close.
      </p>
    );
  }

  // Scaled on the extremes, not the closes: a wick outside the axis is a figure
  // drawn outside the chart.
  const highs = complete.map((candle) => candle.high as number);
  const lows = complete.map((candle) => candle.low as number);
  const max = Math.max(...highs);
  const min = Math.min(...lows);
  const span = max - min || Math.abs(max) || 1;
  const padded = min - span * 0.08;
  // An index cannot go below zero, and an axis that says it could invites the
  // reader to believe otherwise.
  const low = min >= 0 ? Math.max(0, padded) : padded;
  const high = max + span * 0.08;

  const plotWidth = WIDTH - PADDING.left - PADDING.right;
  const plotHeight = HEIGHT - PADDING.top - PADDING.bottom;

  const slot = plotWidth / candles.length;
  const body = Math.max(1, slot - GAP);
  const x = (index: number) => PADDING.left + slot * index + slot / 2;
  const y = (value: number) =>
    PADDING.top + plotHeight - ((value - low) / (high - low)) * plotHeight;

  const ticks = [0, 1, 2, 3, 4].map((step) => low + ((high - low) * step) / 4);
  const active = hover !== null ? candles[hover] : undefined;

  return (
    <figure
      className="viz-root space-y-2 dark:[--fall:var(--fall-dark)] dark:[--rise:var(--rise-dark)]"
      style={
        {
          "--rise": RISE,
          "--fall": FALL,
          "--rise-dark": RISE_DARK,
          "--fall-dark": FALL_DARK,
          "--viz-grid": "color-mix(in oklab, currentColor 12%, transparent)",
        } as React.CSSProperties
      }
    >
      <div className="relative overflow-hidden rounded-sm border bg-card">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="h-[340px] w-full"
          role="img"
          aria-label={caption ?? "Candlestick chart"}
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
            {candles.map((candle, index) => {
              if (
                candle.open === null ||
                candle.high === null ||
                candle.low === null ||
                candle.close === null
              ) {
                // A session with a figure missing is left blank rather than
                // drawn from the parts that survived.
                return null;
              }

              const rose = candle.close >= candle.open;
              const colour = rose ? "var(--rise)" : "var(--fall)";
              const top = y(Math.max(candle.open, candle.close));
              const height = Math.max(
                MIN_BODY,
                y(Math.min(candle.open, candle.close)) - top,
              );

              return (
                <g key={candle.label}>
                  {/* The wick first, so the body sits over it. */}
                  <line
                    x1={x(index)}
                    x2={x(index)}
                    y1={y(candle.high)}
                    y2={y(candle.low)}
                    stroke={colour}
                    strokeWidth={1}
                  />
                  <rect
                    x={x(index) - body / 2}
                    y={top}
                    width={body}
                    height={height}
                    // Hollow on a rise, filled on a fall — the direction is
                    // readable without colour at all.
                    fill={rose ? "var(--color-card, #fff)" : colour}
                    stroke={colour}
                    strokeWidth={1}
                  />
                </g>
              );
            })}
          </g>

          {labelIndices(candles.length).map((index) => (
            <text
              key={`tick-${candles[index]!.label}`}
              x={x(index)}
              y={HEIGHT - 8}
              textAnchor={
                index === 0 ? "start" : index === candles.length - 1 ? "end" : "middle"
              }
              className="fill-muted-foreground text-[11px]"
            >
              {candles[index]!.label}
            </text>
          ))}

          {hover !== null ? (
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1={PADDING.top}
              y2={PADDING.top + plotHeight}
              stroke="var(--viz-grid)"
              strokeWidth={1}
            />
          ) : null}

          {/* Hit targets a whole slot wide, so a one-pixel body is catchable. */}
          {candles.map((candle, index) => (
            <rect
              key={`hit-${candle.label}`}
              x={x(index) - slot / 2}
              y={PADDING.top}
              width={slot}
              height={plotHeight}
              fill="transparent"
              onMouseEnter={() => setHover(index)}
            />
          ))}
        </svg>

        {active ? (
          <div
            className="pointer-events-none absolute top-3 rounded-sm border bg-popover px-2.5 py-1.5 text-xs shadow-sm"
            style={{
              left: `calc(${(x(hover as number) / WIDTH) * 100}% + 8px)`,
              transform:
                (hover as number) > candles.length * 0.7
                  ? "translateX(-110%)"
                  : undefined,
            }}
          >
            <div className="font-medium">{active.label}</div>
            <dl className="mt-0.5 grid grid-cols-[auto_1fr] gap-x-3 tabular-nums">
              {(["open", "high", "low", "close"] as const).map((field) => (
                <div key={field} className="contents">
                  <dt className="capitalize text-muted-foreground">{field}</dt>
                  <dd className="text-right">
                    {active[field] === null
                      ? "—"
                      : formatDecimal(String(active[field]))}
                  </dd>
                </div>
              ))}
            </dl>
            {unit ? <div className="mt-0.5 text-muted-foreground">{unit}</div> : null}
          </div>
        ) : null}
      </div>

      {/* Both cues named, because neither is obvious to someone who has not read
          a candle before. */}
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <li className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="size-2.5 shrink-0 rounded-[2px] border"
            style={{
              borderColor: "var(--rise)",
              background: "var(--color-card, #fff)",
            }}
          />
          Hollow — closed above its open
        </li>
        <li className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="size-2.5 shrink-0 rounded-[2px]"
            style={{ background: "var(--fall)" }}
          />
          Filled — closed below its open
        </li>
      </ul>

      {caption ? (
        <figcaption className="text-xs text-muted-foreground">{caption}</figcaption>
      ) : null}
    </figure>
  );
}
