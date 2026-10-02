/**
 * Served series lines, as the time-series chart draws them.
 *
 * The same shaping the indicator page does (see `linesFrom` there): every line
 * against one shared axis of periods, a missing figure kept as a gap with its
 * status rather than bridged, and a bucket that is a mean saying how many
 * figures it is the mean of.
 */

import type { Point, Series as Line } from "~/components/time-series-chart";
import type { SeriesLine, SeriesPoint } from "~/lib/api";
import { formatCount, statusLabel } from "~/lib/format";

export function periodsOf(series: SeriesLine[]): string[] {
  return [
    ...new Set(series.flatMap((line) => line.points.map((p) => p.period))),
  ].sort();
}

function pointStatus(point: SeriesPoint): string {
  const present = point.count - point.missing;
  if (point.value === null) return statusLabel("missing");
  if (point.count === 1) return statusLabel("ok");
  if (point.missing) {
    return `mean of ${formatCount(present)} of ${formatCount(point.count)} figures`;
  }
  return `mean of ${formatCount(point.count)} figures`;
}

export function linesFrom(series: SeriesLine[], fallbackName: string): Line[] {
  const periods = periodsOf(series);
  return series.map((line) => {
    const byPeriod = new Map(line.points.map((point) => [point.period, point]));
    return {
      name: line.member || fallbackName,
      points: periods.map((period): Point => {
        const point = byPeriod.get(period);
        if (!point || point.value === null) {
          return { label: period, value: null, status: statusLabel("missing") };
        }
        return {
          label: period,
          value: Number(point.value),
          status: pointStatus(point),
        };
      }),
    };
  });
}
