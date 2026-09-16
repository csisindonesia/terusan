import { z } from "zod";

/**
 * Search-parameter shapes the router actually produces.
 *
 * TanStack Router parses a value that looks numeric as a number, so
 * `?q=199` arrives as 199 and a plain `z.string()` rejects it — turning a
 * perfectly good search into an error page. These schemas accept whichever
 * type the URL implies and the caller converts where a string is needed;
 * coercing in the schema instead makes the stored value disagree with the
 * parsed one, and the router rewrites the URL to `q="199"`, quotes and all.
 */

/** Free text, which may look like a number. */
export const textParam = z.union([z.string(), z.number()]).optional();

/** A filter holding one value or several. */
export const listParam = z
  .union([z.string(), z.number(), z.array(z.union([z.string(), z.number()]))])
  .optional();

export type TextParam = z.infer<typeof textParam>;
export type ListParam = z.infer<typeof listParam>;

/** A search value as text, for the API and for form fields. */
export function asText(value: TextParam): string | undefined {
  return value === undefined ? undefined : String(value);
}

/** A filter's values as text, however many the URL carried. */
export function asTextList(value: ListParam): string[] {
  if (value === undefined) return [];
  return (Array.isArray(value) ? value : [value]).map(String);
}
