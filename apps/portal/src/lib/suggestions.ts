/**
 * The vocabulary of the "collect this too" queue.
 *
 * The values are the API's — it validates against the same closed lists — and
 * what lives here is only how to say them to a reader. Kept in one place
 * because the same words appear in the form that files a request and in the
 * list of what has already been asked for, and two spellings of "one-off"
 * would read as two different things.
 */

export const CADENCES = [
  "daily",
  "weekly",
  "monthly",
  "quarterly",
  "annual",
  "irregular",
  "one-off",
  "unknown",
] as const;

export type Cadence = (typeof CADENCES)[number];

const CADENCE_LABELS: Record<string, string> = {
  daily: "Daily",
  weekly: "Weekly",
  monthly: "Monthly",
  quarterly: "Quarterly",
  annual: "Annual",
  irregular: "Irregularly",
  "one-off": "One-off — published once",
  unknown: "I do not know",
};

export function cadenceLabel(value: string): string {
  return CADENCE_LABELS[value] ?? value;
}

const STATUS_LABELS: Record<string, string> = {
  open: "Open",
  planned: "Planned",
  ingested: "Ingested",
  declined: "Declined",
};

export function statusLabel(value: string): string {
  return STATUS_LABELS[value] ?? value;
}
