/**
 * Holding several values for one filter, in the URL.
 */

/** Read a search parameter that may arrive as one value or as several. */
export function asList(value: string | string[] | undefined): string[] {
  if (value === undefined) return [];
  return Array.isArray(value) ? value : [value];
}

/** Add or remove a value, returning undefined when nothing is left. */
export function toggle(current: string[], value: string): string[] | undefined {
  const next = current.includes(value)
    ? current.filter((entry) => entry !== value)
    : [...current, value];
  // Undefined rather than an empty array, so an exhausted filter leaves the
  // URL rather than sitting in it as `?geo_type=`.
  return next.length ? next : undefined;
}
