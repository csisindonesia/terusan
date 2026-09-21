/**
 * The tag vocabulary, counted across the catalogue.
 *
 * Nothing in the lake arrives tagged: the pipeline derives every tag from
 * facts already recorded (`pipelines/src/terusan_pipelines/tagging.py`), and
 * two kinds come out mixed. **Topics** say what a record is *about* —
 * `inflation`, `palm-oil`, `public-finance` — and are read off its title.
 * **Facets** say which record it is — its source, its publisher, its cadence,
 * its unit — and are the same word on every record that shares the fact.
 *
 * A Topics page that listed both without saying which is which would put
 * `monthly` and `bank-indonesia` beside `inflation`, and a reader browsing
 * subjects would be reading a list of filters. So the two are told apart here.
 *
 * They are told apart from the data rather than from a copy of the pipeline's
 * keyword list: a tag that matches a source id, a publisher, a frequency, a
 * unit or a dataset slug *already in the catalogue* is a facet, and the rest
 * are topics. A keyword list copied into TypeScript would drift from the
 * Python the first time a topic is added there; the vocabulary the records
 * themselves carry cannot.
 *
 * The one class this misreads is a place read off a geography — `jawa-barat`
 * lands under topics, because nothing in the catalogue response says it is a
 * province. A place is a subject as much as a facet, so it reads acceptably.
 */

import type { Dataset, Indicator, Source } from "~/lib/api";

export type TagKind = "topic" | "facet";

/** Tags that say what kind of record carries them, not what it is about. */
const STRUCTURAL = ["indicator", "dataset", "source-registry"];

/** What the pipeline pads a record with when it has nothing else to say. */
const FALLBACK = [
  "time-series",
  "statistics",
  "observations",
  "research-data",
  "catalogued",
];

/**
 * One tag as the pipeline stores it: lowercase, ASCII, hyphenated.
 *
 * Mirrors `normalize_tag` there, because the comparison this file makes is
 * between a stored tag and a column value that has not been through it yet.
 */
export function normalizeTag(value?: string | null): string | undefined {
  if (!value) return undefined;
  const ascii = String(value)
    .normalize("NFKD")
    // Combining marks first, then anything still outside ASCII: `é` becomes
    // `e` rather than disappearing with its accent.
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^\x20-\x7e]/g, "")
    .toLowerCase();
  const cleaned = ascii.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  if (!cleaned || ["none", "null", "unknown", "na", "n-a"].includes(cleaned)) {
    return undefined;
  }
  return cleaned.slice(0, 48).replace(/-+$/, "") || undefined;
}

/** A publisher by the part of its name that identifies it — "Bank Indonesia". */
function publisherTag(value?: string | null): string | undefined {
  if (!value) return undefined;
  const head = String(value).split(/[—–\-|:]/)[0];
  return normalizeTag(head) ?? normalizeTag(value);
}

