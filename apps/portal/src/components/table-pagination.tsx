import {
  IconChevronLeft,
  IconChevronRight,
  IconChevronsLeft,
  IconChevronsRight,
} from "@tabler/icons-react";

import { Button } from "~/components/ui/button";
import { formatCount } from "~/lib/format";
import { GAP, pageCountFor, pageWindow } from "~/lib/pagination";

/**
 * Page numbers, with jumps to either end.
 *
 * Buttons rather than links: these change a query parameter, and an anchor
 * without an href is a link that goes nowhere as far as a screen reader or a
 * middle click is concerned.
 */
export function TablePagination({
  page,
  total,
  pageSize,
  onPage,
  summary,
}: {
  /** Zero-indexed, as the caller holds it. Displayed one higher. */
  page: number;
  total: number;
  pageSize: number;
  onPage: (page: number) => void;
  /** Shown to the left, typically the range in view. */
  summary?: React.ReactNode;
}) {
  const pageCount = pageCountFor(total, pageSize);
  const slots = pageWindow(page, pageCount);

  const first = page === 0;
  const last = page >= pageCount - 1;

  return (
    <nav
      aria-label="Pagination"
      className="flex flex-wrap items-center justify-between gap-3 text-sm"
    >
      <span className="text-muted-foreground">{summary}</span>

      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon-sm"
          disabled={first}
          onClick={() => onPage(0)}
          aria-label="First page"
        >
          <IconChevronsLeft />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          disabled={first}
          onClick={() => onPage(page - 1)}
          aria-label="Previous page"
        >
          <IconChevronLeft />
        </Button>

        {slots.map((slot, index) =>
          slot === GAP ? (
            <span
              key={`gap-${index}`}
              aria-hidden
              className="px-1 text-muted-foreground"
            >
              …
            </span>
          ) : (
            <Button
              key={slot}
              variant={slot === page ? "outline" : "ghost"}
              size="icon-sm"
              onClick={() => onPage(slot)}
              aria-label={`Page ${slot + 1}`}
              aria-current={slot === page ? "page" : undefined}
              className="tabular-nums"
            >
              {formatCount(slot + 1)}
            </Button>
          ),
        )}

        <Button
          variant="ghost"
          size="icon-sm"
          disabled={last}
          onClick={() => onPage(page + 1)}
          aria-label="Next page"
        >
          <IconChevronRight />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          disabled={last}
          onClick={() => onPage(pageCount - 1)}
          aria-label="Last page"
        >
          <IconChevronsRight />
        </Button>
      </div>
    </nav>
  );
}
