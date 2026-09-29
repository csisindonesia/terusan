import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  IconArrowLeft,
  IconChartDots3,
  IconDeviceFloppy,
  IconDownload,
  IconPencil,
  IconTable,
  IconTrash,
  IconX,
} from "@tabler/icons-react";
import { useState } from "react";

import { CollectionApi } from "~/components/collection-api";
import { CollectionMembers } from "~/components/collection-members";
import { PageHeader } from "~/components/page-header";
import { StickyHeader } from "~/components/sticky-header";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Textarea } from "~/components/ui/textarea";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount, formatDate } from "~/lib/format";
import {
  ITEM_KINDS,
  ITEM_KIND_LABELS,
  deleteCollection,
  describeSearch,
  itemsOfKind,
  managesCollection,
  removeFromCollection,
  saveQuery,
  updateCollection,
  useCollection,
  useShelf,
  type CollectionItem,
} from "~/lib/workspace";

/**
 * One folder, and what can be done with what is in it.
 *
 * The listing is the smaller half. A folder of eight series is a question
 * someone is asking, and the actions here are the ways of asking it: open all
 * eight in the explorer, keep that as a query, or send them straight to the
 * combiner, which lines their periods up into one table. Without those a
 * collection is a bookmark folder, and a bookmark folder is not worth a page.
 */

export const Route = createFileRoute("/collections/$collectionId")({
  component: CollectionDetail,
});

