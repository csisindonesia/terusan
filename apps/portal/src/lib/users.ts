import type { AccessEvent, User } from "~/lib/api";

/**
 * The three roles, in the order the Users page offers them.
 *
 * What each means is said here once, so the table, the forms and the account
 * page describe a role the same way.
 */
export const ROLES = [
  {
    value: "researcher",
    label: "Researcher",
    description: "Reads the warehouse and keeps collections. The ordinary account.",
  },
  {
    value: "guest",
    label: "Guest",
    description: "A researcher for a limited time. Access ends on the date you set.",
  },
  {
    value: "admin",
    label: "Admin",
    description: "Everything a researcher can do, plus creating and approving accounts.",
  },
] as const;

export function roleLabel(role: string): string {
  return ROLES.find((entry) => entry.value === role)?.label ?? role;
}

/** Where an account stands, in the words the table shows. */
export type UserState = "pending" | "disabled" | "expired" | "active";

export function userState(user: User, now = Date.now()): UserState {
  if (user.status === "pending") return "pending";
  if (user.disabled) return "disabled";
  if (
    user.role === "guest" &&
    user.access_expires_at &&
    Date.parse(user.access_expires_at) <= now
  ) {
    return "expired";
  }
  return "active";
}

export const STATE_LABELS: Record<UserState, string> = {
  pending: "Awaiting approval",
  disabled: "Disabled",
  expired: "Access ended",
  active: "Active",
};

/** A readable name for a person, falling back to the address. */
export function displayName(user: User): string {
  return user.name?.trim() || user.email;
}

/** The last time the account was seen, whichever of the two is later. */
export function lastSeen(user: User): string | undefined {
  const candidates = [user.last_active_at, user.last_login_at].filter(Boolean) as string[];
  return candidates.sort().at(-1);
}

const PRIVATE = [
  /^127\./,
  /^10\./,
  /^192\.168\./,
  /^172\.(1[6-9]|2\d|3[01])\./,
  /^::1$/,
  /^f[cd][0-9a-f]{2}:/i,
  /^fe80:/i,
];

/** Whether an address is this machine or the local network. */
export function isPrivateAddress(ip?: string): boolean {
  return !!ip && PRIVATE.some((pattern) => pattern.test(ip));
}

const regionNames =
  typeof Intl !== "undefined" && "DisplayNames" in Intl
    ? new Intl.DisplayNames(["en"], { type: "region" })
    : undefined;

/**
 * Where a request came from, as the proxy reported it.
 *
 * Cloudflare gives a country code always and a city only where its visitor
 * location headers are on; neither is there when the API is reached directly,
 * which on a laptop means the local network.
 */
export function formatLocation(event: AccessEvent): string {
  const country = event.country
    ? (regionNames?.of(event.country) ?? event.country)
    : undefined;
  const parts = [event.city, event.region, country].filter(Boolean);
  if (parts.length) return parts.join(", ");
  if (isPrivateAddress(event.ip)) return "Local network";
  return "Unknown";
}

export const ACCESS_KINDS: Record<string, string> = {
  login: "Signed in",
  web: "Portal",
  api: "API token",
};

/**
 * A date input's value (YYYY-MM-DD) as the end of that day, local time, so a
 * guest given "until 31 March" can still sign in on the 31st.
 */
export function endOfDay(date: string): string | undefined {
  if (!date) return undefined;
  const at = new Date(`${date}T23:59:59`);
  return Number.isNaN(at.getTime()) ? undefined : at.toISOString();
}

/** An ISO timestamp as a date input's value. */
export function toDateInput(value?: string): string {
  if (!value) return "";
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`;
}

/** A default end date for a new guest: thirty days from today. */
export function defaultGuestEnd(): string {
  return toDateInput(new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString());
}
