import {
  IconDimensions,
  IconGripVertical,
  IconMathFunction,
  IconPlus,
  IconTrash,
  IconX,
} from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";

import {
  BlockView,
  DASHBOARD_GRID,
  sizeOf,
  tileClass,
  type Resolve,
} from "~/components/article-blocks";
import {
  AutoText,
  BlockMenu,
  FigureBuilder,
  Line,
  Settings,
  isProse,
  uid,
  type Item,
} from "~/components/article-editor";
import { Button } from "~/components/ui/button";
import type { ArticleBlock } from "~/lib/api";
import { cn } from "cn";

/**
 * A dashboard, edited on its own grid.
 *
 * Each tile shows as a reader sees it. Hovering one offers its size, a handle
 * to drag it to another place, and delete; clicking it opens its settings in
 * a panel at the side, so the tile updates in place while it is set up. The
 * last cell of the grid adds a tile.
 */
export function DashboardEditor({
  items,
  onChange,
  resolve,
}: {
  items: Item[];
  onChange: (items: Item[]) => void;
  resolve: Resolve;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [sizing, setSizing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<{ uid: string; after: boolean } | null>(null);

  const replace = (id: string, block: ArticleBlock) =>
    onChange(items.map((i) => (i.uid === id ? { ...i, block } : i)));
  const remove = (id: string) => {
    onChange(items.filter((i) => i.uid !== id));
    if (selected === id) setSelected(null);
  };
  const drop = () => {
    if (!dragging || !over || dragging === over.uid) return;
    const item = items.find((i) => i.uid === dragging);
    if (!item) return;
    const rest = items.filter((i) => i.uid !== dragging);
    const at = rest.findIndex((i) => i.uid === over.uid) + (over.after ? 1 : 0);
    onChange([...rest.slice(0, at), item, ...rest.slice(at)]);
  };
  const current = items.find((i) => i.uid === selected);

  // Delete or Backspace removes the selected tile, unless a field has the keys.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!selected) return;
      const target = e.target as HTMLElement;
      if (target.closest("input, textarea, [contenteditable], [role=listbox]")) return;
      if (e.key === "Backspace" || e.key === "Delete") {
        e.preventDefault();
        remove(selected);
      } else if (e.key === "Escape") {
        setSelected(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <>
      <div
        className={cn(
          "markdown article dashboard",
          DASHBOARD_GRID,
          current && "lg:mr-[26rem]",
        )}
      >
        {items.map((item) => {
          const block = item.block;
          const size = sizeOf(block);
          const isSelected = selected === item.uid;
          return (
            <div
              key={item.uid}
              className={cn(
                tileClass(block),
                "group/tile relative cursor-pointer transition-shadow",
                !tileClass(block).includes("dashboard-tile") && "rounded-lg px-1",
                isSelected ? "ring-2 ring-primary" : "hover:ring-1 hover:ring-border",
                dragging === item.uid && "opacity-40",
              )}
              onClick={(e) => {
                if ((e.target as HTMLElement).closest("button, a, input")) return;
                setSelected(item.uid);
              }}
              onDragOver={(e) => {
                if (!dragging) return;
                e.preventDefault();
                const box = e.currentTarget.getBoundingClientRect();
                setOver({ uid: item.uid, after: e.clientX > box.left + box.width / 2 });
              }}
              onDrop={(e) => {
                e.preventDefault();
                drop();
                setDragging(null);
                setOver(null);
              }}
            >
              {over?.uid === item.uid ? (
                <div
                  className={cn(
                    "pointer-events-none absolute inset-y-1 w-1 rounded bg-primary",
                    over.after ? "-right-2" : "-left-2",
                  )}
                />
              ) : null}

              {/* The tile's tools, over its corner. */}
              <div
                className={cn(
                  "absolute top-2 right-2 z-10 flex items-center gap-0.5 rounded-md bg-background/90 p-0.5 opacity-0 shadow-sm ring-1 ring-border transition-opacity group-hover/tile:opacity-100",
                  (isSelected || sizing === item.uid) && "opacity-100",
                )}
              >
                <button
                  type="button"
                  draggable
                  aria-label="Drag to move"
                  className="flex size-7 cursor-grab items-center justify-center rounded text-muted-foreground hover:bg-accent active:cursor-grabbing"
                  onDragStart={(e) => {
                    setDragging(item.uid);
                    e.dataTransfer.effectAllowed = "move";
                  }}
                  onDragEnd={() => {
                    setDragging(null);
                    setOver(null);
                  }}
                >
                  <IconGripVertical className="size-4" />
                </button>
                <button
                  type="button"
                  aria-label={`Size, now ${size.width} by ${size.height}`}
                  className="flex h-7 items-center gap-1 rounded px-1.5 text-xs text-muted-foreground tabular-nums hover:bg-accent"
                  onClick={() => setSizing(sizing === item.uid ? null : item.uid)}
                >
                  <IconDimensions className="size-4" />
                  {size.width}×{size.height}
                </button>
                <button
                  type="button"
                  aria-label="Delete tile"
                  className="flex size-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-destructive"
                  onClick={() => remove(item.uid)}
                >
                  <IconTrash className="size-4" />
                </button>
              </div>
              {sizing === item.uid ? (
                <SizePicker
                  width={size.width}
                  height={size.height}
                  onClose={() => setSizing(null)}
                  onPick={(width, height) => {
                    replace(item.uid, { ...block, width, height });
                    setSizing(null);
                  }}
                />
              ) : null}

              <div className="pointer-events-none h-full">
                {isProse(block) && !block.markdown ? (
                  <p className="text-sm text-muted-foreground">
                    Empty {block.type}. Click to write it.
                  </p>
                ) : (
                  <BlockView block={block} resolve={resolve} />
                )}
              </div>
            </div>
          );
        })}

        <div className="relative col-span-2 md:col-span-1">
          <button
            type="button"
            className="flex h-full min-h-28 w-full items-center justify-center gap-2 rounded-lg border border-dashed text-sm text-muted-foreground hover:border-foreground/30 hover:text-foreground"
            onClick={() => setAdding(true)}
          >
            <IconPlus className="size-4" />
            Add a tile
          </button>
          {adding ? (
            <BlockMenu
              onClose={() => setAdding(false)}
              onPick={(choice) => {
                setAdding(false);
                const item = { uid: uid(), block: choice.make() };
                onChange([...items, item]);
                setSelected(item.uid);
              }}
            />
          ) : null}
        </div>
      </div>

      {current ? (
        <aside className="fixed top-(--app-header) right-0 bottom-0 z-30 w-full overflow-y-auto border-l bg-background p-4 shadow-lg sm:w-[26rem]">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold">
              {current.block.type === "stat"
                ? "Stat"
                : current.block.type[0]?.toUpperCase() + current.block.type.slice(1)}
            </h2>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Close"
              onClick={() => setSelected(null)}
            >
              <IconX />
            </Button>
          </div>
          <div className="space-y-4 text-sm">
            {isProse(current.block) ? (
              <ProseSettings
                block={current.block}
                onChange={(b) => replace(current.uid, b)}
              />
            ) : (
              <Settings
                block={current.block}
                onChange={(b) => replace(current.uid, b)}
              />
            )}
            <div className="flex items-center justify-between border-t pt-4">
              <Button
                size="sm"
                variant="ghost"
                className="text-destructive"
                onClick={() => remove(current.uid)}
              >
                <IconTrash className="size-4" />
                Delete tile
              </Button>
              <Button size="sm" variant="outline" onClick={() => setSelected(null)}>
                Done
              </Button>
            </div>
          </div>
        </aside>
      ) : null}
    </>
  );
}

/** Choose a tile's span by pointing at it on a small copy of the grid. */
function SizePicker({
  width,
  height,
  onPick,
  onClose,
}: {
  width: number;
  height: number;
  onPick: (width: number, height: number) => void;
  onClose: () => void;
}) {
  const [hover, setHover] = useState<[number, number] | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const away = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) onClose();
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [onClose]);
  const [w, h] = hover ?? [width, height];
  return (
    <div
      ref={box}
      className="absolute top-11 right-2 z-20 rounded-lg bg-popover p-2 shadow-md ring-1 ring-foreground/10"
      onMouseLeave={() => setHover(null)}
    >
      <div className="grid grid-cols-4 gap-1">
        {[1, 2, 3].map((row) =>
          [1, 2, 3, 4].map((col) => (
            <button
              key={`${col}-${row}`}
              type="button"
              aria-label={`${col} by ${row}`}
              className={cn(
                "size-6 rounded-sm border",
                col <= w && row <= h ? "border-primary bg-primary/20" : "bg-muted/40",
              )}
              onMouseEnter={() => setHover([col, row])}
              onClick={() => onPick(col, row)}
            />
          )),
        )}
      </div>
      <p className="mt-1.5 text-center text-xs text-muted-foreground tabular-nums">
        {w} column{w > 1 ? "s" : ""} × {h} row{h > 1 ? "s" : ""}
      </p>
    </div>
  );
}

/** Prose on a tile, written in the side panel. */
function ProseSettings({
  block,
  onChange,
}: {
  block: Extract<ArticleBlock, { markdown: string }>;
  onChange: (block: ArticleBlock) => void;
}) {
  const [figure, setFigure] = useState(false);
  const area = useRef<HTMLTextAreaElement | null>(null);
  return (
    <div className="space-y-3">
      {block.type === "heading" ? (
        <div className="flex gap-2">
          {([2, 3] as const).map((level) => (
            <Button
              key={level}
              size="sm"
              variant={block.level === level ? "secondary" : "ghost"}
              onClick={() => onChange({ ...block, level })}
            >
              {level === 2 ? "Heading" : "Subheading"}
            </Button>
          ))}
        </div>
      ) : null}
      <label className="block space-y-1">
        <span className="text-xs text-muted-foreground">
          Markdown; figure tokens are filled in
        </span>
        <AutoText
          value={block.markdown}
          inputRef={(node) => {
            area.current = node;
          }}
          autoFocus
          className="min-h-24 rounded-md border p-2 text-sm"
          onChange={(e) => onChange({ ...block, markdown: e.target.value })}
        />
      </label>
      {block.type === "quote" ? (
        <Line
          label="Who said it (optional)"
          value={block.note ?? ""}
          onChange={(note) => onChange({ ...block, note })}
        />
      ) : null}
      <button
        type="button"
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        onClick={() => setFigure((open) => !open)}
      >
        <IconMathFunction className="size-3.5" />
        Insert a figure
      </button>
      {figure ? (
        <FigureBuilder
          onClose={() => setFigure(false)}
          onInsert={(token) => {
            const at = area.current?.selectionStart ?? block.markdown.length;
            onChange({
              ...block,
              markdown: block.markdown.slice(0, at) + token + block.markdown.slice(at),
            });
            setFigure(false);
          }}
        />
      ) : null}
    </div>
  );
}
