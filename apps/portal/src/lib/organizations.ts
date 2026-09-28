import type { Dataset, Source } from "~/lib/api";

/**
 * An organization the figures come from: Bank Indonesia, BPS, a ministry.
 *
 * Not a table of its own. The source registry names the organization behind
 * each source, and a dataset carries its source's organization, so the list
 * is those two grouped by name. One organization often runs several sources —
 * BPS publishes through its web API and through its tables — and a reader
 * asking "what do we hold from BPS" means all of them.
 *
 * A source with no organization recorded stands for itself, under its own
 * name, rather than being dropped: its figures still came from somewhere.
 */
export type Organization = {
  name: string;
  sources: Source[];
  datasets: Dataset[];
  /** Series a reader sees: a price's four are one, as on the datasets page. */
  series: number;
  observations: number;
  period_start?: string;
  period_end?: string;
  last_updated?: string;
  countries: string[];
  licenses: string[];
};

export function organizationOf(source: Pick<Source, "organization" | "name">): string {
  return source.organization?.trim() || source.name;
}

export function organizationsFrom(sources: Source[], datasets: Dataset[]): Organization[] {
  const byName = new Map<string, Organization>();
  const sourceOrg = new Map<string, string>();

  const entry = (name: string): Organization => {
    let organization = byName.get(name);
    if (!organization) {
      organization = {
        name,
        sources: [],
        datasets: [],
        series: 0,
        observations: 0,
        countries: [],
        licenses: [],
      };
      byName.set(name, organization);
    }
    return organization;
  };

  for (const source of sources) {
    const name = organizationOf(source);
    sourceOrg.set(source.source_id, name);
    const organization = entry(name);
    organization.sources.push(source);
    if (source.country && !organization.countries.includes(source.country)) {
      organization.countries.push(source.country);
    }
    if (source.license && !organization.licenses.includes(source.license)) {
      organization.licenses.push(source.license);
    }
  }

  for (const dataset of datasets) {
    // The registry's word first, so a dataset lands beside the sources it was
    // collected by even where its own organization field is missing.
    const name =
      sourceOrg.get(dataset.source_id) ??
      (dataset.organization?.trim() || dataset.source_name || dataset.source_id);
    const organization = entry(name);
    organization.datasets.push(dataset);
    organization.series += dataset.series ?? dataset.indicators.length;
    organization.observations += dataset.observations;
    if (dataset.period_start && (!organization.period_start || dataset.period_start < organization.period_start)) {
      organization.period_start = dataset.period_start;
    }
    if (dataset.period_end && (!organization.period_end || dataset.period_end > organization.period_end)) {
      organization.period_end = dataset.period_end;
    }
    if (dataset.last_updated && (!organization.last_updated || dataset.last_updated > organization.last_updated)) {
      organization.last_updated = dataset.last_updated;
    }
  }

  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
}
