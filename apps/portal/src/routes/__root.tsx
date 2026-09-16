import {
  HeadContent,
  Outlet,
  Scripts,
  createRootRouteWithContext,
  useRouterState,
} from "@tanstack/react-router";
import type { QueryClient } from "@tanstack/react-query";
import type { CSSProperties, ReactNode } from "react";

import { AppSidebar } from "~/components/app-sidebar";
import { Separator } from "~/components/ui/separator";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "~/components/ui/sidebar";
import { TooltipProvider } from "~/components/ui/tooltip";
import { NAVIGATION } from "~/lib/navigation";
import appCss from "~/styles/app.css?url";

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "Terusan — Research Data Portal" },
      {
        name: "description",
        content:
          "Source-traceable research data: statistics, documents and regulations.",
      },
    ],
    links: [{ rel: "stylesheet", href: appCss }],
  }),
  component: RootComponent,
});

/**
 * Where the current route sits in the navigation, for the breadcrumb.
 *
 * Read from the same tree the sidebar renders, so the two cannot disagree about
 * what a page is called.
 */
function useTrail(pathname: string): string[] {
  for (const section of NAVIGATION) {
    for (const group of section.groups) {
      for (const item of group.items) {
        if (
          item.to &&
          (item.exact ? pathname === item.to : pathname.startsWith(item.to))
        ) {
          return [section.label, group.label, item.label].filter(Boolean) as string[];
        }
      }
    }
  }
  return [];
}

function RootComponent() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const trail = useTrail(pathname);

  return (
    <RootDocument>
      <TooltipProvider>
        {/* Wider than the 16rem default, because the sidebar is now two
            panels: a rail of sections plus the list of pages in one. */}
        <SidebarProvider
          style={
            {
              "--sidebar-width": "21rem",
              "--sidebar-rail-width": "3.5rem",
              // Collapsing leaves exactly the rail.
              "--sidebar-width-icon": "3.5rem",
            } as CSSProperties
          }
        >
          <AppSidebar />
          <SidebarInset>
            <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
              <SidebarTrigger className="-ml-1" />
              <Separator orientation="vertical" className="mr-1 h-4" />
              <nav
                aria-label="Breadcrumb"
                className="flex items-center gap-1.5 text-sm"
              >
                {trail.length ? (
                  trail.map((crumb, index) => (
                    <span key={crumb} className="flex items-center gap-1.5">
                      {index > 0 ? (
                        <span className="text-muted-foreground/50">/</span>
                      ) : null}
                      <span
                        className={
                          index === trail.length - 1
                            ? "font-medium"
                            : "text-muted-foreground"
                        }
                      >
                        {crumb}
                      </span>
                    </span>
                  ))
                ) : (
                  <span className="font-medium">Overview</span>
                )}
              </nav>
            </header>

            <main className="flex-1 px-4 py-6 sm:px-6">
              <Outlet />
            </main>
          </SidebarInset>
        </SidebarProvider>
      </TooltipProvider>
    </RootDocument>
  );
}

function RootDocument({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}
