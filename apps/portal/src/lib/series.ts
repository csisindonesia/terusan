import type { Observation } from "~/lib/api";

/**
 * Which series a figure belongs to.
 *
 * An indicator is rarely one line. `consumer_confidence_by_city` is eighteen
 * cities, `food_price_traditional` is thirty-one commodities across 34
 * provinces, and what separates one row from another is the dimension the
 * indicator varies along — a place for one, a commodity for another, both at
 * once for the third, neither for a single national series.
 *
 * Plotting the rows without splitting them first draws a sawtooth between
 * unrelated things: rice, then chilli, then shallots, joined by a line that
 * says they are one quantity changing over time.
 */

export type Dimension = "geography" | "commodity" | "both" | "none";

/**
 * What joins a place to a commodity in a composite series label.
 *
 * A middle dot rather than a dash: the commodity names already carry dashes
 * — "Rice — low grade I" — and a second one would read as part of the name.
 */
const JOIN = " · ";

export type Series = {
  /** Stable key for selection and URLs. */
  key: string;
  /** What to print — the place, or the commodity as the source named it. */
  label: string;
  count: number;
};

/** The place a figure is about, as the source named it. */
export function geoOf(row: Observation): string | null {
  return row.geo_name ?? row.geo_id ?? null;
}

/** The commodity a figure is about, as the registry or the source named it. */
export function commodityOf(row: Observation): string | null {
  return row.commodity_name ?? row.commodity_id ?? null;
}

/**
 * The dimension an indicator's rows actually vary along.
 *
 * On how many distinct members there are, not on whether the column is filled.
 * Presence was the old test and it read the commonest case backwards: Bank
 * Indonesia's food prices carry a place on every row — `Indonesia`, one value
 * for all of them — and thirty-one commodities, so a test that stopped at the
 * first non-empty geography called it a series about places and never showed
 * the commodity. Every row then looked alike, which is what it reports as.
 *
 * Both can vary at once. Priced across 34 provinces and 31 commodities, a
 * figure is not identified by either alone, and splitting on one of them draws
 * a line through rice in Aceh and rice in Papua as though they were one thing.
 */
export function dimensionOf(rows: Observation[]): Dimension {
  const places = new Set<string>();
  const commodities = new Set<string>();
  for (const row of rows) {
    const place = geoOf(row);
    if (place !== null) places.add(place);
    const commodity = commodityOf(row);
    if (commodity !== null) commodities.add(commodity);
  }

  const byPlace = places.size > 1;
  const byCommodity = commodities.size > 1;
  if (byPlace && byCommodity) return "both";
  if (byPlace) return "geography";
  if (byCommodity) return "commodity";

  // Varies along neither, but still about something. A single national series
  // keeps its one-place column — the alternative is dropping the only column
  // that says what the figures are about.
  if (places.size === 1) return "geography";
  if (commodities.size === 1) return "commodity";
  return "none";
}

export function memberOf(row: Observation, dimension: Dimension): string | null {
  if (dimension === "geography") return geoOf(row);
  if (dimension === "commodity") return commodityOf(row);
  if (dimension === "both") {
    const place = geoOf(row);
    const commodity = commodityOf(row);
    if (place === null && commodity === null) return null;
    return `${place ?? "—"}${JOIN}${commodity ?? "—"}`;
  }
  return null;
}

/** Every distinct member, largest series first — the default is the fullest. */
export function seriesOf(rows: Observation[], dimension: Dimension): Series[] {
  if (dimension === "none") return [];
  const counts = new Map<string, number>();
  for (const row of rows) {
    const member = memberOf(row, dimension);
    if (member === null) continue;
    counts.set(member, (counts.get(member) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([key, count]) => ({ key, label: key, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, "id"));
}

/**
 * The axis the main narrowing control filters on.
 *
 * Where both vary, that is the place, and the commodity gets a control of its
 * own beside it. Two independent filters rather than one over every pairing:
 * 34 provinces and 31 commodities is a list of 1,054 entries, and a reader
 * after the price of shallots should not have to find shallots 34 times in it.
 */
export function primaryAxis(dimension: Dimension): Axis | null {
  if (dimension === "commodity") return "commodity";
  if (dimension === "geography" || dimension === "both") return "geography";
  return null;
}

export type Axis = "geography" | "commodity";

/** One row's member on a given axis. */
export function axisMemberOf(row: Observation, axis: Axis): string | null {
  return axis === "geography" ? geoOf(row) : commodityOf(row);
}

/** What to head the dimension column with. */
export function dimensionLabel(dimension: Dimension): string {
  return dimension === "commodity" ? "Commodity" : "Place";
}

/** What to call the members in prose — "across 31 commodities". */
export function dimensionNoun(dimension: Dimension, count: number): string {
  const plural = count !== 1;
  if (dimension === "commodity") return plural ? "commodities" : "commodity";
  if (dimension === "both") return plural ? "series" : "series";
  return plural ? "places" : "place";
}

/**
 * The calendar year a figure falls in.
 *
 * Read off `period_start` rather than the label, because the label's shape
 * depends on the resolution — `2011`, `2012-01`, `2026-Q1`, `2026-09-10` — and
 * only the bounded start date means the same thing across all of them. The
 * label is the fallback for a row whose start date did not survive the wire.
 */
export function yearOf(row: Observation): string {
  return (row.period_start || row.period).slice(0, 4);
}

/** Every year present, most recent first — a reader looks for the latest. */
export function yearsOf(rows: Observation[]): Series[] {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const year = yearOf(row);
    if (!/^\d{4}$/.test(year)) continue;
    counts.set(year, (counts.get(year) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([key, count]) => ({ key, label: key, count }))
    .sort((a, b) => b.key.localeCompare(a.key));
}
