/**
 * Part-to-whole across many items, as rectangles whose area is their share.
 *
 * A treemap answers "what is this made of" for a set too large to stack into
 * one bar and too uneven to rank as bars: eleven tables across two layers,
 * where one of them is ninety per cent of the lake. Area carries the quantity
 * and position carries the grouping, so the layers read as blocks without a
 * second chart.
 *
 * Two honesty rules, both of which this data forces:
 *
 * Area is never inflated. The lake spans six orders of magnitude — fourteen
 * million regulation sections beside twelve commodities — and a minimum tile
 * size would draw a quantity nobody measured. Instead the tail that cannot be
 * seen is *pooled* into one tile of exactly its own area and named underneath,
 * so those tables are still counted, still findable, and still honest about
 * how small they are.
 *
 * Identity is never colour alone. The hue says which layer a tile belongs to;
 * every group is named in the legend, every tile names itself on hover, and
 * the labels on the fills are written in one measured ink rather than flipping
 * between white and black from tile to tile (see `SERIES_SOLID` in lib/viz).
 */

import { useMemo } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "~/components/ui/tooltip";
import { formatCount } from "~/lib/format";
import { treemap, type TreemapItem } from "~/lib/treemap";
import {
  MAX_SERIES,
  SOLID_DARK_SLOTS,
  SOLID_INK,
  SOLID_LIGHT_SLOTS,
  solidColor,
} from "~/lib/viz";
import { cn } from "~/lib/utils";

export type { TreemapItem };

/**
 * Tiles smaller than this share of their group are pooled.
 *
 * Chosen from what a tile has to be to hold anything at all: below roughly a
 * hundredth of a group, a rectangle in a bento cell is a hairline with no room
 * for a label and no hit target worth hovering.
 */
const POOL_BELOW = 0.012;

/**
 * What fits in a tile, by the shape of the tile rather than by its area alone.
 *
 * A block 70% wide and 95% tall takes a name and a figure on two lines. A band
 * 100% wide and 5% tall takes the same words on one line and nothing else. A
 * tile 6% wide and 20% tall takes a clamped name and no figure. Below that,
 * the hover is the only place the label can live — which is why the tail is
 * also named in prose underneath.
 */
function fit(width: number, height: number): "block" | "strip" | "name" | "none" {
  if (width >= 9 && height >= 12) return "block";
  if (width >= 22 && height >= 3) return "strip";
  if (width >= 5.5 && height >= 12) return "name";
  return "none";
}

type Pooled = TreemapItem & { members?: TreemapItem[] };

