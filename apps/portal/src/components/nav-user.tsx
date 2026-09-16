import {
  IconLogout,
  IconSettings,
  IconUser,
  IconUserCircle,
} from "@tabler/icons-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "~/components/ui/sidebar";

/**
 * Profile and settings, at the foot of the rail.
 *
 * There is no authentication yet (program.md §34), so the account shows as
 * signed out and its actions are visibly unavailable. A sign-out button that
 * signs nobody out is worse than one that says it cannot — and the same rule
 * the rest of the navigation follows.
 */

const ACCOUNT_ACTIONS = [
  { label: "Profile", icon: IconUserCircle },
  { label: "Account settings", icon: IconSettings },
];

export function NavUser() {
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton
          tooltip="Settings — not built yet"
          disabled
          className="justify-center px-0 opacity-45"
        >
          <IconSettings />
          <span className="sr-only">Settings</span>
        </SidebarMenuButton>
      </SidebarMenuItem>

      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <SidebarMenuButton
                tooltip="Profile"
                className="justify-center px-0 data-[popup-open]:bg-sidebar-accent"
              >
                {/* A person rather than initials: there is nobody signed in to
                    take initials from, and an empty avatar reads as broken. */}
                <IconUser />
                <span className="sr-only">Profile</span>
              </SidebarMenuButton>
            }
          />

          <DropdownMenuContent
            className="w-60 rounded-lg"
            side="right"
            align="end"
            sideOffset={8}
          >
            <DropdownMenuLabel className="p-0 font-normal">
              <div className="flex items-center gap-2 px-1 py-1.5">
                <div className="flex size-8 items-center justify-center rounded-lg bg-muted">
                  <IconUser className="size-4" />
                </div>
                <div className="grid flex-1 leading-tight">
                  <span className="truncate font-medium">Not signed in</span>
                  <span className="truncate text-xs text-muted-foreground">
                    Authentication is not built yet
                  </span>
                </div>
              </div>
            </DropdownMenuLabel>

            <DropdownMenuSeparator />

            <DropdownMenuGroup>
              {ACCOUNT_ACTIONS.map((action) => (
                <DropdownMenuItem key={action.label} disabled>
                  <action.icon />
                  {action.label}
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>

            <DropdownMenuSeparator />

            <DropdownMenuItem disabled>
              <IconLogout />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
