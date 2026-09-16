/**
 * The serving layer's HTTP surface.
 *
 * Every response arrives in the envelope from program.md §53 — `data`, plus
 * `meta` or `error` — so this unwraps it once rather than at every call site.
 */

const BASE_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8080";

export type Meta = {
  total?: number;
  limit?: number;
  offset?: number;
  has_more: boolean;
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

  const response = await fetch(url, { headers: { Accept: "application/json" } });
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
  source_id: string;
  source_url?: string;
};

export type Indicator = {
  indicator_id: string;
  temporal_resolution: string;
  unit?: string;
  observations: number;
  geographies: number;
  period_start: string;
  period_end: string;
  /** Named rather than counted: "bps" says where a figure came from. */
  sources: string[];
  /** When the pipeline last wrote these rows, not how recent the figures are. */
  last_updated?: string;
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

export type Dataset = {
  slug: string;
  layer: string;
  name: string;
  rows: number;
};

export type ObservationQuery = {
  indicator?: string[];
  geo?: string;
  geo_type?: string[];
  q?: string;
  period_start?: string;
  period_end?: string;
  order?: string;
  limit?: number;
  offset?: number;
};

export const api = {
  datasets: () => request<Dataset[]>("/v1/datasets"),
  indicators: () => request<Indicator[]>("/v1/indicators"),
  indicator: (id: string) =>
    request<Indicator>(`/v1/indicators/${encodeURIComponent(id)}`),
  geography: (params?: {
    geo_type?: string[];
    q?: string;
    limit?: number;
    offset?: number;
  }) => request<Geography[]>("/v1/geography", params),
  observations: (params?: ObservationQuery) =>
    request<Observation[]>("/v1/observations", params),
};
