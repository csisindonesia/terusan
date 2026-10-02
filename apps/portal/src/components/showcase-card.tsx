import { Link } from "@tanstack/react-router";
import { IconExternalLink, IconPencil, IconTrash } from "@tabler/icons-react";

import { RowActions } from "~/components/row-actions";
import { Badge } from "~/components/ui/badge";
import type { ShowcaseEntry } from "~/lib/api";
import { formatDate } from "~/lib/format";
import { titleFromId } from "~/lib/labels";
import { hostOf, isInternal, kindIcon, kindLabel } from "~/lib/showcase";

/**
 * One piece of work built on the data, as a card.
 *
 * The title opens the work itself, on its own site: the card is a pointer, and
 * a reader who clicks the most prominent thing on it expects to arrive at the
 * dashboard, not at another page about it. The datasets underneath lead back
 * into the warehouse.
 */
export function ShowcaseCard({
  entry,
  onEdit,
  onDelete,
  hideDataset,
}: {
  entry: ShowcaseEntry;
  /** Set for an administrator; the card then offers to change the entry. */
  onEdit?: () => void;
  onDelete?: () => void;
  /** The dataset whose page this is, left out of the list it would repeat. */
  hideDataset?: string;
}) {
  const Icon = kindIcon(entry.kind);
  const internal = isInternal(entry.url);
  const datasets = entry.datasets.filter((d) => d.dataset_id !== hideDataset);

  return (
    <article className="flex flex-col gap-3 rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Badge variant="outline" className="gap-1">
            <Icon />
            {kindLabel(entry.kind)}
          </Badge>
          {/* Said rather than left blank: an undated entry is usually a
              dashboard that is kept current, and a gap reads as missing data. */}
          <span>{entry.published_on ? formatDate(entry.published_on) : "Undated"}</span>
        </div>
        {onEdit || onDelete ? (
          <RowActions
            bare
            label={`Actions for ${entry.title}`}
            actions={[
              { label: "Edit", icon: IconPencil, onSelect: onEdit },
              {
                label: "Delete",
                icon: IconTrash,
                destructive: true,
                onSelect: onDelete,
              },
            ]}
          />
        ) : null}
      </div>

      <div className="space-y-1">
        <a
          href={entry.url}
          // One of the Portal's own articles opens in place; anything else is
          // somebody else's site, and opens beside this one.
          {...(internal ? {} : { target: "_blank", rel: "noopener noreferrer" })}
          className="group inline-flex items-start gap-1 font-heading text-base leading-snug font-semibold text-balance underline-offset-4 hover:underline"
        >
          {entry.title}
          {internal ? null : (
            <IconExternalLink className="mt-1 size-3.5 shrink-0 text-muted-foreground group-hover:text-foreground" />
          )}
        </a>
        <p className="text-sm text-muted-foreground">
          {entry.publisher ? `${entry.publisher} · ` : null}
          {hostOf(entry.url)}
        </p>
      </div>

      {entry.description ? (
        <p className="line-clamp-3 text-sm">{entry.description}</p>
      ) : null}

      {datasets.length ? (
        <div className="mt-auto space-y-1.5 border-t pt-3">
          <p className="text-xs font-medium text-muted-foreground">
            {hideDataset ? "Also uses" : "Uses"}
          </p>
          <ul className="flex flex-wrap gap-1.5">
            {datasets.map((d) => (
              <li key={d.dataset_id}>
                {d.title ? (
                  <Link
                    to="/datasets/$datasetId"
                    params={{ datasetId: d.dataset_id }}
                    className="inline-flex max-w-[18rem] truncate rounded-md bg-muted px-2 py-0.5 text-xs hover:bg-muted/70 hover:underline"
                  >
                    {d.title}
                  </Link>
                ) : (
                  // Kept rather than dropped, so the claim the entry makes
                  // stays visible after a dataset is retired.
                  <span
                    className="inline-flex rounded-md border border-dashed px-2 py-0.5 text-xs text-muted-foreground"
                    title="This dataset is no longer in the warehouse"
                  >
                    {titleFromId(d.dataset_id)} · no longer held
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </article>
  );
}
