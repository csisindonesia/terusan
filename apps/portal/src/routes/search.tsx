import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  IconBox,
  IconChartBar,
  IconDatabase,
  IconFileText,
  IconGavel,
} from "@tabler/icons-react";
import { useMemo } from "react";
import { z } from "zod";

import { CollectButton } from "~/components/collect-button";
import { ChoiceList, FilterChip, summarise } from "~/components/filter-chip";
import { PageHeader } from "~/components/page-header";
import { SaveQueryButton } from "~/components/save-query-button";
import { SearchInput } from "~/components/search-input";
import { StickyHeader } from "~/components/sticky-header";
import { TableToolbar } from "~/components/table-toolbar";
import { Badge } from "~/components/ui/badge";
import { Skeleton } from "~/components/ui/skeleton";
import { api } from "~/lib/api";
import { formatCount, formatDate } from "~/lib/format";
import { datasetLabel, indicatorLabel } from "~/lib/labels";
import { toggle } from "~/lib/multi";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";
import { type NewItem } from "~/lib/workspace";

/**
 * Search as a page, not only as a palette.
 *
 * The ⌘K palette answers "take me there": six rows a kind, keyboard-first, gone
 * as soon as something is picked. A search worth keeping is a different act —
 * a reader comparing what four catalogues hold on "palm oil" needs the results
 * to stay on screen, to be filed into a collection, and to be a link they can
 * send. That is a page, and the palette links here rather than growing into
 * one.
 *
 * The same split the palette makes applies: indicators and datasets are small
 * enough to hold whole and filter in the browser, and the other three are
 * asked of the API under the typed query.
 */

const KINDS = [
  { value: "indicator", label: "Indicators", hint: "Series" },
  { value: "dataset", label: "Datasets", hint: "Collections" },
  { value: "document", label: "Documents", hint: "Sources" },
  { value: "regulation", label: "Regulations", hint: "Legal" },
  { value: "commodity", label: "Commodities", hint: "Goods" },
];

const searchSchema = z.object({
  q: textParam,
  /** Which catalogues to search. Absent means all of them. */
  kind: listParam,
});

export const Route = createFileRoute("/search")({
  validateSearch: searchSchema,
  component: Search,
});

/** Below this a query matches most of the catalogue, and the page is noise. */
const MIN_QUERY = 2;

/** How many rows a section shows before handing the reader to its own page. */
const PER_SECTION = 8;

function matches(tokens: string[], ...fields: (string | undefined)[]): boolean {
  if (!tokens.length) return false;
  const haystack = fields.filter(Boolean).join(" ").toLowerCase();
  return tokens.every((token) => haystack.includes(token));
}

