import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import {
  IconArrowLeft,
  IconCopy,
  IconDownload,
  IconExternalLink,
  IconPhoto,
} from "@tabler/icons-react";
import { useState } from "react";
import { z } from "zod";

import { ClampedText } from "~/components/clamped-text";
import { DataTable, StackedCell } from "~/components/data-table";
import { ChoiceList, FilterChip, summarise } from "~/components/filter-chip";
import { PageHeader } from "~/components/page-header";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { SearchInput } from "~/components/search-input";
import { BELOW_STICKY_HEADER, StickyHeader } from "~/components/sticky-header";
import { TablePagination } from "~/components/table-pagination";
import { TableToolbar } from "~/components/table-toolbar";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import {
  api,
  newsScreenshotUrl,
  type Facet,
  type NewsArticle,
  type NewsFacets,
  type NewsTally,
} from "~/lib/api";
import { describeSchedule } from "~/lib/cron";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount, formatDate, formatRelative } from "~/lib/format";
import { codedLabel, placeLabel } from "~/lib/labels";
import { toggle } from "~/lib/multi";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";

const PAGE_SIZE = 50;

/** Days of crawl log per page. A month at a glance. */
const LOG_PAGE_SIZE = 31;

const searchSchema = z.object({
  q: textParam,
  page: z.number().int().min(0).optional(),
  // Which half of the paper's record is being read: the articles that were
  // kept, or the log of everything that was read to find them. In the URL,
  // because "this paper has been read every day and yielded nothing" is the
  // answer to a question somebody asked, and it should be linkable.
  tab: z.enum(["articles", "log"]).optional(),
  logPage: z.number().int().min(0).optional(),
  // What the classifier made of the article, as multiple choices. A reader
  // narrowing to the brawls means two or three forms, not one, and the
  // narrowing is done by the API so it applies to the corpus rather than to
  // whichever fifty rows happen to be on screen.
  form: listParam,
  issue_type: listParam,
  weapon: listParam,
  escalation: listParam,
});

export const Route = createFileRoute("/news-outlets/$host")({
  validateSearch: searchSchema,
  component: NewsOutletPage,
});

const columns: ColumnDef<NewsArticle>[] = [
  {
    accessorKey: "title",
    header: "Article",
    cell: ({ row }) => {
      const { document_id, title, lead } = row.original;
      return (
        // To the article's own page, not to the paper: this row is a thing the
        // warehouse holds, and what it holds — the coding, the provenance, the
        // picture of the page — is what a reader clicking a headline here is
        // after. The paper's own copy is one of the row actions.
        //
        // One line, clamped, with the whole headline on hover. An Indonesian
        // news headline runs to twenty words and a wrapped one sets the row
        // height for every other row in the table; two clamped lines still
        // left the column ragged. The tooltip is where the rest of it lives,
        // which is the same bargain the documents and indicators tables make.
        <Link
          to="/news-articles/$articleId"
          params={{ articleId: document_id }}
          className="block w-[18rem] max-w-full hover:underline"
        >
          <StackedCell
            primary={<ClampedText className="font-medium">{title}</ClampedText>}
            // The lead only. The archive holds the whole page so a coding
            // stays reproducible, but the words are the newspaper's.
            secondary={lead ? <ClampedText>{lead}</ClampedText> : undefined}
          />
        </Link>
      );
    },
  },
  {
    accessorKey: "published_at",
    header: "Published",
    cell: ({ row }) => (
      <span className="tabular-nums text-muted-foreground">
        {formatDate(row.original.published_at)}
      </span>
    ),
  },
  {
    id: "place",
    header: "Where",
    enableSorting: false,
    cell: ({ row }) => {
      const coding = row.original.coding;
      // The place the article says the incident happened, which is not the
      // outlet's own province: a paper reports on its neighbours.
      if (!coding?.accepted || !(coding.district_city || coding.province)) {
        return <span className="text-muted-foreground">—</span>;
      }
      return (
        <StackedCell
          primary={coding.district_city ?? coding.province}
          secondary={coding.province}
        />
      );
    },
  },
  {
    id: "form",
    header: "Form",
    enableSorting: false,
    cell: ({ row }) => {
      const coding = row.original.coding;
      if (!coding?.accepted || !coding.violence_form) {
        return <span className="text-muted-foreground">—</span>;
      }
      return (
        <StackedCell
          // Sentence case, not the capitals the value is stored in: the coding
          // form shouts because coding forms have since they were printed on
          // paper, and a column of shouting leaves no emphasis for the cells
          // that carry a figure.
          primary={
            <ClampedText className="w-48">
              {codedLabel(coding.violence_form)}
            </ClampedText>
          }
          secondary={coding.issue_type ? codedLabel(coding.issue_type) : undefined}
        />
      );
    },
  },
  {
    id: "harm",
    header: "Killed / hurt",
    meta: { align: "right" },
    enableSorting: false,
    cell: ({ row }) => {
      const coding = row.original.coding;
      // Absent rather than zero where the reporting did not say: an incident
      // nobody counted and one where nobody was hurt are different facts.
      if (!coding?.accepted) return <span className="text-muted-foreground">—</span>;
      const killed = coding.deaths === undefined ? "—" : formatCount(coding.deaths);
      const hurt = coding.injured === undefined ? "—" : formatCount(coding.injured);
      return (
        <span className="tabular-nums text-muted-foreground">{`${killed} / ${hurt}`}</span>
      );
    },
  },
  {
    id: "actions",
    header: "",
    enableSorting: false,
    meta: { align: "right" },
    cell: ({ row }) => (
      <RowActions
        actions={[
          {
            label: "Copy the article link",
            icon: IconCopy,
            onSelect: () => void copyToClipboard(row.original.url),
          },
          {
            label: "Open at the publisher",
            icon: IconExternalLink,
            onSelect: () => window.open(row.original.url, "_blank"),
          },
          ...(row.original.screenshot
            ? [
                {
                  label: "The page that day",
                  icon: IconPhoto,
                  onSelect: () =>
                    window.open(newsScreenshotUrl(row.original.document_id), "_blank"),
                },
              ]
            : []),
        ]}
      />
    ),
  },
];

