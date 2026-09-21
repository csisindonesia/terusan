/**
 * Squarified treemap layout.
 *
 * Area is the whole message: a tile is as big a share of the rectangle as its
 * value is of the total, and nothing here bends that. Tiles are kept as close
 * to square as the algorithm can manage (Bruls, Huizing and van Wijk, 2000),
 * because a long thin sliver is hard to compare against a fat one even when
 * their areas agree.
 *
 * Two consequences worth stating, because both show up in this warehouse:
 *
 * A treemap cannot show six orders of magnitude legibly. Fourteen million
 * regulation sections beside twelve commodities means the commodities are a
 * hairline — correctly, since that *is* the ratio. The caller's job is to say
 * so and to give those rows another way to be read (a hover, a list), not to
 * inflate them into visibility.
 *
 * Grouping happens by laying the groups out first and then the items inside
 * each group's rectangle. That keeps every layer contiguous, which is what
 * makes "how much of the lake is Silver" readable at a glance.
 *
 * Groups are laid as bands across the canvas's long side rather than squarified
 * against each other. Squarifying two groups of 95% and 5% gives the small one
 * a full-height sliver a few pixels wide — no room for a name, on a block that
 * is a twentieth of everything. The same 5% as a band is short but full width,
 * which is a shape a label fits in. Area is identical either way; only the
 * legibility differs.
 *
 * Output is in percentages of the canvas, so the component can position tiles
 * without measuring anything — the same layout serves the server render and
 * every window width.
 */

export type TreemapItem = {
  key: string;
  label: string;
  value: number;
  /** Tiles of one group are laid out together. */
  group?: string;
  note?: string;
};

export type TreemapTile = TreemapItem & {
  /** All four in percent of the canvas. */
  x: number;
  y: number;
  width: number;
  height: number;
  /** Share of the whole, 0–1, for the label and the tooltip. */
  share: number;
};

export type TreemapGroup = {
  group: string;
  value: number;
  x: number;
  y: number;
  width: number;
  height: number;
};

export type Treemap = {
  tiles: TreemapTile[];
  /** The rectangle each group occupies, for a heading or an outline. */
  groups: TreemapGroup[];
  total: number;
};

type Rect = { x: number; y: number; w: number; h: number };

type Sized = { key: string; value: number };

/**
 * One row of tiles laid along the shorter side of what is left.
 *
 * The algorithm's whole idea: keep adding to the current row while doing so
 * improves the worst aspect ratio in it, and close the row as soon as it does
 * not.
 */
function worst(row: number[], length: number, scale: number): number {
  if (!row.length || length <= 0) return Infinity;
  const sum = row.reduce((total, value) => total + value, 0) * scale;
  if (sum <= 0) return Infinity;
  const max = Math.max(...row) * scale;
  const min = Math.min(...row) * scale;
  const side = length * length;
  return Math.max((side * max) / (sum * sum), (sum * sum) / (side * min));
}

/** Lay `values` into `rect`, largest first. */
function squarify(values: Sized[], rect: Rect): (Sized & Rect)[] {
  const positive = values.filter((entry) => entry.value > 0);
  if (!positive.length || rect.w <= 0 || rect.h <= 0) return [];

  const total = positive.reduce((sum, entry) => sum + entry.value, 0);
  const scale = (rect.w * rect.h) / total;

  const out: (Sized & Rect)[] = [];
  let free: Rect = { ...rect };
  let queue = [...positive].sort((a, b) => b.value - a.value);

  while (queue.length) {
    const side = Math.min(free.w, free.h);
    const row: Sized[] = [];
    let best = Infinity;

    while (queue.length) {
      const candidate = [...row.map((entry) => entry.value), queue[0]!.value];
      const score = worst(candidate, side, scale);
      if (row.length && score > best) break;
      best = score;
      row.push(queue.shift()!);
    }

    const rowValue = row.reduce((sum, entry) => sum + entry.value, 0);
    const thickness = (rowValue * scale) / side;

    // Along the shorter side, so the row's tiles stay as square as they can.
    let offset = 0;
    for (const entry of row) {
      const length = (entry.value * scale) / thickness;
      out.push(
        free.w >= free.h
          ? { ...entry, x: free.x, y: free.y + offset, w: thickness, h: length }
          : { ...entry, x: free.x + offset, y: free.y, w: length, h: thickness },
      );
      offset += length;
    }

    free =
      free.w >= free.h
        ? { x: free.x + thickness, y: free.y, w: free.w - thickness, h: free.h }
        : { x: free.x, y: free.y + thickness, w: free.w, h: free.h - thickness };

    // Floating point leaves a sliver of a rectangle behind on the last row;
    // anything this thin cannot hold a tile anyway.
    if (free.w < 1e-9 || free.h < 1e-9) break;
  }

  return out;
}

