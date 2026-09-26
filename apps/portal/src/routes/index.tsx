import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import {
  IconArrowRight,
  IconChartDots3,
  IconClipboardList,
  IconFolders,
  IconLayoutGrid,
  IconSearch,
} from "@tabler/icons-react";

import { ColumnChart, type Column } from "~/components/column-chart";
import { DotTimeline, type Moment } from "~/components/dot-timeline";
import { ShareBar, type Slice } from "~/components/share-bar";
import { Treemap, type TreemapItem } from "~/components/treemap";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import { Skeleton } from "~/components/ui/skeleton";
import { api } from "~/lib/api";
import { formatCount, formatDate, formatRelative, parseTimestamp } from "~/lib/format";
import { layerNote } from "~/lib/layers";
import { useSessionState } from "~/lib/session";
import { useWorkspace } from "~/lib/workspace";

export const Route = createFileRoute("/")({ component: Overview });

/** How a source is collected, as a reader would say it rather than as it is stored. */
const COLLECTION_METHODS: Record<string, string> = {
  api: "API",
  bulk_download: "Bulk download",
  scrape: "Scraped",
  manual_upload: "By hand",
};

/**
 * The overview.
 *
 * A bento, and a different form in each cell, because each cell answers a
 * different kind of question. What the lake is made of is a part-to-whole over
 * many uneven items — a treemap. A share between publishers is one bar cut up.
 * A distribution across four collection methods is columns. Recency is dots on
 * a time axis. A headline figure is no chart at all.
 *
 * The top row is the reader's own: what they have signed in as, what they have
 * filed, and the four places they are most likely to be going. Everything
 * below it is the warehouse.
 */
