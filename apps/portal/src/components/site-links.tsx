import { Link } from "@tanstack/react-router";

import { Tooltip, TooltipContent, TooltipTrigger } from "~/components/ui/tooltip";

/**
 * What the portal is and who to tell when it is wrong.
 *
 * Beside the mark rather than in the sidebar: none of it is data, and the
 * sidebar is a map of the data. Spread across the bar rather than folded into
 * a menu, so all four are readable without a click — they fold away under
 * `md`, where the search needs the width more than four labels do.
 *
 * A link with a `to` is live; the rest are shown and disabled — the same rule
 * the navigation follows. A link that 404s is worse than one that says it is
 * not here yet.
 */
const LINKS: { label: string; to?: string }[] = [
  { label: "About", to: "/about" },
  { label: "Contact us", to: "/contact" },
  { label: "Report", to: "/report" },
  { label: "Help", to: "/help" },
];

const ITEM_CLASS = "rounded-md px-2 py-1 text-sm";

export function SiteLinks() {
  return (
    <nav aria-label="About this site" className="hidden shrink-0 items-center md:flex">
      {LINKS.map(({ label, to }) =>
        to ? (
          <Link
            key={label}
            to={to}
            className={`${ITEM_CLASS} text-muted-foreground hover:bg-accent hover:text-foreground`}
            activeProps={{ className: `${ITEM_CLASS} font-medium text-foreground` }}
          >
            {label}
          </Link>
        ) : (
          <Tooltip key={label}>
            <TooltipTrigger
              render={
                <span
                  aria-disabled="true"
                  className={`${ITEM_CLASS} cursor-default text-muted-foreground opacity-70`}
                >
                  {label}
                </span>
              }
            />
            <TooltipContent>{label} — not built yet</TooltipContent>
          </Tooltip>
        ),
      )}
    </nav>
  );
}
