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
 * A timestamp as a date, in the reader's locale.
 *
 * Day precision: these are pipeline run times, and the hour a refresh happened
 * is rarely the question. `terusan catalog runs` has the detail.
 */
export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** How long ago, for a column where recency is the point. */
export function formatRelative(value?: string | null): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;

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