function Overview() {
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });
  const indicators = useQuery({
    queryKey: ["indicators"],
    queryFn: () => api.indicators(),
  });
  const storage = useQuery({ queryKey: ["storage"], queryFn: () => api.storage() });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.sources() });

  const { session, hasAuth } = useSessionState();
  const workspace = useWorkspace();

  // Figures, not lake rows: a reader landing here is counting statistics, and
  // Bronze's million parsed cells are not statistics yet.
  // `?.data?.` rather than `?.data.`: the API answers a lake with nothing
  // normalized yet with an empty list, but an older build — or an endpoint
  // that reports its absence as an error — sends `null`, and reducing that
  // takes the whole page down rather than showing a zero.
  const figures =
    indicators.data?.data?.reduce((total, i) => total + i.observations, 0) ?? 0;

  // What the lake is made of. Grouped by layer, so "how much of this is Silver"
  // is answered by the blocks rather than by adding rows up.
  const tables = storage.data?.data ?? [];
  const lakeTiles: TreemapItem[] = tables.map((table) => ({
    key: table.slug,
    label: table.name,
    value: table.rows,
    group: table.layer,
    note: table.slug,
  }));
  const lakeRows = tables.reduce((total, table) => total + table.rows, 0);
  const layers = new Set(tables.map((table) => table.layer));

  // Who holds the figures is a share question, not a ranking one, so the whole
  // is drawn once and cut up rather than as bars to be added together.
  const collections = datasets.data?.data ?? [];
  const organizations: Slice[] = groupBy(
    collections,
    (dataset) => dataset.organization ?? dataset.source_name ?? dataset.source_id,
    (dataset) => dataset.observations,
  );

  // Four short-named categories: a distribution, which columns show at a glance
  // where a list of bars would read as a league table.
  const providers = sources.data?.data ?? [];
  const sourceColumns: Column[] = groupBy(
    providers,
    (source) =>
      COLLECTION_METHODS[source.collection_method] ?? source.collection_method,
    () => 1,
  ).map((entry) => ({ ...entry, note: "sources" }));
  const active = providers.filter((source) => source.active).length;

  // Refresh times, by when the pipeline last wrote the collection — not the
  // period its figures cover. Collections never ingested carry no timestamp and
  // are left off the axis rather than plotted at the epoch.
  const refreshed = collections
    .map((dataset) => ({ dataset, at: parseTimestamp(dataset.last_updated ?? "") }))
    .filter(
      (entry): entry is { dataset: (typeof collections)[number]; at: Date } =>
        entry.at !== undefined,
    )
    .sort((a, b) => b.at.getTime() - a.at.getTime());
  const moments: Moment[] = refreshed.map(({ dataset, at }) => ({
    label: dataset.dataset_id,
    at: at.getTime(),
    raw: dataset.last_updated,
  }));
  const latest = refreshed[0]?.dataset;

  const greeting = session?.user.name?.trim() || session?.user.email.split("@")[0];

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="font-heading text-3xl font-semibold tracking-tight">
          {greeting ? `Welcome back, ${greeting}` : "Research data warehouse"}
        </h1>
        <p className="max-w-2xl text-muted-foreground">
          Source-traceable statistics, documents and regulations. Every figure carries
          the document it came from and the run that produced it.
        </p>
      </div>

      {datasets.isError ? (
        <Card>
          <CardContent className="py-6 text-sm text-muted-foreground">
            The serving layer is not answering. Start it with{" "}
            <code className="rounded bg-muted px-1.5 py-0.5">make dev-api</code>.
          </CardContent>
        </Card>
      ) : null}

      {/* Where a reader goes next, above what the warehouse contains: by the
          third visit the counts are known and the destination is not. */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Shortcut
          to="/search"
          icon={IconSearch}
          title="Search"
          body="One query across every catalogue."
        />
        <Shortcut
          to="/observations"
          icon={IconLayoutGrid}
          title="Data explorer"
          body="Figures, filtered and exported."
        />
        <Shortcut
          to="/collections"
          icon={IconFolders}
          title="Collections"
          body="Folders of records you keep."
        />
        <Shortcut
          to="/saved-queries"
          icon={IconChartDots3}
          title="Combine"
          body="Line saved queries up side by side."
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-7 lg:row-span-2">
          <CardHeader>
            <CardTitle>What the lake is made of</CardTitle>
            <CardDescription>
              Every table, sized by its share of all rows and grouped by layer.
              {/* Said rather than smoothed over: the range is the fact, and a
                  minimum tile size would draw a quantity nobody measured. */}{" "}
              The range spans six orders of magnitude, so the smallest tables are pooled
              and named below rather than inflated.
            </CardDescription>
            <CardAction className="text-right">
              <p className="font-heading text-2xl font-semibold tabular-nums">
                {storage.isLoading ? "—" : formatCount(lakeRows)}
              </p>
              <p className="text-xs text-muted-foreground">
                rows in {layers.size || "—"} layers
              </p>
            </CardAction>
          </CardHeader>
          <CardContent>
            {storage.isLoading ? (
              <Skeleton className="aspect-[2.2] w-full rounded-md" />
            ) : (
              <Treemap
                items={lakeTiles}
                unit="rows"
                // What a layer *is*, on the legend's hover: the medallion names
                // decide how much a figure can be trusted, and nothing else in
                // the portal says what they mean.
                groupNote={layerNote}
                emptyMessage="The lake is empty — run `make ingest` to fill it."
              />
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-5">
          <CardHeader className="pb-1">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Figures published
            </CardTitle>
          </CardHeader>
          <CardContent>
            {indicators.isLoading ? (
              <Skeleton className="h-9 w-28" />
            ) : (
              <>
                <p className="font-heading text-3xl font-semibold tabular-nums">
                  {formatCount(figures)}
                </p>
                <dl className="mt-3 grid grid-cols-3 gap-2 border-t pt-3">
                  <Stat
                    label="Indicators"
                    value={formatCount(indicators.data?.data?.length ?? 0)}
                    to="/indicators"
                  />
                  <Stat
                    label="Datasets"
                    value={formatCount(collections.length)}
                    to="/datasets"
                  />
                  <Stat
                    label="Sources"
                    value={formatCount(providers.length)}
                    to="/datasets"
                  />
                </dl>
              </>
            )}
          </CardContent>
        </Card>

        <ShelfCard
          collections={workspace.collections.length}
          queries={workspace.queries.length}
          names={workspace.collections.slice(0, 3)}
          signedIn={Boolean(session)}
          hasAuth={hasAuth}
        />

        <Card className="lg:col-span-4">
          <CardHeader>
            <CardTitle>Sources</CardTitle>
            <CardDescription>
              How each provider's figures are collected.
            </CardDescription>
            <CardAction className="text-right">
              <p className="font-heading text-2xl font-semibold tabular-nums">
                {sources.isLoading ? "—" : formatCount(providers.length)}
              </p>
              <p className="text-xs text-muted-foreground">
                {sources.isLoading ? " " : `${active} on a schedule`}
              </p>
            </CardAction>
          </CardHeader>
          <CardContent>
            {sources.isLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : (
              <ColumnChart columns={sourceColumns} unit="sources" />
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-4">
          <CardHeader>
            <CardTitle>Publishers</CardTitle>
            <CardDescription>
              Which publisher the warehouse's figures come from.
            </CardDescription>
            <CardAction className="text-right">
              <Link
                to="/datasets"
                className="text-xs text-muted-foreground hover:underline"
              >
                browse all
              </Link>
            </CardAction>
          </CardHeader>
          <CardContent>
            {datasets.isLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : (
              <ShareBar slices={organizations} unit="figures" />
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-4">
          <CardHeader>
            <CardTitle>Last written</CardTitle>
            <CardDescription>
              When each collection was last written, oldest refresh to now.
              {moments.length > 0 ? ` ${formatCount(moments.length)} refreshes.` : ""}
            </CardDescription>
            <CardAction className="text-right">
              <p className="font-heading text-2xl font-semibold">
                {datasets.isLoading
                  ? "—"
                  : (formatRelative(latest?.last_updated) ??
                    formatDate(latest?.last_updated))}
              </p>
              <p className="text-xs text-muted-foreground">
                {datasets.isLoading ? " " : (latest?.dataset_id ?? "nothing yet")}
              </p>
            </CardAction>
          </CardHeader>
          <CardContent>
            {datasets.isLoading ? (
              <Skeleton className="h-14 w-full" />
            ) : (
              <DotTimeline
                moments={moments}
                emptyMessage="No collection has been ingested yet."
              />
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

/**
 * The reader's own shelf, on the page they land on.
 *
 * Counts alone would be a scoreboard; the three most recent folders are what
 * makes this a way back into the work. Signed out, the cell says what a shelf
 * is for rather than showing an empty one.
 */
function ShelfCard({
  collections,
  queries,
  names,
  signedIn,
  hasAuth,
}: {
  collections: number;
  queries: number;
  names: { id: string; name: string; items: unknown[] }[];
  signedIn: boolean;
  hasAuth: boolean;
}) {
  return (
    <Card className="lg:col-span-5">
      <CardHeader>
        <CardTitle>Your shelf</CardTitle>
        <CardDescription>
          {hasAuth && !signedIn
            ? "Collections and saved queries follow your account."
            : "Folders of records, and the filters worth re-running."}
        </CardDescription>
        <CardAction className="flex items-center gap-1.5">
          <Badge variant="secondary" className="tabular-nums">
            {formatCount(collections)} folders
          </Badge>
          <Badge variant="secondary" className="tabular-nums">
            {formatCount(queries)} queries
          </Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-3">
        {collections === 0 ? (
          <p className="text-sm text-muted-foreground">
            {hasAuth && !signedIn ? (
              <>
                <Link to="/login" className="underline underline-offset-4">
                  Log in
                </Link>{" "}
                to keep collections and saved queries.
              </>
            ) : (
              <>
                Nothing filed yet. Collect a series from{" "}
                <Link to="/search" className="underline underline-offset-4">
                  search
                </Link>{" "}
                or from an indicator page.
              </>
            )}
          </p>
        ) : (
          <ul className="divide-y rounded-lg border">
            {names.map((collection) => (
              <li key={collection.id}>
                <Link
                  to="/collections/$collectionId"
                  params={{ collectionId: collection.id }}
                  className="flex items-center gap-3 px-3 py-2 text-sm hover:bg-muted/50"
                >
                  <IconFolders className="size-4 shrink-0 text-muted-foreground" />
                  <span className="truncate font-medium">{collection.name}</span>
                  <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                    {formatCount(collection.items.length)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}

        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" render={<Link to="/collections" />}>
            <IconFolders className="size-4" />
            Collections
          </Button>
          <Button variant="outline" size="sm" render={<Link to="/saved-queries" />}>
            <IconClipboardList className="size-4" />
            Saved queries
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function Stat({ label, value, to }: { label: string; value: string; to: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">
        <Link to={to} className="hover:underline">
          {label}
        </Link>
      </dt>
      <dd className="font-heading text-lg font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

/** Totals per key, largest first — the shape every chart here takes. */
function groupBy<T>(
  items: T[],
  key: (item: T) => string,
  weight: (item: T) => number,
): { label: string; value: number }[] {
  const totals = new Map<string, number>();
  for (const item of items) {
    const label = key(item);
    totals.set(label, (totals.get(label) ?? 0) + weight(item));
  }
  return [...totals.entries()]
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value);
}

function Shortcut({
  to,
  icon: Icon,
  title,
  body,
}: {
  to: string;
  icon: typeof IconSearch;
  title: string;
  body: string;
}) {
  return (
    <Link to={to} className="group">
      <Card
        size="sm"
        className="h-full transition-colors group-hover:ring-foreground/25"
      >
        <CardContent className="flex items-start gap-3">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted">
            <Icon className="size-4" />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-1 font-medium">
              {title}
              <IconArrowRight className="size-3.5 opacity-0 transition-opacity group-hover:opacity-60" />
            </div>
            <p className="text-xs text-muted-foreground">{body}</p>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
