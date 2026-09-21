/**
 * Displaying figures without lying about them.
 */

/**
 * Format a decimal string for reading.
 *
 * Takes the string the API sent rather than a number: `Number()` on a
 * fourteen-digit GDP figure loses precision silently, and this is a research
 * warehouse. Grouping is applied to the integer part by hand, so nothing goes
 * through a float on the way to the screen.
 */
export function formatDecimal(value: string | null, maximumFractionDigits = 2): string {
  if (value === null) return "—";

  const negative = value.startsWith("-");
  const unsigned = negative ? value.slice(1) : value;
  const [whole = "0", fraction = ""] = unsigned.split(".");

  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const trimmed = fraction.replace(/0+$/, "").slice(0, maximumFractionDigits);

  return `${negative ? "-" : ""}${grouped}${trimmed ? `.${trimmed}` : ""}`;
}

/** Compact form for large figures, where the exact digits are not the point. */
export function formatCompact(value: string | null): string {
  if (value === null) return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return formatDecimal(value);

  const units: [number, string][] = [
    [1e12, "T"],
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ];
  const magnitude = Math.abs(numeric);
  for (const [size, suffix] of units) {
    if (magnitude >= size) return `${(numeric / size).toFixed(2)}${suffix}`;
  }
  return formatDecimal(value);
}

export function formatCount(value: number): string {
  return value.toLocaleString("en-US");
}

/**
 * A file size a reader can judge at a glance.
 *
 * Binary units, because that is what a file manager shows and a reader
 * comparing the two should not find them disagreeing. One decimal place past
 * a kilobyte: "10.5 MB" is the useful precision, "10.46 MB" is not.
 */
export function formatBytes(value?: number | null): string {
  if (value === undefined || value === null) return "—";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = value / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(1)} ${units[unit]}`;
}

/**
 * How a missing value should read.
 *
 * "Not collected" and "collected and zero" are different facts, so each status
 * gets its own word rather than all of them rendering as a blank cell.
 */
export const STATUS_LABELS: Record<string, string> = {
  ok: "OK",
  missing: "Not collected",
  suppressed: "Withheld",
  not_applicable: "Not applicable",
  provisional: "Provisional",
  unparseable: "Unreadable",
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/**
 * A timestamp from either shape the API sends.
 *
 * Indicators carry ISO-8601 — `2026-09-17T06:16:06Z` — while datasets carry
 * PostgreSQL's own rendering, `2026-09-17 06:16:08.365451+07`: a space instead
 * of the T, and a bare-hour offset. V8 happens to accept that; the spec does
 * not, so a browser is free to return Invalid Date and print the raw string at
 * the reader. Normalized here, once, rather than at each call site.
 */
export function parseTimestamp(value: string): Date | undefined {
  const normalized = value.replace(" ", "T").replace(/([+-]\d{2})$/, "$1:00");
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? undefined : date;
}

/**
 * A timestamp as a date, in the reader's locale.
 *
 * Day precision: these are pipeline run times, and the hour a refresh happened
 * is rarely the question. `terusan catalog runs` has the detail.
 */
export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = parseTimestamp(value);
  if (!date) return value;
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** How long ago, for a column where recency is the point. */
export function formatRelative(value?: string | null): string | undefined {
  if (!value) return undefined;
  const date = parseTimestamp(value);
  if (!date) return undefined;

  const days = Math.floor((Date.now() - date.getTime()) / 86_400_000);
  if (days < 0) return undefined;
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} month${months === 1 ? "" : "s"} ago`;
  const years = Math.floor(days / 365);
  return `${years} year${years === 1 ? "" : "s"} ago`;
}

/** A signed percentage, for a change that can go either way. */
export function formatPercent(value: number | null, digits = 1): string {
  if (value === null || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

/**
 * An instant to the minute, for a run history.
 *
 * `formatDate` is day precision, which is right for a release date and wrong
 * here: two runs on one day are the common case, and "which came first" is the
 * whole question.
 */
export function formatMoment(value?: string | null): string {
  if (!value) return "—";
  const date = parseTimestamp(value);
  if (!date) return value;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** How long something took, at the precision the number deserves. */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds)) return "—";
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
