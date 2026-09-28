/**
 * One figure per province, as the shade of the province.
 *
 * A line per place answers "how has each changed"; eight lines is where it
 * stops being readable, and thirty-eight provinces is most of what Indonesian
 * releases publish against. The map answers the other question — where — for
 * one period at a time, and needs no legend of names to do it.
 *
 * Three honesty rules:
 *
 * The scale is linear from zero. A square-root or quantile ramp would make a
 * province with two injuries look like one with two hundred, and the reader
 * would come away with a spread the figures do not have. Where one province
 * dwarfs the rest, the map says so.
 *
 * Zero is not missing. A province that reported none is tinted; one with no
 * figure at all is hatched, and the legend names both.
 *
 * The provinces sit on a Leaflet basemap so the neighbours — Malaysia,
 * Timor-Leste, Papua New Guinea — stay on the page: a province is read against
 * where it is, and a border drawn against nothing looks like a coastline.
 *
 * Identity is never shade alone. Every province names itself and its figure on
 * hover and to a screen reader, and the largest are ranked in text beneath.
 */

import "leaflet/dist/leaflet.css";

import type * as Leaflet from "leaflet";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import {
  PROVINCE_PROJECTION,
  PROVINCE_SHAPES,
  type ProvinceShape,
} from "~/lib/indonesia-provinces";
import { cn } from "~/lib/utils";
import { SERIES_DARK_SLOTS, SERIES_LIGHT_SLOTS } from "~/lib/viz";

export type RegionFigure = { geo_id: string; value: number };

/** The palest a non-zero figure is drawn, as a share of the full hue. */
const FLOOR = 14;

/** How many provinces are ranked in text under the map. */
const RANKED = 5;

/** Sabang to Merauke, Miangas to Rote. */
const BOUNDS: Leaflet.LatLngBoundsExpression = [
  [-11.2, 94.8],
  [6.2, 141.2],
];

// Esri's grey canvas: keyless, quiet enough that the provinces' shade is the
// only colour on the page, with a dark twin for dark mode.
const TILES = {
  light:
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
  dark: "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
};
const ATTRIBUTION = "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ";

/**
 * Pale yellow through green to the deep teal, after ColorBrewer's YlGn: the
 * lightness falls steadily end to end, so a darker province is always a larger
 * figure, and the hue shift keeps neighbouring shades apart.
 */
const RAMP = ["#ffffcc", "#78c679", "#005357"] as const;

/** A point along the ramp, from 0 (palest) to 100 (darkest). */
function along(share: number): string {
  const [low, mid, high] = RAMP;
  return share <= 50
    ? `color-mix(in oklab, ${mid} ${(share * 2).toFixed(1)}%, ${low})`
    : `color-mix(in oklab, ${high} ${((share - 50) * 2).toFixed(1)}%, ${mid})`;
}

function shade(value: number, max: number): string {
  if (value <= 0 || max <= 0) return along(0);
  return along(FLOOR + (100 - FLOOR) * Math.min(value / max, 1));
}

/** The outline's SVG path, unprojected back to latitude and longitude. */
function ringsOf(shape: ProvinceShape): Leaflet.LatLngTuple[][] {
  const { west, north, scale } = PROVINCE_PROJECTION;
  return shape.d
    .split("M")
    .filter(Boolean)
    .map((ring) =>
      ring
        .replace("Z", "")
        .split("L")
        .map((point) => {
          const [x = 0, y = 0] = point.split(" ").map(Number);
          return [north - y / scale, x / scale + west] as Leaflet.LatLngTuple;
        }),
    );
}

function isDark() {
  return document.documentElement.classList.contains("dark");
}