const EXPORT_COLUMNS = [
  { key: "published_at" as const, header: "published_at" },
  { key: "outlet_host" as const, header: "outlet_host" },
  { key: "title" as const, header: "title" },
  { key: "url" as const, header: "url" },
  { key: "issues" as const, header: "issues" },
  { key: "matched_terms" as const, header: "matched_terms" },
];

/**
 * The crawl's log for one paper, a row per day.
 *
 * Four numbers that narrow: what discovery turned up, what was read in full,
 * what carried the dictionary's terms, and what a classifier agreed was about
 * the issue. Sixty read and two kept is the ordinary day, and the point of
 * showing it is that the two are only meaningful beside the sixty.
 *
 * The articles behind the first three numbers do not exist anywhere: a page
 * that is not about a monitored issue is read, counted and thrown away. This
 * table is the only record that it was read at all.
 */
const logColumns: ColumnDef<NewsTally>[] = [
  {
    accessorKey: "date",
    header: "Day",
    cell: ({ row }) => (
      <StackedCell
        primary={formatDate(row.original.date)}
        // The list is sharded across the working day, so a paper is normally
        // visited once. Two visits is a shard that was re-run, and it explains
        // a day whose numbers look doubled.
        secondary={
          row.original.runs > 1 ? `${formatCount(row.original.runs)} visits` : undefined
        }
      />
    ),
  },
  {
    accessorKey: "discovered",
    header: "Found",
    meta: { align: "right" },
    enableSorting: false,
    cell: ({ row }) => (
      <span className="tabular-nums text-muted-foreground">
        {formatCount(row.original.discovered)}
      </span>
    ),
  },
  {
    accessorKey: "scanned",
    header: "Read",
    meta: { align: "right" },
    enableSorting: false,
    cell: ({ row }) => (
      <span className="tabular-nums">{formatCount(row.original.scanned)}</span>
    ),
  },
  {
    accessorKey: "matched",
    header: "Matched",
    meta: { align: "right" },
    enableSorting: false,
    cell: ({ row }) => (
      <span className="tabular-nums text-muted-foreground">
        {formatCount(row.original.matched)}
      </span>
    ),
  },
  {
    accessorKey: "recorded",
    header: "Kept",
    meta: { align: "right" },
    enableSorting: false,
    cell: ({ row }) =>
      row.original.recorded ? (
        <Badge>{formatCount(row.original.recorded)}</Badge>
      ) : (
        // Zero is the normal answer and must not read as a failure: most days
        // a paper publishes nothing that is collective violence.
        <span className="tabular-nums text-muted-foreground">0</span>
      ),
  },
  {
    id: "share",
    header: "Kept of read",
    meta: { align: "right" },
    enableSorting: false,
    cell: ({ row }) => {
      const { scanned, recorded } = row.original;
      if (!scanned) return <span className="text-muted-foreground">—</span>;
      return (
        <span className="tabular-nums text-muted-foreground">
          {`${((recorded / scanned) * 100).toFixed(1)}%`}
        </span>
      );
    },
  },
];

