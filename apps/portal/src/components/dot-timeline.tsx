/**
 * When things last happened, as dots on a time axis.
 *
 * A list of dates says which collection is freshest; this says whether the
 * warehouse is being kept up at all — a cluster at the right edge with a long
 * empty stretch behind it reads differently from dots spread evenly, and
 * neither reads at all in a column of timestamps.
 *
 * One hue, because every dot is the same kind of event. Each carries a 2px
 * ring in the surface colour so that dots landing on the same hour stay
 * countable where they overlap.
 */

import { formatDate, formatRelative } from "~/lib/format";
import { GRID, MONO_DARK_SLOT, MONO_SLOTS } from "~/lib/viz";

export type Moment = {
  label: string;
  /** Milliseconds since the epoch — parsed by the caller, which knows the shape. */
  at: number;
  /** The timestamp as the API sent it, for the hover. */
  raw?: string;
};

export function DotTimeline({
  moments,
  emptyMessage = "Nothing has run yet.",
}: {
  moments: Moment[];
  emptyMessage?: string;
}) {
  if (moments.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">{emptyMessage}</p>
    );
  }

  const now = Date.now();
  const earliest = Math.min(...moments.map((moment) => moment.at));
  // A single refresh, or several within the same instant, has no span to plot
  // against. Give the axis a day so the dots sit at the end of a real scale
  // rather than dividing by zero.
  const span = Math.max(now - earliest, 86_400_000);

  return (
    <div
      className={`${MONO_DARK_SLOT} space-y-2`}
      style={{ ...MONO_SLOTS, "--viz-grid": GRID } as React.CSSProperties}
    >
      <div className="relative h-10">
        {/* The axis, hairline and recessive: it carries position, not weight. */}
        <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-(--viz-grid)" />

        {moments.map((moment, index) => {
          const offset = ((moment.at - earliest) / span) * 100;
          // Nightly pipelines land within hours of each other, so dots collide
          // on the axis. Alternate rows spread them the way a beeswarm does:
          // the vertical position carries nothing, and two marks side by side
          // are countable where one drawn on top of the other is not.
          const row = index % 2 === 0 ? -6 : 6;
          return (
            <div
              key={moment.label}
              className="group/dot absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
              // Inset so a dot at either end keeps its whole mark on the card.
              style={{
                left: `calc(${Math.min(Math.max(offset, 0), 100)}% * 0.94 + 3%)`,
                marginTop: `${row}px`,
              }}
            >
              <span className="block size-2.5 rounded-full bg-(--viz-bar) ring-2 ring-card" />
              <div
                role="tooltip"
                className="pointer-events-none absolute -top-2 left-1/2 z-10 hidden -translate-x-1/2 -translate-y-full rounded-lg border bg-popover px-2.5 py-1.5 text-xs whitespace-nowrap shadow-sm group-hover/dot:block"
              >
                <div className="font-medium">{moment.label}</div>
                <div className="text-muted-foreground">
                  {formatDate(moment.raw ?? new Date(moment.at).toISOString())}
                  {" · "}
                  {formatRelative(moment.raw ?? new Date(moment.at).toISOString()) ??
                    "unknown"}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{formatDate(new Date(earliest).toISOString())}</span>
        <span>now</span>
      </div>
    </div>
  );
}
