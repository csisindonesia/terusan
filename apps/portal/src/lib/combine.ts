/**
 * Putting several queries' figures into one table.
 *
 * A saved query answers one question. The questions worth asking next are
 * comparisons — this province against that one, the policy rate against the
 * inflation it is set for — and answering them today means running two queries
 * and lining the periods up by hand in a spreadsheet.
 *
 * Aligning is not concatenating. Two queries rarely return the same periods:
 * one is monthly from 2015, the other quarterly from 2019. The union of their
 * periods, with a hole where a query has nothing to say, is the only shape
 * that can be read across — and a hole is left as a hole rather than filled
 * with a zero, because a period that was never collected is not a period of
 * nothing.
 *
 * Nothing here converts units. Two series in different units can be aligned
 * and printed side by side, and the caller is told they disagree so it can say
 * so; silently plotting rupiah against percent on one axis would be a lie the
 * chart tells convincingly.
 */

import type { Observation } from "~/lib/api";

/** One query's answer, named as the reader named the query. */
export type CombineSource = {
  key: string;
  name: string;
  rows: Observation[];
};

/**
 * What to do when a query returns several figures for one period.
 *
 * It usually does: a query over "every province" returns thirty-four rows a
 * month, and a column of a combined table holds one number. Which number is a
 * decision about meaning, not a formatting choice, so it is the reader's to
 * make and the table states the answer beside the column.
 */
export type Aggregate = "mean" | "sum" | "min" | "max" | "count";

export const AGGREGATES: { value: Aggregate; label: string; hint: string }[] = [
  { value: "mean", label: "Mean", hint: "Average of the figures in that period" },
  { value: "sum", label: "Sum", hint: "Total — only meaningful for quantities" },
  { value: "min", label: "Minimum", hint: "The lowest figure in that period" },
  { value: "max", label: "Maximum", hint: "The highest figure in that period" },
  { value: "count", label: "Count", hint: "How many figures the query returned" },
];

export type CombinedColumn = {
  key: string;
  name: string;
  /** The units the query's rows carry — more than one is worth showing. */
  units: string[];
  /** One value per period of the alignment, in the same order. */
  values: (number | null)[];
  /** How many periods held more than one figure and were folded into one. */
  folded: number;
  /** How many of the periods this query has any figure for. */
  present: number;
};

export type Combined = {
  /** Every period any query answered for, oldest first. */
  periods: string[];
  columns: CombinedColumn[];
  /** Set when the columns are not all in the same unit. */
  mixedUnits: boolean;
};

function fold(values: number[], aggregate: Aggregate): number {
  switch (aggregate) {
    case "sum":
      return values.reduce((total, value) => total + value, 0);
    case "min":
      return Math.min(...values);
    case "max":
      return Math.max(...values);
    case "count":
      return values.length;
    default:
      return values.reduce((total, value) => total + value, 0) / values.length;
  }
}

/**
 * The period label a figure is filed under, and what it sorts by.
 *
 * The label is what a reader recognises — `2026-Q1` — and it is not sortable:
 * `2026-Q1` and `2026-01` interleave wrongly as text. `period_start` is a
 * bounded date and sorts correctly across every resolution, so it is the key
 * and the label is what gets printed.
 */
function sortKey(row: Observation): string {
  return row.period_start || row.period;
}

export function combineByPeriod(
  sources: CombineSource[],
  aggregate: Aggregate = "mean",
): Combined {
  // Built from every source at once so the axis is the union: a query that
  // starts late must not truncate the table for one that does not.
  const order = new Map<string, string>();
  for (const source of sources) {
    for (const row of source.rows) order.set(row.period, sortKey(row));
  }

  const periods = [...order.entries()]
    .sort(([labelA, keyA], [labelB, keyB]) =>
      keyA === keyB ? labelA.localeCompare(labelB) : keyA.localeCompare(keyB),
    )
    .map(([label]) => label);

  const columns = sources.map((source) => {
    const byPeriod = new Map<string, number[]>();
    const units = new Set<string>();

    for (const row of source.rows) {
      if (row.unit) units.add(row.unit);
      // A row with no value is a period the query reached and found nothing
      // in — counted as absent rather than as a zero.
      if (row.value === null) continue;
      const value = Number(row.value);
      if (!Number.isFinite(value)) continue;
      const held = byPeriod.get(row.period);
      if (held) held.push(value);
      else byPeriod.set(row.period, [value]);
    }

    let folded = 0;
    let present = 0;
    const values = periods.map((period) => {
      const held = byPeriod.get(period);
      if (!held?.length) return null;
      present += 1;
      if (held.length > 1) folded += 1;
      return fold(held, aggregate);
    });

    return {
      key: source.key,
      name: source.name,
      units: [...units].sort(),
      values,
      folded,
      present,
    };
  });

  const units = new Set(columns.flatMap((column) => column.units));

  return { periods, columns, mixedUnits: units.size > 1 };
}

/**
 * Every row of every query, one under the other, tagged with where it came
 * from.
 *
 * The other half of combining: aligning answers "how do these compare", and
 * stacking answers "give me all of it" — which is what a download for further
 * work in R or a spreadsheet actually wants. The query's name rides on each
 * row, because a concatenated file whose rows cannot be attributed is worse
 * than two files.
 */
export type StackedRow = Observation & { query: string };

export function stackRows(sources: CombineSource[]): StackedRow[] {
  return sources.flatMap((source) =>
    source.rows.map((row) => ({ ...row, query: source.name })),
  );
}

/** The aligned table as CSV: a period column, then one column per query. */
export function combinedCsv(combined: Combined): string {
  const quote = (value: unknown) =>
    value === null || value === undefined
      ? ""
      : `"${String(value).replace(/"/g, '""')}"`;

  const head = ["period", ...combined.columns.map((column) => column.name)]
    .map(quote)
    .join(",");

  const body = combined.periods.map((period, index) =>
    [
      quote(period),
      // Not formatted: a CSV is read by a machine, and a thousands separator
      // is what makes a spreadsheet import a number as text.
      ...combined.columns.map((column) => quote(column.values[index])),
    ].join(","),
  );

  return [head, ...body].join("\n");
}