/**
 * The coded questions a reader may narrow by, in the order the coding form
 * asks them: what happened, what it was about, what with, how far it went.
 *
 * Each is a multiple choice, because that is how the question is actually put
 * to a corpus — "the two kinds of armed attack", not "this one" — and the
 * narrowing is done by the API, so it applies to every article this paper has
 * yielded rather than to the fifty on screen.
 */
const CODED_FILTERS: {
  param: "form" | "issue_type" | "weapon" | "escalation";
  label: string;
  options: (facets?: NewsFacets) => Facet[];
}[] = [
  { param: "form", label: "Form", options: (f) => f?.forms ?? [] },
  { param: "issue_type", label: "Issue", options: (f) => f?.issues ?? [] },
  { param: "weapon", label: "Weapon", options: (f) => f?.weapons ?? [] },
  { param: "escalation", label: "Escalation", options: (f) => f?.escalations ?? [] },
];

const LOG_EXPORT_COLUMNS = [
  { key: "date" as const, header: "date" },
  { key: "discovered" as const, header: "discovered" },
  { key: "scanned" as const, header: "scanned" },
  { key: "matched" as const, header: "matched" },
  { key: "recorded" as const, header: "recorded" },
  { key: "runs" as const, header: "runs" },
];

function NewsOutletPage() {
  const { host } = Route.useParams();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [selected, setSelected] = useState<NewsArticle[]>([]);
  const page = search.page ?? 0;
  const logPage = search.logPage ?? 0;
  const tab = search.tab ?? "articles";

  const outlet = useQuery({
    queryKey: ["news-outlet", host],
    queryFn: () => api.newsOutlet(host),
  });

  // What the reader has narrowed to, and the one object both the list and its
  // facet counts are asked with: a facet counted under a different filter than
  // the list offers an option that returns nothing.
  const narrowing = {
    q: asText(search.q),
    form: asTextList(search.form),
    issue_type: asTextList(search.issue_type),
    weapon: asTextList(search.weapon),
    escalation: asTextList(search.escalation),
  };
  const narrowed =
    Boolean(narrowing.q) ||
    narrowing.form.length > 0 ||
    narrowing.issue_type.length > 0 ||
    narrowing.weapon.length > 0 ||
    narrowing.escalation.length > 0;

  const list = useQuery({
    queryKey: ["news-outlet-articles", host, narrowing, page],
    queryFn: () =>
      api.newsOutletArticles(host, {
        ...narrowing,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  });

  const facets = useQuery({
    queryKey: ["news-outlet-facets", host, narrowing],
    queryFn: () => api.newsArticleFacets({ outlet: host, ...narrowing }),
    // Only where there are chips to fill. The crawl log has no filters, and
    // counting the corpus to render nothing is a query nobody asked for.
    enabled: tab === "articles",
  });

  const log = useQuery({
    queryKey: ["news-outlet-tallies", host, logPage],
    queryFn: () =>
      api.newsOutletTallies(host, {
        limit: LOG_PAGE_SIZE,
        offset: logPage * LOG_PAGE_SIZE,
      }),
  });

  // The crawl's own registration: how often it runs, under what terms the
  // articles are held. One record for every paper, which is why it is read
  // from the source rather than stored per outlet.
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.sources() });
  const source = sources.data?.data?.find((row) => row.source_id === "news-monitoring");

  const paper = outlet.data?.data;
  const rows = list.data?.data ?? [];
  const total = list.data?.meta?.total ?? 0;
  const logRows = log.data?.data ?? [];
  const logTotal = log.data?.meta?.total ?? 0;

  function exportRows(chosen: NewsArticle[], suffix: string) {
    downloadCsv(
      `${host}-articles-${suffix}.csv`,
      toCsv(chosen as unknown as Record<string, unknown>[], EXPORT_COLUMNS),
    );
  }

  return (
    <div className="space-y-5">
      <Link
        to="/news-outlets"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <IconArrowLeft className="size-4" />
        All news outlets
      </Link>

      {/* Two halves of one paper's record, and the choice lives in the URL:
          "this title has been read every day and yielded nothing" is an answer
          somebody asked for and should be able to send. */}
      <Tabs
        value={tab}
        onValueChange={(next) =>
          navigate({
            search: (prev) => ({
              ...prev,
              tab: next === "articles" ? undefined : (next as "log"),
            }),
          })
        }
      >
        <StickyHeader
          heading={
            <div className="space-y-3">
              <PageHeader
                title={paper?.outlet ?? host}
                count={tab === "articles" ? total : logTotal}
                isLoading={
                  outlet.isLoading ||
                  (tab === "articles" ? list.isLoading : log.isLoading)
                }
                description={
                  paper
                    ? `${placeLabel(paper.province)} · every article this paper published is read and counted; only collective violence is kept.${paper.note ? ` ${paper.note}.` : ""}`
                    : undefined
                }
                actions={
                  <div className="flex items-center gap-2">
                    {paper ? (
                      <a
                        href={paper.base_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-sm underline underline-offset-4"
                      >
                        <IconExternalLink className="size-4" />
                        Visit paper
                      </a>
                    ) : null}
                    {tab === "articles" ? (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={!rows.length}
                        onClick={() => exportRows(rows, `page-${page + 1}`)}
                      >
                        <IconDownload className="size-4" />
                        Export page
                      </Button>
                    ) : (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={!logRows.length}
                        onClick={() =>
                          downloadCsv(
                            `${host}-crawl-log.csv`,
                            toCsv(
                              logRows as unknown as Record<string, unknown>[],
                              LOG_EXPORT_COLUMNS,
                            ),
                          )
                        }
                      >
                        <IconDownload className="size-4" />
                        Export log
                      </Button>
                    )}
                  </div>
                }
              />
              <TabsList>
                <TabsTrigger value="articles">Articles kept</TabsTrigger>
                <TabsTrigger value="log">Crawl log</TabsTrigger>
              </TabsList>
            </div>
          }
          filters={
            tab === "articles" ? (
              <TableToolbar
                filters={
                  <>
                    {/* One chip per coded question, each a multiple choice.
                        Offered only where this paper has produced the answer:
                        a filter whose list is empty is a control that cannot
                        do anything, and most papers have never yielded a
                        bomb. */}
                    {CODED_FILTERS.map(({ param, label, options }) => {
                      const chosen = asTextList(search[param]);
                      const available = options(facets.data?.data);
                      if (!available.length && !chosen.length) return null;
                      return (
                        <FilterChip
                          key={param}
                          label={label}
                          value={summarise(chosen, codedLabel)}
                          onClear={() =>
                            navigate({
                              search: (prev) => ({
                                ...prev,
                                [param]: undefined,
                                page: 0,
                              }),
                            })
                          }
                        >
                          <ChoiceList
                            options={available.map((facet) => ({
                              // The value stays as stored — it is what the API
                              // filters by — and only the label is calmed.
                              value: facet.value,
                              label: codedLabel(facet.value),
                              hint: formatCount(facet.count),
                            }))}
                            selected={chosen}
                            onToggle={(value) =>
                              navigate({
                                search: (prev) => ({
                                  ...prev,
                                  [param]: toggle(chosen, value),
                                  page: 0,
                                }),
                              })
                            }
                            onClear={() =>
                              navigate({
                                search: (prev) => ({
                                  ...prev,
                                  [param]: undefined,
                                  page: 0,
                                }),
                              })
                            }
                          />
                        </FilterChip>
                      );
                    })}
                    {narrowed ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8"
                        onClick={() =>
                          navigate({ search: (prev) => ({ tab: prev.tab }) })
                        }
                      >
                        Clear all
                      </Button>
                    ) : null}
                  </>
                }
                search={
                  <SearchInput
                    value={asText(search.q)}
                    placeholder="Search headlines"
                    onSearch={(q) =>
                      navigate({ search: (prev) => ({ ...prev, q, page: 0 }) })
                    }
                  />
                }
              />
            ) : null
          }
        />

        <div className="grid gap-8 pt-2 lg:grid-cols-[16rem_minmax(0,1fr)]">
          {/* The registry entry and the totals, on the left in both tabs, as on
              the dataset and document pages: it is what a reader checks while
              deciding what the table on the right is worth. */}
          <aside className={`lg:sticky lg:self-start ${BELOW_STICKY_HEADER}`}>
            <h2 className="font-heading text-sm font-semibold tracking-tight">About</h2>
            <dl className="mt-3 space-y-3">
              <Fact
                label="Province"
                value={paper ? placeLabel(paper.province) : undefined}
                hint={paper?.geo_id}
              />
              <Fact label="Host" value={paper?.host ?? host} mono />
              {/* Operational, and the first thing to look at when a paper
                  stops yielding: it names the shape of search page the crawl
                  expects this CMS to serve. */}
              <Fact
                label="Search shape"
                value={paper?.adapter}
                hint="how discovery asks this site"
              />
              <Fact
                label="Collection"
                value={paper ? (paper.active ? "Scheduled" : "Retired") : undefined}
                hint={paper?.note ?? undefined}
              />
              <Fact
                label="Refresh"
                value={describeSchedule(source?.schedule) ?? "Manual only"}
                hint={source?.schedule}
              />
              <Fact label="Cadence" value={source?.update_frequency} />
              {/* The three numbers the log breaks down by day, totalled. Read
                  is the denominator: without it, kept is a number with nothing
                  to compare it against. */}
              <Fact
                label="Articles read"
                value={paper ? formatCount(paper.scanned) : undefined}
                hint="the denominator"
              />
              <Fact
                label="Matched the dictionary"
                value={paper ? formatCount(paper.matched) : undefined}
                hint="candidates"
              />
              <Fact
                label="Kept"
                value={paper ? formatCount(paper.recorded) : undefined}
                hint="collective violence"
              />
              <Fact
                label="Pages photographed"
                value={paper ? formatCount(paper.screenshots) : undefined}
              />
              <Fact
                label="Earliest article"
                value={paper?.first_seen ? formatDate(paper.first_seen) : undefined}
              />
              <Fact
                label="Last read"
                value={paper?.last_seen ? formatDate(paper.last_seen) : undefined}
                hint={paper?.last_seen ? formatRelative(paper.last_seen) : undefined}
              />
              <Fact
                label="Licence"
                value={
                  source?.license ?? "Articles remain the copyright of their publishers"
                }
              />
            </dl>
          </aside>

          <div className="min-w-0">
            <TabsContent value="articles" className="space-y-5">
              <DataTable
                columns={columns}
                data={rows}
                isLoading={list.isLoading}
                loadingRows={10}
                // Zero is the normal answer and needs to read as one. "Nothing
                // collected" would suggest the crawl failed, when the usual case is a
                // paper that published plenty and none of it was collective violence.
                emptyMessage={
                  paper?.scanned
                    ? `${formatCount(paper.scanned)} articles from this paper were read; none of them were collective violence. The crawl log has the day-by-day figures.`
                    : "Nothing has been read from this paper yet."
                }
                selectable
                getRowId={(row) => row.url}
                onSelectionChange={setSelected}
                renderSelectionActions={(chosen) => (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => exportRows(chosen, "selection")}
                  >
                    <IconDownload className="size-4" />
                    Export {formatCount(chosen.length)}
                  </Button>
                )}
              />

              <TablePagination
                page={page}
                total={total}
                pageSize={PAGE_SIZE}
                onPage={(next) =>
                  navigate({ search: (prev) => ({ ...prev, page: next }) })
                }
                summary={
                  total > 0 ? (
                    <>
                      {formatCount(page * PAGE_SIZE + 1)}–
                      {formatCount(Math.min((page + 1) * PAGE_SIZE, total))} of{" "}
                      {formatCount(total)}
                      {selected.length
                        ? ` · ${formatCount(selected.length)} selected`
                        : null}
                    </>
                  ) : null
                }
              />
            </TabsContent>

            <TabsContent value="log" className="space-y-5">
              <p className="max-w-2xl text-sm text-muted-foreground">
                What the crawl did with this paper, by the day it ran. Each row narrows:
                found, read in full, carried the dictionary&rsquo;s terms, kept as
                collective violence. The articles behind the first three numbers were
                read and thrown away — a page that is not about a monitored issue is
                never stored, so this is the only record that it was read at all.
              </p>

              <DataTable
                columns={logColumns}
                data={logRows}
                isLoading={log.isLoading}
                loadingRows={8}
                getRowId={(row) => row.date}
                emptyMessage="The crawl has not reached this paper yet. A day with no row is a day it was not visited, which is a different thing from a day it found nothing."
              />

              <TablePagination
                page={logPage}
                total={logTotal}
                pageSize={LOG_PAGE_SIZE}
                onPage={(next) =>
                  navigate({ search: (prev) => ({ ...prev, logPage: next }) })
                }
                summary={
                  logTotal > 0 ? (
                    <>
                      {formatCount(logPage * LOG_PAGE_SIZE + 1)}–
                      {formatCount(Math.min((logPage + 1) * LOG_PAGE_SIZE, logTotal))}{" "}
                      of {formatCount(logTotal)} days
                    </>
                  ) : null
                }
              />
            </TabsContent>
          </div>
        </div>
      </Tabs>
    </div>
  );
}

function Fact({
  label,
  value,
  hint,
  mono,
}: {
  label: string;
  value?: string;
  hint?: string;
  /** For a host or an identifier, which is read character by character. */
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className={`mt-0.5 text-sm${mono ? " font-mono text-xs break-all" : ""}`}>
        {value ?? <span className="text-muted-foreground">—</span>}
        {hint && value ? (
          <span className="ml-1.5 text-xs text-muted-foreground">{hint}</span>
        ) : null}
      </dd>
    </div>
  );
}
