import { Link } from "@tanstack/react-router";
import { IconBell } from "@tabler/icons-react";

import { AskAI } from "~/components/ask-ai";
import { CommandPalette } from "~/components/command-palette";
import { SiteLinks } from "~/components/site-links";
import { SuggestData } from "~/components/suggest-data";
import { ThemeToggle } from "~/components/theme-toggle";
import { Button } from "~/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "~/components/ui/tooltip";
import logoUrl from "~/assets/logo.png";

/**
 * The bar across the top of every page: what this is, what you are looking
 * for, and the two switches that belong to the window rather than the page.
 *
 * Full width and above the sidebar, so the mark and the search stay in the
 * same place whether the sidebar is open, down to icons, or off on a phone.
 * Its height is published as `--app-header` in `app.css`, because the sidebar
 * and every page's own sticky header have to start below it.
 *
 * The sidebar's own toggle is not here — it sits beside the breadcrumb, next
 * to the panel it opens and closes.
 */
export function TopNav() {
  return (
    <header className="sticky top-0 z-30 flex h-(--app-header) w-full shrink-0 items-center gap-3 border-b bg-background px-3 sm:px-4">
      <Link to="/" className="flex shrink-0 items-center gap-2 rounded-md">
        {/* The seal is dark teal, which is what it is: inverting it would
            make it salmon, and a monochrome copy would be a second file to
            keep in step. So in dark mode it sits on a light disc instead —
            the same treatment the login panel gives it over a colour. It
            stands alone here, so the alt carries the name a wordmark would
            have. */}
        <img
          src={logoUrl}
          alt="Terusan"
          className="h-8 w-auto rounded-full dark:bg-white/90 dark:p-px"
        />
      </Link>

      <SiteLinks />

      {/* Centred on the window rather than on what is left of it, so it does
          not shift sideways when the sidebar collapses. */}
      <div className="mx-auto w-full max-w-md">
        <CommandPalette />
      </div>

      <div className="flex shrink-0 items-center gap-0.5">
        {/* Beside the bell rather than on a page: the moment somebody notices
            a source is missing is the moment they are looking at what is
            here, and a suggestion made three clicks later is a suggestion not
            made. */}
        <SuggestData />

        {/* The assistant, where this deployment has one. */}
        <AskAI />

        <Tooltip>
          <TooltipTrigger
            render={
              <Button variant="ghost" size="icon" disabled aria-label="Notifications">
                <IconBell />
              </Button>
            }
          />
          {/* Disabled rather than absent, on the same rule the navigation
              follows: a bell that opens an empty panel claims there is nothing
              to tell you, which is a different thing from not being built. */}
          <TooltipContent>Notifications — not built yet</TooltipContent>
        </Tooltip>

        <ThemeToggle />
      </div>
    </header>
  );
}
