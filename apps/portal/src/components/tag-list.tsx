import { Badge } from "~/components/ui/badge";
import { cn } from "~/lib/utils";

/**
 * The words a record is found by.
 *
 * Shown as chips rather than as a sentence because that is how they are used:
 * a reader scans them and clicks one. Every catalogue record carries at least
 * five, so a row would be half tags if they all showed — the first few are the
 * most identifying ones, and the rest are counted.
 */
export function TagList({
  tags,
  max = 4,
  onSelect,
  className,
}: {
  tags: string[] | undefined;
  max?: number;
  /** Makes each chip a filter. Omitted where there is nothing to filter. */
  onSelect?: (tag: string) => void;
  className?: string;
}) {
  if (!tags?.length) return null;

  const shown = tags.slice(0, max);
  const hidden = tags.length - shown.length;

  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      {shown.map((tag) =>
        onSelect ? (
          <button key={tag} type="button" onClick={() => onSelect(tag)}>
            <Badge
              variant="outline"
              className="cursor-pointer text-xs font-normal hover:bg-accent"
            >
              {tag}
            </Badge>
          </button>
        ) : (
          <Badge key={tag} variant="outline" className="text-xs font-normal">
            {tag}
          </Badge>
        ),
      )}
      {hidden > 0 ? (
        // Titled rather than dropped: the count says there are more, and the
        // hover says which, without widening the column for every row.
        <span
          className="text-xs text-muted-foreground"
          title={tags.slice(max).join(", ")}
        >
          +{hidden}
        </span>
      ) : null}
    </div>
  );
}