/**
 * Lay items out in a canvas of the given aspect ratio.
 *
 * The aspect matters to the algorithm — squareness is measured against the
 * real rectangle — so it is asked for rather than assumed, and the component
 * gives the container the same one.
 */
export function treemap(
  items: TreemapItem[],
  options?: { aspect?: number; grouped?: boolean },
): Treemap {
  const aspect = options?.aspect ?? 2;
  const grouped = options?.grouped ?? true;

  const usable = items.filter((item) => item.value > 0);
  const total = usable.reduce((sum, item) => sum + item.value, 0);
  if (!usable.length || total <= 0) return { tiles: [], groups: [], total: 0 };

  // Unit space: 100 wide, and as tall as the aspect says. Converted to
  // percentages at the end.
  const canvas: Rect = { x: 0, y: 0, w: 100, h: 100 / aspect };

  const scaleX = (value: number) => value;
  const scaleY = (value: number) => (value / canvas.h) * 100;

  if (!grouped) {
    const laid = squarify(
      usable.map((item) => ({ key: item.key, value: item.value })),
      canvas,
    );
    return {
      total,
      groups: [],
      tiles: laid.map((rect) => tile(rect, usable, total, scaleX, scaleY)),
    };
  }

  const totals = new Map<string, number>();
  for (const item of usable) {
    const group = item.group ?? "";
    totals.set(group, (totals.get(group) ?? 0) + item.value);
  }

  const groupRects = bands(
    [...totals.entries()]
      .map(([key, value]) => ({ key, value }))
      .sort((a, b) => b.value - a.value),
    canvas,
  );

  const tiles: TreemapTile[] = [];
  const groups: TreemapGroup[] = [];

  for (const rect of groupRects) {
    groups.push({
      group: rect.key,
      value: rect.value,
      x: scaleX(rect.x),
      y: scaleY(rect.y),
      width: scaleX(rect.w),
      height: scaleY(rect.h),
    });

    const members = usable.filter((item) => (item.group ?? "") === rect.key);
    const laid = squarify(
      members.map((item) => ({ key: item.key, value: item.value })),
      rect,
    );
    for (const placed of laid) {
      tiles.push(tile(placed, usable, total, scaleX, scaleY));
    }
  }

  return { tiles, groups, total };
}

/**
 * Groups as full-width (or full-height) bands, largest first.
 *
 * Across the long side, so each band is as wide and as shallow as it can be:
 * a small group then has room for a name, where squarifying would have given
 * it the same area as an unlabellable sliver.
 */
function bands(values: Sized[], rect: Rect): (Sized & Rect)[] {
  const total = values.reduce((sum, entry) => sum + entry.value, 0);
  if (total <= 0) return [];

  const horizontal = rect.w >= rect.h;
  const out: (Sized & Rect)[] = [];
  let offset = 0;

  for (const entry of values) {
    const share = entry.value / total;
    const extent = share * (horizontal ? rect.h : rect.w);
    out.push(
      horizontal
        ? { ...entry, x: rect.x, y: rect.y + offset, w: rect.w, h: extent }
        : { ...entry, x: rect.x + offset, y: rect.y, w: extent, h: rect.h },
    );
    offset += extent;
  }
  return out;
}

function tile(
  rect: Sized & Rect,
  items: TreemapItem[],
  total: number,
  scaleX: (value: number) => number,
  scaleY: (value: number) => number,
): TreemapTile {
  const item = items.find((entry) => entry.key === rect.key)!;
  return {
    ...item,
    x: scaleX(rect.x),
    y: scaleY(rect.y),
    width: scaleX(rect.w),
    height: scaleY(rect.h),
    share: item.value / total,
  };
}
