/**
 * Counts across a handful of short-named categories, as columns.
 *
 * Columns rather than horizontal bars because the categories are few and their
 * names are short — "API", "Scraped" — and a column chart reads as a
 * distribution at a glance where a list of bars reads as a ranking. Where the
 * names are long or the values span orders of magnitude, use `BarChart`.
 *
 * One hue: every column measures the same thing. The value sits on the cap
 * rather than in a tooltip alone, so the chart is readable in a screenshot.
 */

import { formatCount } from "~/lib/format";
import { GRID, MONO_DARK_SLOT, MONO_SLOTS } from "~/lib/viz";

export type Column = {
  label: string;
  value: number;
  /** A qualifier for the hover — what the category actually means. */
  note?: string;
};

export function ColumnChart({
  columns,
  unit,
  format = formatCount,
  emptyMessage = "Nothing to plot yet.",
}: {
  columns: Column[];
  unit?: string;
  format?: (value: number) => string;
  emptyMessage?: string;
}) {
  if (columns.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">{emptyMessage}</p>
    );
  }

  const ceiling = Math.max(...columns.map((column) => column.value), 1);
  const total = columns.reduce((sum, column) => sum + column.value, 0);

  return (
    <div
      className={`${MONO_DARK_SLOT} space-y-2`}
      style={{ ...MONO_SLOTS, "--viz-grid": GRID } as React.CSSProperties}
    >
      {/* The baseline every column grows from. A column chart without one asks
          the reader to guess where zero is. */}
      <ul className="flex items-end gap-2 border-b border-(--viz-grid) pb-px">
        {columns.map((column) => (
          <li
            key={column.label}
            className="group/column relative flex h-28 flex-1 flex-col justify-end"
          >
            <span className="mb-1 text-center text-xs tabular-nums text-muted-foreground">
              {format(column.value)}
            </span>
            {/* Capped rather than filling the slot: the leftover is the air
                that keeps neighbouring columns from touching. */}
            <div
              className="mx-auto w-full max-w-6 rounded-t-[4px] bg-(--viz-bar)"
              style={{ height: `${(column.value / ceiling) * 100}%` }}
            />
            <div
              role="tooltip"
              className="pointer-events-none absolute -top-1 left-1/2 z-10 hidden -translate-x-1/2 -translate-y-full rounded-lg border bg-popover px-2.5 py-1.5 text-xs whitespace-nowrap shadow-sm group-hover/column:block"
            >
              <div className="font-medium">{column.label}</div>
              <div className="tabular-nums text-muted-foreground">
                {format(column.value)}
                {unit ? ` ${unit}` : ""} · {((column.value / total) * 100).toFixed(0)}%
                of {format(total)}
              </div>
              {column.note ? (
                <div className="text-muted-foreground">{column.note}</div>
              ) : null}
            </div>
          </li>
        ))}
      </ul>

      <ul className="flex gap-2">
        {columns.map((column) => (
          <li
            key={column.label}
            // Wrapped rather than truncated: "Bulk download" in a narrow cell
            // becomes "Bulk downl…", which names nothing.
            className="flex-1 text-center text-xs leading-tight text-balance text-muted-foreground"
          >
            {column.label}
          </li>
        ))}
      </ul>
    </div>
  );
}
