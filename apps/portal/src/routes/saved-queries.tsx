import { useQueries, useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  IconAlertTriangle,
  IconCheck,
  IconChevronRight,
  IconDownload,
  IconPencil,
  IconTrash,
} from "@tabler/icons-react";
import { useMemo, useState } from "react";
import { z } from "zod";

import { PageHeader } from "~/components/page-header";
import { ShelfNote } from "~/components/shelf-note";
import { StickyHeader } from "~/components/sticky-header";
import {
  MAX_SERIES,
  TimeSeriesChart,
  type Series as Line,
} from "~/components/time-series-chart";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "~/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { api, type ObservationQuery } from "~/lib/api";
import {
  AGGREGATES,
  combineByPeriod,
  combinedCsv,
  stackRows,
  type Aggregate,
  type CombineSource,
} from "~/lib/combine";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount, formatDate, formatDecimal } from "~/lib/format";
import { indicatorLabel } from "~/lib/labels";
import { toggle } from "~/lib/multi";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";
import {
  QUERY_KIND_LABELS,
  deleteQuery,
  renameQuery,
  useShelf,
  useWorkspace,
  type SavedQuery,
} from "~/lib/workspace";
import { cn } from "~/lib/utils";

/**
 * Queries worth keeping, and the tool for asking them together.
 *
 * A saved query is a link with a name on it — the filters, not the figures, so
 * it answers against the warehouse as it is when it is reopened. That much is
 * a bookmark.
 *
 * The combiner is why the page exists. The question after "what did inflation
 * do" is "compared to what", and today answering it means running two queries
 * and lining their periods up in a spreadsheet. Two queries rarely share a
 * calendar — one is monthly from 2015, the other quarterly from 2019 — so the
 * union of their periods, with holes left as holes, is the table that can
 * actually be read across. That table is here, as a chart, as figures, and as
 * two downloads.
 *
 * Nothing is converted between units. Series in different units are lined up
 * and the page says so, because plotting rupiah against percent on one axis is
 * a lie a chart tells convincingly.
 */

const searchSchema = z.object({
  /** Saved queries selected for combining. */
  query: listParam,
  /** Series combined directly, without saving a query first — what a collection sends. */
  indicator: listParam,
  aggregate: textParam,
});

export const Route = createFileRoute("/saved-queries")({
  validateSearch: searchSchema,
  component: SavedQueries,
});

/**
 * How many figures one source contributes.
 *
 * High enough for a monthly series across every province since 2010, low
 * enough that four of them combined is still a page that renders. A source
 * that hits it says so rather than quietly plotting a slice.
 */
const SOURCE_LIMIT = 5000;

/** Rows of the aligned table drawn at once; the rest are in the download. */
const SHOWN_PERIODS = 300;

/** The routes a saved query is allowed to reopen. */
const KNOWN_PATHS = [
  "/observations",
  "/documents",
  "/regulations",
  "/indicators",
  "/datasets",
  "/commodities",
  "/search",
] as const;

type KnownPath = (typeof KNOWN_PATHS)[number];

function knownPath(path: string): KnownPath | undefined {
  return (KNOWN_PATHS as readonly string[]).includes(path)
    ? (path as KnownPath)
    : undefined;
}

/** The stored filters as the observations endpoint takes them. */
function toObservationQuery(search: Record<string, unknown>): ObservationQuery {
  const text = (value: unknown) =>
    value === undefined || value === null || value === "" ? undefined : String(value);
  const list = (value: unknown) =>
    value === undefined || value === null
      ? []
      : Array.isArray(value)
        ? value.map(String)
        : [String(value)];

  return {
    indicator: list(search.indicator),
    geo: text(search.geo),
    geo_type: list(search.geo_type),
    commodity: list(search.commodity),
    q: text(search.q),
    period_start: text(search.period_start),
    period_end: text(search.period_end),
  };
}

