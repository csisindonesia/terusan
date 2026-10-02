import { useQuery } from "@tanstack/react-query";
import {
  IconBlockquote,
  IconChartLine,
  IconDatabase,
  IconGavel,
  IconGripVertical,
  IconH2,
  IconH3,
  IconInfoCircle,
  IconLayoutDashboard,
  IconLetterT,
  IconMathFunction,
  IconNumber,
  IconPlus,
  IconSeparator,
  IconSuperscript,
  IconTable,
  IconTrash,
  type Icon,
} from "@tabler/icons-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { builtCharts } from "~/articles";
import { BlockView, type Resolve } from "~/components/article-blocks";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import {
  api,
  type ArticleBlock,
  type ArticleBlockType,
  type ArticleQuery,
} from "~/lib/api";
import { datasetLabel, indicatorLabel } from "~/lib/labels";
import { cn } from "cn";

/**
 * The article editor: the page itself, written in place.
 *
 * Each block is shown as a reader will see it and turns into its editor when
 * clicked. A `+` in the margin, or `/` typed into an empty line, opens the
 * block menu; the handle beside it drags a block, or opens its own menu. Prose
 * blocks take markdown, with the shortcuts a writer expects — `# ` for a
 * heading, `> ` for a quote, `---` for a divider — and Enter starts the next
 * block. Everything else a block needs (a chart's series, a table's cells, the
 * regulation it cites) is set in a panel under it while it is selected.
 */

export type Item = { uid: string; block: ArticleBlock };

let counter = 0;
export const uid = () => `b${Date.now().toString(36)}${(counter++).toString(36)}`;

export const PROSE: ArticleBlockType[] = [
  "text",
  "heading",
  "quote",
  "callout",
  "footnote",
];
export const isProse = (
  block: ArticleBlock,
): block is Extract<ArticleBlock, { markdown: string }> => PROSE.includes(block.type);

export type Choice = {
  key: string;
  label: string;
  hint: string;
  icon: Icon;
  group: "Write" | "Data" | "Warehouse";
  make: () => ArticleBlock;
};

export const CHOICES: Choice[] = [
  {
    key: "text",
    label: "Text",
    hint: "A paragraph, lists, links",
    icon: IconLetterT,
    group: "Write",
    make: () => ({ type: "text", markdown: "" }),
  },
  {
    key: "heading",
    label: "Heading",
    hint: "A section",
    icon: IconH2,
    group: "Write",
    make: () => ({ type: "heading", markdown: "", level: 2 }),
  },
  {
    key: "subheading",
    label: "Subheading",
    hint: "A part of a section",
    icon: IconH3,
    group: "Write",
    make: () => ({ type: "heading", markdown: "", level: 3 }),
  },
  {
    key: "quote",
    label: "Quote",
    hint: "Somebody's words",
    icon: IconBlockquote,
    group: "Write",
    make: () => ({ type: "quote", markdown: "" }),
  },
  {
    key: "callout",
    label: "Callout",
    hint: "A note set apart",
    icon: IconInfoCircle,
    group: "Write",
    make: () => ({ type: "callout", markdown: "" }),
  },
  {
    key: "footnote",
    label: "Footnote",
    hint: "Listed under the article; cite as [^1]",
    icon: IconSuperscript,
    group: "Write",
    make: () => ({ type: "footnote", markdown: "" }),
  },
  {
    key: "divider",
    label: "Divider",
    hint: "A break between parts",
    icon: IconSeparator,
    group: "Write",
    make: () => ({ type: "divider" }),
  },
  {
    key: "chart",
    label: "Chart",
    hint: "Series from the warehouse, drawn live",
    icon: IconChartLine,
    group: "Data",
    make: () => ({ type: "chart", title: "", query: { indicator: [] } }),
  },
  {
    key: "table",
    label: "Table",
    hint: "Rows and columns; cells take figures",
    icon: IconTable,
    group: "Data",
    make: () => ({
      type: "table",
      header: true,
      rows: [
        ["", ""],
        ["", ""],
      ],
    }),
  },
  {
    key: "stat",
    label: "Stat",
    hint: "One number, large, with what it is",
    icon: IconNumber,
    group: "Data",
    make: () => ({ type: "stat", title: "", markdown: "", note: "" }),
  },
  {
    key: "embed",
    label: "Built chart",
    hint: "A chart from an analysis built in code",
    icon: IconLayoutDashboard,
    group: "Data",
    make: () => {
      const first = builtCharts()[0];
      return {
        type: "embed",
        article: first?.article ?? "",
        chart: first?.chart ?? "",
      };
    },
  },
  {
    key: "regulation",
    label: "Regulation",
    hint: "Cite one from the warehouse",
    icon: IconGavel,
    group: "Warehouse",
    make: () => ({ type: "regulation", key: "" }),
  },
  {
    key: "dataset",
    label: "Dataset",
    hint: "Point to one in the warehouse",
    icon: IconDatabase,
    group: "Warehouse",
    make: () => ({ type: "dataset", dataset: "" }),
  },
];

const TURN_INTO = CHOICES.filter((c) =>
  ["text", "heading", "subheading", "quote", "callout", "footnote"].includes(c.key),
);

