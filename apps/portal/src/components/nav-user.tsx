import { Link, useNavigate } from "@tanstack/react-router";
import {
  IconDeviceDesktop,
  IconDotsVertical,
  IconLogin,
  IconLogout,
  IconShieldLock,
  IconUser,
  IconUserCircle,
} from "@tabler/icons-react";
import { useState } from "react";

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
  useSidebar,
} from "~/components/ui/sidebar";
import { useSessionState, useSignOut } from "~/lib/session";
import { formatRelative } from "~/lib/format";

/**
 * Who is signed in, at the foot of the sidebar, and everything that belongs to
 * them rather than to the warehouse.
 *
 * Four entries and no more: the profile, the password, the list of browsers
 * signed in as this person, and the way out. Each goes to a section of the
 * account page rather than to a page of its own — they are one screen's worth
 * of settings, and three near-empty pages would be worse than one.
 */

/** Initials from a name, or from the address when there is no name. */
function initials(name: string | undefined, email: string): string {
  const source = name?.trim() || email.split("@")[0] || "";
  const parts = source.split(/[\s._-]+/).filter(Boolean);
  const letters = parts.slice(0, 2).map((part) => part[0] ?? "");
  return (letters.join("") || source.slice(0, 2)).toUpperCase();
}

export function NavUser() {
  const { isMobile } = useSidebar();
  const { session, hasAuth, isLoading } = useSessionState();
  const signOut = useSignOut();
  const navigate = useNavigate();
  const [leaving, setLeaving] = useState(false);

  const user = session?.user;

  // Two lines that say the same thing in every state: who this is, and what
  // the state of their session is.
  const primary = user
    ? user.name?.trim() || user.email
    : isLoading
      ? "…"
      : "Not signed in";
  const secondary = user
    ? user.name?.trim()
      ? user.email
      : (user.role ?? "member")
    : hasAuth
      ? "Log in to keep collections"
      : "This deployment keeps no accounts";

  async function leave() {
    setLeaving(true);
    try {
      await signOut();
      void navigate({ to: "/login", replace: true });
    } finally {
      setLeaving(false);
    }
  }

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <SidebarMenuButton
                size="lg"
                tooltip="Account"
                className="data-[popup-open]:bg-sidebar-accent group-data-[collapsible=icon]:p-0!"
              >
                <div className="flex aspect-square size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-xs font-medium">
                  {/* Initials once there is somebody to take them from; a
                      person icon while there is not, because an empty avatar
                      reads as broken rather than as signed out. */}
                  {user ? (
                    initials(user.name, user.email)
                  ) : (
                    <IconUser className="size-4!" />
                  )}
                </div>
                <div className="grid flex-1 text-left leading-tight">
                  <span className="truncate text-sm font-medium">{primary}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {secondary}
                  </span>
                </div>
                <IconDotsVertical className="ml-auto size-4! text-muted-foreground group-data-[collapsible=icon]:hidden" />
              </SidebarMenuButton>
            }
          />

          <DropdownMenuContent
            className="w-(--anchor-width) min-w-60 rounded-lg"
            side={isMobile ? "bottom" : "right"}
            align="end"
            sideOffset={8}
          >
            <DropdownMenuLabel className="p-0 font-normal">
              <div className="flex items-center gap-2 px-1 py-1.5">
                <div className="flex size-8 items-center justify-center rounded-lg bg-muted text-xs font-medium">
                  {user ? (
                    initials(user.name, user.email)
                  ) : (
                    <IconUser className="size-4" />
                  )}
                </div>
                <div className="grid flex-1 leading-tight">
                  <span className="truncate font-medium">{primary}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {user
                      ? // What the session is worth knowing about: when it was
                        // last started, since a shared machine is the case this
                        // matters for.
                        formatRelative(user.last_login_at)
                        ? `Signed in ${formatRelative(user.last_login_at)}`
                        : user.email
                      : secondary}
                  </span>
                </div>
              </div>
            </DropdownMenuLabel>

            <DropdownMenuSeparator />

            {user ? (
              <>
                <DropdownMenuGroup>
                  <DropdownMenuItem
                    render={
                      <Link to="/profile">
                        <IconUserCircle />
                        Profile
                      </Link>
                    }
                  />
                  <DropdownMenuItem
                    render={
                      <Link to="/profile" hash="password">
                        <IconShieldLock />
                        Change password
                      </Link>
                    }
                  />
                  <DropdownMenuItem
                    render={
                      <Link to="/profile" hash="sessions">
                        <IconDeviceDesktop />
                        Where you are signed in
                      </Link>
                    }
                  />
                </DropdownMenuGroup>

                <DropdownMenuSeparator />

                <DropdownMenuItem disabled={leaving} onClick={() => void leave()}>
                  <IconLogout />
                  {leaving ? "Signing out…" : "Sign out"}
                </DropdownMenuItem>
              </>
            ) : (
              <DropdownMenuItem
                disabled={!hasAuth}
                onClick={() => void navigate({ to: "/login" })}
              >
                <IconLogin />
                {hasAuth ? "Log in" : "No accounts on this deployment"}
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
