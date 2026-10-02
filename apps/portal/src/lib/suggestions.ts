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

/**
 * Where a request has got to, in the order a request moves through them.
 *
 * "planned" is said as "Accepted" because that is the decision a reader is
 * waiting to hear: an administrator has agreed to collect it, and the work has
 * not landed yet.
 */
export const STATUSES = ["open", "planned", "ingested", "declined"] as const;

const STATUS_LABELS: Record<string, string> = {
  open: "Waiting for review",
  planned: "Accepted",
  ingested: "Ingested",
  declined: "Declined",
};

export function statusLabel(value: string): string {
  return STATUS_LABELS[value] ?? value;
}

/** Why a request was declined. The API refuses a declined request without one. */
export const REASONS = [
  "already-collected",
  "out-of-scope",
  "unreachable",
  "other",
] as const;

const REASON_LABELS: Record<string, string> = {
  "already-collected": "Already in the warehouse",
  "out-of-scope": "Outside what this warehouse collects",
  unreachable: "Cannot be collected automatically",
  other: "Other",
};

export function reasonLabel(value: string): string {
  return REASON_LABELS[value] ?? value;
}
