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
export function dimensionOfMembers(places: number, commodities: number): Dimension {
  const byPlace = places > 1;
  const byCommodity = commodities > 1;
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
  if (dimension === "geography" || dimension === "both") return "geography";
  return null;
}

export type Axis = "geography" | "commodity";

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
