/**
 * The serving layer's HTTP surface.
 *
 * Every response arrives in the envelope from program.md §53 — `data`, plus
 * `meta` or `error` — so this unwraps it once rather than at every call site.
 */

const BASE_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8080";

/** Where the API lives, for pages that show a reader how to call it. */
export const apiBaseUrl = BASE_URL;

/** The picture taken of an article when it was collected. */
export function newsScreenshotUrl(documentId: string): string {
  return new URL(
    `/v1/news/articles/${encodeURIComponent(documentId)}/screenshot`,
    BASE_URL,
  ).toString();
}

export type Meta = {
  total?: number;
  limit?: number;
  offset?: number;
  has_more: boolean;
  /**
   * Where the next page starts, for a caller walking a long list one page at a
   * time. Opaque: it is the server's bookmark, to be handed back as `after` and
   * not composed. Absent on the last page, and for sorts that cannot carry one.
   */
  next_cursor?: string;
  layer?: string;
  source?: string;
};

export type ApiError = {
  code: string;
  message: string;
  detail?: string;
};

type Envelope<T> = {
  data: T;
  meta?: Meta;
  error?: ApiError;
};

export type Page<T> = {
  data: T;
  meta?: Meta;
};

/**
 * A failure the API described, rather than a transport failure.
 *
 * Kept distinct so the UI can show what the server actually said — "limit must
 * be between 1 and 10000" is worth reading, "Failed to fetch" is not.
 */
export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly error: ApiError,
  ) {
    super(error.detail ? `${error.message}: ${error.detail}` : error.message);
    this.name = "ApiRequestError";
  }
}

type Params = Record<string, string | number | string[] | undefined>;

async function request<T>(path: string, params?: Params): Promise<Page<T>> {
  const url = new URL(path, BASE_URL);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === undefined || value === "") continue;
    if (Array.isArray(value)) {
      // Repeated rather than comma-joined: the API takes both, and repeats
      // survive a value that happens to contain a comma.
      if (!value.length) continue;
      for (const entry of value) url.searchParams.append(key, entry);
    } else {
      url.searchParams.set(key, String(value));
    }
  }

  return unwrap<T>(
    await fetch(url, { headers: { Accept: "application/json" }, credentials }),
  );
}

/**
 * Send the session cookie, and accept one back.
 *
 * The portal and the API are different origins — `:3000` and `:8080` in
 * development, usually two subdomains in a deployment — so a browser leaves
 * cookies off by default and every request would arrive as a stranger. The API
 * allows this for its own origins only (an allow-list, never `*`), which is
 * what makes it safe to ask for.
 */
const credentials: RequestCredentials = "include";

/**
 * The one thing this API is asked to *do* rather than answer.
 *
 * Everything the runner takes is a query parameter rather than a body: the
 * whole request is then a URL, which is what makes it something a maintainer
 * can reproduce with curl from what the browser's network tab shows.
 */
async function post<T>(path: string, params?: Params): Promise<Page<T>> {
  const url = new URL(path, BASE_URL);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === undefined || value === "") continue;
    url.searchParams.set(key, String(value));
  }

  return unwrap<T>(
    await fetch(url, {
      method: "POST",
      headers: { Accept: "application/json" },
      credentials,
    }),
  );
}

/**
 * A request that carries a body, for the shelf.
 *
 * The rest of this client puts everything in the URL, because a query is a
 * thing a maintainer should be able to reproduce with curl from the network
 * tab. A collection is not a query: it carries a list of records, and a
 * hundred of them do not belong in a query string.
 */
