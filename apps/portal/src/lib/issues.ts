/**
 * The register of data problems: what was wrong, and when it stopped being
 * wrong.
 *
 * Published rather than kept in a tracker because a warehouse that never
 * admits a bad figure is not more correct, only quieter — and a reader who
 * charted last month's numbers deserves to find out that they moved. Each
 * entry stays after it is fixed; the point is the record, not the backlog.
 *
 * Checked into the repository instead of served by the API: a correction is
 * part of the pipeline change that fixes it, so it lands in the same commit
 * and is reviewed with the code rather than typed into a form afterwards.
 * When the catalog grows an issues table, this file is what it is seeded from.
 */
export type DataIssue = {
  /** Short, stable, and quotable in a mail thread. */
  id: string;
  /** What a reader would have seen, in one line. */
  summary: string;
  /** The dataset, indicator or page affected. */
  scope: string;
  /** When it was reported or noticed, ISO date. */
  reported: string;
  /** Why it happened — usually a parse, a unit or a source change. */
  cause?: string;
  /** When the fixed figures were published, ISO date. Absent while open. */
  fixed?: string;
  /** What changed to fix it. */
  fix?: string;
};

/**
 * Empty until something is found to be wrong.
 *
 * An entry looks like this:
 *
 * ```ts
 * {
 *   id: "bi-retail-2024-03",
 *   summary: "March 2024 retail sales index read 1,234 instead of 12,340",
 *   scope: "bi-retail-sales-index",
 *   reported: "2026-03-04",
 *   cause: "A thousands separator in the source table was parsed as a decimal point",
 *   fixed: "2026-03-06",
 *   fix: "The parser now reads the release's declared locale; the series was reprocessed from RAW",
 * }
 * ```
 */
export const DATA_ISSUES: DataIssue[] = [];

/** Still wrong, newest first. */
export function openIssues(): DataIssue[] {
  return DATA_ISSUES.filter((issue) => !issue.fixed).sort((a, b) =>
    b.reported.localeCompare(a.reported),
  );
}

/** Corrected, most recently fixed first. */
export function fixedIssues(): DataIssue[] {
  return DATA_ISSUES.filter((issue): issue is DataIssue & { fixed: string } =>
    Boolean(issue.fixed),
  ).sort((a, b) => b.fixed.localeCompare(a.fixed));
}
