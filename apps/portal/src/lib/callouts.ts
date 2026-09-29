/**
 * Where to write a figure beside the place it belongs to, when there are too
 * many places to write each one on top of itself.
 *
 * Two separate questions. The anchor is a point that is certainly inside the
 * place — the centre of a crescent-shaped province is in the sea, and a line
 * pointing at the sea points at the wrong province. The label is then pushed
 * off its anchor and away from its neighbours until no two labels overlap and
 * no label covers another place's anchor, and a leader line joins the two.
 *
 * Plain arithmetic over screen pixels, so it can be rerun on every pan and
 * zoom; the result depends only on the anchors' positions relative to each
 * other and to the frame, so panning moves the labels without shuffling them.
 */

export type Point = { x: number; y: number };
export type Ring = Point[];
/** A box labels keep clear of, by its top-left corner. */
export type Box = { x: number; y: number; width: number; height: number };

/** A placed label: its centre, its size, and where its leader line starts. */
export type Callout = {
  id: string;
  anchor: Point;
  center: Point;
  width: number;
  height: number;
  /** The point on the label's edge nearest its anchor. */
  tail: Point;
};

function area(ring: Ring): number {
  let sum = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    sum += ring[j]!.x * ring[i]!.y - ring[i]!.x * ring[j]!.y;
  }
  return sum / 2;
}

function inside(point: Point, ring: Ring): boolean {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i]!;
    const b = ring[j]!;
    if (
      a.y > point.y !== b.y > point.y &&
      point.x < ((b.x - a.x) * (point.y - a.y)) / (b.y - a.y) + a.x
    ) {
      hit = !hit;
    }
  }
  return hit;
}

function edgeDistance(point: Point, ring: Ring): number {
  let best = Infinity;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[j]!;
    const b = ring[i]!;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const length = dx * dx + dy * dy;
    const t = length
      ? Math.max(0, Math.min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / length))
      : 0;
    best = Math.min(best, Math.hypot(point.x - a.x - t * dx, point.y - a.y - t * dy));
  }
  return best;
}

/**
 * A point well inside the largest of a place's rings: the sampled point
 * furthest from any edge, searched on a coarse grid and then a finer one
 * around the best. Islands smaller than the main ring are ignored, so an
 * archipelago province points at its main island rather than at open water
 * between them.
 */
export function interiorPoint(rings: Ring[]): Point {
  const main = rings.reduce((best, ring) =>
    Math.abs(area(ring)) > Math.abs(area(best)) ? ring : best,
  );
  const xs = main.map((p) => p.x);
  const ys = main.map((p) => p.y);
  let west = Math.min(...xs);
  let east = Math.max(...xs);
  let north = Math.min(...ys);
  let south = Math.max(...ys);
  let best: Point = { x: (west + east) / 2, y: (north + south) / 2 };
  let bestDistance = inside(best, main) ? edgeDistance(best, main) : -1;

  for (let pass = 0; pass < 3; pass++) {
    const steps = 12;
    const dx = (east - west) / steps;
    const dy = (south - north) / steps;
    for (let i = 0; i <= steps; i++) {
      for (let j = 0; j <= steps; j++) {
        const point = { x: west + i * dx, y: north + j * dy };
        if (!inside(point, main)) continue;
        const distance = edgeDistance(point, main);
        if (distance > bestDistance) {
          best = point;
          bestDistance = distance;
        }
      }
    }
    // Narrow the search to the cells around the best point so far.
    west = best.x - dx;
    east = best.x + dx;
    north = best.y - dy;
    south = best.y + dy;
  }
  return best;
}

/** How far a label starts from its anchor, in pixels, before it is pushed. */
const REACH = 16;
/** Clear space kept between two labels, and around an anchor. */
const GAP = 2;
const ROUNDS = 240;

function nearestOnRect(
  point: Point,
  center: Point,
  width: number,
  height: number,
): Point {
  return {
    x: Math.max(center.x - width / 2, Math.min(point.x, center.x + width / 2)),
    y: Math.max(center.y - height / 2, Math.min(point.y, center.y + height / 2)),
  };
}

/**
 * Place one label per anchor inside a `width` × `height` frame.
 *
 * Each label starts on the side of its anchor facing away from the nearby
 * anchors — out of a crowd, towards open space — and is then relaxed: pairs
 * that overlap are pushed apart along whichever axis separates them sooner, a
 * label sitting on any anchor is pushed off it, and a weak spring keeps each
 * label near where it started so the leader lines stay short. Obstacles —
 * the map's own controls — are kept clear the same way.
 */