function CollectionDetail() {
  const { collectionId } = Route.useParams();
  const collection = useCollection(collectionId);
  const shelf = useShelf();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);

  // Until the shelf has said where it lives, a missing folder is a folder that
  // has not arrived yet. Saying "no such collection" during that second is how
  // a shared link looks broken to the person it was sent to.
  if (!collection && shelf.mode === "loading") {
    return (
      <div className="space-y-4">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-9 w-80" />
        <Skeleton className="h-40 w-full rounded-lg" />
      </div>
    );
  }

  if (!collection) {
    return (
      <div className="space-y-4">
        <Link
          to="/collections"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <IconArrowLeft className="size-4" />
          Collections
        </Link>
        <p className="rounded-lg border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">
          {shelf.mode === "server" ? (
            <>
              No collection with this identifier that you are in. It may have been
              deleted, it may be private to someone who has not added you, or the link
              may name a shelf on another deployment.
            </>
          ) : (
            <>
              {/* Without a shelf on the API, storage is per-browser — so a link
                  to a collection is not a link anyone else can open. Saying
                  which is missing is the difference between a bug and a limit. */}
              No collection with this identifier is in this browser, and this deployment
              keeps no collections of its own — so a link to one only opens where it was
              made.
            </>
          )}
        </p>
      </div>
    );
  }

  const indicators = itemsOfKind(collection, "indicator");
  const commodities = itemsOfKind(collection, "commodity");

  /** The explorer filter this folder's figures live behind. */
  const explorerSearch = {
    indicator: indicators.length ? indicators.map((item) => item.id) : undefined,
    commodity: commodities.length ? commodities.map((item) => item.id) : undefined,
  };
  const hasFigures = indicators.length > 0 || commodities.length > 0;
  // A member files and removes records; renaming and deleting are the owner's.
  const manages = managesCollection(collection);

  function exportManifest() {
    if (!collection) return;
    downloadCsv(
      `collection-${collection.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}.csv`,
      toCsv(collection.items as unknown as Record<string, unknown>[], [
        { key: "kind", header: "kind" },
        { key: "id", header: "id" },
        { key: "label", header: "label" },
        { key: "note", header: "note" },
        { key: "added_at", header: "added_at" },
      ]),
    );
  }

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <div className="space-y-2">
            <Link
              to="/collections"
              className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
            >
              <IconArrowLeft className="size-4" />
              Collections
            </Link>

            <PageHeader
              title={collection.name}
              count={collection.items.length}
              description={collection.description}
              actions={
                <>
                  <CollectionMembers collection={collection} />

                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!hasFigures}
                    title={
                      hasFigures
                        ? undefined
                        : "Add series or commodities to open their figures"
                    }
                    onClick={() =>
                      void navigate({ to: "/observations", search: explorerSearch })
                    }
                  >
                    <IconTable className="size-4" />
                    Open figures
                  </Button>

                  <Button
                    variant="outline"
                    size="sm"
                    disabled={indicators.length < 1}
                    title={
                      indicators.length
                        ? undefined
                        : "Add series to line their periods up"
                    }
                    onClick={() =>
                      void navigate({
                        to: "/saved-queries",
                        search: { indicator: indicators.map((item) => item.id) },
                      })
                    }
                  >
                    <IconChartDots3 className="size-4" />
                    Combine series
                  </Button>

                  {/* A read-only shelf keeps the two actions that only read —
                      opening the figures and combining them — and drops the
                      menu that would write. */}
                  {!shelf.writable ? null : (
                    <DropdownMenu>
                      <DropdownMenuTrigger
                        render={
                          <Button
                            variant="outline"
                            size="icon-sm"
                            aria-label="Collection actions"
                          >
                            <IconPencil className="size-4" />
                          </Button>
                        }
                      />
                      <DropdownMenuContent align="end" className="w-52">
                        {manages ? (
                          <DropdownMenuItem onClick={() => setEditing((on) => !on)}>
                            <IconPencil />
                            Rename
                          </DropdownMenuItem>
                        ) : null}
                        <DropdownMenuItem
                          disabled={!hasFigures}
                          onClick={() =>
                            saveQuery({
                              name: collection.name,
                              kind: "observations",
                              path: "/observations",
                              search: explorerSearch,
                              summary: describeSearch(explorerSearch),
                            })
                          }
                        >
                          <IconDeviceFloppy />
                          Save as query
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          disabled={!collection.items.length}
                          onClick={exportManifest}
                        >
                          <IconDownload />
                          Export list (CSV)
                        </DropdownMenuItem>
                        {manages ? (
                          <DropdownMenuItem
                            variant="destructive"
                            onClick={() => {
                              deleteCollection(collection.id);
                              void navigate({ to: "/collections" });
                            }}
                          >
                            <IconTrash />
                            Delete collection
                          </DropdownMenuItem>
                        ) : null}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )}
                </>
              }
            />
          </div>
        }
      />

      {editing ? (
        <form
          className="grid max-w-xl gap-2 rounded-lg border p-3"
          onSubmit={(event) => {
            event.preventDefault();
            const form = event.currentTarget;
            const name = (form.elements.namedItem("name") as HTMLInputElement).value;
            const description = (
              form.elements.namedItem("description") as HTMLTextAreaElement
            ).value;
            updateCollection(collection.id, { name, description });
            setEditing(false);
          }}
        >
          <Input
            name="name"
            autoFocus
            defaultValue={collection.name}
            aria-label="Collection name"
          />
          <Textarea
            name="description"
            defaultValue={collection.description ?? ""}
            placeholder="What this folder is for"
            aria-label="Collection description"
            rows={2}
          />
          <div className="flex justify-end gap-1.5">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setEditing(false)}
            >
              Cancel
            </Button>
            <Button type="submit" size="sm">
              Save
            </Button>
          </div>
        </form>
      ) : null}

      {collection.items.length === 0 ? (
        <p className="rounded-lg border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">
          No indicators here yet. Find series in{" "}
          <Link to="/indicators" className="underline underline-offset-4">
            indicators
          </Link>{" "}
          and use Add to collection in a row's menu.
        </p>
      ) : null}

      <CollectionApi collection={collection} />

      {ITEM_KINDS.map((kind) => {
        const items = itemsOfKind(collection, kind);
        if (!items.length) return null;
        return (
          <section key={kind} className="space-y-2">
            <div className="flex items-center gap-2">
              <h2 className="font-heading text-sm font-medium">
                {ITEM_KIND_LABELS[kind].many}
              </h2>
              <Badge variant="secondary" className="tabular-nums">
                {formatCount(items.length)}
              </Badge>
            </div>
            <div className="divide-y rounded-lg border">
              {items.map((item) => (
                <ItemRow
                  key={`${item.kind}:${item.id}`}
                  item={item}
                  onRemove={
                    shelf.writable
                      ? () => removeFromCollection(collection.id, item.kind, item.id)
                      : undefined
                  }
                />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function ItemRow({
  item,
  onRemove,
}: {
  item: CollectionItem;
  /** Absent where the shelf will not accept the change. */
  onRemove?: () => void;
}) {
  return (
    <div className="flex items-center gap-3 px-3 py-2.5">
      <div className="min-w-0 flex-1 leading-tight">
        <div className="truncate font-medium" title={item.label}>
          <ItemLink item={item} />
        </div>
        <div className="truncate font-mono text-xs text-muted-foreground">
          {item.id}
        </div>
      </div>

      <div className="hidden shrink-0 text-xs text-muted-foreground sm:block">
        {formatDate(item.added_at)}
      </div>

      {onRemove ? (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`Remove ${item.label}`}
          onClick={onRemove}
        >
          <IconX className="size-4" />
        </Button>
      ) : null}
    </div>
  );
}

/**
 * Where a filed record opens.
 *
 * A switch rather than a stored path: the router's links are typed per route,
 * and a `to` read out of storage is a string nobody checked — which is exactly
 * how a folder made last release starts sending readers to 404s.
 */
function ItemLink({ item }: { item: CollectionItem }) {
  const className = "underline-offset-4 hover:underline";

  switch (item.kind) {
    case "indicator":
      return (
        <Link
          to="/indicators/$indicatorId"
          params={{ indicatorId: item.id }}
          className={className}
        >
          {item.label}
        </Link>
      );
    case "dataset":
      return (
        <Link
          to="/datasets/$datasetId"
          params={{ datasetId: item.id }}
          className={className}
        >
          {item.label}
        </Link>
      );
    case "document":
      return (
        <Link
          to="/documents/$documentId"
          params={{ documentId: item.id }}
          className={className}
        >
          {item.label}
        </Link>
      );
    case "regulation":
      return (
        <Link to="/regulations/$key" params={{ key: item.id }} className={className}>
          {item.label}
        </Link>
      );
    case "commodity":
      return (
        <Link
          to="/observations"
          search={{ commodity: [item.id] }}
          className={className}
        >
          {item.label}
        </Link>
      );
    case "topic":
      return (
        <Link to="/topics/$tag" params={{ tag: item.id }} className={className}>
          {item.label}
        </Link>
      );
  }
}