export function Treemap({
  items,
  unit,
  /**
   * The canvas's shape. The layout is computed against it — squareness is
   * measured on the real rectangle — so the container is given the same one.
   */
  aspect = 2.2,
  format = formatCount,
  emptyMessage = "Nothing to plot yet.",
  groupNote,
  className,
}: {
  items: TreemapItem[];
  unit?: string;
  aspect?: number;
  format?: (value: number) => string;
  emptyMessage?: string;
  /**
   * What a group *is*, for the legend's hover. A colour and a name tell a
   * reader which block is which; they do not tell them what a block being
   * large would mean.
   */
  groupNote?: (group: string) => string | undefined;
  className?: string;
}) {
  const { tiles, groups, total, pools } = useMemo(() => {
    const pools: Pooled[] = [];

    // Pooled per group rather than across the whole chart, so a layer's small
    // tables stay inside that layer's block.
    const byGroup = new Map<string, TreemapItem[]>();
    for (const item of items) {
      const group = item.group ?? "";
      byGroup.set(group, [...(byGroup.get(group) ?? []), item]);
    }

    const laid: Pooled[] = [];
    for (const [group, members] of byGroup) {
      const groupTotal = members.reduce((sum, item) => sum + item.value, 0);
      const small = members.filter(
        (item) => groupTotal > 0 && item.value / groupTotal < POOL_BELOW,
      );
      const large = members.filter((item) => !small.includes(item));

      laid.push(...large);
      // One pooled tile is worse than two real ones: if only a single table
      // would be pooled, it is drawn as itself, hairline and all.
      if (small.length > 1) {
        const pooled: Pooled = {
          key: `${group}:pooled`,
          label: `${small.length} smaller tables`,
          value: small.reduce((sum, item) => sum + item.value, 0),
          group,
          members: small,
        };
        laid.push(pooled);
        pools.push(pooled);
      } else {
        laid.push(...small);
      }
    }

    const layout = treemap(laid, { aspect });
    const held = new Map(laid.map((item) => [item.key, item]));

    return {
      total: layout.total,
      groups: layout.groups,
      pools,
      tiles: layout.tiles.map((tile) => ({
        ...tile,
        members: held.get(tile.key)?.members,
      })),
    };
  }, [aspect, items]);

  if (!tiles.length) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">{emptyMessage}</p>
    );
  }

  // Slots follow the group's size, not its name, so the biggest block is the
  // first validated hue and a layer added later cannot repaint the others.
  const order = new Map(
    [...groups]
      .sort((a, b) => b.value - a.value)
      .map((group, index) => [group.group, index]),
  );

  return (
    <div className={cn(SOLID_DARK_SLOTS, className)} style={SOLID_LIGHT_SLOTS}>
      <div
        className="relative w-full overflow-hidden rounded-md"
        // A floor as well as a ratio: on a phone the cell is 350px wide, and
        // 2.2 of that leaves a band five per cent tall with nowhere to put a
        // word. Stretching the canvas taller leaves every area exactly where it
        // was — they are percentages — and only costs the tiles some squareness.
        style={{ aspectRatio: aspect, minHeight: "15rem" }}
      >
        {tiles.map((tile) => {
          const slot = Math.min(order.get(tile.group ?? "") ?? 0, MAX_SERIES - 1);
          const color = solidColor(slot);
          const label = fit(tile.width, tile.height);
          const value = `${format(tile.value)}${
            tile.share < 0.01 ? "" : ` · ${(tile.share * 100).toFixed(0)}%`
          }`;

          return (
            <Tooltip key={tile.key}>
              <TooltipTrigger
                render={
                  <div
                    // The inset is the gap between fills: two washes that touch
                    // read as one shape, and a border would add ink that carries
                    // no data.
                    className="absolute p-[1px]"
                    style={{
                      left: `${tile.x}%`,
                      top: `${tile.y}%`,
                      width: `${tile.width}%`,
                      height: `${tile.height}%`,
                    }}
                  />
                }
              >
                <div
                  className={cn(
                    "h-full w-full overflow-hidden rounded-[3px] transition-[filter] hover:brightness-95 dark:hover:brightness-110",
                    // A strip has no room for vertical padding; a block would
                    // look cramped without it.
                    label === "strip" ? "px-2" : "px-2 py-1.5",
                  )}
                  // Solid, with no border: the 2px of card colour between tiles
                  // is what separates them, and a stroke would add ink that
                  // carries no data.
                  style={{ backgroundColor: color, color: SOLID_INK }}
                >
                  {label === "block" ? (
                    <div className="flex h-full flex-col justify-between gap-1 leading-tight">
                      <span className="line-clamp-2 text-xs font-medium">
                        {tile.label}
                      </span>
                      {/* The share is what the area already says; printing it
                          is for the reader who wants the number rather than
                          the impression. Set apart by weight rather than by
                          fading it: white at 70% on these fills drops under
                          the contrast the full white was chosen for. */}
                      <span className="text-[0.7rem] font-normal tabular-nums">
                        {value}
                      </span>
                    </div>
                  ) : label === "strip" ? (
                    <div className="flex h-full items-center gap-2 leading-none">
                      <span className="truncate text-[0.7rem] font-medium">
                        {tile.label}
                      </span>
                      <span className="shrink-0 text-[0.7rem] font-normal tabular-nums">
                        {value}
                      </span>
                    </div>
                  ) : label === "name" ? (
                    <span className="line-clamp-3 text-[0.7rem] leading-tight font-medium">
                      {tile.label}
                    </span>
                  ) : null}
                </div>
              </TooltipTrigger>

              <TooltipContent className="max-w-xs">
                <div className="font-medium">{tile.label}</div>
                <div className="tabular-nums">
                  {format(tile.value)}
                  {unit ? ` ${unit}` : ""} ·{" "}
                  {(tile.share * 100).toFixed(tile.share < 0.01 ? 3 : 1)}% of{" "}
                  {format(total)}
                </div>
                {tile.group ? <div className="opacity-80">{tile.group}</div> : null}
                {tile.note ? <div className="opacity-80">{tile.note}</div> : null}
                {tile.members ? (
                  <div className="mt-1 opacity-80">
                    {tile.members
                      .map((member) => `${member.label} ${format(member.value)}`)
                      .join(" · ")}
                  </div>
                ) : null}
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>

      {/* Always present, because two groups is two series: the block colours
          mean nothing until something names them. Each entry explains what its
          group is on hover — a reader who can see that Bronze is 5% of the lake
          still has to be told what Bronze is. */}
      <ul className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs">
        {[...groups]
          .sort((a, b) => b.value - a.value)
          .map((group) => {
            const note = groupNote?.(group.group);
            const entry = (
              <li
                className={cn(
                  "flex items-center gap-1.5",
                  note && "cursor-help decoration-dotted underline-offset-4",
                )}
              >
                <span
                  aria-hidden
                  className="size-2.5 shrink-0 rounded-[3px]"
                  style={{
                    backgroundColor: solidColor(
                      Math.min(order.get(group.group) ?? 0, MAX_SERIES - 1),
                    ),
                  }}
                />
                <span
                  className={cn(
                    "text-foreground",
                    note && "underline decoration-dotted",
                  )}
                >
                  {group.group || "Ungrouped"}
                </span>
                <span className="tabular-nums text-muted-foreground">
                  {format(group.value)}
                </span>
              </li>
            );

            if (!note) return <div key={group.group}>{entry}</div>;
            return (
              <Tooltip key={group.group}>
                <TooltipTrigger render={entry} />
                <TooltipContent className="max-w-xs">
                  <div className="font-medium capitalize">{group.group}</div>
                  <div className="opacity-90">{note}</div>
                  <div className="mt-1 tabular-nums opacity-80">
                    {format(group.value)}
                    {unit ? ` ${unit}` : ""} ·{" "}
                    {((group.value / total) * 100).toFixed(1)}% of the lake
                  </div>
                </TooltipContent>
              </Tooltip>
            );
          })}
      </ul>

      {/* What the area could not show. Named rather than left to a hover,
          because a tile a reader cannot see is a tile they cannot hover. */}
      {pools.map((pool) => (
        <p key={pool.key} className="mt-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{pool.label}</span> in{" "}
          {pool.group || "the lake"}, too small to draw at scale:{" "}
          {pool.members
            ?.map((member) => `${member.label} (${format(member.value)})`)
            .join(", ")}
          .
        </p>
      ))}
    </div>
  );
}
