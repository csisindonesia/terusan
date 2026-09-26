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
 * Abbreviations inside a coded label, which stay as they are written.
 *
 * Deliberately short, and deliberately without `API` or `DSB`: `SENJATA API`
 * is a firearm, where `api` is the Indonesian word for fire, and `dsb` is
 * written lowercase wherever a person writes it. A list that guesses is worse
 * than a list that shouts, because a reader cannot tell a guess from a name.
 */
const CODED_ACRONYMS = new Set([
  "TNI",
  "POLRI",
  "KKB",
  "OPM",
  "TPNPB",
  "PMI",
  "DPR",
  "DPRD",
  "MRP",
  "KPU",
  "ASN",
  "PNS",
  "BUMN",
  "NKRI",
  "HAM",
  "LSM",
  "TKI",
  "PKL",
  "SPBU",
  "BBM",
  "KTP",
  "SAR",
  "SD",
  "SMP",
  "SMA",
  "SMK",
  "RT",
  "RW",
  "PT",
  "CV",
]);

/**
 * A label from the coders' vocabularies, as a sentence rather than a shout.
 *
 * VEWS's dropdowns are stored in capitals — `SERANGAN TANPA SENJATA API` — as
 * coding forms have been since they were printed on paper. A column of them
 * reads as an alarm, and a table where every cell shouts has no emphasis left
 * for the cells that matter.
 *
 * The stored value is never changed, only the rendering: it is what the API
 * filters by and what the human record spells, so anywhere the two are being
 * compared — an article's own coding page — the capitals stay.
 */
export function codedLabel(value: string): string {
  const lowered = value
    .split(/(\s+)/)
    .map((token) => {
      const bare = token.replace(/[^\p{L}\p{N}]/gu, "").toUpperCase();
      return CODED_ACRONYMS.has(bare) ? token.toUpperCase() : token.toLowerCase();
    })
    .join("");
  // The first letter of the label, wherever it falls: some values open on a
  // parenthesis or a number.
  return lowered.replace(/\p{L}/u, (letter) => letter.toUpperCase());
}

/**
 * Words inside a place name that are not words: the two provinces whose names
 * open on an abbreviation.
 */
const PLACE_ACRONYMS = new Set(["DKI", "DI"]);

/**
 * A place name as a person writes it, out of the capitals it is stored in.
 *
 * The outlet dimension and the geography reference both hold `NUSA TENGGARA
 * TIMUR`, which is how the spreadsheets they were built from spell it. A
 * column of those reads as an alarm; a column of `Nusa Tenggara Timur` reads
 * as a list of places.
 *
 * Title case rather than the sentence case a coded label gets, because these
 * are proper nouns: `Jawa barat` is not a thing anybody writes.
 */
export function placeLabel(value: string): string {
  return value
    .split(/(\s+)/)
    .map((token) => {
      const bare = token.replace(/[^\p{L}\p{N}]/gu, "").toUpperCase();
      if (!bare) return token;
      if (PLACE_ACRONYMS.has(bare)) return token.toUpperCase();
      const lower = token.toLowerCase();
      return lower.replace(/\p{L}/u, (letter) => letter.toUpperCase());
    })
    .join("");
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
