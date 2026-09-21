/**
 * Turning an identifier into something a reader recognises.
 *
 * `apbd_expenditure_realisasi` is a key: stable, lowercase, safe in a URL and
 * in a filename. It is not a name. A table of thirteen of them reads as a list
 * of database rows rather than of things anyone measured.
 *
 * The identifier is never replaced, only accompanied — it is what the API takes
 * as a parameter and what the pipeline normalizes into, so a reader who has to
 * quote it needs to see the real one, not a prettified version they would have
 * to translate back.
 *
 * This derives the name rather than storing it, which is the weaker of the two
 * options: a proper label belongs in the indicator's record, beside its unit
 * and its source, where it could be Indonesian, or carry the parenthetical a
 * statistician would write. Deriving it means `seki_wpi_food` reads "Seki Wpi
 * Food" when "Wholesale price index — food" is what anyone would say. Good
 * enough to read a table by, and honest about where it came from.
 */

/**
 * Words that are abbreviations rather than words, with the casing they are
 * actually written in. Without this, `apbd` title-cases to `Apbd`, which is
 * not a thing.
 */
const ACRONYMS: Record<string, string> = {
  apbd: "APBD",
  apbn: "APBN",
  bi: "BI",
  bps: "BPS",
  cpi: "CPI",
  dak: "DAK",
  dau: "DAU",
  dbh: "DBH",
  djpk: "DJPK",
  // FRED prefixes its series, and each identifier ends in the series' own
  // FRED code — `spadotfrtidn` — which is what you paste back into FRED to
  // find the figure. It stays as written rather than being hidden.
  fred: "FRED",
  gdp: "GDP",
  jkse: "JKSE",
  idr: "IDR",
  ihk: "IHK",
  ihsg: "IHSG",
  mom: "MoM",
  ojk: "OJK",
  p2p: "P2P",
  pad: "PAD",
  pdb: "PDB",
  pihps: "PIHPS",
  pmi: "PMI",
  qoq: "QoQ",
  spi: "SPI",
  // Trading Economics, which prefixes its series: the same measure taken from
  // the agency that publishes it is a different series, at a different
  // revision and rounding, so the two must not read alike.
  te: "TE",
  usd: "USD",
  yoy: "YoY",
};

/** Words a title leaves lowercase unless they open it. */
const MINOR = new Set([
  "a",
  "and",
  "at",
  "by",
  "for",
  "in",
  "of",
  "or",
  "per",
  "the",
  "to",
  "vs",
]);

/** `apbd_expenditure_realisasi` → `APBD Expenditure Realisasi`. */
export function titleFromId(id: string): string {
  const words = id.split(/[_\-\s]+/).filter(Boolean);
  if (!words.length) return id;

  return words
    .map((word, index) => {
      const lower = word.toLowerCase();
      if (ACRONYMS[lower]) return ACRONYMS[lower];
      // A number keeps its own shape: `q1`, `2024`.
      if (/^\d/.test(lower)) return lower;
      if (index > 0 && MINOR.has(lower)) return lower;
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join(" ");
}

/**
 * What to call an indicator in a table or a heading.
 *
 * The published name where the pipeline has one, and a name derived from the
 * identifier where it does not. Both exist because neither is enough on its
 * own: most series here carry a readable identifier and no name row, while a
 * bulk source like FRED carries an eight-character code and a real title —
 * `nicmjq31` derives to nothing anyone would recognise.
 */
export function indicatorLabel(indicator: {
  indicator_id: string;
  name?: string;
  slug?: string;
}): string {
  // The slug before the identifier: the identifier is a derived code now, and
  // `nicmjq31` derives to nothing anyone would recognise, while the slug is
  // the key someone chose when they declared the mapping.
  return (
    indicator.name?.trim() ||
    (indicator.slug ? titleFromId(indicator.slug) : "") ||
    titleFromId(indicator.indicator_id)
  );
}

/**
 * What to call a dataset in a table or a heading.
 *
 * The catalogue's title where it has one, the slug where it does not, and the
 * code as a last resort — the identifier a dataset is addressed by says
 * nothing, deliberately, so something else has to be shown beside it.
 */
export function datasetLabel(dataset: {
  dataset_id: string;
  title?: string;
  slug?: string;
}): string {
  return (
    dataset.title?.trim() ||
    (dataset.slug ? titleFromId(dataset.slug) : "") ||
    titleFromId(dataset.dataset_id)
  );
}
