import { Link, useRouterState } from "@tanstack/react-router";
import { IconDatabaseSearch } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "~/components/ui/sidebar";
import { cn } from "~/lib/utils";
import {
  NAVIGATION,
  defaultGroup,
  groupForPath,
  navigationCoverage,
  navigationGroups,
  type NavItem,
  type RailGroup,
} from "~/lib/navigation";

/**
 * Two panels: a rail of sections, and the items of whichever is selected.
 *
 * Thirty-four destinations in one list is a wall nobody reads. Splitting them
 * puts one decision on screen at a time — which part of the platform, then
 * which page — and keeps the second panel short enough to scan.
 */
export function AppSidebar() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const groups = navigationGroups();
  const coverage = navigationCoverage();

  const [activeLabel, setActiveLabel] = useState(
    () => groupForPath(pathname)?.label ?? defaultGroup().label,
  );

  // Follow the route when it changes under us — a link from elsewhere in the
  // app, or a pasted URL, should leave the rail pointing at the right section
  // rather than at whatever was last clicked.
  useEffect(() => {
    const match = groupForPath(pathname);
    if (match) setActiveLabel(match.label);
  }, [pathname]);

  const active =
    groups.find((group) => group.label === activeLabel) ?? defaultGroup();

  return (
    <Sidebar
      collapsible="none"
      className="overflow-hidden border-r *:data-[sidebar=sidebar]:flex-row"
    >
      <GroupRail
        groups={groups}
        activeLabel={active.label}
        onSelect={setActiveLabel}
      />
      <ItemPanel group={active} pathname={pathname} coverage={coverage} />
    </Sidebar>
  );
}

/** The narrow panel: one icon per section, labelled only by its tooltip. */
function GroupRail({
  groups,
  activeLabel,
  onSelect,
}: {
  groups: RailGroup[];
  activeLabel: string;
  onSelect: (label: string) => void;
}) {
  return (
    <Sidebar
      collapsible="none"
      className="w-(--sidebar-rail-width)! shrink-0 border-r bg-sidebar"
    >
      <SidebarHeader className="p-0">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              size="lg"
              tooltip="Terusan"
              className="justify-center md:h-14"
              render={<Link to="/" />}
            >
              <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                <IconDatabaseSearch className="size-4" />
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup className="px-1.5">
          <SidebarGroupContent>
            <SidebarMenu>
              {groups.map((group) => (
                <SidebarMenuItem key={group.label}>
                  {/* A rule above the first administration entry, so the two
                      halves of the platform read apart without a heading the
                      rail has no room for. */}
                  {group.startsSection ? (
                    <div
                      className="mx-1.5 my-2 border-t"
                      role="separator"
                      aria-label={group.sectionLabel}
                    />
                  ) : null}
                  <SidebarMenuButton
                    tooltip={
                      group.sectionLabel
                        ? `${group.sectionLabel} — ${group.label}`
                        : group.label
                    }
                    isActive={group.label === activeLabel}
                    onClick={() => onSelect(group.label)}
                    className="justify-center px-0"
                  >
                    <group.icon />
                    <span className="sr-only">{group.label}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  );
}

/** The wide panel: the selected section's pages. */
function ItemPanel({
  group,
  pathname,
  coverage,
}: {
  group: RailGroup;
  pathname: string;
  coverage: { built: number; total: number };
}) {
  function isActive(item: NavItem) {
    if (!item.to) return false;
    return item.exact ? pathname === item.to : pathname.startsWith(item.to);
  }

  return (
    <Sidebar collapsible="none" className="flex min-w-0 flex-1 bg-sidebar">
      <SidebarHeader className="gap-0.5 border-b md:h-14 md:justify-center">
        {group.sectionLabel ? (
          <span className="text-[0.7rem] font-medium tracking-wide text-muted-foreground uppercase">
            {group.sectionLabel}
          </span>
        ) : null}
        <span className="flex items-center gap-2 font-heading font-semibold">
          <group.icon className="size-4 shrink-0" />
          {group.label}
        </span>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {group.items.map((item) => (
                <SidebarMenuItem key={item.label}>
                  {item.to ? (
                    <SidebarMenuButton
                      isActive={isActive(item)}
                      render={<Link to={item.to} />}
                    >
                      <item.icon />
                      <span>{item.label}</span>
                    </SidebarMenuButton>
                  ) : (
                    // Shown rather than hidden: the shape of the platform is
                    // worth seeing, and a link that 404s is worse than one
                    // that says it is not here yet.
                    <SidebarMenuButton
                      disabled
                      className={cn("cursor-default opacity-45")}
                    >
                      <item.icon />
                      <span>{item.label}</span>
                    </SidebarMenuButton>
                  )}
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <p className="px-2 pb-1 text-xs text-muted-foreground">
          {coverage.built} of {coverage.total} pages built. The rest are listed
          so the shape is visible.
        </p>
      </SidebarFooter>
    </Sidebar>
  );
}

export { NAVIGATION };
