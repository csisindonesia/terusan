import { Link, useRouterState } from "@tanstack/react-router";
import { IconMinus, IconPlus } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { NavUser } from "~/components/nav-user";
import {
  Collapsible,
  CollapsiblePanel,
  CollapsibleTrigger,
} from "~/components/ui/collapsible";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  useSidebar,
} from "~/components/ui/sidebar";
import {
  NAVIGATION,
  defaultGroup,
  groupForPath,
  navigationCoverage,
  type NavGroup,
  type NavItem,
} from "~/lib/navigation";

/**
 * One panel, with each section of the platform a collapsible group.
 *
 * It was two panels — a rail of icons plus the pages of whichever was
 * selected — which spent 21rem of the window to show four or five links at a
 * time. Folding the groups into a single list costs nothing when they are
 * shut, shows the whole shape of the platform at a glance, and lets two
 * sections stay open together when work spans both.
 */
export function AppSidebar() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const coverage = navigationCoverage();

  // Open groups rather than one selected group: the rail could only ever point
  // at a single section, which made moving between, say, Discover and Explore
  // a click to switch and a click to arrive.
  const [openGroups, setOpenGroups] = useState<string[]>(() => {
    // Falls back to the first group rather than to nothing: the landing page
    // belongs to no group, and a sidebar that opens with every section shut
    // makes the reader click before it tells them anything.
    const match = groupForPath(pathname) ?? defaultGroup();
    return [match.label];
  });

  // Follow the route when it changes under us — a link from elsewhere in the
  // app, or a pasted URL, should leave its group open rather than the page
  // being highlighted inside something folded shut.
  useEffect(() => {
    const match = groupForPath(pathname);
    if (!match) return;
    setOpenGroups((open) =>
      open.includes(match.label) ? open : [...open, match.label],
    );
  }, [pathname]);

  function toggle(label: string, open: boolean) {
    setOpenGroups((current) =>
      open ? [...current, label] : current.filter((entry) => entry !== label),
    );
  }

  return (
    // Pushed below the navbar: the panel is `fixed inset-y-0` by default,
    // which would put its first group behind the bar. Marked important
    // because both declarations are fighting classes on the same element.
    <Sidebar
      collapsible="icon"
      className="top-(--app-header)! h-[calc(100svh-var(--app-header))]!"
    >
      <SidebarContent>
        {NAVIGATION.map((section, index) => (
          <SidebarGroup key={section.label ?? `section-${index}`}>
            {section.label ? (
              <SidebarGroupLabel className="tracking-wide uppercase">
                {section.label}
              </SidebarGroupLabel>
            ) : null}
            <SidebarGroupContent>
              <SidebarMenu>
                {section.groups.map((group) => (
                  <NavGroupItem
                    key={group.label}
                    group={group}
                    pathname={pathname}
                    open={openGroups.includes(group.label)}
                    onOpenChange={(open) => toggle(group.label, open)}
                  />
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>

      <SidebarFooter className="gap-2 border-t">
        <p className="px-2 text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">
          {coverage.built} of {coverage.total} pages built. The rest are listed so the
          shape is visible.
        </p>
        <NavUser />
      </SidebarFooter>
    </Sidebar>
  );
}

/** One section of the platform: a header that folds its pages away. */
function NavGroupItem({
  group,
  pathname,
  open,
  onOpenChange,
}: {
  group: NavGroup;
  pathname: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { state, isMobile, setOpen } = useSidebar();
  const collapsedToIcons = state === "collapsed" && !isMobile;
  const holdsCurrentPage = group.items.some((item) => isCurrent(item, pathname));
  // Down to icons there is no room for the pages, so a group that the person
  // left open shows shut without forgetting that they left it open.
  const expanded = open && !collapsedToIcons;

  return (
    <Collapsible
      open={expanded}
      onOpenChange={onOpenChange}
      render={<SidebarMenuItem />}
    >
      <CollapsibleTrigger
        render={
          <SidebarMenuButton
            tooltip={group.label}
            // Marked active only while shut: an open group already shows which
            // of its pages you are on, and highlighting both reads as two.
            isActive={holdsCurrentPage && !expanded}
            // Pressing a group while the sidebar is down to icons has nowhere
            // to put the pages, so it opens the sidebar first.
            onClick={collapsedToIcons ? () => setOpen(true) : undefined}
          />
        }
      >
        <group.icon />
        <span>{group.label}</span>
        {/* Which way the press goes, rather than which way the group
                    points. Swapped by the trigger's own state so the mark is
                    right at first paint, the same way the theme icons are. */}
        <IconPlus className="ml-auto size-4! text-muted-foreground group-data-[collapsible=icon]:hidden group-data-[panel-open]/menu-button:hidden" />
        <IconMinus className="ml-auto hidden size-4! text-muted-foreground group-data-[collapsible=icon]:hidden group-data-[panel-open]/menu-button:block" />
      </CollapsibleTrigger>

      <CollapsiblePanel className="group-data-[collapsible=icon]:hidden">
        <SidebarMenuSub>
          {group.items.map((item) => (
            <SidebarMenuSubItem key={item.label}>
              {item.to ? (
                <SidebarMenuSubButton
                  isActive={isCurrent(item, pathname)}
                  render={<Link to={item.to} />}
                >
                  <item.icon />
                  <span>{item.label}</span>
                </SidebarMenuSubButton>
              ) : (
                // Shown rather than hidden: the shape of the platform is worth
                // seeing, and a link that 404s is worse than one that says it
                // is not here yet.
                <SidebarMenuSubButton aria-disabled render={<span />}>
                  <item.icon />
                  <span>{item.label}</span>
                </SidebarMenuSubButton>
              )}
            </SidebarMenuSubItem>
          ))}
        </SidebarMenuSub>
      </CollapsiblePanel>
    </Collapsible>
  );
}

function isCurrent(item: NavItem, pathname: string): boolean {
  if (!item.to) return false;
  return item.exact ? pathname === item.to : pathname.startsWith(item.to);
}

export { NAVIGATION };
