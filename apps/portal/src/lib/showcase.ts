/**
 * The vocabulary of the Portal page — the work built on the warehouse's data.
 *
 * The kinds are the API's, which validates against the same closed list; what
 * lives here is how to say them to a reader, once, so the filter, the badge on
 * each card and the form's select cannot spell them three ways.
 */

import {
  IconChartBar,
  IconFileText,
  IconNews,
  IconPaperclip,
  type Icon,
} from "@tabler/icons-react";

import { isPortalPath } from "~/lib/paths";

export const SHOWCASE_KINDS = ["dashboard", "article", "report", "other"] as const;

export type ShowcaseKind = (typeof SHOWCASE_KINDS)[number];

const LABELS: Record<string, string> = {
  dashboard: "Dashboard",
  article: "Article",
  report: "Report",
  other: "Other",
};

const PLURALS: Record<string, string> = {
  dashboard: "Dashboards",
  article: "Articles",
  report: "Reports",
  other: "Other",
};

const ICONS: Record<string, Icon> = {
  dashboard: IconChartBar,
  article: IconNews,
  report: IconFileText,
  other: IconPaperclip,
};

export function kindLabel(kind: string): string {
  return LABELS[kind] ?? kind;
}

export function kindPlural(kind: string): string {
  return PLURALS[kind] ?? kind;
}

export function kindIcon(kind: string): Icon {
  return ICONS[kind] ?? IconPaperclip;
}

/**
 * One of the Portal's own articles, filed by its path rather than a URL.
 *
 * Judged by the shared path check rather than a leading slash: a `/\host` an
 * administrator pasted would otherwise open another site in place, without
 * the `noopener` an outside link is given.
 */
export function isInternal(url: string): boolean {
  return isPortalPath(url);
}

/** The address's host, which is what a reader recognises a site by. */
export function hostOf(url: string): string {
  if (isInternal(url)) return "Terusan";
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}
