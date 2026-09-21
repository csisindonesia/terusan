/**
 * The palette every chart draws from.
 *
 * Extracted from the time series so a bar, a column and a stacked share all
 * speak the same colour language: a reader who learns that the first slot is
 * blue should not have to relearn it one card down.
 *
 * The categorical order is validated — lightness band, chroma floor, CVD
 * separation on the adjacent pairlist, and the normal-vision floor — against
 * both the light and the dark card surface. Assigned by position and never
 * cycled: past `MAX_SERIES` a chart refuses rather than repeating a hue,
 * because two series sharing a colour is worse than a series not shown.
 *
 * Light steps clear 3:1 against white for the first two slots only, so any
 * chart using the later ones carries its own relief — a legend that names each
 * segment, and the figure printed beside it.
 */

export const SERIES_COLORS = [
  "#2a78d6",
  "#eb6834",
  "#1baf7a",
  "#eda100",
  "#e87ba4",
  "#008300",
  "#4a3aa7",
  "#e34948",
] as const;

export const SERIES_COLORS_DARK = [
  "#3987e5",
  "#d95926",
  "#199e70",
  "#c98500",
  "#d55181",
  "#008300",
  "#9085e9",
  "#e66767",
] as const;

export const MAX_SERIES = SERIES_COLORS.length;

/**
 * Slots are referenced through CSS variables rather than as raw hex, so the
 * dark steps swap in one place rather than at every mark.
 */
export const seriesColor = (index: number) => `var(--series-${index + 1})`;

export const SERIES_LIGHT_SLOTS = Object.fromEntries(
  SERIES_COLORS.map((hex, index) => [`--series-${index + 1}`, hex]),
) as React.CSSProperties;

//: Tailwind arbitrary properties, so the dark steps ride the same `dark:` class
//: the rest of the page uses.
export const SERIES_DARK_SLOTS = SERIES_COLORS_DARK.map(
  (hex, index) => `dark:[--series-${index + 1}:${hex}]`,
).join(" ");

/**
 * The single hue, for a chart where every mark measures the same thing.
 *
 * One measure is not an identity, so it gets one colour: a second hue would
 * claim a distinction the data does not carry. This is the first categorical
 * slot, which clears every check against both surfaces on its own.
 */
export const MONO_SLOTS = { "--viz-bar": SERIES_COLORS[0] } as React.CSSProperties;
export const MONO_DARK_SLOT = `dark:[--viz-bar:${SERIES_COLORS_DARK[0]}]`;

/**
 * The same hues, as fills that carry white text.
 *
 * A mark beside a label and a block with the label written on it are different
 * problems: the first answers to the card behind it, the second to the fill
 * underneath. White at 11px needs 4.5:1, so each hue is stepped towards black
 * until it clears 4.6:1 — measured, not judged. Some move barely at all (the
 * blue loses 3%), and the ones that carry a lot of light move a long way: the
 * orange lands at a rust, the amber at a bronze, because that is what an amber
 * carrying white text *is*.
 *
 * White for every slot rather than white on some and near-black on others: a
 * chart whose label colour flips between neighbouring tiles reads as two
 * encodings where there is one.
 *
 * These are deliberately below the categorical order's lightness band. That
 * band exists so a small mark reads against the surface; a filled block with a
 * label on it has the opposite constraint, and 6:1 against a white card is not
 * a problem for a rectangle the size of a card.
 */
export const SERIES_SOLID = [
  "#2974d0",
  "#be542a",
  "#15855d",
  "#9c6a00",
  // Re-stepped rather than simply darkened: the pink and the amber landed
  // 14.6 apart once both were dark enough for white text, under the
  // normal-vision floor of 15. This is the nearest point to the original hue
  // that clears it.
  "#c24a7c",
  "#008300",
  "#4a3aa7",
  "#cf4242",
] as const;

export const SERIES_SOLID_DARK = [
  "#3275c7",
  "#c35022",
  "#15855e",
  "#9f6900",
  "#c24a75",
  "#008300",
  "#736aba",
  "#bd5454",
] as const;

/** What a label on one of those fills is written in, in either theme. */
export const SOLID_INK = "#ffffff";

export const solidColor = (index: number) => `var(--solid-${index + 1})`;

export const SOLID_LIGHT_SLOTS = Object.fromEntries(
  SERIES_SOLID.map((hex, index) => [`--solid-${index + 1}`, hex]),
) as React.CSSProperties;

export const SOLID_DARK_SLOTS = SERIES_SOLID_DARK.map(
  (hex, index) => `dark:[--solid-${index + 1}:${hex}]`,
).join(" ");

/** Gridlines and tracks: one step off the surface, never a darker ink. */
export const GRID = "color-mix(in oklab, currentColor 12%, transparent)";
