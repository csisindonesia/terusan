/**
 * The sidebar's two layers: sections holding groups, groups holding items.
 *
 * Everything the platform is meant to offer is listed, not only what is built.
 * A navigation that grows a row each time a feature lands tells nobody what the
 * platform is for; one that shows the whole shape, with the unbuilt parts
 * visibly unbuilt, is honest and orients a newcomer. Items without a `to` are
 * rendered as such rather than linking somewhere that 404s.
 */

import {
  IconApi,
  IconBinaryTree,
  IconBook,
  IconBox,
  IconBuilding,
  IconBuildingBank,
  IconChartBar,
  IconClipboardList,
  IconCompass,
  IconDatabase,
  IconDatabaseCog,
  IconDeviceDesktopAnalytics,
  IconDownload,
  IconFileText,
  IconFolders,
  IconGavel,
  IconHistory,
  IconKey,
  IconLayoutGrid,
  IconListCheck,
  IconLock,
  IconMapPin,
  IconNews,
  IconRoute,
  IconSchema,
  IconSearch,
  IconServer,
  IconSettings,
  IconShieldCheck,
  IconSitemap,
  IconSparkles,
  IconStack2,
  IconTags,
  IconTimeline,
  IconTopologyStar3,
  IconUsers,
  IconVersions,
  type Icon,
} from "@tabler/icons-react";

export type NavItem = {
  label: string;
  icon: Icon;
  /** Absent while the page does not exist. */
  to?: string;
  /** Matched exactly rather than by prefix, for a route that is a parent. */
  exact?: boolean;
  /**
   * Other path prefixes that belong to this item.
   *
   * For a page that is reached from a listing but does not sit under its path.
   * An article has its own page addressed by the id of the bytes, not by the
   * newspaper it came from — a reader following a link should still be told
   * they are under News Outlets rather than nowhere.
   */
  also?: string[];
};

export type NavGroup = {
  label: string;
  icon: Icon;
  items: NavItem[];
};

export type NavSection = {
  /** Absent for the first section, which needs no heading above the rest. */
  label?: string;
  groups: NavGroup[];
};

export const NAVIGATION: NavSection[] = [
  {
    groups: [
      {
        label: "Discover",
        icon: IconCompass,
        items: [
          { label: "Datasets", icon: IconDatabase, to: "/datasets" },
          { label: "Indicators", icon: IconChartBar, to: "/indicators" },
          { label: "Documents", icon: IconFileText, to: "/documents" },
          // The papers the news monitor reads. Under Discover rather than
          // Explore: a reader arrives wanting to know which sources exist and
          // what has come from them, which is the same question they bring to
          // datasets and documents.
          {
            label: "News Outlets",
            icon: IconNews,
            to: "/news-outlets",
            also: ["/news-articles"],
          },
          { label: "Regulations", icon: IconGavel, to: "/regulations" },
          { label: "Topics", icon: IconTags, to: "/topics" },
        ],
      },
      {
        label: "Explore",
        icon: IconDeviceDesktopAnalytics,
        items: [
          { label: "Data Explorer", icon: IconLayoutGrid, to: "/observations" },
          { label: "Geography", icon: IconMapPin, to: "/geography" },
          { label: "Commodities", icon: IconBox, to: "/commodities" },
          { label: "Organizations", icon: IconBuilding },
          { label: "Entities", icon: IconTopologyStar3 },
        ],
      },
      {
        label: "Research",
        icon: IconSearch,
        items: [
          { label: "Search", icon: IconSearch, to: "/search" },
          { label: "Assistant", icon: IconSparkles, to: "/assistant" },
          { label: "Collections", icon: IconFolders, to: "/collections" },
          { label: "Saved Queries", icon: IconClipboardList, to: "/saved-queries" },
        ],
      },
      {
        label: "Developers",
        icon: IconApi,
        items: [
          { label: "API", icon: IconApi },
          { label: "Downloads", icon: IconDownload },
          { label: "Documentation", icon: IconBook, to: "/docs" },
        ],
      },
    ],
  },
  {
    label: "Administration",
    groups: [
      {
        label: "Data Management",
        icon: IconDatabaseCog,
        items: [
          { label: "Datasets", icon: IconDatabase },
          { label: "Ingestion", icon: IconRoute },
          { label: "Pipelines", icon: IconSitemap },
        ],
      },
      {
        label: "Data Governance",
        icon: IconShieldCheck,
        items: [
          { label: "Data Quality", icon: IconListCheck },
          { label: "Schemas", icon: IconSchema },
          { label: "Classifications", icon: IconBinaryTree },
          { label: "Lineage", icon: IconTimeline },
          { label: "Versions", icon: IconVersions },
        ],
      },
      {
        label: "Access",
        icon: IconLock,
        items: [
          { label: "Users", icon: IconUsers, to: "/users" },
          { label: "Organizations", icon: IconBuildingBank },
          { label: "Roles & Permissions", icon: IconShieldCheck },
          { label: "API Keys", icon: IconKey },
        ],
      },
      {
        label: "System",
        icon: IconServer,
        items: [
          { label: "Jobs", icon: IconStack2, to: "/jobs" },
          { label: "Storage", icon: IconDatabase },
          { label: "Audit Logs", icon: IconHistory },
          { label: "Settings", icon: IconSettings },
        ],
      },
    ],
  },
];

/**
 * The group the sidebar opens on when the route points at none of them.
 *
 * NAVIGATION is a constant that always declares groups, but the type system
 * cannot know that; the check keeps it honest rather than casting it away.
 */
export function defaultGroup(): NavGroup {
  const first = NAVIGATION[0]?.groups[0];
  if (!first) throw new Error("NAVIGATION declares no groups");
  return first;
}

/** The group holding the page at `pathname`, if any. */
/** Whether a path belongs to a navigation item. */
export function matchesItem(item: NavItem, pathname: string): boolean {
  if (item.to && (item.exact ? pathname === item.to : pathname.startsWith(item.to))) {
    return true;
  }
  return (item.also ?? []).some((prefix) => pathname.startsWith(prefix));
}

export function groupForPath(pathname: string): NavGroup | undefined {
  for (const section of NAVIGATION) {
    for (const group of section.groups) {
      const hit = group.items.some((item) => matchesItem(item, pathname));
      if (hit) return group;
    }
  }
  return undefined;
}

/** How many destinations exist, for the "not built" note in the footer. */
export function navigationCoverage(): { built: number; total: number } {
  let built = 0;
  let total = 0;
  for (const section of NAVIGATION) {
    for (const group of section.groups) {
      for (const item of group.items) {
        total += 1;
        if (item.to) built += 1;
      }
    }
  }
  return { built, total };
}