export function ChoroplethMap({
  figures,
  unit,
  format,
  className,
}: {
  figures: RegionFigure[];
  unit?: string;
  format: (value: number) => string;
  className?: string;
}) {
  const [active, setActive] = useState<string | null>(null);
  const hatch = `choropleth-missing-${useId().replace(/:/g, "")}`;
  const container = useRef<HTMLDivElement>(null);
  const layers = useRef(new Map<string, Leaflet.Polygon>());
  const [ready, setReady] = useState(false);

  const { byGeo, max, ranked } = useMemo(() => {
    const byGeo = new Map(figures.map((figure) => [figure.geo_id, figure.value]));
    const drawn = PROVINCE_SHAPES.filter((shape) => byGeo.has(shape.geo_id));
    const max = Math.max(0, ...drawn.map((shape) => byGeo.get(shape.geo_id)!));
    const ranked = drawn
      .map((shape) => ({ ...shape, value: byGeo.get(shape.geo_id)! }))
      .filter((shape) => shape.value > 0)
      .sort((a, b) => b.value - a.value);
    return { byGeo, max, ranked };
  }, [figures]);

  const missing =
    PROVINCE_SHAPES.length - PROVINCE_SHAPES.filter((s) => byGeo.has(s.geo_id)).length;
  const shown = PROVINCE_SHAPES.find((shape) => shape.geo_id === active);
  const shownValue = shown ? byGeo.get(shown.geo_id) : undefined;
  const suffix = unit ? ` ${unit}` : "";

  // Leaflet touches `window` on import, so it is loaded only in the browser.
  useEffect(() => {
    let map: Leaflet.Map | undefined;
    let observer: MutationObserver | undefined;
    let resize: ResizeObserver | undefined;
    let cancelled = false;

    void import("leaflet").then((L) => {
      if (cancelled || !container.current) return;
      map = L.map(container.current, {
        // The page scrolls past the map; a wheel that zooms instead traps it.
        scrollWheelZoom: false,
        attributionControl: true,
        zoomSnap: 0.25,
      });
      map.fitBounds(BOUNDS, { padding: [8, 8] });
      // A map made while its box has no size (a hidden tab, a card still
      // laying out) fits to nothing and draws every province as an empty
      // path. Re-measure whenever the box changes, and fit again until the
      // reader has moved the map themselves.
      let moved = false;
      const box = container.current;
      box.addEventListener("pointerdown", () => (moved = true), { once: true });
      box.addEventListener("keydown", () => (moved = true), { once: true });
      resize = new ResizeObserver(() => {
        if (!map) return;
        map.invalidateSize();
        if (!moved) map.fitBounds(BOUNDS, { padding: [8, 8] });
      });
      resize.observe(box);
      // A tap on the sea clears a province picked by a tap.
      map.on("click", () => setActive(null));

      const tiles = L.tileLayer(isDark() ? TILES.dark : TILES.light, {
        attribution: ATTRIBUTION,
        maxZoom: 12,
      }).addTo(map);
      observer = new MutationObserver(() =>
        tiles.setUrl(isDark() ? TILES.dark : TILES.light),
      );
      observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["class"],
      });

      const renderer = L.svg({ padding: 0.5 });
      for (const shape of PROVINCE_SHAPES) {
        const polygon = L.polygon(ringsOf(shape), {
          renderer,
          smoothFactor: 0.5,
          lineJoin: "round",
        }).addTo(map);
        // Name and figure on the province itself, following the pointer.
        // Leaflet opens it on tap for touch screens, where there is no hover.
        polygon.bindTooltip(shape.name, {
          sticky: true,
          direction: "top",
          offset: [0, -10],
          opacity: 1,
          className:
            "!rounded-md !border-border !bg-popover !px-2 !py-1 !text-xs !text-popover-foreground !shadow-md before:!hidden",
        });
        polygon.on("mouseover", () => setActive(shape.geo_id));
        polygon.on("mouseout", () => setActive(null));
        polygon.on("click", () => setActive(shape.geo_id));
        const element = polygon.getElement();
        if (element) {
          element.setAttribute("tabindex", "0");
          element.addEventListener("focus", () => setActive(shape.geo_id));
          element.addEventListener("blur", () => setActive(null));
        }
        layers.current.set(shape.geo_id, polygon);
      }

      // The hatch for "no figure" lives in the renderer's own <svg>, where
      // the province paths can reach it by id.
      const svg = (renderer as unknown as { _container?: SVGSVGElement })._container;
      if (svg) {
        const ns = "http://www.w3.org/2000/svg";
        const defs = document.createElementNS(ns, "defs");
        defs.innerHTML = `<pattern id="${hatch}" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="4" height="4" fill="var(--muted)"/><line x1="0" y1="0" x2="0" y2="4" stroke="var(--border)" stroke-width="2"/></pattern>`;
        svg.prepend(defs);
      }
      setReady(true);
    });

    return () => {
      cancelled = true;
      observer?.disconnect();
      resize?.disconnect();
      map?.remove();
      layers.current.clear();
    };
  }, [hatch]);

  // Fills are set as styles, not Leaflet options, so they can use the
  // theme's variables and follow a switch to dark without a redraw.
  useEffect(() => {
    if (!ready) return;
    for (const shape of PROVINCE_SHAPES) {
      const element = layers.current.get(shape.geo_id)?.getElement() as
        SVGPathElement | undefined;
      if (!element) continue;
      const value = byGeo.get(shape.geo_id);
      const on = active === shape.geo_id;
      element.style.fill = value === undefined ? `url(#${hatch})` : shade(value, max);
      element.style.fillOpacity = "0.9";
      // The card's own surface as the border, so provinces read as separate
      // fills without adding ink that carries no data.
      element.style.stroke = on ? "var(--foreground)" : "var(--card)";
      element.style.strokeWidth = on ? "1.6" : "0.6";
      element.style.outline = "none";
      const figure = value === undefined ? "no figure" : `${format(value)}${suffix}`;
      element.setAttribute("aria-label", `${shape.name}: ${figure}`);
      layers.current
        .get(shape.geo_id)
        ?.setTooltipContent(
          `<span class="font-medium">${shape.name}</span><span class="text-muted-foreground"> · ${figure}</span>`,
        );
    }
    // Brought forward so its outline is not hidden under a neighbour's fill.
    if (active) layers.current.get(active)?.bringToFront();
  }, [ready, byGeo, max, active, hatch, format, suffix]);

  return (
    // Its own palette slots, so the shade does not depend on being mounted
    // inside a chart that happens to set them.
    <div
      className={cn("space-y-3", SERIES_DARK_SLOTS, className)}
      style={SERIES_LIGHT_SLOTS}
    >
      {/* The readout repeats the tooltip for screen readers and for a
          province picked by keyboard. It keeps its height when empty so the
          map does not jump. */}
      <p className="h-5 text-sm" aria-live="polite">
        {shown ? (
          <>
            <span className="font-medium">{shown.name}</span>
            <span className="text-muted-foreground">
              {" · "}
              {shownValue === undefined
                ? "no figure"
                : `${format(shownValue)}${suffix}`}
            </span>
          </>
        ) : (
          <span className="text-muted-foreground">
            Hover a province for its figure.
          </span>
        )}
      </p>

      {/* `isolate` keeps Leaflet's pane z-indexes from climbing over the
          sticky header and open menus. */}
      <div
        ref={container}
        className="isolate w-full overflow-hidden rounded-md bg-muted"
        // Inline so the box has a height before any stylesheet arrives:
        // Leaflet takes its first measure on creation.
        style={{ aspectRatio: "928 / 400", minHeight: 256 }}
        role="group"
        aria-label="Map of Indonesia's provinces, shaded by figure"
      />

      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-muted-foreground">
        <div className="flex items-center gap-2">
          <span className="tabular-nums">0</span>
          <span
            className="h-2.5 w-40 rounded-sm"
            style={{
              background: `linear-gradient(to right, ${shade(0, 1)}, ${shade(1e-9, 1)} 2%, ${along(50)} ${2 + 98 * ((50 - FLOOR) / (100 - FLOOR))}%, ${shade(1, 1)})`,
            }}
          />
          <span className="tabular-nums">
            {format(max)}
            {suffix}
          </span>
        </div>
        {missing ? (
          <div className="flex items-center gap-1.5">
            <svg className="size-3 rounded-sm" viewBox="0 0 12 12" aria-hidden>
              <defs>
                <pattern
                  id={`${hatch}-key`}
                  width="4"
                  height="4"
                  patternUnits="userSpaceOnUse"
                  patternTransform="rotate(45)"
                >
                  <rect width="4" height="4" fill="var(--muted)" />
                  <line
                    x1="0"
                    y1="0"
                    x2="0"
                    y2="4"
                    stroke="var(--border)"
                    strokeWidth="2"
                  />
                </pattern>
              </defs>
              <rect width="12" height="12" fill={`url(#${hatch}-key)`} />
            </svg>
            No figure ({missing} {missing === 1 ? "province" : "provinces"})
          </div>
        ) : null}
      </div>

      {ranked.length ? (
        <ol className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2 lg:grid-cols-3">
          {ranked.slice(0, RANKED).map((shape, index) => (
            <li key={shape.geo_id} className="flex items-baseline gap-2">
              <span className="w-4 text-xs text-muted-foreground tabular-nums">
                {index + 1}
              </span>
              <span className="truncate">{shape.name}</span>
              <span className="ml-auto tabular-nums text-muted-foreground">
                {format(shape.value)}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
