import { Tooltip, TooltipContent, TooltipTrigger } from "~/components/ui/tooltip";
import { cn } from "~/lib/utils";

/**
 * A cell that keeps its column's width, and gives up the rest on hover.
 *
 * Published names and units are as long as their publisher wrote them: FRED
 * titles run to ninety characters and its units to "2005 International Dollars
 * per Person Counted in Total Employment". Left alone, one of those sets the
 * width of the column for every row, and the columns a reader came for — the
 * frequency, the coverage — end up off the right edge.
 *
 * `truncate` rather than `line-clamp-1`: the clamp utility sets its own
 * display, which a `block` class then overrides, leaving the text cut off
 * mid-word with no ellipsis to say so.
 */
export function ClampedText({
  children,
  className,
}: {
  children: string;
  /** The width to clamp at, as a utility class. */
  className?: string;
}) {
  return (
    <Tooltip>
      <TooltipTrigger render={<span className={cn("block truncate", className)} />}>
        {children}
      </TooltipTrigger>
      {/* The whole of it, wrapped rather than clamped again. */}
      <TooltipContent className="max-w-sm">{children}</TooltipContent>
    </Tooltip>
  );
}
