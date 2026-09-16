/**
 * Which page numbers to show, and where to elide.
 *
 * Kept pure and separate from the component so the awkward part — the gaps —
 * can be checked without rendering anything.
 */

export const GAP = "gap" as const;

export type PageSlot = number | typeof GAP;

/**
 * Page numbers around the current one, with the first and last always present.
 *
 * Pages are zero-indexed, as the caller holds them; the component adds one for
 * display. `span` is how many neighbours to keep either side.
 *
 * The window is a fixed width whichever page you are on, so the control does
 * not change size as you move through it — a row of numbers that reflows under
 * the pointer is unusable.
 */
export function pageWindow(page: number, pageCount: number, span = 1): PageSlot[] {
  if (pageCount <= 0) return [];

  // First, last, and the neighbourhood of the current page.
  const width = span * 2 + 1;
  const shown = new Set<number>([0, pageCount - 1]);

  // Slide the window so it keeps its width at either end rather than
  // collapsing: on page 0 that means showing the pages after it.
  let start = Math.max(0, Math.min(page - span, pageCount - width));
  let end = Math.min(pageCount - 1, Math.max(page + span, width - 1));
  start = Math.max(0, start);
  end = Math.min(pageCount - 1, end);

  for (let index = start; index <= end; index += 1) shown.add(index);

  const ordered = [...shown].sort((a, b) => a - b);
  const slots: PageSlot[] = [];

  for (const [position, value] of ordered.entries()) {
    const previous = ordered[position - 1];
    if (previous !== undefined && value - previous > 1) {
      // One missing page is worth showing rather than eliding: a gap standing
      // in for a single number is longer than the number.
      if (value - previous === 2) slots.push(value - 1);
      else slots.push(GAP);
    }
    slots.push(value);
  }

  return slots;
}

/** How many pages a result set needs. */
export function pageCountFor(total: number, pageSize: number): number {
  if (pageSize <= 0) return 0;
  return Math.max(1, Math.ceil(total / pageSize));
}
