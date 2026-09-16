import { Badge } from "~/components/ui/badge";
import { formatCount } from "~/lib/format";

/**
 * A table page's heading: what it is, how much of it there is, and what can be
 * done with it.
 *
 * The count sits beside the title rather than under it, because it is the
 * first thing anyone checks after applying a filter.
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
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <h1 className="font-heading text-2xl font-semibold tracking-tight">
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

      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}
