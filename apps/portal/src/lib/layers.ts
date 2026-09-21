/**
 * What the lake's layers are, in one sentence each.
 *
 * The medallion names are load-bearing here — a figure's trustworthiness is a
 * function of how far along this path it has come (program.md §5–8) — and
 * nothing in the portal says what they mean. A reader looking at a chart of
 * "Bronze 4.9%, Silver 95.1%" cannot tell whether that is good news.
 */

export const LAYER_NOTES: Record<string, string> = {
  raw: "The original material, exactly as it was downloaded — never parsed, never edited. What every later layer is checked against.",
  bronze:
    "Machine-readable extraction of RAW: one row per row the source printed, typed but not yet reconciled. It may still be imperfect or only partly normalized.",
  silver:
    "The published layer: standardized observations, dimensions and documents, with periods bounded, units explicit and geography resolved. This is what the portal serves.",
  gold: "Aggregates built for serving — pre-computed answers to questions asked often enough to be worth keeping.",
};

/** The sentence for a layer, where there is one. */
export function layerNote(layer: string): string | undefined {
  return LAYER_NOTES[layer.toLowerCase()];
}
