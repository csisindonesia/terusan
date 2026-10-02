/**
 * The charts an article draws that the portal's own components do not.
 *
 * The portal's column chart reads its bars as shares of a total and has no
 * negative side; an article about change needs both signs. These draw on the
 * same palette slots as every other chart (lib/viz.ts), so an article looks
 * like the rest of the warehouse, and are sized from their container rather
 * than scaled, so an 11px label stays 11px on a phone.
 */

import { useEffect, useRef, useState } from "react";

import { formatPercent } from "~/lib/format";
import { SERIES_DARK_SLOTS, SERIES_LIGHT_SLOTS, seriesColor } from "~/lib/viz";

const GRID = "color-mix(in oklab, currentColor 12%, transparent)";
const WET = seriesColor(0);
const DRY = seriesColor(7);

function useWidth<T extends HTMLElement>(fallback = 640) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(280, Math.round(entry.contentRect.width)));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function scale(d0: number, d1: number, r0: number, r1: number) {
  return (v: number) => r0 + ((v - d0) / (d1 - d0)) * (r1 - r0);
}

/** What the pointer is over, shown beside it. */
function Tip({
  at,
  children,
}: {
  at: { x: number; y: number } | null;
  children: React.ReactNode;
}) {
  if (!at) return null;
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-[calc(100%+10px)] rounded-lg border bg-popover px-2.5 py-1.5 text-xs whitespace-nowrap shadow-sm"
      style={{ left: at.x, top: at.y }}
    >
      {children}
    </div>
  );
}

function Frame({
  children,
  width,
  height,
  label,
  innerRef,
  tip,
}: {
  children: React.ReactNode;
  width: number;
  height: number;
  label: string;
  innerRef: React.Ref<HTMLDivElement>;
  /** Drawn over the chart, outside the SVG, so it is laid out as HTML. */
  tip?: React.ReactNode;
}) {
  return (
    <div
      ref={innerRef}
      className={`viz-root relative ${SERIES_DARK_SLOTS}`}
      style={SERIES_LIGHT_SLOTS}
    >
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={label}
        className="block overflow-visible text-[11px] [&_text]:fill-muted-foreground"
      >
        {children}
      </svg>
      {tip}
    </div>
  );
}