function Search() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });

  const q = asText(search.q)?.trim() ?? "";
  const kinds = asTextList(search.kind);
  const wants = (kind: string) => !kinds.length || kinds.includes(kind);
  const searching = q.length >= MIN_QUERY;

  const tokens = useMemo(() => q.toLowerCase().split(/\s+/).filter(Boolean), [q]);

  // Keyed as the catalogue pages key them, so a visit from /indicators has
  // already paid for these.
  const indicators = useQuery({
    queryKey: ["indicators"],
    queryFn: () => api.indicators(),
    enabled: searching && wants("indicator"),
  });
  const datasets = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.datasets(),
    enabled: searching && wants("dataset"),
  });
  const documents = useQuery({
    queryKey: ["search-documents", q],
    queryFn: () => api.documents({ q, limit: PER_SECTION }),
    enabled: searching && wants("document"),
  });
  const regulations = useQuery({
    queryKey: ["search-regulations", q],
    queryFn: () => api.regulations({ q, limit: PER_SECTION }),
    enabled: searching && wants("regulation"),
  });
  const commodities = useQuery({
    queryKey: ["search-commodities", q],
    queryFn: () => api.commodities({ q, limit: PER_SECTION }),
    enabled: searching && wants("commodity"),
  });

  const indicatorHits = useMemo(() => {
    if (!searching) return [];
    return (indicators.data?.data ?? []).filter((indicator) =>
      matches(
        tokens,
        indicatorLabel(indicator),
        indicator.indicator_id,
        indicator.slug,
        indicator.code,
        indicator.description,
        indicator.publisher,
        indicator.tags.join(" "),
      ),
    );
  }, [indicators.data, searching, tokens]);

  const datasetHits = useMemo(() => {
    if (!searching) return [];
    return (datasets.data?.data ?? []).filter((dataset) =>
      matches(
        tokens,
        datasetLabel(dataset),
        dataset.dataset_id,
        dataset.slug,
        dataset.description,
        dataset.organization,
        dataset.tags.join(" "),
      ),
    );
  }, [datasets.data, searching, tokens]);

  const documentHits = documents.data?.data ?? [];
  const regulationHits = regulations.data?.data ?? [];
  const commodityHits = commodities.data?.data ?? [];

  // Counts are what the catalogue holds, not what is on screen: the two
  // client-side sections know their own total, and the three server-side ones
  // are told it in `meta`.
  const counts = {
    indicator: indicatorHits.length,
    dataset: datasetHits.length,
    document: documents.data?.meta?.total ?? documentHits.length,
    regulation: regulations.data?.meta?.total ?? regulationHits.length,
    commodity: commodities.data?.meta?.total ?? commodityHits.length,
  };

  const total = Object.entries(counts)
    .filter(([kind]) => wants(kind))
    .reduce((sum, [, count]) => sum + count, 0);

  const loading =
    searching &&
    (indicators.isLoading ||
      datasets.isLoading ||
      documents.isLoading ||
      regulations.isLoading ||
      commodities.isLoading);

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Search"
            count={searching ? total : undefined}
            isLoading={loading}
            description="One query across the series, the collections, the documents they were read out of, the regulations and the commodities."
            actions={
              <SaveQueryButton
                kind="search"
                path="/search"
                search={{ q: q || undefined, kind: search.kind }}
                suggestion={q}
                disabled={!searching}
                label="Save search"
              />
            }
          />
        }
        filters={
          <TableToolbar
            filters={
              <FilterChip
                label="In"
                value={summarise(
                  kinds,
                  (value) => KINDS.find((kind) => kind.value === value)?.label ?? value,
                )}
                onClear={() =>
                  navigate({ search: (prev) => ({ ...prev, kind: undefined }) })
                }
              >
                <ChoiceList
                  options={KINDS}
                  selected={kinds}
                  onToggle={(value) =>
                    navigate({
                      search: (prev) => ({ ...prev, kind: toggle(kinds, value) }),
                    })
                  }
                  onClear={() =>
                    navigate({ search: (prev) => ({ ...prev, kind: undefined }) })
                  }
                />
              </FilterChip>
            }
            search={
              <SearchInput
                value={q}
                placeholder="Search the whole catalogue"
                onSearch={(next) =>
                  navigate({ search: (prev) => ({ ...prev, q: next }) })
                }
              />
            }
          />
        }
      />

      {!searching ? (
        <p className="rounded-lg border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">
          {q ? "Type at least two characters." : "Type to search."} Results can be filed
          into a{" "}
          <Link to="/collections" className="underline underline-offset-4">
            collection
          </Link>{" "}
          or kept as a{" "}
          <Link to="/saved-queries" className="underline underline-offset-4">
            saved query
          </Link>
          .
        </p>
      ) : null}

      {searching && !loading && total === 0 ? (
        <p className="rounded-lg border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">
          Nothing in the catalogue matches “{q}”.
        </p>
      ) : null}

      {searching && wants("indicator") ? (
        <Section
          title="Indicators"
          icon={IconChartBar}
          count={counts.indicator}
          isLoading={indicators.isLoading}
          more={
            counts.indicator > PER_SECTION ? (
              <Link
                to="/indicators"
                search={{ q }}
                className="underline underline-offset-4"
              >
                All {formatCount(counts.indicator)} indicators
              </Link>
            ) : null
          }
        >
          {indicatorHits.slice(0, PER_SECTION).map((indicator) => (
            <Row
              key={indicator.indicator_id}
              title={indicatorLabel(indicator)}
              subtitle={indicator.code ?? indicator.indicator_id}
              meta={`${formatCount(indicator.observations)} figures · ${
                indicator.period_start
              }–${indicator.period_end}`}
              to={
                <Link
                  to="/indicators/$indicatorId"
                  params={{ indicatorId: indicator.indicator_id }}
                  className="underline-offset-4 hover:underline"
                >
                  {indicatorLabel(indicator)}
                </Link>
              }
              item={{
                kind: "indicator",
                id: indicator.indicator_id,
                label: indicatorLabel(indicator),
              }}
            />
          ))}
        </Section>
      ) : null}

      {searching && wants("dataset") ? (
        <Section
          title="Datasets"
          icon={IconDatabase}
          count={counts.dataset}
          isLoading={datasets.isLoading}
          more={
            counts.dataset > PER_SECTION ? (
              <Link
                to="/datasets"
                search={{ q }}
                className="underline underline-offset-4"
              >
                All {formatCount(counts.dataset)} datasets
              </Link>
            ) : null
          }
        >
          {datasetHits.slice(0, PER_SECTION).map((dataset) => (
            <Row
              key={dataset.dataset_id}
              title={datasetLabel(dataset)}
              subtitle={dataset.organization ?? dataset.source_id}
              meta={`${formatCount(dataset.indicators.length)} series · ${formatCount(
                dataset.observations,
              )} figures`}
              to={
                <Link
                  to="/datasets/$datasetId"
                  params={{ datasetId: dataset.dataset_id }}
                  className="underline-offset-4 hover:underline"
                >
                  {datasetLabel(dataset)}
                </Link>
              }
              item={{
                kind: "dataset",
                id: dataset.dataset_id,
                label: datasetLabel(dataset),
              }}
            />
          ))}
        </Section>
      ) : null}

      {searching && wants("document") ? (
        <Section
          title="Documents"
          icon={IconFileText}
          count={counts.document}
          isLoading={documents.isLoading}
          more={
            counts.document > PER_SECTION ? (
              <Link
                to="/documents"
                search={{ q }}
                className="underline underline-offset-4"
              >
                All {formatCount(counts.document)} documents
              </Link>
            ) : null
          }
        >
          {documentHits.map((document) => (
            <Row
              key={document.document_id}
              title={document.title}
              subtitle={document.publisher ?? document.source_id}
              meta={[
                document.document_type,
                document.published_at ? formatDate(document.published_at) : undefined,
                document.observation_count
                  ? `${formatCount(document.observation_count)} figures`
                  : undefined,
              ]
                .filter(Boolean)
                .join(" · ")}
              to={
                <Link
                  to="/documents/$documentId"
                  params={{ documentId: document.document_id }}
                  className="underline-offset-4 hover:underline"
                >
                  {document.title}
                </Link>
              }
              item={{
                kind: "document",
                id: document.document_id,
                label: document.title,
              }}
            />
          ))}
        </Section>
      ) : null}

      {searching && wants("regulation") ? (
        <Section
          title="Regulations"
          icon={IconGavel}
          count={counts.regulation}
          isLoading={regulations.isLoading}
          more={
            counts.regulation > PER_SECTION ? (
              <Link
                to="/regulations"
                search={{ q }}
                className="underline underline-offset-4"
              >
                All {formatCount(counts.regulation)} regulations
              </Link>
            ) : null
          }
        >
          {regulationHits.map((regulation) => (
            <Row
              key={regulation.key}
              title={regulation.title}
              subtitle={[regulation.region_name, regulation.instrument]
                .filter(Boolean)
                .join(" · ")}
              meta={[regulation.number, regulation.year].filter(Boolean).join("/")}
              to={
                <Link
                  to="/regulations/$key"
                  params={{ key: regulation.key }}
                  className="underline-offset-4 hover:underline"
                >
                  {regulation.title}
                </Link>
              }
              item={{
                kind: "regulation",
                id: regulation.key,
                label: regulation.title,
              }}
            />
          ))}
        </Section>
      ) : null}

      {searching && wants("commodity") ? (
        <Section
          title="Commodities"
          icon={IconBox}
          count={counts.commodity}
          isLoading={commodities.isLoading}
          more={
            counts.commodity > PER_SECTION ? (
              <Link
                to="/commodities"
                search={{ q }}
                className="underline underline-offset-4"
              >
                All {formatCount(counts.commodity)} commodities
              </Link>
            ) : null
          }
        >
          {commodityHits.map((commodity) => (
            <Row
              key={commodity.name}
              title={commodity.name}
              subtitle={commodity.category ?? "Uncategorised"}
              meta={`${formatCount(commodity.observations)} figures · ${formatCount(
                commodity.indicators,
              )} series`}
              to={
                // A commodity has no page of its own: what a reader wants of
                // one is its figures, so the row opens the explorer already
                // narrowed to it.
                <Link
                  to="/observations"
                  search={{ commodity: [commodity.name] }}
                  className="underline-offset-4 hover:underline"
                >
                  {commodity.name}
                </Link>
              }
              item={{ kind: "commodity", id: commodity.name, label: commodity.name }}
            />
          ))}
        </Section>
      ) : null}
    </div>
  );
}