export function placeCallouts(
  items: { id: string; anchor: Point; width: number; height: number }[],
  frame: { width: number; height: number },
  obstacles: Box[] = [],
): Callout[] {
  const labels = items.map((item) => {
    let ax = 0;
    let ay = 0;
    for (const other of items) {
      if (other === item) continue;
      const dx = item.anchor.x - other.anchor.x;
      const dy = item.anchor.y - other.anchor.y;
      const d2 = dx * dx + dy * dy;
      if (d2 > 0 && d2 < 160 * 160) {
        ax += dx / d2;
        ay += dy / d2;
      }
    }
    const length = Math.hypot(ax, ay);
    // Alone on the map: above the anchor, where a reader looks first.
    const [ux, uy] = length > 1e-4 ? [ax / length, ay / length] : [0, -1];
    const reach =
      REACH + Math.abs(ux) * (item.width / 2) + Math.abs(uy) * (item.height / 2);
    const home = { x: item.anchor.x + ux * reach, y: item.anchor.y + uy * reach };
    return { ...item, home, center: { ...home } };
  });

  const clamp = (label: (typeof labels)[number]) => {
    const w = label.width / 2 + GAP;
    const h = label.height / 2 + GAP;
    label.center.x = Math.max(w, Math.min(frame.width - w, label.center.x));
    label.center.y = Math.max(h, Math.min(frame.height - h, label.center.y));
  };

  for (let round = 0; round < ROUNDS; round++) {
    let moved = false;
    for (let i = 0; i < labels.length; i++) {
      const a = labels[i]!;
      for (let j = i + 1; j < labels.length; j++) {
        const b = labels[j]!;
        const dx = b.center.x - a.center.x;
        const dy = b.center.y - a.center.y;
        const ox = (a.width + b.width) / 2 + GAP - Math.abs(dx);
        const oy = (a.height + b.height) / 2 + GAP - Math.abs(dy);
        if (ox <= 0 || oy <= 0) continue;
        moved = true;
        // Ties broken by index, so two labels on one spot still part.
        if (ox < oy) {
          const push = (ox / 2) * (dx === 0 ? (i < j ? -1 : 1) : Math.sign(dx));
          a.center.x -= push;
          b.center.x += push;
        } else {
          const push = (oy / 2) * (dy === 0 ? (i < j ? -1 : 1) : Math.sign(dy));
          a.center.y -= push;
          b.center.y += push;
        }
      }
      // Off every anchor, its own included: a label must not hide the dot
      // its line points at, or another place's.
      for (const other of labels) {
        const dx = a.center.x - other.anchor.x;
        const dy = a.center.y - other.anchor.y;
        const ox = a.width / 2 + GAP + 1 - Math.abs(dx);
        const oy = a.height / 2 + GAP + 1 - Math.abs(dy);
        if (ox <= 0 || oy <= 0) continue;
        moved = true;
        if (ox < oy) a.center.x += ox * (dx === 0 ? 1 : Math.sign(dx));
        else a.center.y += oy * (dy === 0 ? -1 : Math.sign(dy));
      }
    }
    for (const label of labels) {
      for (const box of obstacles) {
        const dx = label.center.x - (box.x + box.width / 2);
        const dy = label.center.y - (box.y + box.height / 2);
        const ox = (label.width + box.width) / 2 + GAP - Math.abs(dx);
        const oy = (label.height + box.height) / 2 + GAP - Math.abs(dy);
        if (ox <= 0 || oy <= 0) continue;
        moved = true;
        // Out by the shortest way that stays in the frame: a control in a
        // corner can only be left by its open sides, and pushing a label
        // towards the frame's edge would clamp it straight back under.
        const w = label.width / 2 + GAP;
        const h = label.height / 2 + GAP;
        const exits = [
          { x: box.x - w, y: label.center.y },
          { x: box.x + box.width + w, y: label.center.y },
          { x: label.center.x, y: box.y - h },
          { x: label.center.x, y: box.y + box.height + h },
        ].filter(
          (exit) =>
            exit.x >= w &&
            exit.x <= frame.width - w &&
            exit.y >= h &&
            exit.y <= frame.height - h,
        );
        const exit = exits.sort(
          (p, q) =>
            Math.hypot(p.x - label.center.x, p.y - label.center.y) -
            Math.hypot(q.x - label.center.x, q.y - label.center.y),
        )[0];
        if (exit) label.center = exit;
      }
    }
    // The spring fades out over the rounds, so the last rounds only part
    // labels and never pull one back onto a neighbour.
    const pull = 0.04 * (1 - round / ROUNDS);
    for (const label of labels) {
      label.center.x += (label.home.x - label.center.x) * pull;
      label.center.y += (label.home.y - label.center.y) * pull;
      clamp(label);
    }
    if (!moved && round > 20) break;
  }

  return labels.map(({ id, anchor, center, width, height }) => ({
    id,
    anchor,
    center,
    width,
    height,
    tail: nearestOnRect(anchor, center, width, height),
  }));
}