/** One column per period, growing up or down from zero. */
export function ChangeColumns({
  columns,
  label,
}: {
  columns: { label: string; value: number; note: string }[];
  label: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const height = 200;
  const m = { l: 36, r: 8, t: 20, b: 24 };
  const top = Math.max(5, ...columns.map((c) => c.value));
  const bottom = Math.min(0, ...columns.map((c) => c.value));
  const y = scale(bottom, top, height - m.b, m.t);
  const slot = (width - m.l - m.r) / columns.length;
  const bar = Math.min(28, slot * 0.6);
  const ticks = [0, Math.round(top / 2 / 5) * 5, Math.floor(top / 5) * 5].filter(
    (t, i, a) => a.indexOf(t) === i,
  );
  const hovered = hover === null ? undefined : columns[hover];

  return (
    <Frame
      innerRef={ref}
      width={width}
      height={height}
      label={label}
      tip={
        hover !== null && hovered ? (
          <Tip
            at={{
              x: m.l + slot * hover + slot / 2,
              y: y(Math.max(0, hovered.value)),
            }}
          >
            {hovered.note}
          </Tip>
        ) : null
      }
    >
      {ticks.map((t) => (
        <g key={t}>
          <line x1={m.l} x2={width - m.r} y1={y(t)} y2={y(t)} stroke={GRID} />
          <text x={m.l - 6} y={y(t) + 4} textAnchor="end">
            {t}%
          </text>
        </g>
      ))}
      {columns.map((c, i) => {
        const cx = m.l + slot * i + slot / 2;
        const y0 = y(Math.max(0, c.value));
        const y1 = y(Math.min(0, c.value));
        return (
          <g
            key={c.label}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          >
            <rect
              x={cx - slot / 2}
              y={m.t}
              width={slot}
              height={height - m.t - m.b}
              fill="transparent"
            />
            <rect
              x={cx - bar / 2}
              y={y0}
              width={bar}
              height={Math.max(1.5, y1 - y0)}
              rx={3}
              fill={WET}
              opacity={hover === null || hover === i ? 1 : 0.5}
            />
            <text
              x={cx}
              y={(c.value >= 0 ? y0 : y1 + 12) - 4}
              textAnchor="middle"
              className="!fill-foreground tabular-nums"
            >
              {formatPercent(c.value)}
            </text>
            <text x={cx} y={height - 6} textAnchor="middle">
              {c.label}
            </text>
          </g>
        );
      })}
    </Frame>
  );
}

export type ProvinceRow = {
  geo_id: string;
  name: string;
  core: boolean;
  rain_normal: number;
  rain: number;
  rain_change: number;
  rice_before: number;
  rice_after: number;
  rice_change: number;
  chili_change: number | null;
};

/** Each province's dry season against its normal, drier to the left. */
export function DivergingBars({ rows, label }: { rows: ProvinceRow[]; label: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<string | null>(null);
  const sorted = [...rows].sort((a, b) => a.rain_change - b.rain_change);
  const narrow = width < 520;
  const row = 20;
  const m = { l: narrow ? 124 : 168, r: 40, t: 4, b: 24 };
  const height = sorted.length * row + m.t + m.b;
  const low = Math.min(
    -75,
    Math.floor(Math.min(...rows.map((r) => r.rain_change)) / 25) * 25,
  );
  const high = Math.max(
    20,
    Math.ceil(Math.max(...rows.map((r) => r.rain_change)) / 10) * 10,
  );
  const x = scale(low, high, m.l, width - m.r);
  const ticks = [-75, -50, -25, 0].filter((t) => t >= low);
  const hovered = sorted.find((r) => r.geo_id === hover);

  return (
    <Frame
      innerRef={ref}
      width={width}
      height={height}
      label={label}
      tip={
        hovered ? (
          <Tip
            at={{
              x: x(hovered.rain_change / 2),
              y: m.t + sorted.indexOf(hovered) * row,
            }}
          >
            <div className="font-medium">{hovered.name}</div>
            <div className="tabular-nums text-muted-foreground">
              {hovered.rain.toLocaleString("en-US")} mm against a normal of{" "}
              {hovered.rain_normal.toLocaleString("en-US")} mm
            </div>
          </Tip>
        ) : null
      }
    >
      {ticks.map((t) => (
        <g key={t}>
          <line
            x1={x(t)}
            x2={x(t)}
            y1={m.t}
            y2={height - m.b}
            stroke={t === 0 ? "currentColor" : GRID}
            strokeOpacity={t === 0 ? 0.4 : 1}
          />
          <text x={x(t)} y={height - 6} textAnchor="middle">
            {t === 0 ? "normal" : `${t}%`}
          </text>
        </g>
      ))}
      {sorted.map((r, i) => {
        const cy = m.t + i * row + row / 2;
        const a = x(Math.min(0, r.rain_change));
        const b = x(Math.max(0, r.rain_change));
        const name = narrow && r.name.length > 17 ? `${r.name.slice(0, 16)}…` : r.name;
        return (
          <g
            key={r.geo_id}
            onMouseEnter={() => setHover(r.geo_id)}
            onMouseLeave={() => setHover(null)}
          >
            <rect
              x={0}
              y={cy - row / 2}
              width={width}
              height={row}
              fill="transparent"
            />
            <text x={m.l - 8} y={cy + 4} textAnchor="end" className="!fill-foreground">
              <title>{r.name}</title>
              {name}
            </text>
            <rect
              x={a}
              y={cy - 6}
              width={Math.max(1.5, b - a)}
              height={12}
              rx={3}
              fill={r.rain_change < 0 ? DRY : WET}
            />
            <text
              x={r.rain_change < 0 ? a - 4 : b + 4}
              y={cy + 4}
              textAnchor={r.rain_change < 0 ? "end" : "start"}
              className="tabular-nums"
            >
              {formatPercent(r.rain_change, 0)}
            </text>
          </g>
        );
      })}
    </Frame>
  );
}

/** Rainfall against normal across, the rice price rise up, one dot a province. */
export function RainPriceScatter({
  rows,
  named,
  label,
}: {
  rows: ProvinceRow[];
  /** Provinces labelled on the chart; the rest are on hover. */
  named: string[];
  label: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<string | null>(null);
  const height = 320;
  const m = { l: 44, r: 16, t: 12, b: 40 };
  const x = scale(-80, 20, m.l, width - m.r);
  const top = Math.ceil(Math.max(...rows.map((r) => r.rice_change)) / 10) * 10;
  const y = scale(0, top, height - m.b, m.t);

  // A least-squares line, drawn faint: the eye reads a slope from the dots
  // anyway, and this keeps it honest about how shallow it is.
  const n = rows.length;
  const mx = rows.reduce((s, r) => s + r.rain_change, 0) / n;
  const my = rows.reduce((s, r) => s + r.rice_change, 0) / n;
  const slope =
    rows.reduce((s, r) => s + (r.rain_change - mx) * (r.rice_change - my), 0) /
    rows.reduce((s, r) => s + (r.rain_change - mx) ** 2, 0);
  const fit = (v: number) => my + slope * (v - mx);
  const xs = rows.map((r) => r.rain_change);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const hovered = rows.find((r) => r.geo_id === hover);

  return (
    <Frame
      innerRef={ref}
      width={width}
      height={height}
      label={label}
      tip={
        hovered ? (
          <Tip at={{ x: x(hovered.rain_change), y: y(hovered.rice_change) }}>
            <div className="font-medium">{hovered.name}</div>
            <div className="tabular-nums text-muted-foreground">
              rain {formatPercent(hovered.rain_change, 0)} · rice{" "}
              {formatPercent(hovered.rice_change)}
            </div>
            <div className="tabular-nums text-muted-foreground">
              Rp{hovered.rice_before.toLocaleString("en-US")} → Rp
              {hovered.rice_after.toLocaleString("en-US")} per kg
            </div>
          </Tip>
        ) : null
      }
    >
      {[0, top / 2, top].map((t) => (
        <g key={t}>
          <line x1={m.l} x2={width - m.r} y1={y(t)} y2={y(t)} stroke={GRID} />
          <text x={m.l - 6} y={y(t) + 4} textAnchor="end">
            {t}%
          </text>
        </g>
      ))}
      {[-75, -50, -25, 0].map((t) => (
        <text key={t} x={x(t)} y={height - 22} textAnchor="middle">
          {t === 0 ? "normal" : `${t}%`}
        </text>
      ))}
      <text x={(m.l + width - m.r) / 2} y={height - 4} textAnchor="middle">
        dry-season rainfall against normal
      </text>
      <line
        x1={x(x0)}
        y1={y(fit(x0))}
        x2={x(x1)}
        y2={y(fit(x1))}
        stroke="currentColor"
        strokeOpacity={0.45}
        strokeWidth={1.5}
        strokeDasharray="5 4"
      />
      {rows.map((r) => {
        const cx = x(r.rain_change);
        const cy = y(r.rice_change);
        return (
          <g
            key={r.geo_id}
            onMouseEnter={() => setHover(r.geo_id)}
            onMouseLeave={() => setHover(null)}
          >
            <circle
              cx={cx}
              cy={cy}
              r={hover === r.geo_id ? 6.5 : 5}
              fill={r.core ? seriesColor(1) : seriesColor(0)}
              stroke="var(--article-surface, var(--background))"
              strokeWidth={2}
            />
            <circle cx={cx} cy={cy} r={11} fill="transparent" />
            {named.includes(r.name) ? (
              <text x={cx + 8} y={cy + 4} className="!fill-foreground">
                {r.name}
              </text>
            ) : null}
          </g>
        );
      })}
    </Frame>
  );
}

export { DRY, WET };
