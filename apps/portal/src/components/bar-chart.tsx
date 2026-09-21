/**
 * Ranked magnitudes as horizontal bars.
 *
 * Horizontal rather than columns because these categories carry long names —
 * "Regulation sections", "PIHPS — Pusat Informasi Harga Pangan Strategis" —
 * and a column chart would either clip them or turn them on their side.
 *
 * One hue, not the categorical order: every bar here measures the same thing,
 * and a second hue would claim a distinction that does not exist. The hue is
 * the validated first categorical slot, which clears the lightness, chroma and
 * contrast checks against both the light and the dark card surface.
 *
 * Drawn in HTML rather than SVG. The marks are rectangles in a list, and HTML
 * reflows them inside a bento cell without the viewBox arithmetic an SVG would
 * need to keep its labels legible at a third of the page's width.
 */

import { formatCount } from "~/lib/format";
import { MONO_DARK_SLOT, MONO_SLOTS } from "~/lib/viz";

export type Bar = {
  label: string;
  value: number;
  /** A qualifier shown beside the label — a layer, a frequency, a source. */
  note?: string;
};

export function BarChart({
  bars,
  /**
   * What the bars are measured against. Defaults to the largest bar, so the
   * chart fills its cell; pass a total to make the bars read as shares.
   */
  max,
  /** Absent where the reader would not thank you for another unit name. */
  unit,
  format = formatCount,
  emptyMessage = "Nothing to plot yet.",
}: {
  bars: Bar[];
  max?: number;
  unit?: string;
  format?: (value: number) => string;
  emptyMessage?: string;
}) {
  if (bars.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">{emptyMessage}</p>
    );
  }

  const ceiling = max ?? Math.max(...bars.map((bar) => bar.value), 1);
  const total = bars.reduce((sum, bar) => sum + bar.value, 0);

  return (
    <ul className={`space-y-2.5 ${MONO_DARK_SLOT}`} style={MONO_SLOTS}>
      {bars.map((bar) => {
        // Exactly proportional, with no minimum width. These tables span 22
        // rows to fourteen million, and widening the small ones to "make them
        // visible" would show the reader a quantity nobody measured. The value
        // sits beside the label instead, so a bar too thin to read is never
        // the only place the figure appears.
        const width = ceiling > 0 ? (bar.value / ceiling) * 100 : 0;
        const share = total > 0 ? (bar.value / total) * 100 : 0;

        return (
          <li key={bar.label} className="group/bar relative">
            <div className="flex items-baseline justify-between gap-3 text-xs">
              <span className="truncate text-foreground" title={bar.label}>
                {bar.label}
              </span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {format(bar.value)}
              </span>
            </div>

            {/* The track is the scale, so it stays one step off the surface —
                recessive, the way a gridline is. */}
            <div className="mt-1 h-2 w-full overflow-hidden rounded-[2px] bg-foreground/[0.06]">
              <div
                className="h-full rounded-r-[4px] bg-(--viz-bar) transition-[width] duration-300"
                style={{ width: `${width}%` }}
              />
            </div>

            {/* Per-mark hover, as an HTML chart should have. CSS rather than
                React state: the row is its own trigger, so there is nothing to
                track and no way for two rows to disagree about who is hovered. */}
            <div
              role="tooltip"
              className="pointer-events-none absolute -top-1 right-0 z-10 hidden -translate-y-full rounded-lg border bg-popover px-2.5 py-1.5 text-xs whitespace-nowrap shadow-sm group-hover/bar:block"
            >
              <div className="font-medium">{bar.label}</div>
              <div className="tabular-nums text-muted-foreground">
                {format(bar.value)}
                {unit ? ` ${unit}` : ""} · {share.toFixed(share < 1 ? 2 : 1)}% of{" "}
                {format(total)}
              </div>
              {bar.note ? (
                <div className="text-muted-foreground">{bar.note}</div>
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
