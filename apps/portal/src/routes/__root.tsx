import {
  HeadContent,
  Outlet,
  Scripts,
  createRootRouteWithContext,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router";
import type { QueryClient } from "@tanstack/react-query";
import { useEffect, type CSSProperties, type ReactNode } from "react";

import { AppSidebar } from "~/components/app-sidebar";
import { TopNav } from "~/components/top-nav";
import { Separator } from "~/components/ui/separator";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "~/components/ui/sidebar";
import { TooltipProvider } from "~/components/ui/tooltip";
import { NAVIGATION } from "~/lib/navigation";
import { useSessionState } from "~/lib/session";
import { THEME_INIT_SCRIPT } from "~/lib/theme";
import favicon180 from "~/assets/favicon-180.png";
import favicon32 from "~/assets/favicon-32.png";
import appCss from "~/styles/app.css?url";

/**
 * How wide the page is allowed to get.
 *
 * Shared by the breadcrumb bar and the content beneath it so the two line up.
 * Wide enough for a table of eight columns, and no wider: past this a row's
 * first cell and its last sit a screen apart on a large monitor.
 */
const PAGE_WIDTH = "mx-auto w-full max-w-7xl";

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
    links: [
      { rel: "stylesheet", href: appCss },
      // The mark, squared and trimmed to the artwork rather than to the file,
      // which has uneven whitespace around it. Two sizes because a browser
      // picks by pixel density as well as by slot, and 32px upscaled to a
      // retina bookmark is mush.
      { rel: "icon", type: "image/png", sizes: "32x32", href: favicon32 },
      { rel: "icon", type: "image/png", sizes: "180x180", href: favicon180 },
    ],
  }),
  component: RootComponent,
});

/**
 * Where the current route sits in the navigation, for the breadcrumb.
 *
 * Read from the same tree the sidebar renders, so the two cannot disagree about
 * what a page is called.
 */
/**
 * Pages that belong to the reader rather than to the warehouse, and so are not
 * in the navigation tree the sidebar renders. They still need a breadcrumb.
 */
const OFF_TREE: Record<string, string[]> = {
  "/profile": ["Account"],
};

function useTrail(pathname: string): string[] {
  const offTree = OFF_TREE[pathname];
  if (offTree) return offTree;

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

/** The one page that is reachable without a session, and draws its own shell. */
const LOGIN = "/login";

function RootComponent() {
  const location = useRouterState({ select: (state) => state.location });
  const pathname = location.pathname;
  const trail = useTrail(pathname);
  const navigate = useNavigate();
  const { session, hasAuth, isLoading } = useSessionState();

  // Signed out, on a deployment that has accounts: the page being asked for is
  // remembered so that logging in lands where the reader was going rather than
  // on the overview.
  //
  // The check is here rather than in a route loader because the session lives
  // in an HttpOnly cookie the browser holds: the server rendering this page
  // never sees it, so a loader would decide "signed out" for everyone and
  // redirect a logged-in reader on every hard refresh.
  const locked = hasAuth && !isLoading && !session && pathname !== LOGIN;

  useEffect(() => {
    if (!locked) return;
    void navigate({
      to: LOGIN,
      search: { redirect: pathname + (location.searchStr || "") },
      replace: true,
    });
  }, [locked, navigate, pathname, location.searchStr]);

  if (pathname === LOGIN) {
    // No sidebar, no breadcrumb: there is nowhere to navigate to yet.
    return (
      <RootDocument>
        <TooltipProvider>
          <Outlet />
        </TooltipProvider>
      </RootDocument>
    );
  }

  if (locked) {
    // The redirect is a tick away. Rendering the shell in the meantime would
    // flash a sidebar and an empty table at somebody who is not signed in.
    return <RootDocument>{null}</RootDocument>;
  }

  return (
    <RootDocument>
      <TooltipProvider>
        {/* A column rather than the provider's default row: the navbar spans
            the window, and the sidebar and the page sit side by side beneath
            it. The sidebar is a little over the 16rem default because its
            group labels sit above an indented list of pages, and the longest
            of them ("Roles & Permissions") wrapped at the default width. */}
        <SidebarProvider
          className="flex-col"
          style={
            {
              "--sidebar-width": "17rem",
              "--sidebar-width-icon": "3.5rem",
            } as CSSProperties
          }
        >
          <TopNav />

          <div className="flex w-full flex-1">
            <AppSidebar />
            <SidebarInset>
              {/* The row spans the window; what is in it lines up with the
                  page below, so the breadcrumb sits above the title rather
                  than out in the margin. The sidebar's toggle rides along at
                  the left, next to the panel it opens. */}
              <div className="flex h-12 shrink-0 items-center">
                <div className={`${PAGE_WIDTH} flex items-center gap-2 px-4 sm:px-6`}>
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
                </div>
              </div>

              {/* Measured rather than full-bleed: a table stretched across a
                  wide monitor puts a row's first cell and its last a whole
                  screen apart, and the eye loses the row between them. */}
              <div className={`${PAGE_WIDTH} flex-1 px-4 py-6 sm:px-6`}>
                <Outlet />
              </div>
            </SidebarInset>
          </div>
        </SidebarProvider>
      </TooltipProvider>
    </RootDocument>
  );
}

function RootDocument({ children }: { children: ReactNode }) {
  return (
    // The inline script below puts the theme's class and `color-scheme` on
    // this element before React sees it, which is the whole point of running
    // it early — and exactly what React otherwise reports as a mismatch.
    <html lang="en" suppressHydrationWarning>
      <head>
        <HeadContent />
        {/* Before the stylesheet paints anything: by the time React hydrates,
            a dark-mode reader has already been shown a white page. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}