function SavedQueries() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const workspace = useWorkspace();
  const shelf = useShelf();

  const chosen = asTextList(search.query);
  const adHoc = asTextList(search.indicator);
  const aggregate = (asText(search.aggregate) ?? "mean") as Aggregate;

  const combinable = workspace.queries.filter((query) => query.kind === "observations");

  // Named from the catalogue rather than from the identifier: a chart legend
  // reading `nicmjq31` names nothing. The list is the one every other page
  // uses, so it is usually already in the cache.
  const indicators = useQuery({
    queryKey: ["indicators"],
    queryFn: () => api.indicators(),
    enabled: adHoc.length > 0,
  });
  const indicatorsById = useMemo(
    () => new Map((indicators.data?.data ?? []).map((row) => [row.indicator_id, row])),
    [indicators.data],
  );

  /** What the combiner will run: chosen saved queries, then any series sent straight here. */
  const requests = useMemo(() => {
    const fromQueries = chosen
      .map((id) => combinable.find((query) => query.id === id))
      .filter((query): query is SavedQuery => Boolean(query))
      .map((query) => ({
        key: `query:${query.id}`,
        name: query.name,
        params: toObservationQuery(query.search),
      }));

    const fromIndicators = adHoc.map((id) => ({
      key: `indicator:${id}`,
      name: indicatorLabel(indicatorsById.get(id) ?? { indicator_id: id }),
      params: { indicator: [id] } as ObservationQuery,
    }));

    return [...fromQueries, ...fromIndicators];
  }, [adHoc, chosen, combinable, indicatorsById]);

  const results = useQueries({
    queries: requests.map((request) => ({
      // Keyed on the filters rather than on the saved query, so two queries
      // that happen to ask the same thing are fetched once.
      queryKey: ["combine", request.params],
      queryFn: () => api.observations({ ...request.params, limit: SOURCE_LIMIT }),
    })),
  });

  const sources: CombineSource[] = requests.map((request, index) => ({
    key: request.key,
    name: request.name,
    rows: results[index]?.data?.data ?? [],
  }));

  const loading = results.some((result) => result.isLoading);
  const failed = results.find((result) => result.isError);
  // A source the API had more of than it returned is a source whose line ends
  // early for no reason the reader can see.
  const capped = requests
    .map((request, index) => ({
      name: request.name,
      total: results[index]?.data?.meta?.total ?? 0,
      got: results[index]?.data?.data.length ?? 0,
    }))
    .filter((entry) => entry.total > entry.got);

  // Aligning five thousand rows a source is not free, and `sources` is a fresh
  // array every render, so the memo is keyed on what actually changes: which
  // queries are selected, when each last fetched, and how periods are folded.
  const fingerprint = requests
    .map((request, index) => `${request.key}@${results[index]?.dataUpdatedAt ?? 0}`)
    .join("|");

  const combined = useMemo(
    () => combineByPeriod(sources, aggregate),
    [fingerprint, aggregate],
  );

  function setChosen(id: string) {
    void navigate({ search: (prev) => ({ ...prev, query: toggle(chosen, id) }) });
  }

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Saved queries"
            count={shelf.mode === "loading" ? undefined : workspace.queries.length}
            isLoading={shelf.mode === "loading"}
            description={
              <>
                A named set of filters, not a copy of the figures — so it answers
                against the warehouse as it is today.
                <ShelfNote className="mt-1" />
              </>
            }
            actions={
              requests.length ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    void navigate({
                      search: (prev) => ({
                        ...prev,
                        query: undefined,
                        indicator: undefined,
                      }),
                    })
                  }
                >
                  Clear selection
                </Button>
              ) : null
            }
          />
        }
      />

      {shelf.mode === "loading" ? (
        <div className="space-y-2 rounded-lg border p-3">
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton key={index} className="h-6 w-full" />
          ))}
        </div>
      ) : null}

      {shelf.mode !== "loading" && workspace.queries.length === 0 && !adHoc.length ? (
        <p className="rounded-lg border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">
          Nothing saved yet. Filter the{" "}
          <Link to="/observations" className="underline underline-offset-4">
            data explorer
          </Link>{" "}
          or a{" "}
          <Link to="/search" className="underline underline-offset-4">
            search
          </Link>{" "}
          and press Save.
        </p>
      ) : null}

      {workspace.queries.length ? (
        <section className="space-y-2">
          <h2 className="font-heading text-sm font-medium">Saved</h2>
          <div className="divide-y rounded-lg border">
            {workspace.queries.map((query) => (
              <QueryRow
                key={query.id}
                query={query}
                selected={chosen.includes(query.id)}
                onSelect={
                  query.kind === "observations" ? () => setChosen(query.id) : undefined
                }
              />
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            {/* The selection is what the combiner runs on, and a reader who
                does not know that sees a page of tick boxes that do nothing. */}
            Tick two or more figure queries to line their periods up below.
          </p>
        </section>
      ) : null}

      {requests.length ? (
        <Combiner
          sources={sources}
          combined={combined}
          aggregate={aggregate}
          onAggregate={(next) =>
            void navigate({ search: (prev) => ({ ...prev, aggregate: next }) })
          }
          isLoading={loading}
          error={failed ? (failed.error as Error).message : undefined}
          capped={capped}
        />
      ) : null}
    </div>
  );
}

function QueryRow({
  query,
  selected,
  onSelect,
}: {
  query: SavedQuery;
  selected: boolean;
  /** Absent for a query the combiner cannot run — a document list, a search. */
  onSelect?: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const navigate = useNavigate();
  const path = knownPath(query.path);

  function open() {
    if (!path) return;
    // The stored path and filters are strings no compiler checked, so the path
    // is matched against the routes this page knows before navigating and the
    // filters are handed over as the route's own schema will re-validate them.
    void navigate({ to: path, search: query.search as never });
  }

  return (
    <div className="flex items-center gap-3 px-3 py-2.5">
      {onSelect ? (
        <button
          type="button"
          onClick={onSelect}
          aria-pressed={selected}
          aria-label={`Combine ${query.name}`}
          className={cn(
            "flex size-4 shrink-0 items-center justify-center rounded-[4px] border",
            selected
              ? "border-primary bg-primary text-primary-foreground"
              : "border-input",
          )}
        >
          {selected ? <IconCheck className="size-3" /> : null}
        </button>
      ) : (
        <span
          className="size-4 shrink-0"
          title="Only figure queries can be combined"
          aria-hidden
        />
      )}

      <div className="min-w-0 flex-1 leading-tight">
        {renaming ? (
          <form
            className="flex items-center gap-1.5"
            onSubmit={(event) => {
              event.preventDefault();
              const input = event.currentTarget.elements.namedItem(
                "name",
              ) as HTMLInputElement;
              renameQuery(query.id, input.value);
              setRenaming(false);
            }}
          >
            <Input
              name="name"
              autoFocus
              defaultValue={query.name}
              aria-label="Query name"
              className="h-7"
            />
            <Button type="submit" size="sm">
              Save
            </Button>
          </form>
        ) : (
          <button
            type="button"
            onClick={open}
            disabled={!path}
            title={
              path
                ? `Open ${query.path}`
                : "This query points at a page that no longer exists"
            }
            className="flex max-w-full items-center gap-1 truncate font-medium underline-offset-4 hover:underline disabled:no-underline disabled:opacity-60"
          >
            <span className="truncate">{query.name}</span>
            {path ? <IconChevronRight className="size-3.5 shrink-0" /> : null}
          </button>
        )}
        <div className="truncate text-xs text-muted-foreground">
          {query.summary ?? query.path}
        </div>
      </div>

      <Badge variant="outline" className="hidden shrink-0 sm:inline-flex">
        {QUERY_KIND_LABELS[query.kind] ?? query.kind}
      </Badge>

      <div className="hidden shrink-0 text-xs text-muted-foreground sm:block">
        {formatDate(query.created_at)}
      </div>

      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={`Rename ${query.name}`}
        onClick={() => setRenaming((on) => !on)}
      >
        <IconPencil className="size-4" />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={`Delete ${query.name}`}
        onClick={() => deleteQuery(query.id)}
      >
        <IconTrash className="size-4" />
      </Button>
    </div>
  );
}

function Combiner({
  sources,
  combined,
  aggregate,
  onAggregate,
  isLoading,
  error,
  capped,
}: {
  sources: CombineSource[];
  combined: ReturnType<typeof combineByPeriod>;
  aggregate: Aggregate;
  onAggregate: (value: Aggregate) => void;
  isLoading: boolean;
  error?: string;
  capped: { name: string; total: number; got: number }[];
}) {
  const [hidden, setHidden] = useState<string[]>([]);

  const lines: Line[] = combined.columns.slice(0, MAX_SERIES).map((column) => ({
    name: column.name,
    hidden: hidden.includes(column.name),
    points: combined.periods.map((period, index) => ({
      label: period,
      value: column.values[index] ?? null,
    })),
  }));

  const folded = combined.columns.filter((column) => column.folded > 0);
  const rows = stackRows(sources);
  const shown = combined.periods.slice(-SHOWN_PERIODS);
  const offset = combined.periods.length - shown.length;

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="font-heading text-sm font-medium">Combined</h2>
        <Badge variant="secondary" className="tabular-nums">
          {formatCount(combined.columns.length)} series ·{" "}
          {formatCount(combined.periods.length)} periods
        </Badge>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {/* What to do when a query answers a period with several figures —
              a decision about meaning, so it is the reader's and it is on
              screen rather than buried in a menu. */}
          <label className="text-xs text-muted-foreground" htmlFor="combine-aggregate">
            Several figures in a period
          </label>
          <select
            id="combine-aggregate"
            value={aggregate}
            onChange={(event) => onAggregate(event.target.value as Aggregate)}
            className="h-8 rounded-lg border bg-background px-2 text-sm outline-none focus-visible:border-ring"
          >
            {AGGREGATES.map((option) => (
              <option key={option.value} value={option.value} title={option.hint}>
                {option.label}
              </option>
            ))}
          </select>

          <Button
            variant="outline"
            size="sm"
            disabled={!combined.periods.length}
            onClick={() => downloadCsv("combined-aligned.csv", combinedCsv(combined))}
          >
            <IconDownload className="size-4" />
            Aligned CSV
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!rows.length}
            onClick={() =>
              downloadCsv(
                "combined-rows.csv",
                toCsv(rows as unknown as Record<string, unknown>[], [
                  { key: "query", header: "query" },
                  { key: "indicator_id", header: "indicator_id" },
                  { key: "period", header: "period" },
                  { key: "period_start", header: "period_start" },
                  { key: "value", header: "value" },
                  { key: "unit", header: "unit" },
                  { key: "status", header: "status" },
                  { key: "geo_id", header: "geo_id" },
                  { key: "geo_name", header: "geo_name" },
                  { key: "commodity_name", header: "commodity_name" },
                  { key: "source_id", header: "source_id" },
                ]),
              )
            }
          >
            <IconDownload className="size-4" />
            All rows ({formatCount(rows.length)})
          </Button>
        </div>
      </div>

      {error ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
          {error}
        </p>
      ) : null}

      <Notes
        mixedUnits={combined.mixedUnits}
        folded={folded.map((column) => `${column.name} (${column.folded})`)}
        aggregate={aggregate}
        capped={capped}
        overflow={combined.columns.length - lines.length}
      />

      {isLoading ? (
        <p className="rounded-lg border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">
          Running {sources.length} quer{sources.length === 1 ? "y" : "ies"}…
        </p>
      ) : !combined.periods.length ? (
        <p className="rounded-lg border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">
          These queries returned no figures.
        </p>
      ) : (
        <Tabs defaultValue="chart">
          <TabsList>
            <TabsTrigger value="chart">Chart</TabsTrigger>
            <TabsTrigger value="table">Figures</TabsTrigger>
          </TabsList>

          <TabsContent value="chart">
            <TimeSeriesChart
              series={lines}
              caption={`${combined.columns.length} queries, ${combined.periods.length} periods`}
              onToggle={(name) =>
                setHidden((current) =>
                  current.includes(name)
                    ? current.filter((entry) => entry !== name)
                    : [...current, name],
                )
              }
            />
          </TabsContent>

          <TabsContent value="table">
            <div className="overflow-x-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Period</TableHead>
                    {combined.columns.map((column) => (
                      <TableHead key={column.key} className="text-right">
                        {column.name}
                        {column.units.length ? (
                          <span className="block text-xs font-normal text-muted-foreground">
                            {column.units.join(", ")}
                          </span>
                        ) : null}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {shown.map((period, index) => (
                    <TableRow key={period}>
                      <TableCell className="font-medium">{period}</TableCell>
                      {combined.columns.map((column) => {
                        const value = column.values[offset + index];
                        return (
                          <TableCell
                            key={column.key}
                            className="text-right tabular-nums"
                          >
                            {value === null || value === undefined ? (
                              // A hole, and said so: a blank cell cannot tell
                              // "this query has nothing here" from zero.
                              <span className="text-muted-foreground">—</span>
                            ) : (
                              formatDecimal(String(value))
                            )}
                          </TableCell>
                        );
                      })}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            {offset > 0 ? (
              <p className="mt-2 text-xs text-muted-foreground">
                The most recent {formatCount(shown.length)} periods. The whole alignment
                is in the download.
              </p>
            ) : null}
          </TabsContent>
        </Tabs>
      )}
    </section>
  );
}

/**
 * What the reader has to know before reading the table.
 *
 * Each of these changes what the figures mean — a mixed axis, an average
 * standing in for thirty-four provinces, a series cut off at the fetch limit —
 * so they are stated beside the chart rather than left to be inferred from it.
 */
function Notes({
  mixedUnits,
  folded,
  aggregate,
  capped,
  overflow,
}: {
  mixedUnits: boolean;
  folded: string[];
  aggregate: Aggregate;
  capped: { name: string; total: number; got: number }[];
  overflow: number;
}) {
  const notes: string[] = [];

  if (mixedUnits) {
    notes.push(
      "These queries are not in the same unit. They are lined up, not converted — read each column against its own unit.",
    );
  }
  if (folded.length) {
    const label =
      AGGREGATES.find((option) => option.value === aggregate)?.label ?? aggregate;
    notes.push(
      `${label.toLowerCase()} of several figures where a query answered one period more than once: ${folded.join(", ")}.`,
    );
  }
  for (const entry of capped) {
    notes.push(
      `${entry.name} has ${formatCount(entry.total)} figures and ${formatCount(entry.got)} were fetched; narrow it by period or place.`,
    );
  }
  if (overflow > 0) {
    notes.push(
      `${overflow} more quer${overflow === 1 ? "y is" : "ies are"} in the table and the downloads but not the chart — past ${MAX_SERIES} lines the colours would repeat.`,
    );
  }

  if (!notes.length) return null;

  return (
    <ul className="space-y-1 rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
      {notes.map((note) => (
        <li key={note} className="flex gap-2">
          <IconAlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>{note}</span>
        </li>
      ))}
    </ul>
  );
}
