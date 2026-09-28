import { IconCheck, IconFolderPlus, IconPlus } from "@tabler/icons-react";
import { useState } from "react";

import { Button } from "~/components/ui/button";
import {
  DropdownMenuCheckboxItem,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "~/components/ui/dropdown-menu";
import { Input } from "~/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "~/components/ui/popover";
import {
  addToCollection,
  createCollection,
  holdersOf,
  removeFromCollection,
  useWorkspace,
  type NewItem,
} from "~/lib/workspace";
import { cn } from "~/lib/utils";

/**
 * File a record — or a whole selection of them — into a collection.
 *
 * A popover rather than a page: filing is something a reader does *while*
 * reading, and a round trip to a collections page to do it would mean losing
 * the row they were looking at. Creating a collection is offered in the same
 * place for the same reason — the first record is usually what makes someone
 * want a folder at all.
 *
 * A tick means every record here is already in that collection. With several
 * selected they need not agree, so a partial one is drawn hollow and clicking
 * it files the rest rather than removing what is filed.
 */
export function CollectButton({
  items,
  size = "sm",
  variant = "outline",
  label,
  className,
}: {
  items: NewItem[];
  size?: "sm" | "icon-sm";
  variant?: "outline" | "ghost";
  /** Overrides the default, which counts what would be filed. */
  label?: string;
  className?: string;
}) {
  const workspace = useWorkspace();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);

  const disabled = items.length === 0;
  const text = label ?? (items.length > 1 ? `Collect ${items.length}` : "Collect");

  /** Which of `items` a collection already holds. */
  function heldBy(collectionId: string): number {
    return items.filter((item) =>
      holdersOf(workspace, item.kind, item.id).has(collectionId),
    ).length;
  }

  function toggle(collectionId: string) {
    const held = heldBy(collectionId);
    if (held === items.length) {
      for (const item of items) removeFromCollection(collectionId, item.kind, item.id);
    } else {
      addToCollection(collectionId, items);
    }
  }

  function create(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    const collection = createCollection(trimmed);
    addToCollection(collection.id, items);
    setName("");
    setCreating(false);
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant={variant}
            size={size}
            disabled={disabled}
            className={className}
            aria-label={size === "icon-sm" ? text : undefined}
          >
            <IconFolderPlus className="size-4" />
            {size === "icon-sm" ? null : text}
          </Button>
        }
      />
      <PopoverContent align="end" className="w-72 p-2">
        <div className="grid gap-0.5">
          {workspace.collections.length === 0 ? (
            <p className="px-2 py-3 text-sm text-muted-foreground">
              No collections yet. A collection is a folder of records you keep in this
              browser.
            </p>
          ) : null}

          <div className="grid max-h-64 gap-0.5 overflow-y-auto">
            {workspace.collections.map((collection) => {
              const held = heldBy(collection.id);
              const all = held === items.length && held > 0;
              return (
                <button
                  key={collection.id}
                  type="button"
                  onClick={() => toggle(collection.id)}
                  aria-pressed={all}
                  className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted"
                >
                  <span
                    className={cn(
                      "flex size-4 shrink-0 items-center justify-center rounded-[4px] border",
                      all
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-input",
                    )}
                  >
                    {all ? <IconCheck className="size-3" /> : null}
                  </span>
                  <span className="truncate">{collection.name}</span>
                  <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                    {/* What is in the folder, or — where a selection is partly
                        filed — how much of the selection is. */}
                    {held && !all ? `${held}/${items.length}` : collection.items.length}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="my-1 border-t" />

          {creating || !workspace.collections.length ? (
            <form onSubmit={create} className="flex items-center gap-1.5 p-1">
              <Input
                autoFocus
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Collection name"
                aria-label="Collection name"
                className="h-8"
              />
              <Button type="submit" size="sm" disabled={!name.trim()}>
                Create
              </Button>
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setCreating(true)}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <IconPlus className="size-4" />
              New collection
            </button>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

/**
 * The same filing, as a submenu of a row's menu: on a table the popover's
 * button would be one more control on every row, and the three dots are where
 * a reader already looks for what can be done with one.
 */
export function CollectSubmenu({ items }: { items: NewItem[] }) {
  const workspace = useWorkspace();
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);

  function create(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    const collection = createCollection(trimmed);
    addToCollection(collection.id, items);
    setName("");
    setCreating(false);
  }

  return (
    <DropdownMenuSub>
      <DropdownMenuSubTrigger>
        <IconFolderPlus />
        Add to collection
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent className="w-60">
        <div className="max-h-64 overflow-y-auto">
          {workspace.collections.map((collection) => {
            const held = items.every((item) =>
              holdersOf(workspace, item.kind, item.id).has(collection.id),
            );
            return (
              <DropdownMenuCheckboxItem
                key={collection.id}
                checked={held}
                onCheckedChange={(checked) => {
                  if (checked) addToCollection(collection.id, items);
                  else
                    for (const item of items)
                      removeFromCollection(collection.id, item.kind, item.id);
                }}
              >
                <span className="truncate">{collection.name}</span>
              </DropdownMenuCheckboxItem>
            );
          })}
        </div>
        {workspace.collections.length ? <DropdownMenuSeparator /> : null}
        {creating || !workspace.collections.length ? (
          <form
            onSubmit={create}
            // The menu reads keys for typeahead and arrow navigation; typing a
            // name here is not either.
            onKeyDown={(event) => event.stopPropagation()}
            className="flex items-center gap-1.5 p-1"
          >
            <Input
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Collection name"
              aria-label="Collection name"
              className="h-8"
            />
            <Button type="submit" size="sm" disabled={!name.trim()}>
              Create
            </Button>
          </form>
        ) : (
          <DropdownMenuItem closeOnClick={false} onClick={() => setCreating(true)}>
            <IconPlus />
            New collection
          </DropdownMenuItem>
        )}
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  );
}
