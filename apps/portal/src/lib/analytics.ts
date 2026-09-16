/**
 * Summary statistics over one series.
 *
 * Computed here rather than by the API because these are questions about the
 * rows already on screen, and a second round trip to answer them would let the
 * figures and their summary disagree.
 *
 * Every function ignores absent values rather than treating them as zero: a
 * year that was never collected is not a year of nothing.
 */

export type Figure = {
  period: string;
  value: number | null;
  status: string;
};

export type SeriesSummary = {
  count: number;
  present: number;
  missing: number;
  first?: Figure;
  latest?: Figure;
  min?: Figure;
  max?: Figure;
  /** Change from the first recorded figure to the latest, as a percentage. */
  totalChange: number | null;
  /** Compound annual growth, where the series is annual and spans a range. */
  cagr: number | null;
};

export function summarise(figures: Figure[]): SeriesSummary {
  const present = figures.filter((figure) => figure.value !== null);

  const summary: SeriesSummary = {
    count: figures.length,
    present: present.length,
    missing: figures.length - present.length,
    totalChange: null,
    cagr: null,
  };

  if (!present.length) return summary;

  summary.first = present[0];
  summary.latest = present[present.length - 1];
  summary.min = present.reduce((a, b) =>
    (b.value as number) < (a.value as number) ? b : a,
  );
  summary.max = present.reduce((a, b) =>
    (b.value as number) > (a.value as number) ? b : a,
  );

  const from = summary.first?.value ?? null;
  const to = summary.latest?.value ?? null;

  // A ratio against zero or a negative base is not a percentage anyone can
  // read, so it is left unstated rather than rendered as infinity.
  if (from !== null && to !== null && from > 0) {
    summary.totalChange = ((to - from) / from) * 100;

    const years = yearsBetween(summary.first!.period, summary.latest!.period);
    if (years >= 1 && to > 0) {
      summary.cagr = ((to / from) ** (1 / years) - 1) * 100;
    }
  }

  return summary;
}

/** Whole years between two canonical period labels, or 0 if either is unreadable. */
function yearsBetween(from: string, to: string): number {
  const start = Number(from.slice(0, 4));
  const end = Number(to.slice(0, 4));
  if (!Number.isFinite(start) || !Number.isFinite(end)) return 0;
  return Math.max(0, end - start);
}

/** Absent figures grouped by why, for a coverage note that says more than a count. */
export function gapsByStatus(figures: Figure[]): { status: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const figure of figures) {
    if (figure.value !== null) continue;
    counts.set(figure.status, (counts.get(figure.status) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([status, count]) => ({ status, count }))
    .sort((a, b) => b.count - a.count);
}
