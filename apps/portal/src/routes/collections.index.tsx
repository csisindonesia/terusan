import { Link, createFileRoute } from "@tanstack/react-router";
import {
  IconDownload,
  IconFolder,
  IconFolderPlus,
  IconPencil,
  IconTrash,
  IconUpload,
} from "@tabler/icons-react";
import { useRef, useState } from "react";

import { PageHeader } from "~/components/page-header";
import { ShelfNote } from "~/components/shelf-note";
import { StickyHeader } from "~/components/sticky-header";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import { Input } from "~/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "~/components/ui/popover";
import { Skeleton } from "~/components/ui/skeleton";
import { Textarea } from "~/components/ui/textarea";
import { formatCount, formatRelative } from "~/lib/format";
import {
  ITEM_KINDS,
  ITEM_KIND_LABELS,
  createCollection,
  deleteCollection,
  exportWorkspace,
  importWorkspace,
  updateCollection,
  useShelf,
  useWorkspace,
  type Collection,
} from "~/lib/workspace";

/**
 * Folders of records, kept in this browser.
 *
 * A research portal's unit of work is rarely one record. "The eight series I
 * need for the fuel-subsidy note" is a real thing a reader holds in their
 * head, and holding it in the portal instead is what turns a catalogue into
 * somewhere work happens. A collection is that folder: a named set of
 * references — series, datasets, documents, regulations, commodities, topics —
 * mixed freely, because the question is the unit, not the kind.
 *
 * References rather than copies, so a folder opened next month shows the
 * warehouse as it is then. And in this browser rather than on the server,
 * because the serving layer is read-only and an account system nobody asked
 * for is worse than a shelf that says plainly where it lives.
 */

export const Route = createFileRoute("/collections/")({
  component: Collections,
});

function Collections() {
  const workspace = useWorkspace();
  const shelf = useShelf();
  const collections = workspace.collections;

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Collections"
            // Nothing rather than zero while the shelf is still arriving: a
            // badge reading 0 on a page that is about to show eleven folders
            // is a wrong answer, where "…" is an honest one.
            count={shelf.mode === "loading" ? undefined : collections.length}
            isLoading={shelf.mode === "loading"}
            description={
              <>
                Folders of records — series, datasets, documents, regulations — gathered
                under one question.
                <ShelfNote className="mt-1" />
              </>
            }
            actions={
              <>
                <ShelfMenu />
                {/* A deployment can serve a curated shelf read-only. The
                    button is left out rather than shown failing. */}
                {shelf.writable || shelf.mode === "loading" ? <NewCollection /> : null}
              </>
            }
          />
        }
      />

      {shelf.mode === "loading" ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton key={index} className="h-32 rounded-sm" />
          ))}
        </div>
      ) : collections.length === 0 ? (
        <div className="rounded-lg border border-dashed px-6 py-12 text-center">
          <IconFolder className="mx-auto size-6 text-muted-foreground" />
          <p className="mt-3 text-sm font-medium">No collections yet</p>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
            Make one here, or file a record straight into a new folder with the collect
            button on a{" "}
            <Link to="/search" className="underline underline-offset-4">
              search result
            </Link>
            .
          </p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {collections.map((collection) => (
            <CollectionCard key={collection.id} collection={collection} />
          ))}
        </div>
      )}
    </div>
  );
}

