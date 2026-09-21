import { useLayoutEffect, useRef } from "react";

import { cn } from "~/lib/utils";

/**
 * The part of a page that stays put while the rest scrolls: what the page is,
 * and what is narrowing it.
 *
 * Both belong here for the same reason. Ten screens into a table the reader
 * has lost sight of which filters are on, and a figure read under a filter
 * nobody can see is a figure read wrong — so the chips stay visible next to
 * the title that names what they are narrowing.
 *
 * The two are given as separate slots rather than as children so the rule
 * between them is drawn here, once, instead of by each of the five pages that
 * use this.
 *
 * It publishes its own height as `--page-header` on the document root, because
 * anything else that sticks — the metadata column on an indicator page — has
 * to sit below it, and those elements are siblings further down the page
 * rather than descendants of this one. The height is not a constant: the chips
 * wrap onto a second row on a narrow window, and a hardcoded offset would let
 * them overlap.
 */
export function StickyHeader({
  heading,
  filters,
  divided = true,
  className,
}: {
  /** The title, and whatever names the page beneath it. */
  heading: React.ReactNode;
  /** The row of chips and the search box. */
  filters?: React.ReactNode;
  /**
   * Whether a rule divides the two rows. On by default, because a row of chips
   * under a title reads as a second band of the header. A row of tabs does not
   * need it: the tab that is on already draws its own edge, and a rule above it
   * fences the tabs off from the panel they open.
   */
  divided?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const root = document.documentElement;

    // Measured rather than assumed: the row grows when the chips wrap.
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      const height = entry.borderBoxSize?.[0]?.blockSize ?? entry.contentRect.height;
      root.style.setProperty("--page-header", `${Math.round(height)}px`);
    });
    observer.observe(element);

    return () => {
      observer.disconnect();
      // Left behind, a stale height would push the next page's sticky column
      // down by the height of a header it no longer has.
      root.style.removeProperty("--page-header");
    };
  }, []);

  return (
    <div
      ref={ref}
      className={cn(
        // Pulled out to the page gutter so both rules span the full width
        // instead of stopping at the content edge; the padding goes on the
        // rows inside instead. `-mt-6` cancels the page's own top padding so
        // this supplies it — otherwise there is twice the gap before scrolling
        // and none above the title once it pins. The background is opaque
        // because the page scrolls underneath it.
        "sticky top-(--app-header) z-20 -mx-4 -mt-6 border-b bg-background sm:-mx-6",
        className,
      )}
    >
      <div className="px-4 pb-3 pt-6 sm:px-6">{heading}</div>
      {filters ? (
        <div className={cn("px-4 py-3 sm:px-6", divided && "border-t")}>{filters}</div>
      ) : null}
    </div>
  );
}

/**
 * Where a second sticky element should start: below the header, plus a gap.
 *
 * Falls back to a plain gap before the header has measured itself, which is
 * the first paint and the server render.
 */
export const BELOW_STICKY_HEADER =
  "lg:top-[calc(var(--app-header)+var(--page-header,0px)+1rem)]";
