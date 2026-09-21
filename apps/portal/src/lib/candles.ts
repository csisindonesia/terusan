import type { Observation } from "~/lib/api";
import type { Candle } from "~/components/candlestick-chart";

/**
 * Assembling candles out of four series.
 *
 * Silver stores one figure per observation, so an OHLC bar is four rows sharing
 * a period rather than one row with four columns. That is the right shape for
 * the warehouse — every other series is one figure too — and the reassembly
 * belongs here, at the point where something wants to draw a candle.
 */

/** The suffixes a dataset must carry for its series to form a candle. */
export const OHLC = ["open", "high", "low", "close"] as const;

export type OhlcField = (typeof OHLC)[number];

/** A dataset's four price series: what they are called, and what to ask for. */
export type OhlcSet = {
  /** The shared key the four series are named under — `ihsg`. */
  prefix: string;
  /** The identifiers to query, in OHLC order. */
  ids: string[];
  /** Which of the four each identifier is. */
  field: Record<string, OhlcField>;
};

/** What a series is recognised by here: its readable key, not its identifier. */
type Named = { indicator_id: string; slug?: string };

/**
 * A series' key, which is what says whether it is an open or a close.
 *
 * Deliberately not the identifier. A published identifier is a derived code —
 * `ml03my8u` — and carries no suffix to match (program.md §10), so matching on
 * it found nothing and the chart quietly disappeared. The key is what the
 * mapping declared, and it survives recoding. Older lakes have no key at all,
 * and there the identifier still is the readable one.
 */
function key(series: Named): string {
  return series.slug ?? series.indicator_id;
}

/**
 * Whether these series include a complete OHLC set, and which they are.
 *
 * Detected rather than declared: nothing in the warehouse marks a series as a
 * price, and four series named `<prefix>_open|high|low|close` are what a candle
 * is made of.
 */
export function ohlcSet(series: Named[]): OhlcSet | null {
  for (const candidate of series) {
    const match = /^(.*)_open$/.exec(key(candidate));
    if (!match) continue;
    const prefix = match[1] as string;

    const found = OHLC.map((field) =>
      series.find((entry) => key(entry) === `${prefix}_${field}`),
    );
    if (found.some((entry) => entry === undefined)) continue;

    const ids = found.map((entry) => (entry as Named).indicator_id);
    return {
      prefix,
      ids,
      field: Object.fromEntries(ids.map((id, index) => [id, OHLC[index] as OhlcField])),
    };
  }
  return null;
}

/**
 * Rows into bars, in period order.
 *
 * A period missing any of the four is kept with nulls rather than dropped: the
 * chart leaves it blank, which says "no session" where dropping it would close
 * the gap and quietly shorten the year.
 */
export function toCandles(rows: Observation[], set: OhlcSet): Candle[] {
  const byPeriod = new Map<string, Candle>();

  for (const row of rows) {
    const field = set.field[row.indicator_id];
    if (!field) continue;

    const candle =
      byPeriod.get(row.period) ??
      ({ label: row.period, open: null, high: null, low: null, close: null } as Candle);
    candle[field] = row.value === null ? null : Number(row.value);
    byPeriod.set(row.period, candle);
  }

  return [...byPeriod.values()].sort((a, b) => a.label.localeCompare(b.label));
}

/**
 * Whether these bars are worth drawing as candles.
 *
 * A candle earns its extra ink from the range within a session. Some contracts
 * have none: the CME's palm oil calendar and the API2 coal swap *settle* once a
 * day rather than trading, so the open, high, low and close are one number
 * repeated — 99% of palm oil sessions have high equal to low. Drawn as candles
 * they become a row of dashes that look like bars and encode nothing, which is
 * worse than a line because it implies a range the figures do not have.
 */
export function hasIntradayRange(candles: Candle[]): boolean {
  const priced = candles.filter(
    (candle) => candle.high !== null && candle.low !== null,
  );
  if (!priced.length) return false;
  const ranged = priced.filter((candle) => candle.high !== candle.low);
  return ranged.length / priced.length >= 0.5;
}
