/**
 * The request on an indicator's API tab, rewritten for Excel.
 *
 * Excel and Power BI read the warehouse through its OData feed
 * (`/v1/odata`, docs/excel-odata.md), where a dataset is one table and a
 * series is the rows of it with one `IndicatorID`. The filters a reader picked
 * on the page are the JSON API's query parameters; this turns the same
 * filters into the feed's columns, twice over: as a `$filter` on the feed's
 * URL, for Excel's "From OData Feed" dialog, and as a `Table.SelectRows` step
 * in Power Query's own language, which Power Query sends to the warehouse
 * rather than applying after downloading everything.
 *
 * Paging and ordering are left behind on purpose: a workbook takes the whole
 * series, page by page, and sorts it after loading.
 */

import { apiBaseUrl } from "./api";

type Pair = { key: string; value: string };

/** A JSON API parameter, as the feed's column and how it is compared. */
const COLUMNS: Record<
  string,
  { column: string; op: "eq" | "ge" | "le"; year?: boolean } | undefined
> = {
  indicator: { column: "IndicatorID", op: "eq" },
  geo: { column: "GeoID", op: "eq" },
  geo_name: { column: "GeoName", op: "eq" },
  geo_type: { column: "GeoType", op: "eq" },
  commodity: { column: "CommodityName", op: "eq" },
  category: { column: "Category", op: "eq" },
  status: { column: "Status", op: "eq" },
  year: { column: "PeriodStart", op: "eq", year: true },
  // Compared as published labels, as the JSON API compares them: `2026-Q1`
  // and `2026-01` both sort correctly as text.
  period_start: { column: "Period", op: "ge" },
  period_end: { column: "Period", op: "le" },
};

/** Parameters with no column to carry them, which the tab says it dropped. */
const IGNORED = new Set(["order", "limit", "offset", "after"]);

type Condition = {
  column: string;
  op: "eq" | "ge" | "le";
  year?: boolean;
  values: string[];
};

function conditions(query: Pair[]): { conditions: Condition[]; dropped: string[] } {
  const grouped = new Map<string, Condition>();
  const dropped: string[] = [];
  for (const { key, value } of query) {
    if (IGNORED.has(key)) continue;
    const column = COLUMNS[key];
    if (!column) {
      if (!dropped.includes(key)) dropped.push(key);
      continue;
    }
    const held = grouped.get(key);
    if (held) held.values.push(value);
    else grouped.set(key, { ...column, values: [value] });
  }
  return { conditions: [...grouped.values()], dropped };
}

const odataString = (value: string) => `'${value.replaceAll("'", "''")}'`;
const mString = (value: string) => `"${value.replaceAll('"', '""')}"`;

/** The filters as an OData `$filter` expression. */
export function odataFilter(query: Pair[]): string {
  return conditions(query)
    .conditions.map((condition) => {
      const terms = condition.values.map((value) =>
        condition.year
          ? `year(${condition.column}) eq ${Number(value)}`
          : `${condition.column} ${condition.op} ${odataString(value)}`,
      );
      return terms.length > 1 ? `(${terms.join(" or ")})` : terms[0];
    })
    .join(" and ");
}

/** The address of the feed, narrowed to the filters. */
export function odataSeriesUrl(set: string, query: Pair[]): string {
  const url = `${apiBaseUrl}/v1/odata/${encodeURIComponent(set)}`;
  const filter = odataFilter(query);
  return filter ? `${url}?$filter=${encodeURIComponent(filter)}` : url;
}

const M_OPERATORS = { eq: "=", ge: ">=", le: "<=" } as const;

/**
 * A Power Query query that loads the series, for Excel's Advanced Editor.
 *
 * The filter is a step after the feed rather than a `$filter` in its URL, so
 * a reader can see it and edit it in the editor; Power Query folds it back
 * into the request either way.
 */
export function powerQuery(set: string, query: Pair[]): string {
  const terms = conditions(query).conditions.map((condition) => {
    const field = condition.year
      ? `Date.Year([${condition.column}])`
      : `[${condition.column}]`;
    const parts = condition.values.map(
      (value) =>
        `${field} ${M_OPERATORS[condition.op]} ${condition.year ? Number(value) : mString(value)}`,
    );
    return parts.length > 1 ? `(${parts.join(" or ")})` : parts[0];
  });
  const lines = [
    "let",
    `    Source = OData.Feed(${mString(`${apiBaseUrl}/v1/odata/${set}`)}),`,
  ];
  if (terms.length) {
    lines.push(
      "    Figures = Table.SelectRows(",
      "        Source,",
      `        each ${terms.join("\n            and ")}`,
      "    )",
    );
  } else {
    lines.push("    Figures = Source");
  }
  lines.push("in", "    Figures");
  return lines.join("\n");
}

/** Which of the page's filters the feed cannot carry. */
export function odataDropped(query: Pair[]): string[] {
  return conditions(query).dropped;
}