/** A textarea that grows with what is in it, so a block is as tall as its text. */
export function AutoText({
  value,
  className,
  inputRef,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement> & {
  value: string;
  inputRef?: (node: HTMLTextAreaElement | null) => void;
}) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${node.scrollHeight}px`;
  }, [value]);
  return (
    <textarea
      ref={(node) => {
        ref.current = node;
        inputRef?.(node);
      }}
      rows={1}
      value={value}
      className={cn(
        "block w-full resize-none overflow-hidden bg-transparent outline-none placeholder:text-muted-foreground/60",
        className,
      )}
      {...props}
    />
  );
}

/** The menu of blocks, filtered as the writer types. */
export function BlockMenu({
  onPick,
  onClose,
}: {
  onPick: (choice: Choice) => void;
  onClose: () => void;
}) {
  const [filter, setFilter] = useState("");
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const shown = CHOICES.filter((c) =>
    `${c.label} ${c.hint} ${c.key}`.toLowerCase().includes(filter.toLowerCase()),
  );
  useEffect(() => {
    const away = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) onClose();
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [onClose]);

  return (
    <div
      ref={box}
      className="absolute top-full left-0 z-30 mt-1 w-72 rounded-lg bg-popover p-1.5 text-sm shadow-md ring-1 ring-foreground/10"
    >
      <input
        autoFocus
        value={filter}
        placeholder="Filter blocks…"
        className="mb-1 w-full rounded-md bg-muted/50 px-2 py-1.5 text-sm outline-none"
        onChange={(e) => {
          setFilter(e.target.value);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((n) => Math.min(shown.length - 1, n + 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((n) => Math.max(0, n - 1));
          } else if (e.key === "Enter") {
            e.preventDefault();
            const choice = shown[active];
            if (choice) onPick(choice);
          } else if (e.key === "Escape") {
            e.preventDefault();
            onClose();
          }
        }}
      />
      <div className="max-h-80 overflow-y-auto">
        {(["Write", "Data", "Warehouse"] as const).map((group) => {
          const items = shown.filter((c) => c.group === group);
          if (!items.length) return null;
          return (
            <div key={group} className="py-1">
              <div className="px-2 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                {group}
              </div>
              {items.map((c) => {
                const index = shown.indexOf(c);
                return (
                  <button
                    key={c.key}
                    type="button"
                    className={cn(
                      "flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left",
                      index === active ? "bg-accent" : "hover:bg-accent/60",
                    )}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => onPick(c)}
                  >
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-md border bg-background">
                      <c.icon className="size-4" />
                    </span>
                    <span className="min-w-0">
                      <span className="block font-medium">{c.label}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {c.hint}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          );
        })}
        {!shown.length ? (
          <p className="px-2 py-3 text-muted-foreground">No block by that name.</p>
        ) : null}
      </div>
    </div>
  );
}

/** What the handle offers for one block. */
function RowMenu({
  block,
  first,
  last,
  onTurn,
  onDuplicate,
  onMove,
  onDelete,
  onClose,
}: {
  block: ArticleBlock;
  first: boolean;
  last: boolean;
  onTurn: (choice: Choice) => void;
  onDuplicate: () => void;
  onMove: (by: number) => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const away = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) onClose();
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [onClose]);
  const item =
    "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-accent disabled:opacity-40";
  return (
    <div
      ref={box}
      className="absolute top-full left-0 z-30 mt-1 w-56 rounded-lg bg-popover p-1.5 text-sm shadow-md ring-1 ring-foreground/10"
    >
      {isProse(block) ? (
        <>
          <div className="px-2 pt-1 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            Turn into
          </div>
          {TURN_INTO.map((c) => (
            <button
              key={c.key}
              type="button"
              className={item}
              onClick={() => onTurn(c)}
            >
              <c.icon className="size-4 text-muted-foreground" />
              {c.label}
            </button>
          ))}
          <div className="my-1 border-t" />
        </>
      ) : null}
      <button type="button" className={item} onClick={onDuplicate}>
        Duplicate
      </button>
      <button
        type="button"
        className={item}
        disabled={first}
        onClick={() => onMove(-1)}
      >
        Move up
      </button>
      <button type="button" className={item} disabled={last} onClick={() => onMove(1)}>
        Move down
      </button>
      <button type="button" className={cn(item, "text-destructive")} onClick={onDelete}>
        Delete
      </button>
    </div>
  );
}

type Focus = { uid: string; at: "start" | "end" | number } | null;

/**
 * The blocks of an article, edited in place. `items` carry a client-side uid
 * each, so focus and drag follow a block as blocks around it move.
 */
export function BlockEditor({
  items,
  onChange,
  resolve,
  footnotes,
  focus,
  setFocus,
}: {
  items: Item[];
  onChange: (items: Item[]) => void;
  resolve: Resolve;
  /** Each footnote block's number, by uid. */
  footnotes: Map<string, number>;
  focus: Focus;
  setFocus: (focus: Focus) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [menu, setMenu] = useState<{
    uid: string;
    mode: "insert" | "replace" | "row";
  } | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<{ uid: string; after: boolean } | null>(null);
  const inputs = useRef(new Map<string, HTMLTextAreaElement>());

  // A focus request from outside or from a keystroke that made a new block:
  // honoured once the block is on the page.
  useEffect(() => {
    if (!focus) return;
    setEditing(focus.uid);
    const node = inputs.current.get(focus.uid);
    if (!node) return;
    node.focus();
    const at =
      focus.at === "start" ? 0 : focus.at === "end" ? node.value.length : focus.at;
    node.setSelectionRange(at, at);
    setFocus(null);
  }, [focus, items, setFocus]);

  const index = (id: string) => items.findIndex((i) => i.uid === id);
  const replace = (id: string, block: ArticleBlock) =>
    onChange(items.map((i) => (i.uid === id ? { ...i, block } : i)));
  const insertAfter = (id: string | null, block: ArticleBlock) => {
    const item = { uid: uid(), block };
    const at = id === null ? items.length : index(id) + 1;
    onChange([...items.slice(0, at), item, ...items.slice(at)]);
    if (isProse(block)) setFocus({ uid: item.uid, at: "start" });
    else setEditing(item.uid);
    return item;
  };
  const remove = (id: string) => {
    const at = index(id);
    const next = items.filter((i) => i.uid !== id);
    onChange(
      next.length ? next : [{ uid: uid(), block: { type: "text", markdown: "" } }],
    );
    const previous = next[Math.max(0, at - 1)];
    if (previous && isProse(previous.block)) setFocus({ uid: previous.uid, at: "end" });
  };
  const move = (id: string, by: number) => {
    const at = index(id);
    const next = [...items];
    const [item] = next.splice(at, 1);
    if (!item) return;
    next.splice(Math.max(0, Math.min(next.length, at + by)), 0, item);
    onChange(next);
  };
  const drop = (target: string, after: boolean) => {
    if (!dragging || dragging === target) return;
    const item = items.find((i) => i.uid === dragging);
    if (!item) return;
    const rest = items.filter((i) => i.uid !== dragging);
    const at = rest.findIndex((i) => i.uid === target) + (after ? 1 : 0);
    onChange([...rest.slice(0, at), item, ...rest.slice(at)]);
  };
  const turn = (id: string, choice: Choice) => {
    const current = items[index(id)]?.block;
    const made = choice.make();
    // Prose keeps its words when it changes kind.
    if (current && isProse(current) && isProse(made)) made.markdown = current.markdown;
    replace(id, made);
    if (isProse(made)) setFocus({ uid: id, at: "end" });
    else setEditing(id);
  };
  const neighbour = (id: string, by: number) => {
    for (let n = index(id) + by; n >= 0 && n < items.length; n += by) {
      const candidate = items[n];
      if (candidate && isProse(candidate.block)) return candidate;
    }
    return null;
  };

  const onKeyDown = (item: Item, event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const block = item.block;
    if (!isProse(block)) return;
    const node = event.currentTarget;
    const { selectionStart: start, selectionEnd: end, value } = node;
    if (event.key === "/" && value === "") {
      event.preventDefault();
      setMenu({ uid: item.uid, mode: "replace" });
    } else if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      // A new block, carrying whatever followed the cursor.
      event.preventDefault();
      if (block.type === "text" && value === "---") {
        replace(item.uid, { type: "divider" });
        insertAfter(item.uid, { type: "text", markdown: "" });
        return;
      }
      const next: ArticleBlock = { type: "text", markdown: value.slice(end) };
      const kept = { ...block, markdown: value.slice(0, start) };
      const made = { uid: uid(), block: next };
      const at = index(item.uid);
      onChange([
        ...items.slice(0, at),
        { ...item, block: kept },
        made,
        ...items.slice(at + 1),
      ]);
      setFocus({ uid: made.uid, at: "start" });
    } else if (event.key === "Backspace" && start === 0 && end === 0) {
      if (value === "") {
        event.preventDefault();
        remove(item.uid);
      } else if (block.type !== "text") {
        event.preventDefault();
        replace(item.uid, { type: "text", markdown: value });
      } else {
        const previous = items[index(item.uid) - 1];
        const prior = previous?.block;
        if (previous && prior && isProse(prior)) {
          event.preventDefault();
          const join = prior.markdown.length;
          onChange(
            items
              .filter((i) => i.uid !== item.uid)
              .map((i) =>
                i.uid === previous.uid
                  ? {
                      ...i,
                      block: { ...prior, markdown: prior.markdown + value },
                    }
                  : i,
              ),
          );
          setFocus({ uid: previous.uid, at: join });
        }
      }
    } else if (event.key === "ArrowUp" && start === 0) {
      const previous = neighbour(item.uid, -1);
      if (previous) {
        event.preventDefault();
        setFocus({ uid: previous.uid, at: "end" });
      }
    } else if (event.key === "ArrowDown" && end === value.length) {
      const next = neighbour(item.uid, 1);
      if (next) {
        event.preventDefault();
        setFocus({ uid: next.uid, at: "start" });
      }
    } else if (event.key === "Escape") {
      node.blur();
    }
  };

  // Markdown shortcuts typed at the start of a line of text.
  const onProseChange = (item: Item, value: string) => {
    const block = item.block;
    if (!isProse(block)) return;
    if (block.type === "text") {
      const shortcut: [string, ArticleBlock][] = [
        ["### ", { type: "heading", markdown: "", level: 3 }],
        ["## ", { type: "heading", markdown: "", level: 3 }],
        ["# ", { type: "heading", markdown: "", level: 2 }],
        ["> ", { type: "quote", markdown: "" }],
        ["! ", { type: "callout", markdown: "" }],
      ];
      for (const [prefix, made] of shortcut) {
        if (value.startsWith(prefix) && block.markdown === prefix.slice(0, -1)) {
          replace(item.uid, {
            ...made,
            markdown: value.slice(prefix.length),
          } as ArticleBlock);
          return;
        }
      }
    }
    replace(item.uid, { ...block, markdown: value });
  };

  return (
    <div className="markdown article space-y-1">
      {items.map((item, n) => {
        const block = item.block;
        const prose = isProse(block);
        const active = editing === item.uid;
        const showInput = prose && (active || block.markdown === "");
        return (
          <div
            key={item.uid}
            className={cn(
              "group/block relative -mx-2 rounded-md px-2",
              !prose && active && "bg-muted/30 ring-1 ring-border",
              dragging === item.uid && "opacity-40",
            )}
            onDragOver={(e) => {
              if (!dragging) return;
              e.preventDefault();
              const box = e.currentTarget.getBoundingClientRect();
              setOver({ uid: item.uid, after: e.clientY > box.top + box.height / 2 });
            }}
            onDragLeave={() => setOver(null)}
            onDrop={(e) => {
              e.preventDefault();
              if (over) drop(over.uid, over.after);
              setDragging(null);
              setOver(null);
            }}
          >
            {over?.uid === item.uid ? (
              <div
                className={cn(
                  "pointer-events-none absolute inset-x-0 h-0.5 rounded bg-primary",
                  over.after ? "-bottom-0.5" : "-top-0.5",
                )}
              />
            ) : null}

            {/* The margin: add below, and the handle. */}
            <div
              className={cn(
                "absolute top-1 -left-14 flex items-center gap-0.5 opacity-0 transition-opacity group-hover/block:opacity-100",
                (menu?.uid === item.uid || active) && "opacity-100",
              )}
            >
              <button
                type="button"
                aria-label="Add a block below"
                className="flex size-6 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
                onClick={() => setMenu({ uid: item.uid, mode: "insert" })}
              >
                <IconPlus className="size-4" />
              </button>
              <button
                type="button"
                draggable
                aria-label="Drag to move, click for options"
                className="flex size-6 cursor-grab items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground active:cursor-grabbing"
                onDragStart={(e) => {
                  setDragging(item.uid);
                  e.dataTransfer.effectAllowed = "move";
                }}
                onDragEnd={() => {
                  setDragging(null);
                  setOver(null);
                }}
                onClick={() => setMenu({ uid: item.uid, mode: "row" })}
              >
                <IconGripVertical className="size-4" />
              </button>
            </div>

            {menu?.uid === item.uid && menu.mode !== "row" ? (
              <BlockMenu
                onClose={() => setMenu(null)}
                onPick={(choice) => {
                  setMenu(null);
                  if (menu.mode === "replace") turn(item.uid, choice);
                  else insertAfter(item.uid, choice.make());
                }}
              />
            ) : null}
            {menu?.uid === item.uid && menu.mode === "row" ? (
              <RowMenu
                block={block}
                first={n === 0}
                last={n === items.length - 1}
                onClose={() => setMenu(null)}
                onTurn={(choice) => {
                  setMenu(null);
                  turn(item.uid, choice);
                }}
                onDuplicate={() => {
                  setMenu(null);
                  insertAfter(item.uid, structuredClone(block));
                }}
                onMove={(by) => {
                  setMenu(null);
                  move(item.uid, by);
                }}
                onDelete={() => {
                  setMenu(null);
                  remove(item.uid);
                }}
              />
            ) : null}

            {showInput ? (
              <ProseInput
                block={block as Extract<ArticleBlock, { markdown: string }>}
                footnote={footnotes.get(item.uid)}
                inputRef={(node) => {
                  if (node) inputs.current.set(item.uid, node);
                  else inputs.current.delete(item.uid);
                }}
                active={active}
                onFocus={() => setEditing(item.uid)}
                onBlur={() =>
                  setEditing((current) => (current === item.uid ? null : current))
                }
                onChange={(value) => onProseChange(item, value)}
                onKeyDown={(e) => onKeyDown(item, e)}
                onNote={(note) =>
                  replace(item.uid, {
                    ...(block as Extract<ArticleBlock, { type: "quote" }>),
                    note,
                  })
                }
              />
            ) : (
              <div
                role="button"
                tabIndex={0}
                className="cursor-text rounded-sm py-0.5 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={(e) => {
                  // A link in the rendered block still opens.
                  if ((e.target as HTMLElement).closest("a")) return;
                  if (prose) setFocus({ uid: item.uid, at: "end" });
                  else setEditing(item.uid);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    if (prose) setFocus({ uid: item.uid, at: "end" });
                    else setEditing(item.uid);
                  } else if (!prose && (e.key === "Backspace" || e.key === "Delete")) {
                    // A selected chart, table or card goes the way a selected
                    // line of text would.
                    e.preventDefault();
                    remove(item.uid);
                  }
                }}
              >
                <div className={cn(!prose && "pointer-events-none")}>
                  <BlockView
                    block={block}
                    resolve={resolve}
                    footnote={footnotes.get(item.uid)}
                  />
                </div>
              </div>
            )}

            {!prose && active ? (
              <div className="not-prose my-3 space-y-3 rounded-md border bg-background p-3 text-sm">
                <Settings block={block} onChange={(b) => replace(item.uid, b)} />
                <div className="flex items-center justify-between gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-destructive"
                    onClick={() => remove(item.uid)}
                  >
                    <IconTrash className="size-4" />
                    Delete block
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setEditing(null)}>
                    Done
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
        );
      })}
      {/* Below the last block: somewhere to click to keep writing. */}
      <button
        type="button"
        aria-label="Add a block at the end"
        className="block h-24 w-full cursor-text"
        onClick={() => {
          const last = items[items.length - 1];
          if (last && isProse(last.block) && last.block.markdown === "") {
            setFocus({ uid: last.uid, at: "start" });
          } else {
            insertAfter(null, { type: "text", markdown: "" });
          }
        }}
      />
    </div>
  );
}

const PROSE_STYLE: Record<string, string> = {
  text: "text-[0.9375rem] leading-relaxed",
  heading2: "font-heading text-xl font-semibold tracking-tight mt-6",
  heading3: "font-heading text-base font-semibold tracking-tight mt-4",
  quote: "border-l-2 pl-4 text-[0.9375rem] leading-relaxed",
  callout: "rounded-lg bg-muted/60 px-4 py-3 text-[0.9375rem] leading-relaxed",
  footnote: "text-sm text-muted-foreground",
};

const PLACEHOLDER: Record<string, string> = {
  text: "Write, or type / for blocks",
  heading2: "Heading",
  heading3: "Subheading",
  quote: "Quote",
  callout: "A note set apart",
  footnote: "Footnote text; cite it in the prose as [^n]",
};

/** A prose block being written. */
function ProseInput({
  block,
  footnote,
  inputRef,
  active,
  onFocus,
  onBlur,
  onChange,
  onKeyDown,
  onNote,
}: {
  block: Extract<ArticleBlock, { markdown: string }>;
  footnote?: number;
  inputRef: (node: HTMLTextAreaElement | null) => void;
  active: boolean;
  onFocus: () => void;
  onBlur: () => void;
  onChange: (value: string) => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onNote: (note: string) => void;
}) {
  const style = block.type === "heading" ? `heading${block.level}` : block.type;
  const [figureOpen, setFigureOpen] = useState(false);
  const node = useRef<HTMLTextAreaElement | null>(null);
  const insert = (token: string) => {
    const area = node.current;
    const at = area?.selectionStart ?? block.markdown.length;
    const to = area?.selectionEnd ?? at;
    onChange(block.markdown.slice(0, at) + token + block.markdown.slice(to));
  };
  return (
    <div className="py-0.5">
      <div className={cn("flex gap-2", PROSE_STYLE[style])}>
        {block.type === "footnote" ? (
          <span className="tabular-nums">{footnote ?? "·"}.</span>
        ) : null}
        <AutoText
          value={block.markdown}
          inputRef={(n) => {
            node.current = n;
            inputRef(n);
          }}
          placeholder={active ? PLACEHOLDER[style] : ""}
          className="min-w-0 flex-1"
          onFocus={onFocus}
          onBlur={onBlur}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
        />
      </div>
      {block.type === "quote" && active ? (
        <input
          value={block.note ?? ""}
          placeholder="Who said it (optional)"
          className="mt-1 ml-4 w-full bg-transparent text-sm text-muted-foreground outline-none"
          onFocus={onFocus}
          onChange={(e) => onNote(e.target.value)}
        />
      ) : null}
      {active ? (
        <div className="not-prose mt-1 flex items-center gap-3 text-xs text-muted-foreground">
          <button
            type="button"
            className="inline-flex items-center gap-1 hover:text-foreground"
            // Kept from taking focus, so the cursor stays where the figure goes.
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => setFigureOpen((open) => !open)}
          >
            <IconMathFunction className="size-3.5" />
            Insert a figure
          </button>
          <span>
            Markdown · Shift+Enter for a new line · # heading · &gt; quote · --- divider
          </span>
        </div>
      ) : null}
      {figureOpen && active ? (
        <FigureBuilder
          onInsert={(token) => {
            insert(token);
            setFigureOpen(false);
          }}
          onClose={() => setFigureOpen(false)}
        />
      ) : null}
    </div>
  );
}

const MEASURES: {
  value: string;
  label: string;
  needs: ("period" | "from" | "geo")[];
}[] = [
  { value: "latest", label: "Latest value", needs: [] },
  { value: "latest-period", label: "Latest period", needs: [] },
  { value: "value", label: "Value in a period", needs: ["period"] },
  { value: "change", label: "Change between two periods", needs: ["from"] },
  { value: "median", label: "Median across places", needs: ["period"] },
  { value: "mean", label: "Mean across places", needs: ["period"] },
  { value: "min", label: "Lowest place", needs: ["period"] },
  { value: "max", label: "Highest place", needs: ["period"] },
  { value: "rank", label: "A place's rank", needs: ["period", "geo"] },
];

/** Composes a figure token from fields, so nobody need remember the syntax. */
export function FigureBuilder({
  onInsert,
  onClose,
}: {
  onInsert: (token: string) => void;
  onClose: () => void;
}) {
  const [measure, setMeasure] = useState("change");
  const [f, setF] = useState<Record<string, string>>({});
  const spec = MEASURES.find((m) => m.value === measure) ?? MEASURES[0]!;
  const get = (k: string) => f[k] ?? "";
  const put = (k: string, v: string) => setF((x) => ({ ...x, [k]: v.trim() }));
  const args = [
    ["indicator", get("indicator")],
    ["geo", get("geo")],
    ["commodity", get("commodity")],
    ...(spec.needs.includes("period") ? [["period", get("period")]] : []),
    ...(spec.needs.includes("from")
      ? [
          ["from", get("from")],
          ["to", get("to")],
        ]
      : []),
    ["digits", get("digits")],
  ].filter(([, v]) => v);
  const token = `{{${measure}${args.map(([k, v]) => ` ${k}=${v}`).join("")}}}`;
  const complete =
    get("indicator") &&
    spec.needs.every((need) =>
      need === "from" ? get("from") && get("to") : get(need),
    );

  return (
    <div
      className="not-prose mt-2 space-y-3 rounded-md border bg-background p-3 text-sm"
      onMouseDown={(e) => {
        // Clicks in the builder must not end the block's editing.
        if ((e.target as HTMLElement).tagName !== "INPUT") e.preventDefault();
      }}
    >
      <div className="grid gap-2 sm:grid-cols-3">
        <label className="space-y-1">
          <span className="text-xs text-muted-foreground">Figure</span>
          <Select
            value={measure}
            onValueChange={(v) => setMeasure(String(v))}
            items={MEASURES.map((m) => ({ value: m.value, label: m.label }))}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {MEASURES.map((m) => (
                <SelectItem key={m.value} value={m.value}>
                  {m.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <SeriesField value={get("indicator")} onChange={(v) => put("indicator", v)} />
        <Line
          label={spec.needs.includes("geo") ? "Place" : "Place (optional)"}
          placeholder="IDN, ID-31"
          value={get("geo")}
          onChange={(v) => put("geo", v)}
        />
        <Line
          label="Commodity (optional)"
          placeholder="rice-medium-grade-1"
          value={get("commodity")}
          onChange={(v) => put("commodity", v)}
        />
        {spec.needs.includes("period") ? (
          <Line
            label="Period"
            placeholder="2024-03"
            value={get("period")}
            onChange={(v) => put("period", v)}
          />
        ) : null}
        {spec.needs.includes("from") ? (
          <>
            <Line
              label="From"
              placeholder="2023-07"
              value={get("from")}
              onChange={(v) => put("from", v)}
            />
            <Line
              label="To"
              placeholder="2024-03"
              value={get("to")}
              onChange={(v) => put("to", v)}
            />
          </>
        ) : null}
        <Line
          label="Decimals (optional)"
          placeholder="1"
          value={get("digits")}
          onChange={(v) => put("digits", v)}
        />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <code className="min-w-0 truncate rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
          {token}
        </code>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button size="sm" disabled={!complete} onClick={() => onInsert(token)}>
            Insert
          </Button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        From a built analysis:{" "}
        {"{{figure article=rice-after-the-dry-season key=y2023.rho}}"}. Across-place
        figures leave out the national row unless a kind of place is named.
      </p>
    </div>
  );
}

export function Line({
  label,
  placeholder,
  value,
  onChange,
}: {
  label: string;
  placeholder?: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <input
        value={value}
        placeholder={placeholder}
        className="h-8 w-full rounded-md border bg-transparent px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

/** A series id, found by searching the catalogue by name. */
function SeriesField({
  value,
  onChange,
  label = "Series",
}: {
  value: string;
  onChange: (id: string) => void;
  label?: string;
}) {
  const [q, setQ] = useState("");
  const found = useQuery({
    queryKey: ["indicator-search", q],
    queryFn: () =>
      api.indicators({ q, limit: 8 } as Parameters<typeof api.indicators>[0]),
    enabled: q.trim().length >= 2,
  });
  return (
    <div className="relative space-y-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <input
        value={q || value}
        placeholder="Search series by name"
        className="h-8 w-full rounded-md border bg-transparent px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onChange={(e) => {
          setQ(e.target.value);
          if (!e.target.value) onChange("");
        }}
      />
      {q.trim().length >= 2 && found.data?.data.length ? (
        <div className="absolute z-40 mt-1 max-h-60 w-80 overflow-y-auto rounded-md bg-popover p-1 shadow-md ring-1 ring-foreground/10">
          {found.data.data.map((ind) => (
            <button
              key={ind.indicator_id}
              type="button"
              className="block w-full rounded px-2 py-1.5 text-left hover:bg-accent"
              onClick={() => {
                onChange(ind.indicator_id);
                setQ("");
              }}
            >
              <span className="block truncate text-sm">{indicatorLabel(ind)}</span>
              <span className="block font-mono text-[11px] text-muted-foreground">
                {ind.indicator_id}
                {ind.unit ? ` · ${ind.unit}` : ""}
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const list = (value: string) =>
  value
    .split(/[,\s]+/)
    .map((part) => part.trim())
    .filter(Boolean);

/** What a selected block is set up with, under it. */
export function Settings({
  block,
  onChange,
}: {
  block: ArticleBlock;
  onChange: (block: ArticleBlock) => void;
}) {
  switch (block.type) {
    case "chart":
      return <ChartSettings block={block} onChange={onChange} />;
    case "embed":
      return <EmbedSettings block={block} onChange={onChange} />;
    case "table":
      return <TableSettings block={block} onChange={onChange} />;
    case "regulation":
      return <RegulationSettings block={block} onChange={onChange} />;
    case "dataset":
      return <DatasetSettings block={block} onChange={onChange} />;
    case "stat":
      return <StatSettings block={block} onChange={onChange} />;
    case "divider":
      return (
        <p className="text-muted-foreground">
          A divider has nothing to set. Use the handle to move or delete it.
        </p>
      );
    default:
      return null;
  }
}

function fromSavedSearch(search: Record<string, unknown>): ArticleQuery {
  const many = (v: unknown) =>
    Array.isArray(v) ? v.map(String) : typeof v === "string" && v ? [v] : undefined;
  const one = (v: unknown) => (typeof v === "string" && v ? v : undefined);
  return {
    indicator: many(search.indicator) ?? [],
    geo: many(search.geo),
    geo_type: many(search.geo_type),
    commodity: many(search.commodity),
    period_start: one(search.period_start),
    period_end: one(search.period_end),
  };
}

const DIMENSIONS = [
  { value: "", label: "As the series varies" },
  { value: "geography", label: "One line per place" },
  { value: "commodity", label: "One line per commodity" },
  { value: "none", label: "A single line" },
];

function ChartSettings({
  block,
  onChange,
}: {
  block: Extract<ArticleBlock, { type: "chart" }>;
  onChange: (block: ArticleBlock) => void;
}) {
  const saved = useQuery({
    queryKey: ["saved-queries"],
    queryFn: () => api.savedQueries(),
  });
  const observations = (saved.data?.data ?? []).filter(
    (q) => q.kind === "observations",
  );
  const q = block.query;
  const set = (patch: Partial<ArticleQuery>) =>
    onChange({ ...block, query: { ...q, ...patch } });
  const joined = (v?: string[]) => (v ?? []).join(", ");
  return (
    <div className="space-y-3">
      {observations.length ? (
        <label className="block space-y-1">
          <span className="text-xs text-muted-foreground">
            Copy a saved query's series
          </span>
          <Select
            value=""
            onValueChange={(v) => {
              const found = observations.find((s) => s.id === v);
              if (found)
                onChange({
                  ...block,
                  title: block.title || found.name,
                  query: fromSavedSearch(found.search),
                });
            }}
            items={observations.map((s) => ({ value: s.id, label: s.name }))}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Choose a saved Data Explorer query" />
            </SelectTrigger>
            <SelectContent>
              {observations.map((s) => (
                <SelectItem key={s.id} value={s.id}>
                  {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
      ) : null}
      <div className="grid gap-2 sm:grid-cols-2">
        <Line
          label="Title"
          value={block.title ?? ""}
          onChange={(v) => onChange({ ...block, title: v })}
        />
        <Line
          label="Note"
          placeholder="Unit, source, what a line is"
          value={block.note ?? ""}
          onChange={(v) => onChange({ ...block, note: v })}
        />
        <SeriesField
          label={`Add a series${q.indicator.length ? ` (${q.indicator.join(", ")})` : ""}`}
          value=""
          onChange={(id) =>
            id && !q.indicator.includes(id) && set({ indicator: [...q.indicator, id] })
          }
        />
        <Line
          label="Series ids"
          value={joined(q.indicator)}
          onChange={(v) => set({ indicator: list(v) })}
        />
        <Line
          label="Places (optional)"
          placeholder="IDN, ID-31"
          value={joined(q.geo)}
          onChange={(v) => set({ geo: list(v) })}
        />
        <Line
          label="Commodities (optional)"
          placeholder="rice-medium-grade-1"
          value={joined(q.commodity)}
          onChange={(v) => set({ commodity: list(v) })}
        />
        <Line
          label="Kinds of place (optional)"
          placeholder="province"
          value={joined(q.geo_type)}
          onChange={(v) => set({ geo_type: list(v) })}
        />
        <Line
          label="From (optional)"
          placeholder="2018-01"
          value={q.period_start ?? ""}
          onChange={(v) => set({ period_start: v || undefined })}
        />
        <Line
          label="To (optional)"
          placeholder="2026-09"
          value={q.period_end ?? ""}
          onChange={(v) => set({ period_end: v || undefined })}
        />
        <label className="block space-y-1">
          <span className="text-xs text-muted-foreground">Lines</span>
          <Select
            value={q.dimension ?? ""}
            onValueChange={(v) => set({ dimension: String(v) || undefined })}
            items={DIMENSIONS}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {DIMENSIONS.map((d) => (
                <SelectItem key={d.value || "auto"} value={d.value}>
                  {d.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
      </div>
    </div>
  );
}

function EmbedSettings({
  block,
  onChange,
}: {
  block: Extract<ArticleBlock, { type: "embed" }>;
  onChange: (block: ArticleBlock) => void;
}) {
  const options = builtCharts();
  return (
    <label className="block space-y-1">
      <span className="text-xs text-muted-foreground">Chart from a built analysis</span>
      <Select
        value={`${block.article}/${block.chart}`}
        onValueChange={(v) => {
          const [article, chart] = String(v).split("/");
          if (article && chart) onChange({ ...block, article, chart });
        }}
        items={options.map((o) => ({
          value: `${o.article}/${o.chart}`,
          label: `${o.articleTitle}: ${o.title}`,
        }))}
      >
        <SelectTrigger className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem
              key={`${o.article}/${o.chart}`}
              value={`${o.article}/${o.chart}`}
            >
              {o.articleTitle}: {o.title}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  );
}

function TableSettings({
  block,
  onChange,
}: {
  block: Extract<ArticleBlock, { type: "table" }>;
  onChange: (block: ArticleBlock) => void;
}) {
  const rows = block.rows;
  const cols = Math.max(1, ...rows.map((r) => r.length));
  const setRows = (next: string[][]) => onChange({ ...block, rows: next });
  const setCell = (r: number, c: number, value: string) =>
    setRows(
      rows.map((row, i) =>
        i === r ? row.map((cell, j) => (j === c ? value : cell)) : row,
      ),
    );
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2">
        <Line
          label="Title (optional)"
          value={block.title ?? ""}
          onChange={(v) => onChange({ ...block, title: v })}
        />
        <Line
          label="Note (optional)"
          value={block.note ?? ""}
          onChange={(v) => onChange({ ...block, note: v })}
        />
      </div>
      <label className="flex items-center gap-2 text-sm">
        <Checkbox
          checked={Boolean(block.header)}
          onCheckedChange={(v) => onChange({ ...block, header: Boolean(v) })}
        />
        The first row names the columns
      </label>
      <div className="overflow-x-auto">
        <table className="w-full border-separate border-spacing-1">
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>
                {Array.from({ length: cols }, (_, c) => (
                  <td key={c} className="p-0">
                    <input
                      value={row[c] ?? ""}
                      placeholder={r === 0 && block.header ? "Column" : ""}
                      className={cn(
                        "h-8 w-full min-w-24 rounded border bg-transparent px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        r === 0 && block.header && "font-medium",
                      )}
                      onChange={(e) => setCell(r, c, e.target.value)}
                    />
                  </td>
                ))}
                <td className="w-8 p-0">
                  <button
                    type="button"
                    aria-label="Remove row"
                    disabled={rows.length === 1}
                    className="size-8 rounded text-muted-foreground hover:bg-accent disabled:opacity-30"
                    onClick={() => setRows(rows.filter((_, i) => i !== r))}
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={rows.length >= 60}
          onClick={() => setRows([...rows, Array(cols).fill("")])}
        >
          Add a row
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={cols >= 12}
          onClick={() =>
            setRows(
              rows.map((row) => [...row, ...Array(cols - row.length + 1).fill("")]),
            )
          }
        >
          Add a column
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={cols <= 1}
          onClick={() => setRows(rows.map((row) => row.slice(0, cols - 1)))}
        >
          Remove the last column
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Cells take markdown and figure tokens, such as {"{{change …}}"}.
      </p>
    </div>
  );
}

function RegulationSettings({
  block,
  onChange,
}: {
  block: Extract<ArticleBlock, { type: "regulation" }>;
  onChange: (block: ArticleBlock) => void;
}) {
  const [q, setQ] = useState("");
  const found = useQuery({
    queryKey: ["regulation-search", q],
    queryFn: () =>
      api.regulations({ q, limit: 8 } as Parameters<typeof api.regulations>[0]),
    enabled: q.trim().length >= 2,
  });
  return (
    <div className="space-y-2">
      <Line
        label="Find a regulation"
        placeholder="PP 26 2023, cadangan beras, Perpres pangan…"
        value={q}
        onChange={setQ}
      />
      {found.isLoading ? (
        <p className="text-xs text-muted-foreground">Searching…</p>
      ) : null}
      <div className="space-y-1">
        {(found.data?.data ?? []).map((r) => (
          <button
            key={r.key}
            type="button"
            className={cn(
              "block w-full rounded-md px-2 py-1.5 text-left hover:bg-accent",
              r.key === block.key && "bg-accent",
            )}
            onClick={() => onChange({ ...block, key: r.key })}
          >
            <span className="block text-xs text-muted-foreground">
              {[
                r.instrument,
                r.number ? `No. ${r.number}` : null,
                r.year,
                r.region_name,
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
            <span className="line-clamp-2 block">{r.title}</span>
          </button>
        ))}
        {q.trim().length >= 2 && found.data && !found.data.data.length ? (
          <p className="text-xs text-muted-foreground">No regulation matches “{q}”.</p>
        ) : null}
      </div>
    </div>
  );
}

function DatasetSettings({
  block,
  onChange,
}: {
  block: Extract<ArticleBlock, { type: "dataset" }>;
  onChange: (block: ArticleBlock) => void;
}) {
  const catalogue = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();
  const shown = (catalogue.data?.data ?? [])
    .filter(
      (d) =>
        !needle ||
        `${datasetLabel(d)} ${d.dataset_id} ${d.source_id}`
          .toLowerCase()
          .includes(needle),
    )
    .slice(0, 12);
  return (
    <div className="space-y-2">
      <Line
        label="Find a dataset"
        placeholder="food prices, ERA5, APBD…"
        value={q}
        onChange={setQ}
      />
      <div className="max-h-64 space-y-1 overflow-y-auto">
        {shown.map((d) => (
          <button
            key={d.dataset_id}
            type="button"
            className={cn(
              "flex w-full items-baseline justify-between gap-2 rounded-md px-2 py-1.5 text-left hover:bg-accent",
              d.dataset_id === block.dataset && "bg-accent",
            )}
            onClick={() => onChange({ ...block, dataset: d.dataset_id })}
          >
            <span className="min-w-0 truncate">{datasetLabel(d)}</span>
            <span className="font-mono text-[11px] text-muted-foreground">
              {d.dataset_id}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

/** A headline number: what it is, the figure, and what qualifies it. */
function StatSettings({
  block,
  onChange,
}: {
  block: Extract<ArticleBlock, { type: "stat" }>;
  onChange: (block: ArticleBlock) => void;
}) {
  const [target, setTarget] = useState<"markdown" | "note" | null>(null);
  return (
    <div className="space-y-3">
      <Line
        label="What it is"
        placeholder="Medium-grade rice, Jakarta"
        value={block.title ?? ""}
        onChange={(v) => onChange({ ...block, title: v })}
      />
      <div className="space-y-1">
        <Line
          label="The figure"
          placeholder="Rp{{latest indicator=6yugbox8 geo=ID-31 commodity=rice-medium-grade-1}}"
          value={block.markdown}
          onChange={(v) => onChange({ ...block, markdown: v })}
        />
        <button
          type="button"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          onClick={() => setTarget(target === "markdown" ? null : "markdown")}
        >
          <IconMathFunction className="size-3.5" />
          Insert a figure
        </button>
      </div>
      <div className="space-y-1">
        <Line
          label="Beneath it (optional)"
          placeholder="{{change …}} since July 2023"
          value={block.note ?? ""}
          onChange={(v) => onChange({ ...block, note: v })}
        />
        <button
          type="button"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          onClick={() => setTarget(target === "note" ? null : "note")}
        >
          <IconMathFunction className="size-3.5" />
          Insert a figure
        </button>
      </div>
      {target ? (
        <FigureBuilder
          onClose={() => setTarget(null)}
          onInsert={(token) => {
            if (target === "markdown")
              onChange({ ...block, markdown: block.markdown + token });
            else onChange({ ...block, note: `${block.note ?? ""}${token}` });
            setTarget(null);
          }}
        />
      ) : null}
    </div>
  );
}

/** Every string in the blocks a figure token may sit in. */
export function textsOf(items: Item[]): string[] {
  return items.flatMap(({ block }) => [
    ...("markdown" in block ? [block.markdown] : []),
    ...("title" in block && block.title ? [block.title] : []),
    ...("note" in block && block.note ? [block.note] : []),
    ...(block.type === "table" ? block.rows.flat() : []),
  ]);
}

/** The blocks as saved: empty prose dropped, since it has nothing to show. */
export function savedBlocks(items: Item[]): ArticleBlock[] {
  return items.map((i) => i.block).filter((b) => !(isProse(b) && !b.markdown.trim()));
}
