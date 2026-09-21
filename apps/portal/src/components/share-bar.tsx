/**
 * Part-to-whole, as one stacked bar with a legend.
 *
 * The question this answers is "who holds the figures", which is a share
 * question rather than a ranking one — so the whole is drawn once and cut up,
 * rather than as separate bars the reader has to add together.
 *
 * Segments are separated by a 2px gap in the surface colour rather than by a
 * stroke: the gap is what makes two neighbouring hues read as distinct, and a
 * border would add ink that carries no data. Every segment is named and
 * counted in the legend below, which is also what relieves the lighter steps'
 * contrast against a white card — identity is never colour alone.
 */

import { formatCount } from "~/lib/format";
import {
  MAX_SERIES,
  SERIES_DARK_SLOTS,
  SERIES_LIGHT_SLOTS,
  seriesColor,
} from "~/lib/viz";

export type Slice = {
  label: string;
  value: number;
};

export function ShareBar({
  slices,
  unit,
  format = formatCount,
  emptyMessage = "Nothing to plot yet.",
}: {
  slices: Slice[];
  unit?: string;
  format?: (value: number) => string;
  emptyMessage?: string;
}) {
  if (slices.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">{emptyMessage}</p>
    );
  }

  // Past the palette a hue would have to repeat, so the tail is folded into one
  // named segment instead. "Other" is honest; a second blue is not.
  const ranked = [...slices].sort((a, b) => b.value - a.value);
  const head = ranked.slice(0, MAX_SERIES - 1);
  const tail = ranked.slice(MAX_SERIES - 1);
  const shown =
    tail.length > 0
      ? [
          ...head,
          {
            label: `${tail.length} others`,
            value: tail.reduce((sum, slice) => sum + slice.value, 0),
          },
        ]
      : head;

  const total = shown.reduce((sum, slice) => sum + slice.value, 0) || 1;

  return (
    <div className={`space-y-3 ${SERIES_DARK_SLOTS}`} style={SERIES_LIGHT_SLOTS}>
      <div className="flex h-3 w-full gap-0.5 overflow-hidden">
        {shown.map((slice, slot) => (
          <div
            key={slice.label}
            className="group/slice relative h-full first:rounded-l-[2px] last:rounded-r-[2px]"
            style={{
              width: `${(slice.value / total) * 100}%`,
              background: seriesColor(slot),
            }}
          >
            <div
              role="tooltip"
              className="pointer-events-none absolute -top-2 left-0 z-10 hidden -translate-y-full rounded-lg border bg-popover px-2.5 py-1.5 text-xs whitespace-nowrap shadow-sm group-hover/slice:block"
            >
              <div className="font-medium">{slice.label}</div>
              <div className="tabular-nums text-muted-foreground">
                {format(slice.value)}
                {unit ? ` ${unit}` : ""} · {((slice.value / total) * 100).toFixed(1)}%
              </div>
            </div>
          </div>
        ))}
      </div>

      <ul className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
        {shown.map((slice, slot) => (
          <li key={slice.label} className="flex items-center gap-2">
            <span
              aria-hidden
              className="size-2 shrink-0 rounded-full"
              style={{ background: seriesColor(slot) }}
            />
            <span className="truncate" title={slice.label}>
              {slice.label}
            </span>
            <span className="ml-auto shrink-0 tabular-nums text-muted-foreground">
              {format(slice.value)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
