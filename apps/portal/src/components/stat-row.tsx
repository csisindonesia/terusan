import { cn } from "~/lib/utils";

export type Stat = {
  label: string;
  value: string;
  /** What the number counts, or what it is out of. */
  note?: string;
  /** Set where the number is a problem to look at rather than a size. */
  attention?: boolean;
  /** Where the number can narrow the table below, what clicking it does. */
  onSelect?: () => void;
};

/**
 * The headline counts above an administration table.
 *
 * A count that is a problem — figures with no number, series nobody has
 * classified — is drawn in the destructive colour only while it is above zero,
 * so a clean lake reads as clean at a glance. Where a count can filter the
 * table it is a button, because "show me those" is the next thing anyone does
 * after reading one.
 */
export function StatRow({ stats, isLoading }: { stats: Stat[]; isLoading?: boolean }) {
  return (
    <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border bg-border sm:grid-cols-4">
      {stats.map((stat) => {
        const body = (
          <>
            <dt className="text-xs text-muted-foreground">{stat.label}</dt>
            <dd
              className={cn(
                "font-heading text-xl font-semibold tabular-nums",
                stat.attention && "text-destructive",
              )}
            >
              {isLoading ? "…" : stat.value}
            </dd>
            {stat.note ? (
              <dd className="text-xs text-muted-foreground">{stat.note}</dd>
            ) : null}
          </>
        );
        return stat.onSelect ? (
          <button
            key={stat.label}
            type="button"
            onClick={stat.onSelect}
            className="space-y-0.5 bg-background p-3 text-left transition-colors hover:bg-muted/50"
          >
            {body}
          </button>
        ) : (
          <div key={stat.label} className="space-y-0.5 bg-background p-3">
            {body}
          </div>
        );
      })}
    </dl>
  );
}