function Section({
  title,
  icon: Icon,
  count,
  isLoading,
  more,
  children,
}: {
  title: string;
  icon: typeof IconChartBar;
  count: number;
  isLoading: boolean;
  more?: React.ReactNode;
  children: React.ReactNode;
}) {
  // A section with nothing in it is left out rather than printed empty: five
  // "no results" panels bury the one catalogue that answered.
  if (!isLoading && count === 0) return null;

  return (
    <section className="space-y-2">
      <div className="flex items-center gap-2">
        <Icon className="size-4 text-muted-foreground" />
        <h2 className="font-heading text-sm font-medium">{title}</h2>
        <Badge variant="secondary" className="tabular-nums">
          {isLoading ? "…" : formatCount(count)}
        </Badge>
        {more ? (
          <span className="ml-auto text-xs text-muted-foreground">{more}</span>
        ) : null}
      </div>

      <div className="divide-y rounded-lg border">
        {isLoading
          ? Array.from({ length: 3 }, (_, index) => (
              <div key={index} className="px-3 py-2.5">
                <Skeleton className="h-4 w-1/3" />
              </div>
            ))
          : children}
      </div>
    </section>
  );
}

function Row({
  title,
  subtitle,
  meta,
  to,
  item,
}: {
  title: string;
  subtitle?: string;
  meta?: string;
  /** The linked title. Given as a node because each kind links differently. */
  to: React.ReactNode;
  item: NewItem;
}) {
  return (
    <div className="flex items-center gap-3 px-3 py-2.5">
      <div className="min-w-0 flex-1 leading-tight">
        {/* Truncated rather than wrapped: a document title runs to ninety
            characters, and a result list whose rows are three lines each shows
            four results a screen. */}
        <div className="truncate font-medium" title={title}>
          {to}
        </div>
        {subtitle ? (
          <div className="truncate text-xs text-muted-foreground">{subtitle}</div>
        ) : null}
      </div>
      {meta ? (
        <div className="hidden shrink-0 text-xs text-muted-foreground sm:block">
          {meta}
        </div>
      ) : null}
      <CollectButton
        items={[item]}
        size="icon-sm"
        variant="ghost"
        label={`Collect ${title}`}
      />
    </div>
  );
}
