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

export type Dimension = "geography" | "commodity" | "both" | "category" | "none";

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

/** The row of a table broken down by something else — an age group, a sector. */
export function categoryOf(row: Observation): string | null {
  return row.category ?? null;
}

/**
 * The dimension an indicator varies along, from how many members it has.
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
 *
 * Counted in the warehouse rather than over the rows on screen: a series can be
 * larger than any one request, and the members of whatever page arrived answer
 * a different question.
 */
export function dimensionOfMembers(
  places: number,
  commodities: number,
  categories = 0,
): Dimension {
  const byPlace = places > 1;
  const byCommodity = commodities > 1;
  // Before the place: a table of age groups is national, and its one place
  // would otherwise claim it and draw sixteen groups as one line.
  if (categories > 1 && !byPlace && !byCommodity) return "category";
  if (byPlace && byCommodity) return "both";
  if (byPlace) return "geography";
  if (byCommodity) return "commodity";

  // Varies along neither, but still about something. A single national series
  // keeps its one-place column — the alternative is dropping the only column
  // that says what the figures are about.
  if (places === 1) return "geography";
  if (commodities === 1) return "commodity";
  return "none";
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
  if (dimension === "category") return "category";
  if (dimension === "geography" || dimension === "both") return "geography";
  return null;
}

export type Axis = "geography" | "commodity" | "category";

/** What to head the dimension column with. */
export function dimensionLabel(dimension: Dimension): string {
  if (dimension === "commodity") return "Commodity";
  if (dimension === "category") return "Category";
  return "Place";
}

/** What to call the members in prose — "across 31 commodities". */
export function dimensionNoun(dimension: Dimension, count: number): string {
  const plural = count !== 1;
  if (dimension === "commodity") return plural ? "commodities" : "commodity";
  if (dimension === "category") return plural ? "categories" : "category";
  if (dimension === "both") return plural ? "series" : "series";
  return plural ? "places" : "place";
}

/**
 * Categories in the order a table prints them, as near as their labels say.
 *
 * By leading number, then by name — the order the API gives the facet. Plain
 * text order reads age groups 10-14, 15-19, 5-9, and Silver keeps no record of
 * the row order the publisher used.
 */
export function compareCategories(a: string, b: string): number {
  const lead = (label: string) => {
    const match = /^\d+/.exec(label);
    return match ? Number(match[0]) : null;
  };
  const left = lead(a);
  const right = lead(b);
  if (left !== null && right !== null && left !== right) return left - right;
  if (left !== null && right === null) return -1;
  if (left === null && right !== null) return 1;
  return a.localeCompare(b);
}