function CollectionCard({ collection }: { collection: Collection }) {
  const [editing, setEditing] = useState(false);

  // Counted per kind rather than in total: a folder of nine documents and one
  // series is a different thing from nine series and one document, and the
  // total says neither.
  const counts = ITEM_KINDS.map((kind) => ({
    kind,
    count: collection.items.filter((item) => item.kind === kind).length,
  })).filter((entry) => entry.count > 0);

  return (
    <Card className="transition-colors hover:ring-foreground/20">
      <CardHeader>
        <CardTitle>
          <Link
            to="/collections/$collectionId"
            params={{ collectionId: collection.id }}
            className="underline-offset-4 hover:underline"
          >
            {collection.name}
          </Link>
        </CardTitle>
        {collection.description ? (
          <CardDescription className="line-clamp-2">
            {collection.description}
          </CardDescription>
        ) : null}
        <CardAction>
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button variant="ghost" size="icon-sm" aria-label="Collection actions">
                  <IconPencil className="size-4" />
                </Button>
              }
            />
            <DropdownMenuContent align="end" className="w-44">
              <DropdownMenuItem onClick={() => setEditing(true)}>
                <IconPencil />
                Rename
              </DropdownMenuItem>
              <DropdownMenuItem
                variant="destructive"
                onClick={() => deleteCollection(collection.id)}
              >
                <IconTrash />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-3">
        {editing ? (
          <EditForm collection={collection} onDone={() => setEditing(false)} />
        ) : null}

        <div className="flex flex-wrap gap-1.5">
          {counts.length ? (
            counts.map(({ kind, count }) => (
              <Badge key={kind} variant="secondary" className="tabular-nums">
                {count}{" "}
                {count === 1 ? ITEM_KIND_LABELS[kind].one : ITEM_KIND_LABELS[kind].many}
              </Badge>
            ))
          ) : (
            <span className="text-sm text-muted-foreground">Empty</span>
          )}
        </div>

        <p className="text-xs text-muted-foreground">
          {formatCount(collection.items.length)} item
          {collection.items.length === 1 ? "" : "s"}
          {formatRelative(collection.updated_at)
            ? ` · updated ${formatRelative(collection.updated_at)}`
            : null}
        </p>
      </CardContent>
    </Card>
  );
}

function EditForm({
  collection,
  onDone,
}: {
  collection: Collection;
  onDone: () => void;
}) {
  const [name, setName] = useState(collection.name);
  const [description, setDescription] = useState(collection.description ?? "");

  return (
    <form
      className="grid gap-2 rounded-md border p-2"
      onSubmit={(event) => {
        event.preventDefault();
        updateCollection(collection.id, { name, description });
        onDone();
      }}
    >
      <Input
        autoFocus
        value={name}
        onChange={(event) => setName(event.target.value)}
        aria-label="Collection name"
        className="h-8"
      />
      <Textarea
        value={description}
        onChange={(event) => setDescription(event.target.value)}
        placeholder="What this folder is for"
        aria-label="Collection description"
        rows={2}
      />
      <div className="flex justify-end gap-1.5">
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" size="sm">
          Save
        </Button>
      </div>
    </form>
  );
}

function NewCollection() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button size="sm">
            <IconFolderPlus className="size-4" />
            New collection
          </Button>
        }
      />
      <PopoverContent align="end" className="w-80 p-3">
        <form
          className="grid gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (!name.trim()) return;
            createCollection(name, description);
            setName("");
            setDescription("");
            setOpen(false);
          }}
        >
          <Input
            autoFocus
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Name"
            aria-label="Collection name"
          />
          <Textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="What this folder is for (optional)"
            aria-label="Collection description"
            rows={2}
          />
          <Button type="submit" size="sm" disabled={!name.trim()}>
            Create
          </Button>
        </form>
      </PopoverContent>
    </Popover>
  );
}

/**
 * Moving the shelf between browsers.
 *
 * Storage in the browser means one browser, which is a real limit rather than
 * a detail to hide: the export is how a reader takes their folders to another
 * machine, or keeps them before clearing site data. Import merges rather than
 * replaces, because the usual case is carrying folders *to* a browser that
 * already has some.
 */
function ShelfMenu() {
  const file = useRef<HTMLInputElement>(null);
  const [note, setNote] = useState<string>();

  function download() {
    const blob = new Blob([exportWorkspace()], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "terusan-collections.json";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function load(event: React.ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0];
    if (!chosen) return;
    try {
      const added = importWorkspace(await chosen.text());
      setNote(
        `Imported ${added.collections} collection${
          added.collections === 1 ? "" : "s"
        } and ${added.queries} quer${added.queries === 1 ? "y" : "ies"}.`,
      );
    } catch {
      setNote("That file is not a collections export.");
    }
    // Cleared so choosing the same file again still fires a change.
    event.target.value = "";
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button variant="outline" size="sm">
              Shelf
            </Button>
          }
        />
        <DropdownMenuContent align="end" className="w-52">
          <DropdownMenuItem onClick={download}>
            <IconDownload />
            Export collections
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => file.current?.click()}>
            <IconUpload />
            Import
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <input
        ref={file}
        type="file"
        accept="application/json"
        className="hidden"
        onChange={(event) => void load(event)}
      />

      {note ? <span className="text-xs text-muted-foreground">{note}</span> : null}
    </>
  );
}