/** A unit's first clause: "Millions of US Dollars" out of the full sentence. */
function unitTag(value?: string | null): string | undefined {
  if (!value) return undefined;
  return normalizeTag(String(value).split(/[,(]/)[0]);
}

/** A place by the name a reader would type rather than its code. */
function countryTag(value?: string | null): string | undefined {
  const tag = normalizeTag(value);
  if (!tag) return undefined;
  const known: Record<string, string> = {
    id: "indonesia",
    idn: "indonesia",
    wld: "global",
    world: "global",
  };
  return known[tag] ?? tag;
}

export type Catalogue = {
  datasets: Dataset[];
  indicators: Indicator[];
  /** Optional: without it, source categories and countries read as topics. */
  sources?: Source[];
};

/**
 * Every tag the catalogue can account for as a fact about a record rather
 * than a subject.
 */
export function facetVocabulary({
  datasets,
  indicators,
  sources,
}: Catalogue): Set<string> {
  const facets = new Set<string>([...STRUCTURAL, ...FALLBACK]);
  const add = (value?: string | null) => {
    const tag = normalizeTag(value);
    if (tag) facets.add(tag);
  };

  for (const source of sources ?? []) {
    add(source.source_id);
    add(source.category);
    add(source.source_type);
    add(source.collection_method);
    add(source.update_frequency);
    facets.add(countryTag(source.country) ?? "");
    const publisher = publisherTag(source.organization) ?? publisherTag(source.name);
    if (publisher) facets.add(publisher);
  }

  for (const dataset of datasets) {
    add(dataset.dataset_id);
    add(dataset.slug);
    add(dataset.source_id);
    const publisher = publisherTag(dataset.organization);
    if (publisher) facets.add(publisher);
  }

  for (const indicator of indicators) {
    add(indicator.temporal_resolution);
    const unit = unitTag(indicator.unit);
    if (unit) facets.add(unit);
    const publisher = publisherTag(indicator.publisher);
    if (publisher) facets.add(publisher);
    for (const id of indicator.sources) add(id);
  }

  facets.delete("");
  return facets;
}

export function tagKind(tag: string, facets: Set<string>): TagKind {
  return facets.has(tag) ? "facet" : "topic";
}

/** One row of the topics table. */
export type TopicSummary = {
  tag: string;
  kind: TagKind;
  datasets: number;
  /** Series carrying the tag, or belonging to a dataset that carries it. */
  indicators: number;
  observations: number;
  publishers: string[];
  periodStart?: string;
  periodEnd?: string;
};

/**
 * The records a topic covers.
 *
 * A series inherits its dataset's topic. The pipeline only hangs a tag on a
 * dataset when every series inside it agrees, or when the fact is the
 * dataset's own, so the inheritance cannot widen a topic to a series that
 * contradicts it — and without it a dataset tagged `fiscal` would show under
 * the topic with none of its figures beneath.
 */
export function topicRecords(
  tag: string,
  datasets: Dataset[],
  indicators: Indicator[],
): { datasets: Dataset[]; indicators: Indicator[] } {
  const tagged = datasets.filter((dataset) => (dataset.tags ?? []).includes(tag));
  const ids = new Set(tagged.map((dataset) => dataset.dataset_id));
  return {
    datasets: tagged,
    indicators: indicators.filter(
      (indicator) =>
        (indicator.tags ?? []).includes(tag) ||
        (indicator.dataset_id ? ids.has(indicator.dataset_id) : false),
    ),
  };
}

/** Every tag in the catalogue, with what rests on it. */
export function collectTopics(catalogue: Catalogue): TopicSummary[] {
  const { datasets, indicators } = catalogue;
  const facets = facetVocabulary(catalogue);

  const byDataset = new Map<string, Indicator[]>();
  for (const indicator of indicators) {
    if (!indicator.dataset_id) continue;
    const group = byDataset.get(indicator.dataset_id);
    if (group) group.push(indicator);
    else byDataset.set(indicator.dataset_id, [indicator]);
  }

  type Bucket = {
    datasets: Set<string>;
    /** Held as a set so a series counted through its dataset counts once. */
    series: Set<string>;
    observations: number;
    publishers: Set<string>;
    periodStart?: string;
    periodEnd?: string;
  };
  const buckets = new Map<string, Bucket>();

  function bucket(tag: string): Bucket {
    let found = buckets.get(tag);
    if (!found) {
      found = {
        datasets: new Set(),
        series: new Set(),
        observations: 0,
        publishers: new Set(),
      };
      buckets.set(tag, found);
    }
    return found;
  }

  function cover(target: Bucket, start?: string, end?: string) {
    // Lexicographic, which is what ISO dates are ordered by anyway.
    if (start && (!target.periodStart || start < target.periodStart)) {
      target.periodStart = start;
    }
    if (end && (!target.periodEnd || end > target.periodEnd)) target.periodEnd = end;
  }

  function addSeries(target: Bucket, indicator: Indicator) {
    if (target.series.has(indicator.indicator_id)) return;
    target.series.add(indicator.indicator_id);
    target.observations += indicator.observations;
    const publisher = indicator.publisher ?? indicator.sources[0];
    if (publisher) target.publishers.add(publisher);
    cover(target, indicator.period_start, indicator.period_end);
  }

  for (const indicator of indicators) {
    for (const tag of indicator.tags ?? []) addSeries(bucket(tag), indicator);
  }

  for (const dataset of datasets) {
    const members = byDataset.get(dataset.dataset_id) ?? [];
    for (const tag of dataset.tags ?? []) {
      const target = bucket(tag);
      target.datasets.add(dataset.dataset_id);
      if (dataset.organization) target.publishers.add(dataset.organization);

      for (const member of members) addSeries(target, member);
      // A collection whose series are not in the catalogue response still has
      // figures, and counting none would understate the topic.
      if (!members.length) {
        target.observations += dataset.observations;
        cover(target, dataset.period_start, dataset.period_end);
      }
    }
  }

  return [...buckets.entries()]
    .map(([tag, entry]) => ({
      tag,
      kind: tagKind(tag, facets),
      datasets: entry.datasets.size,
      indicators: entry.series.size,
      observations: entry.observations,
      publishers: [...entry.publishers].sort(),
      periodStart: entry.periodStart,
      periodEnd: entry.periodEnd,
    }))
    .sort((a, b) => b.indicators - a.indicators || a.tag.localeCompare(b.tag));
}

/**
 * Topics that keep company with this one, most shared series first.
 *
 * The way out of a topic that turned out to be the wrong one: `palm-oil` next
 * to `commodities` and `agriculture` says where else the same figures live.
 *
 * `exclude` is how a topic page keeps facets out of the list. Nearly every
 * record carries `statistics` and `annual`, so without it the neighbours of
 * every topic are the same five facets and the list says nothing.
 */
export function relatedTags(
  tag: string,
  records: { datasets: Dataset[]; indicators: Indicator[] },
  options: { exclude?: Set<string>; limit?: number } = {},
): string[] {
  const { exclude, limit = 12 } = options;
  const counts = new Map<string, number>();
  const bump = (tags: string[] | undefined) => {
    for (const other of tags ?? []) {
      if (other === tag || exclude?.has(other)) continue;
      counts.set(other, (counts.get(other) ?? 0) + 1);
    }
  };
  for (const indicator of records.indicators) bump(indicator.tags);
  for (const dataset of records.datasets) bump(dataset.tags);

  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([value]) => value);
}
