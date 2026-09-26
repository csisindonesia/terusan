import { Badge } from "~/components/ui/badge";
import { formatCount } from "~/lib/format";

/**
 * A table page's heading: what it is, how much of it there is, and what can be
 * done with it.
 *
 * The count sits beside the title rather than under it, because it is the
 * first thing anyone checks after applying a filter.
 *
 * The heading column is allowed to shrink and its title to wrap, so the
 * actions stay on the title's own line. Left to grow, a long title — a news
 * headline runs to twenty words — takes the full width and pushes the actions
 * onto a row of their own, where they read as belonging to the description
 * rather than to the page.
 *
 * The minimum width is what keeps that from going too far the other way. With
 * only `flex-1` the actions hold their width and the title is squeezed into a
 * column three words wide on a laptop at half screen. Below the minimum the
 * row wraps and the actions drop underneath, which on a narrow screen is the
 * right answer.
 */
export function PageHeader({
  title,
  count,
  isLoading = false,
  description,
  actions,
}: {
  title: string;
  count?: number;
  isLoading?: boolean;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-[18rem] flex-1 space-y-1">
        <div className="flex items-start gap-2">
          <h1 className="font-heading text-2xl font-semibold tracking-tight text-balance">
            {title}
          </h1>
          {count !== undefined || isLoading ? (
            <Badge variant="secondary" className="tabular-nums">
              {isLoading ? "…" : formatCount(count ?? 0)}
            </Badge>
          ) : null}
        </div>
        {description ? (
          <p className="max-w-3xl text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>

      {actions ? (
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      ) : null}
    </div>
  );
}