async function send<T>(
  method: "POST" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<Page<T>> {
  const url = new URL(path, BASE_URL);
  return unwrap<T>(
    await fetch(url, {
      method,
      headers: body
        ? { Accept: "application/json", "Content-Type": "application/json" }
        : { Accept: "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials,
    }),
  );
}

async function unwrap<T>(response: Response): Promise<Page<T>> {
  const body = (await response.json()) as Envelope<T>;

  if (!response.ok || body.error) {
    throw new ApiRequestError(
      response.status,
      body.error ?? { code: "unknown", message: response.statusText },
    );
  }
  return { data: body.data, meta: body.meta };
}

export type Observation = {
  observation_id: string;
  indicator_id: string;
  period: string;
  period_start: string;
  period_end: string;
  temporal_resolution: string;
  /**
   * A string, not a number. Silver stores decimal128 because published
   * statistics are decimal quantities; parsing to a JS number here would
   * reintroduce the float drift the decimal storage exists to avoid.
   */
  value: string | null;
  unit?: string;
  status: string;
  value_unambiguous: boolean;
  geo_id: string | null;
  geo_name?: string;
  geo_type?: string;
  /**
   * Not every series varies by place. Bank Indonesia's food prices are a
   * commodity by a date and name no place at all, so `geo_id` is null for all
   * of them and the commodity is what tells one row from another.
   *
   * `commodity_id` stays null until the commodity is in the reference registry;
   * `commodity_name` is what the source printed, and is present either way.
   */
  commodity_id: string | null;
  commodity_name?: string;
  source_id: string;
  source_url?: string;
};

export type Indicator = {
  indicator_id: string;
  /**
   * What the series is called, and the publisher's own identifier for it.
   * Absent until a source publishes the indicators table — most series carry a
   * readable identifier and need neither, but FRED's identifiers are
   * eight-character codes and the title is the only readable thing about them.
   */
  name?: string;
  code?: string;
  /**
   * The readable key the series was declared under. The identifier is a
   * derived code, so this is what a reader recognises and what a search
   * matches; the URL deliberately carries the code (program.md §10).
   */
  slug?: string;
  /**
   * What the series counts, in the publisher's own words, and who publishes
   * it. FRED redistributes the Treasury's and the OECD's figures, so the
   * source we collected from is not who produced them.
   */
  description?: string;
  publisher?: string;
  release?: string;
  temporal_resolution: string;
  unit?: string;
  observations: number;
  geographies: number;
  period_start: string;
  period_end: string;
  /** Named rather than counted: "bps" says where a figure came from. */
  sources: string[];
  /** The collection this series belongs to. */
  dataset_id?: string;
  /** What a reader searches by, derived when the series is published. */
  tags: string[];
  /** When the pipeline last wrote these rows, not how recent the figures are. */
  last_updated?: string;
  /**
   * Where this is one of a price's four series, all four: on the close of a
   * folded list, and on any of the four asked for by itself.
   */
  ohlc?: { open: string; high: string; low: string; close: string };
};

export type Geography = {
  geo_id: string;
  name: string;
  geo_type: string;
  parent_geo_id?: string;
  bps_code?: string;
  iso_code?: string;
  valid_from?: string;
};

/**
 * One member of the commodity dimension (program.md §12).
 *
 * Derived from the observations rather than read from the registry: almost
 * none of these commodities are in it yet, so the name the source printed is
 * the identity and `commodity_id` is null for most of them. What the registry
 * does know — a category, an HS code — is joined on where it does.
 */
export type Commodity = {
  /** What the registry calls it, or what the source printed. */
  name: string;
  commodity_id: string | null;
  category?: string;
  subcategory?: string;
  hs_code?: string;
  /** What the figures are measured in; several where a commodity is published
   * both as a price and as a quantity. */
  units: string[];
  observations: number;
  indicators: number;
  /** Which series those are; a price's four appear as its close. */
  indicator_ids: string[];
  /** Zero for most: a commodity series names a date and a commodity, no place. */
  geographies: number;
  period_start: string;
  period_end: string;
  sources: string[];
  /** When the pipeline last wrote these rows, not how recent the figures are. */
  last_updated?: string;
};

export type CommodityQuery = {
  q?: string;
  category?: string[];
  source?: string[];
  indicator?: string[];
  order?: string;
  limit?: number;
  offset?: number;
};

/**
 * What `/v1/indicators` narrows the series list by.
 *
 * Any one of these turns the request from "every series" into a filtered,
 * sorted page, which is what a page wanting a handful of rows should ask for:
 * the unfiltered list runs to tens of thousands of series and many megabytes.
 */
export type IndicatorQuery = {
  q?: string;
  frequency?: string[];
  unit?: string[];
  source?: string[];
  tag?: string[];
  id?: string[];
  dataset?: string;
  sort?:
    | "indicator_id"
    | "temporal_resolution"
    | "unit"
    | "source"
    | "coverage"
    | "last_updated";
  dir?: "asc" | "desc";
  /** A price's open, high, low and close listed once, as the close. */
  fold?: "ohlc";
  limit?: number;
  offset?: number;
};

/** The values the series list can be filtered by, taken over every series. */
export type IndicatorFacets = {
  frequencies: string[];
  units: string[];
  sources: string[];
  tags: string[];
};

/** The registry record for a provider: how its figures are collected. */
export type Source = {
  source_id: string;
  name: string;
  organization?: string;
  category: string;
  source_type: string;
  collection_method: string;
  base_url?: string;
  country?: string;
  license?: string;
  update_frequency: string;
  /** Cron, absent where the source is run by hand only. */
  schedule?: string;
  active: boolean;
  max_requests_per_second: number;
  notes?: string;
  tags: string[];
};

/**
 * A collection as its publisher issues it (program.md §9).
 *
 * The unit between a source and an indicator: Bank Indonesia's consumer survey
 * is one dataset holding four series.
 */
export type Dataset = {
  dataset_id: string;
  /**
   * What the collection is called, from the Silver dataset catalogue. Absent
   * until `terusan silver dimensions` has published it — the figures alone
   * cannot say any of this.
   */
  slug?: string;
  title?: string;
  description?: string;
  tags: string[];
  source_id: string;
  source_name?: string;
  organization?: string;
  license?: string;
  /** Cron, from the source registry. */
  schedule?: string;
  indicators: string[];
  /**
   * How many series a reader sees in it: a price's open, high, low and close
   * are one. Absent from an API older than the folding.
   */
  series?: number;
  observations: number;
  period_start: string;
  period_end: string;
  last_updated?: string;
};

/** One physical table in the lake — an operational view, not a research one. */
export type LakeTable = {
  slug: string;
  layer: string;
  name: string;
  rows: number;
};

export type ObservationQuery = {
  indicator?: string[];
  geo?: string;
  /**
   * Places by the name they were published under, where `geo` takes the
   * identifier. Most survey cities have no registry entry and therefore no
   * identifier, so the name is the only handle a reader has on them.
   */
  geo_name?: string[];
  geo_type?: string[];
  commodity?: string[];
  status?: string[];
  /** Calendar years as a set — not the range they span. */
  year?: string[];
  q?: string;
  period_start?: string;
  period_end?: string;
  order?: string;
  limit?: number;
  offset?: number;
  /**
   * The previous page's `next_cursor`, in place of an offset.
   *
   * `OFFSET 2,596,600` makes the warehouse produce and discard every row before
   * the one asked for — 2.4s against 0.3s for the first page of the same
   * series. A cursor names the boundary instead and costs what the first page
   * costs. It can only step forward from a page already seen, so a jump to page
   * twelve still goes by offset.
   */
  after?: string;
  /** Series only: how many points a line is worth drawing with. */
  points?: number;
  /** Series only: how many lines to return, largest first. */
  members?: number;
  /** Series only: which dimension to split the lines along. */
  dimension?: string;
};

/**
 * One plotted point.
 *
 * A published figure where the bucket holds exactly one, and the mean of the
 * bucket where it holds more — `count` and `missing` say which, and how much
 * the point stands for.
 */
export type SeriesPoint = {
  period: string;
  value: string | null;
  count: number;
  missing: number;
};

export type SeriesLine = {
  /** The label the page knows the member by — `Aceh`, `Rice`, `Aceh · Rice`. */
  member: string;
  count: number;
  points: SeriesPoint[];
};

/**
 * A series as a chart needs it, aggregated where the data is.
 *
 * Bank Indonesia's daily food prices are 2.6 million figures: sending them to a
 * browser to be plotted is several hundred megabytes for a picture a thousand
 * pixels wide, and taking the first few thousand instead draws a line through
 * one week and calls it a decade.
 */
export type ObservationSeries = {
  /** native, month, quarter or year. `native` means nothing was averaged. */
  granularity: string;
  dimension: string;
  /** How many members there are, against however many lines came back. */
  members: number;
  series: SeriesLine[];
};

/**
 * What the filters on a series can offer, counted in the warehouse.
 *
 * Read from the API rather than off the rows on screen, because a series can be
 * larger than any one request: the first page of Bank Indonesia's daily food
 * prices holds every province and a single week of 2017, and a Year control
 * built from it would offer one year out of ten.
 *
 * A dimension the series does not vary along comes back empty, which is what
 * tells the page not to show that control at all.
 */
export type ObservationFacets = {
  places: Facet[];
  commodities: Facet[];
  years: Facet[];
  statuses: Facet[];
};

/**
 * One artifact the warehouse collected: the material a figure was read out of
 * (program.md §13).
 *
 * The grain is the retrieval, not the publication. Two editions of one
 * handbook are two documents, because they are two files with two content
 * hashes, and an observation points at one of them and not the other.
 *
 * Distinct from `Regulation`, which is a legal instrument with articles and
 * citations and a model of its own (program.md §14).
 */
export type Document = {
  document_id: string;
  /**
   * regulation, report, publication, web_page or data_file. Never absent: an
   * artifact nobody has classified is a `data_file`, which is what an unread
   * download is.
   */
  document_type: string;
  title: string;
  /** What tells two artifacts of one collection apart — "edition 2025". */
  subtitle?: string;
  language?: string;
  author?: string;
  /** Who issued it, which is not who we collected it from. */
  publisher?: string;
  published_at?: string;
  /**
   * Where the publisher put it. This is the address that breaks when an agency
   * reorganises its site, which is why the preserved copy exists.
   */
  source_url?: string;
  original_filename?: string;
  media_type?: string;
  size_bytes?: number;
  /** Known only for what an extractor has read, so absent for most. */
  page_count?: number;
  word_count?: number;
  /**
   * What rests on it, counted from the observations' own provenance. Zero is a
   * real answer: much of what is collected is a listing page that no figure
   * was read out of.
   */
  indicator_count: number;
  observation_count: number;
  dataset_id?: string;
  dataset_slug?: string;
  /** The RAW partition it landed under — ["edition=2025"]. */
  partition: string[];
  source_id: string;
  content_hash?: string;
  retrieved_at?: string;
};

export type DocumentDetail = Document & {
  source_name?: string;
  organization?: string;
  license?: string;
  /**
   * Whether the preserved copy can be served from this deployment. False where
   * the lake is a bucket, and the reader should follow `source_url` instead.
   */
  file_available: boolean;
};

export type DocumentFacets = {
  types: Facet[];
  sources: Facet[];
  media_types: Facet[];
  publishers: Facet[];
  /** Keyed by `dataset_id`; the readable title comes from the dataset list. */
  datasets: Facet[];
  years: Facet[];
};

export type DocumentQuery = {
  type?: string[];
  source_id?: string[];
  dataset_id?: string[];
  media_type?: string[];
  publisher?: string;
  year?: number;
  /** "true" for documents figures were read out of, "false" for the rest. */
  has_data?: string;
  q?: string;
  order?: string;
  limit?: number;
  offset?: number;
};

/**
 * Where the preserved copy of a document is served from.
 *
 * `inline` asks the API to let the browser render it rather than download it,
 * which it grants for PDFs alone — anything else is bytes from a third-party
 * site and has no business rendering in the API's origin.
 */
export function documentFileUrl(
  id: string,
  options?: { inline?: boolean; name?: string },
): string {
  let url = `${BASE_URL}/v1/documents/${encodeURIComponent(id)}/file`;
  // A trailing filename, which the API ignores. Browsers title their PDF
  // viewer from the last path segment, so without one a reader sits in front
  // of a document called "file".
  if (options?.name) url += `/${encodeURIComponent(options.name)}`;
  return options?.inline ? `${url}?inline=1` : url;
}

/**
 * One regulation as BPK catalogues it — regional or central.
 *
 * The grain is the catalogue record, not the parsed text: BPK publishes
 * entries whose PDF never converted, so `parse_status` is absent on some rows
 * and the page says so rather than showing an empty document.
 *
 * `number` is a string because a regulation's number is a label rather than a
 * quantity — "1", "01" and "12A" all occur.
 */
export type Regulation = {
  key: string;
  bpk_id?: string;
  source_id: string;
  /**
   * perda or pkd for regional instruments, pusat or kementerian for central
   * ones. The corpus never spells pkd "perkada".
   */
  track: string;
  /** Perda, Perbup, UU, PP, Permen… — read off the document, so it can be absent. */
  instrument?: string;
  scope?: string;
  title: string;
  number?: string;
  year?: number;
  region_name?: string;
  region_type?: string;
  region_code?: string;
  category?: string;
  subject?: string;
  status?: string;
  legal_status?: string;
  enacted_date?: string;
  published_date?: string;
  detail_url?: string;
  pdf_url?: string;
  /** ok, no_sections, unbounded, empty, or absent where the PDF never converted. */
  parse_status?: string;
  /** How much of the PDF the conversion kept, 0–1. */
  md_coverage?: number;
  words?: number;
  bab?: number;
  pasal?: number;
  ayat?: number;
  citations?: number;
};

export type RegulationDetail = Regulation & {
  preamble?: string;
  menimbang?: string;
  mengingat?: string;
  penutup?: string;
  source_name?: string;
  organization?: string;
  license?: string;
};

/** One structural unit of a regulation: a bab, bagian, pasal or ayat. */
export type RegulationSection = {
  seq: number;
  kind: string;
  bab?: string;
  bab_num?: number;
  bab_title?: string;
  bagian?: string;
  paragraf?: string;
  pasal?: string;
  pasal_num?: number;
  ayat?: number;
  text: string;
  words?: number;
};

/**
 * One instrument a regulation cites.
 *
 * `cite_key` is present only where the citation resolved to exactly one
 * document in the corpus. Four fifths name no jurisdiction and resolve to
 * every same-numbered regulation in the country, so an unresolved citation is
 * shown as the document wrote it rather than guessed at.
 */
export type RegulationCitation = {
  index: number;
  instrument?: string;
  scope?: string;
  number?: number;
  year?: number;
  cited_title?: string;
  found_in?: string;
  cite_key?: string;
};

/** One value a filter can take, with how many documents carry it. */
export type Facet = { value: string; count: number };

export type RegulationFacets = {
  tracks: Facet[];
  instruments: Facet[];
  region_types: Facet[];
  regions: Facet[];
  categories: Facet[];
  years: Facet[];
};

export type RegulationQuery = {
  track?: string[];
  instrument?: string[];
  region_type?: string[];
  region_code?: string[];
  category?: string[];
  region?: string;
  year?: number;
  year_from?: number;
  year_to?: number;
  has_text?: string;
  q?: string;
  order?: string;
  limit?: number;
  offset?: number;
};

/**
 * One pipeline run, as the journal in the lake recorded it (program.md §39).
 *
 * The audit trail behind a series: whether the scraper ran, whether it landed
 * anything, and what it said when it did not. Served from the lake rather than
 * the catalog database, so it answers when there is no database.
 */
export type PipelineRun = {
  run_id: string;
  /** `ingest-bps`, `extract-bronze`, `normalize-bi-food-prices`. */
  pipeline: string;
  /** ingest | extract | normalize. */
  kind: string;
  /** succeeded | failed. A run that died mid-flight records no row at all. */
  status: string;
  trigger: string;
  source_id?: string;
  dataset?: string;
  indicator_id?: string;
  started_at: string;
  finished_at: string;
  duration_seconds: number;
  /**
   * What came in and what went out. `records_out` is the number worth reading:
   * a run that succeeded and produced nothing is the quiet failure.
   */
  records_in: number;
  records_out: number;
  bytes_written: number;
  error_message?: string;
  /** Stage-specific counters as JSON text — each stage counts different things. */
  detail?: string;
  pipeline_version?: string;
  parser_version?: string;
  /** A dry run lands nothing on purpose, so its zero is not a failure. */
  dry_run: boolean;
  command?: string;
  host?: string;
};

export type RunQuery = {
  source?: string[];
  pipeline?: string[];
  kind?: string[];
  status?: string[];
  indicator?: string;
  limit?: number;
  offset?: number;
};

/**
 * What this deployment will do beyond answering questions.
 *
 * The serving layer is read-only everywhere it is deployed publicly, and can
 * be started with the pipeline runner switched on where a maintainer is
 * working. Asked rather than assumed, so the page offers a button that works
 * or says plainly that it cannot, instead of one that fails on click.
 */
export type Capabilities = {
  /** Whether `POST /v1/sources/{id}/run` will start anything. */
  run_pipelines: boolean;
  /**
   * Whether this deployment keeps the shelf — which is what decides whether a
   * collection has a URL anyone can open, or lives in one browser.
   */
  collections: boolean;
  /** Whether that shelf accepts changes. */
  collections_write: boolean;
  /**
   * Whether a collection has an owner and members. Needs accounts; without
   * them every folder is shared by the whole deployment. Absent from an API
   * older than members.
   */
  collection_members?: boolean;
  /**
   * Whether this deployment has accounts at all. False on one started without
   * an application database, and the portal then shows no login because there
   * is nothing to log in to.
   */
  auth: boolean;
  /** Whether a session is needed to read anything, not only to be named. */
  auth_required: boolean;
  /** Whether the login page can offer "request access". */
  registration?: boolean;
  /** Whether readers can ask for a source to be collected. */
  suggestions: boolean;
  /** Whether `POST /v1/assistant/chat` has a model behind it. */
  assistant?: boolean;
  /**
   * Whether the assistant's conversations are kept on the server, so a chat's
   * URL opens it anywhere. Without it they live in the browser.
   */
  assistant_history?: boolean;
};

/**
 * One request to collect a source the warehouse does not have.
 *
 * A work item rather than a message: it carries a status, and it is visible to
 * everyone signed in, so the next person about to ask for the same source can
 * see that somebody already did.
 */
export type Suggestion = {
  id: string;
  title: string;
  url: string;
  /** daily, weekly, monthly, quarterly, annual, irregular, one-off, unknown. */
  cadence: string;
  description?: string;
  /** open, planned, ingested or declined. */
  status: string;
  /** What a maintainer said when they moved it. */
  note?: string;
  requested_by: string;
  requested_by_email: string;
  created_at: string;
  updated_at: string;
};

/**
 * An account.
 *
 * `role` is admin, researcher or guest. An admin manages accounts; a guest is
 * a researcher whose access ends at `access_expires_at`.
 */
export type User = {
  id: string;
  email: string;
  name?: string;
  role: string;
  department?: string;
  /** active, or pending while a registration waits for an admin. */
  status: string;
  created_at: string;
  last_login_at?: string;
  last_active_at?: string;
  access_expires_at?: string;
  disabled?: boolean;
};

/** One row of an account's access log. */
export type AccessEvent = {
  id: string;
  /** login, web (the portal) or api (an API token). */
  kind: string;
  at: string;
  ip?: string;
  country?: string;
  region?: string;
  city?: string;
  user_agent?: string;
};

export type UserDetail = { user: User; access: AccessEvent[] };

/** What an admin sends to create or change an account. */
export type UserInput = {
  email?: string;
  name?: string;
  department?: string;
  role?: string;
  password?: string;
  /** ISO timestamp for a guest; null clears it. */
  access_expires_at?: string | null;
  disabled?: boolean;
};

export type Session = {
  user: User;
  /** Which login this is, so the account page can mark "this browser". */
  id: string;
  expires_at: string;
};

/** One row of "where am I signed in". */
export type LoginRecord = {
  id: string;
  /** What that browser called itself, unparsed. */
  user_agent?: string;
  created_at: string;
  expires_at: string;
  current: boolean;
};

/** One API token, as the account page lists it. Never the secret. */
export type ApiToken = {
  id: string;
  name: string;
  /** The first few characters, to match a row to the value in a script. */
  hint: string;
  created_at: string;
  expires_at: string;
  last_used_at?: string;
};

/** The one response the secret appears in. */
export type CreatedApiToken = ApiToken & { token: string };

/**
 * One record filed into a collection, as the API stores it.
 *
 * `kind` is a plain string here rather than the portal's union: this is what
 * came over the wire, and the shelf validates it into the narrower type (see
 * `lib/workspace.ts`) rather than trusting the server to have sent one.
 */
export type ShelfItem = {
  kind: string;
  id: string;
  label: string;
  note?: string;
  added_at: string;
};

/** An account as a collection shows it. */
export type ShelfPerson = {
  id: string;
  /** Absent for an account deleted since it was added. */
  email?: string;
  name?: string;
};

/**
 * The viewer's part in a collection: `owner` does everything, `member` files
 * and removes records, `shared` is a folder from before there were owners
 * that everyone may change.
 */
export type ShelfRole = "owner" | "member" | "shared";

export type ShelfCollection = {
  id: string;
  name: string;
  description?: string;
  created_at: string;
  updated_at: string;
  items: ShelfItem[];
  /**
   * Whether its figures are served under `/v1/collections/{id}/…` to its
   * owner and members. Absent from an API older than the setting.
   */
  api?: boolean;
  /** Absent from an API older than members. */
  role?: ShelfRole;
  owner?: ShelfPerson;
  members?: (ShelfPerson & { added_at: string })[];
};

export type ShelfQuery = {
  id: string;
  name: string;
  kind: string;
  path: string;
  search: Record<string, unknown>;
  summary?: string;
  created_at: string;
};

/**
 * One pipeline command the serving layer started, while it is still going.
 *
 * Distinct from `PipelineRun`, and the difference matters: a run is the row the
 * pipeline writes to the journal *once it has finished*, and a job is the
 * process itself — it exists during the minutes when there is no row yet, and
 * it dies with the server that started it. A finished job's lasting record is
 * the run it produced.
 */
export type Job = {
  job_id: string;
  /** ingest. */
  kind: string;
  /** The source slug it runs. */
  target: string;
  indicator_id?: string;
  /** running | succeeded | failed. */
  status: "running" | "succeeded" | "failed";
  /** The exact argv, for a reader who would rather run it themselves. */
  command: string;
  started_at: string;
  finished_at?: string;
  duration_seconds: number;
  exit_code?: number;
  error?: string;
  /** The tail of what the command printed, oldest first. */
  output: string[];
};

/**
 * One newspaper the news monitor reads.
 *
 * The registry half — which paper, which province, which host — comes from the
 * committed outlet list; the counts come from what has actually been collected.
 * An outlet with no articles is still listed, with a zero: a province whose
 * coverage has gone quiet is a thing to see, not a row to hide.
 */
export type NewsOutlet = {
  host: string;
  outlet: string;
  province: string;
  geo_id?: string;
  base_url: string;
  /** Which search-page shape the crawl uses. The first thing to check when an
   * outlet stops yielding. */
  adapter: string;
  active: boolean;
  /** Why a row was corrected or retired — a vanity domain that redirects into
   * a network, or a title with no working site. */
  note?: string;
  /** Kept: articles stored because they are about an issue. A small fraction
   * of what the paper published. */
  articles: number;
  /** Read at all — the denominator. Every article the paper published in the
   * window is read and counted; only the issue ones are stored, so without
   * this a rise in incidents cannot be told from a crawl that reached further. */
  scanned: number;
  /** Matched the vocabulary (a candidate), and recorded (the classifier
   * agreed it is the issue). */
  matched: number;
  recorded: number;
  screenshots: number;
  first_seen?: string;
  last_seen?: string;
};

/** What the classifier made of one article. */
export type NewsCoding = {
  profile: string;
  engine: string;
  /** Null while the classifier was unreachable and the article is a candidate
   * waiting to be coded. False means it was read and rejected. */
  accepted: boolean | null;
  gate_probability?: number;
  province?: string;
  district_city?: string;
  date?: string;
  violence_form?: string;
  weapon_type?: string;
  issue_type?: string;
  actor1?: string;
  actor2?: string;
  intervene?: string;
  /** Absent where the reporting did not say. Never zero for "unknown" — the
   * API drops the missing marker rather than serving it as a figure. */
  deaths?: number;
  injured?: number;
  /** How far the incident got: tension, a limited beating, violence that
   * spread, a riot. Machine-coded only — VEWS has no such column, so it is
   * never shown as comparable with a human coding. */
  escalation?: string;
};

/**
 * One article in the corpus.
 *
 * The body is deliberately not here. The archive holds the whole page so a
 * coding stays reproducible, but the words belong to the paper that wrote
 * them: this carries a title, a lead and a link back.
 */
export type NewsArticle = {
  /** The Bronze document id, which addresses this article's own page. */
  document_id: string;
  url: string;
  title: string;
  lead: string;
  outlet: string;
  outlet_host: string;
  outlet_province: string;
  published_at?: string;
  /** Which issues claimed this article. Empty for one the crawl kept and no
   * profile wanted. */
  issues: string[];
  matched_terms: string[];
  /** Whether the page was photographed when it was collected. */
  screenshot: boolean;
  coding?: NewsCoding;
};

/** One article's own page: everything that answers "can I trust this coding". */
export type NewsArticleDetail = NewsArticle & {
  /** A lexicon term, the sitemap, the section page. An article found down a
   * route that is about to break is a gap in tomorrow's coverage. */
  discovered_by?: string;
  /** How much text the coding was made from. A paywalled piece leaves a lead
   * and little else. */
  body_chars?: number;
  content_hash?: string;
  raw_path?: string;
  media_type?: string;
  retrieved_at?: string;
  source_id?: string;
  /** The classifier's probability per field, so a reader can see which answer
   * to doubt first. */
  confidence?: Record<string, number>;
  /** Which dictionary categories the article carried — the act, who was
   * involved, what it left behind, what it was about. The rule is the pairing,
   * so the categories say why the article was read at all. */
  matched_categories?: string[];
  /** Why the coding was put to a second, larger model, and which fields that
   * model supplied. Absent where the first classifier was sure. A reason with
   * no fields beside it means the second reader was unreachable. */
  escalation_reason?: string;
  deepened?: string[];
  /** The incident this article was clustered into, and the other papers that
   * reported it. */
  event_id?: string;
  also_reported_by?: string[];
};

/** One incident, after several papers' reports of it were collapsed. */
export type NewsEvent = {
  event_id: string;
  profile: string;
  date?: string;
  province?: string;
  district_city?: string;
  violence_form?: string;
  weapon_type?: string;
  issue_type?: string;
  actor1?: string;
  actor2?: string;
  intervene?: string;
  deaths?: number;
  injured?: number;
  /** The worst escalation any of the reports described: papers file at
   * different moments, and the event is the worse of them. */
  escalation?: string;
  /** How many separate reports were collapsed into this one event. */
  report_count: number;
  outlets: string[];
  sources: string[];
};

/**
 * One day of crawling, from one newspaper's side of it.
 *
 * The crawl's own log, and the only place the articles it read and threw away
 * are counted: an article that is not about a monitored issue is never stored,
 * so `scanned` minus `recorded` is a number with no rows behind it by design.
 */
export type NewsTally = {
  /** The day the crawl ran, not the day the articles were published. */
  date: string;
  /** What discovery turned up, before the window and the gate. Zero against a
   * reachable site means discovery is broken for this outlet. */
  discovered: number;
  /** Read in full: fetched, dated and put to the dictionary. */
  scanned: number;
  /** Carried the dictionary's terms — a candidate, not a finding. */
  matched: number;
  /** Kept: the classifier agreed it was about the issue. */
  recorded: number;
  /** How many times the crawl visited this outlet that day. */
  runs: number;
};

export type NewsArticleQuery = {
  outlet?: string;
  issue?: string;
  /** Where the *paper* is. The incident's own province is `incident_province`:
   * an outlet reports on its neighbours, and the two are different questions. */
  province?: string;
  q?: string;
  from?: string;
  to?: string;
  /** Only articles a classifier read and accepted. */
  coded?: string;
  /** The coded labels, each a multiple choice. Sent as repeated parameters, so
   * a label containing a comma stays one value. */
  form?: string[];
  issue_type?: string[];
  weapon?: string[];
  escalation?: string[];
  incident_province?: string[];
  limit?: number;
  offset?: number;
};

/**
 * What the corpus can be narrowed by, counted under the filters already on.
 *
 * The place is the incident's, not the paper's.
 */
export type NewsFacets = {
  forms: Facet[];
  issues: Facet[];
  weapons: Facet[];
  escalations: Facet[];
  provinces: Facet[];
};

/** The home page's headline counts, without downloading every series. */
export type LakeStats = {
  series: number;
  observations: number;
  datasets: number;
  commodities: number;
  sources: number;
  as_of: string;
};

export const api = {
  stats: () => request<LakeStats>("/v1/stats"),
  datasets: () => request<Dataset[]>("/v1/datasets"),
  dataset: (id: string) => request<Dataset>(`/v1/datasets/${encodeURIComponent(id)}`),
  storage: () => request<LakeTable[]>("/v1/storage"),
  /**
   * Every series when called bare, which only the whole-catalogue summaries
   * should do; with parameters, a filtered page of them.
   */
  indicators: (params?: IndicatorQuery) =>
    request<Indicator[]>("/v1/indicators", params),
  indicatorFacets: () => request<IndicatorFacets>("/v1/indicators/facets"),
  indicator: (id: string) =>
    request<Indicator>(`/v1/indicators/${encodeURIComponent(id)}`),
  geography: (params?: {
    geo_type?: string[];
    q?: string;
    limit?: number;
    offset?: number;
  }) => request<Geography[]>("/v1/geography", params),
  commodities: (params?: CommodityQuery) =>
    request<Commodity[]>("/v1/commodities", params),
  sources: () => request<Source[]>("/v1/sources"),
  /** The newspapers the news monitor reads, with what has come from each. */
  newsOutlets: (params?: {
    q?: string;
    province?: string;
    active?: string;
    order?: string;
    limit?: number;
    offset?: number;
  }) => request<NewsOutlet[]>("/v1/news/outlets", params),
  newsOutlet: (host: string) =>
    request<NewsOutlet>(`/v1/news/outlets/${encodeURIComponent(host)}`),
  /** One outlet's articles, newest first. */
  newsOutletArticles: (host: string, params?: NewsArticleQuery) =>
    request<NewsArticle[]>(
      `/v1/news/outlets/${encodeURIComponent(host)}/articles`,
      params,
    ),
  /** One outlet's crawl log, a row per day, newest first. */
  newsOutletTallies: (host: string, params?: { limit?: number; offset?: number }) =>
    request<NewsTally[]>(
      `/v1/news/outlets/${encodeURIComponent(host)}/tallies`,
      params,
    ),
  /** The filter options for the corpus, counted under the filters already on. */
  newsArticleFacets: (params?: NewsArticleQuery) =>
    request<NewsFacets>("/v1/news/articles/facets", params),
  newsArticles: (params?: NewsArticleQuery) =>
    request<NewsArticle[]>("/v1/news/articles", params),
  newsArticle: (id: string) =>
    request<NewsArticleDetail>(`/v1/news/articles/${encodeURIComponent(id)}`),
  newsEvents: (params?: {
    province?: string;
    from?: string;
    to?: string;
    limit?: number;
    offset?: number;
  }) => request<NewsEvent[]>("/v1/news/events", params),
  observations: (params?: ObservationQuery) =>
    request<Observation[]>("/v1/observations", params),
  /** The filter options for a series, counted under the filters already on. */
  observationFacets: (params?: ObservationQuery) =>
    request<ObservationFacets>("/v1/observations/facets", params),
  /** The same figures as a chart needs them — a line per member, bucketed. */
  observationSeries: (params?: ObservationQuery) =>
    request<ObservationSeries>("/v1/observations/series", params),
  documents: (params?: DocumentQuery) => request<Document[]>("/v1/documents", params),
  /**
   * Whether the pipelines behind one series are still running, and what
   * happened last time they did. Ingestions of the sources behind the series
   * and normalizations naming it; extraction reads the whole of RAW at once
   * and belongs to no series, so it is under `runs` instead.
   */
  indicatorRuns: (id: string, params?: { limit?: number; offset?: number }) =>
    request<PipelineRun[]>(`/v1/indicators/${encodeURIComponent(id)}/runs`, params),
  /** The whole run history, newest first. */
  runs: (params?: RunQuery) => request<PipelineRun[]>("/v1/runs", params),
  /** What one series was read out of — the question a reader of a figure has. */
  indicatorDocuments: (id: string) =>
    request<Document[]>(`/v1/indicators/${encodeURIComponent(id)}/documents`),
  document: (id: string) =>
    request<DocumentDetail>(`/v1/documents/${encodeURIComponent(id)}`),
  /** The series read out of one document — the link the catalogue exists for. */
  documentIndicators: (id: string) =>
    request<Indicator[]>(`/v1/documents/${encodeURIComponent(id)}/indicators`),
  // Counted under whatever filters are already applied, so an option that
  // would return nothing is never offered.
  documentFacets: (params?: DocumentQuery) =>
    request<DocumentFacets>("/v1/documents/facets", params),
  regulations: (params?: RegulationQuery) =>
    request<Regulation[]>("/v1/regulations", params),
  regulation: (key: string) =>
    request<RegulationDetail>(`/v1/regulations/${encodeURIComponent(key)}`),
  regulationSections: (key: string, params?: { limit?: number; offset?: number }) =>
    request<RegulationSection[]>(
      `/v1/regulations/${encodeURIComponent(key)}/sections`,
      params,
    ),
  regulationCitations: (key: string) =>
    request<RegulationCitation[]>(
      `/v1/regulations/${encodeURIComponent(key)}/citations`,
    ),
  // Counted under whatever filters are already applied, so an option that
  // would return nothing is never offered.
  regulationFacets: (params?: RegulationQuery) =>
    request<RegulationFacets>("/v1/regulations/facets", params),

  /** Whether this serving layer will run a pipeline when asked. */
  capabilities: () => request<Capabilities>("/v1/capabilities"),

  /**
   * Logging in, and staying logged in.
   *
   * The session itself is a cookie the browser holds and this code never sees:
   * it is `HttpOnly`, so a script on the page — ours or an injected one —
   * cannot read it. What these return is who the cookie belongs to.
   */
  login: (email: string, password: string, remember: boolean) =>
    send<Session>("POST", "/v1/auth/login", { email, password, remember }),
  logout: () => send<{ logged_out: boolean }>("POST", "/v1/auth/logout"),
  me: () => request<Session>("/v1/auth/me"),
  /** The first account on a deployment that has none. Loopback only. */
  bootstrap: (email: string, password: string, name?: string) =>
    send<User>("POST", "/v1/auth/bootstrap", { email, password, name }),

  /**
   * The account's own settings.
   *
   * Every one of these is scoped to the session making the call — none of them
   * takes a user id, because there is no route that would let one person act
   * on another.
   */
  updateProfile: (name: string) => send<User>("PATCH", "/v1/auth/profile", { name }),
  changePassword: (currentPassword: string, newPassword: string) =>
    send<{ changed: boolean }>("POST", "/v1/auth/password", {
      current_password: currentPassword,
      new_password: newPassword,
    }),
  /** What readers have asked the warehouse to collect. */
  suggestions: (limit = 50) => request<Suggestion[]>("/v1/suggestions", { limit }),
  suggest: (body: {
    title: string;
    url: string;
    cadence: string;
    description?: string;
  }) => send<Suggestion>("POST", "/v1/suggestions", body),
  /** Move a request through the queue — open, planned, ingested, declined. */
  updateSuggestion: (id: string, status: string, note?: string) =>
    send<Suggestion>("PATCH", `/v1/suggestions/${encodeURIComponent(id)}`, {
      status,
      note,
    }),
  deleteSuggestion: (id: string) =>
    send<{ deleted: boolean }>("DELETE", `/v1/suggestions/${encodeURIComponent(id)}`),

  logins: () => request<LoginRecord[]>("/v1/auth/sessions"),
  endLogin: (id: string) =>
    send<{ ended: string }>("DELETE", `/v1/auth/sessions/${encodeURIComponent(id)}`),
  endOtherLogins: () => send<{ ended: number }>("DELETE", "/v1/auth/sessions/others"),
  /** Ask for an account; an admin approves it before it can sign in. */
  register: (body: {
    email: string;
    password: string;
    name: string;
    department: string;
  }) => send<{ requested: boolean }>("POST", "/v1/auth/register", body),
  /** The Users page. Admins only. */
  users: () => request<User[]>("/v1/admin/users"),
  user: (id: string) =>
    request<UserDetail>(`/v1/admin/users/${encodeURIComponent(id)}`),
  createUser: (body: UserInput) =>
    send<User & { password?: string }>("POST", "/v1/admin/users", body),
  updateUser: (id: string, body: UserInput) =>
    send<User>("PATCH", `/v1/admin/users/${encodeURIComponent(id)}`, body),
  approveUser: (id: string, role: string, accessExpiresAt?: string) =>
    send<User>("POST", `/v1/admin/users/${encodeURIComponent(id)}/approve`, {
      role,
      access_expires_at: accessExpiresAt,
    }),
  rejectUser: (id: string) =>
    send<{ rejected: string }>("DELETE", `/v1/admin/users/${encodeURIComponent(id)}`),
  resetUserPassword: (id: string) =>
    send<{ password: string }>(
      "POST",
      `/v1/admin/users/${encodeURIComponent(id)}/reset-password`,
    ),
  /** API tokens, for reading the warehouse from a script without a browser. */
  apiTokens: () => request<ApiToken[]>("/v1/auth/tokens"),
  createApiToken: (name: string, expiresInDays: number) =>
    send<CreatedApiToken>("POST", "/v1/auth/tokens", {
      name,
      expires_in_days: expiresInDays,
    }),
  revokeApiToken: (id: string) =>
    send<{ revoked: string }>("DELETE", `/v1/auth/tokens/${encodeURIComponent(id)}`),
  /**
   * Ask for one source to be ingested now.
   *
   * Answers as soon as the process is running — an ingestion takes minutes —
   * so the job that comes back is a handle to poll, not a result.
   */
  runSource: (
    sourceId: string,
    params?: { indicator?: string; dry_run?: boolean; limit?: number },
  ) =>
    post<Job>(`/v1/sources/${encodeURIComponent(sourceId)}/run`, {
      indicator: params?.indicator,
      dry_run: params?.dry_run ? "true" : undefined,
      limit: params?.limit,
    }),
  job: (id: string) => request<Job>(`/v1/jobs/${encodeURIComponent(id)}`),

  /**
   * The shelf: folders of records and the queries worth re-running.
   *
   * The only part of this API that answers from something a person wrote
   * rather than from what a pipeline landed. Absent on a deployment started
   * without `COLLECTIONS_DB`, which answers 404 here and is why every call
   * goes through the capability check first.
   */
  collections: () => request<ShelfCollection[]>("/v1/collections"),
  collection: (id: string) =>
    request<ShelfCollection>(`/v1/collections/${encodeURIComponent(id)}`),
  createCollection: (body: {
    id?: string;
    name: string;
    description?: string;
    items?: { kind: string; id: string; label: string; note?: string }[];
  }) => send<ShelfCollection>("POST", "/v1/collections", body),
  updateCollection: (
    id: string,
    body: { name?: string; description?: string; api?: boolean },
  ) =>
    send<ShelfCollection>("PATCH", `/v1/collections/${encodeURIComponent(id)}`, body),
  deleteCollection: (id: string) =>
    send<{ deleted: string }>("DELETE", `/v1/collections/${encodeURIComponent(id)}`),
  addCollectionItems: (
    id: string,
    items: { kind: string; id: string; label: string; note?: string }[],
  ) =>
    send<ShelfCollection>("POST", `/v1/collections/${encodeURIComponent(id)}/items`, {
      items,
    }),
  removeCollectionItem: (id: string, kind: string, ref: string) =>
    send<ShelfCollection>(
      "DELETE",
      // A commodity is identified by the name its source printed — spaces and
      // all — so the reference is escaped rather than interpolated raw.
      `/v1/collections/${encodeURIComponent(id)}/items/${encodeURIComponent(
        kind,
      )}/${encodeURIComponent(ref)}`,
    ),

  /** Make a folder from before there were owners the caller's, and private. */
  claimCollection: (id: string) =>
    send<ShelfCollection>("POST", `/v1/collections/${encodeURIComponent(id)}/claim`),
  /** Let an account in, by the address it signs in with. Owner only. */
  addCollectionMember: (id: string, email: string) =>
    send<ShelfCollection>("POST", `/v1/collections/${encodeURIComponent(id)}/members`, {
      email,
    }),
  /** The owner removing someone, or a member leaving. */
  removeCollectionMember: (id: string, user: string) =>
    send<ShelfCollection | { left: string }>(
      "DELETE",
      `/v1/collections/${encodeURIComponent(id)}/members/${encodeURIComponent(user)}`,
    ),

  savedQueries: () => request<ShelfQuery[]>("/v1/queries"),
  saveQuery: (body: {
    id?: string;
    name: string;
    kind: string;
    path: string;
    search: Record<string, unknown>;
    summary?: string;
  }) => send<ShelfQuery>("POST", "/v1/queries", body),
  renameQuery: (id: string, name: string) =>
    send<ShelfQuery>("PATCH", `/v1/queries/${encodeURIComponent(id)}`, { name }),
  deleteSavedQuery: (id: string) =>
    send<{ deleted: string }>("DELETE", `/v1/queries/${encodeURIComponent(id)}`),
  /** Merge a whole exported shelf in; ids already there are left alone. */
  importShelf: (shelf: { collections: unknown[]; queries: unknown[] }) =>
    send<{ collections: number; queries: number; skipped: number }>(
      "POST",
      "/v1/collections/import",
      shelf,
    ),
  /**
   * Jobs this API process has started, newest first. Narrowed to one source,
   * it is how a page reopened mid-run finds the run it is already waiting on.
   */
  jobs: (params?: { source?: string }) => request<Job[]>("/v1/jobs", params),
};
