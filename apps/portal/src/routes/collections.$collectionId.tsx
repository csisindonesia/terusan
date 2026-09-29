import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  IconArrowLeft,
  IconDeviceFloppy,
  IconDownload,
  IconDots,
  IconLock,
  IconUsers,
  IconWorld,
} from "@tabler/icons-react";
import { z } from "zod";

import { CollectionApi } from "~/components/collection-api";
import { CollectionIndicators } from "~/components/collection-indicators";
import { CollectionLogs } from "~/components/collection-logs";
import { CollectionSettings } from "~/components/collection-settings";
import { PageHeader } from "~/components/page-header";
import { StickyHeader } from "~/components/sticky-header";
import { Button } from "~/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { api } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import {
  describeSearch,
  fromServerCollection,
  itemsOfKind,
  personLabel,
  saveQuery,
  useCollection,
  useShelf,
  type Collection,
} from "~/lib/workspace";

/**
 * One folder, and what can be done with what is in it.
 *
 * The listing is the smaller half. A folder of eight series is a question
 * someone is asking, and the tabs here are the ways of asking it: what each
 * series covers, the one request that returns all their figures, and whether
 * the pipelines behind them are still running. Without those a collection is
 * a bookmark folder, and a bookmark folder is not worth a page.
 *
 * Laid out as an indicator is — Data, API, Logs, Settings — because a
 * collection is several indicators asked one question, and a reader who knows
 * where an indicator keeps its API request knows where this keeps its.
 */

const searchSchema = z.object({
  // Which tab is open, so a link can point at the API notes or the settings
  // rather than always landing on the table.
  tab: z.enum(["data", "api", "logs", "settings"]).optional(),
});

type TabName = "data" | "api" | "logs" | "settings";

export const Route = createFileRoute("/collections/$collectionId")({
  validateSearch: searchSchema,
  component: CollectionDetail,
});

function CollectionDetail() {
  const { collectionId } = Route.useParams();
  const search = Route.useSearch();
  const shelved = useCollection(collectionId);
  const shelf = useShelf();
  const navigate = useNavigate({ from: Route.fullPath });

  // A public collection the reader is not in is not on their shelf — it is
  // found by its link — so it is read on its own when the shelf lacks it.
  const direct = useQuery({
    queryKey: ["collection", collectionId],
    enabled: shelf.mode === "server" && !shelved,
    retry: false,
    queryFn: () => api.collection(collectionId),
  });
  const collection: Collection | undefined =
    shelved ?? (direct.data ? fromServerCollection(direct.data.data) : undefined);

  const tab: TabName = search.tab ?? "data";
  const setTab = (next: TabName) =>
    void navigate({ search: (prev) => ({ ...prev, tab: next }) });

  // Until the shelf has said where it lives, a missing folder is a folder that
  // has not arrived yet. Saying "no such collection" during that second is how
  // a shared link looks broken to the person it was sent to.
  if (!collection && (shelf.mode === "loading" || direct.isLoading)) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-9 w-80" />
        <Skeleton className="h-40 w-full rounded-lg" />
      </div>
    );
  }

  if (!collection) {
    return (
      <div className="space-y-4">
        <BackLink />
        <p className="rounded-lg border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">
          {shelf.mode === "server" ? (
            <>
              No collection with this identifier that you can open. It may have been
              deleted, it may be limited to people who have not added you, or the link
              may name a shelf on another deployment.
            </>
          ) : (
            <>
              {/* Without a shelf on the API, storage is per-browser — so a link
                  to a collection is not a link anyone else can open. Saying
                  which is missing is the difference between a bug and a limit. */}
              No collection with this identifier is in this browser, and this deployment
              keeps no collections of its own — so a link to one only opens where it was
              made.
            </>
          )}
        </p>
      </div>
    );
  }

  const indicators = itemsOfKind(collection, "indicator");

  /** The explorer filter this folder's figures live behind. */
  const explorerSearch = {
    indicator: indicators.length ? indicators.map((item) => item.id) : undefined,
  };
  const hasFigures = indicators.length > 0;

  function exportManifest() {
    if (!collection) return;
    downloadCsv(
      `collection-${collection.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}.csv`,
      toCsv(collection.items as unknown as Record<string, unknown>[], [
        { key: "kind", header: "kind" },
        { key: "id", header: "id" },
        { key: "label", header: "label" },
        { key: "note", header: "note" },
        { key: "added_at", header: "added_at" },
      ]),
    );
  }

  return (
    <div className="space-y-6">
      <Tabs value={tab} onValueChange={(next) => setTab(next as TabName)}>
        <StickyHeader
          divided={false}
          heading={
            <div className="space-y-2">
              <BackLink />

              <PageHeader
                title={collection.name}
                count={collection.items.length}
                description={collection.description}
                actions={
                  <>
                    {shelf.mode === "server" ? (
                      <Button
                        variant="outline"
                        size="sm"
                        title="Who may open this collection"
                        onClick={() => setTab("settings")}
                      >
                        <AccessLabel collection={collection} />
                      </Button>
                    ) : null}

                    <DropdownMenu>
                      <DropdownMenuTrigger
                        render={
                          <Button
                            variant="outline"
                            size="icon-sm"
                            aria-label="Collection actions"
                          >
                            <IconDots className="size-4" />
                          </Button>
                        }
                      />
                      <DropdownMenuContent align="end" className="w-52">
                        {/* Saving a query writes to the reader's own shelf,
                            so a read-only shelf drops it and keeps the export,
                            which only reads. */}
                        {shelf.writable ? (
                          <DropdownMenuItem
                            disabled={!hasFigures}
                            onClick={() =>
                              saveQuery({
                                name: collection.name,
                                kind: "observations",
                                path: "/observations",
                                search: explorerSearch,
                                summary: describeSearch(explorerSearch),
                              })
                            }
                          >
                            <IconDeviceFloppy />
                            Save as query
                          </DropdownMenuItem>
                        ) : null}
                        <DropdownMenuItem
                          disabled={!collection.items.length}
                          onClick={exportManifest}
                        >
                          <IconDownload />
                          Export list (CSV)
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </>
                }
              />
            </div>
          }
          filters={
            <TabsList>
              <TabsTrigger value="data">Data</TabsTrigger>
              <TabsTrigger value="api">API</TabsTrigger>
              <TabsTrigger value="logs">Logs</TabsTrigger>
              <TabsTrigger value="settings">Settings</TabsTrigger>
            </TabsList>
          }
        />

        <TabsContent value="data" className="pt-2">
          {collection.items.length === 0 ? (
            <p className="rounded-lg border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">
              No indicators here yet. Find series in{" "}
              <Link to="/indicators" className="underline underline-offset-4">
                indicators
              </Link>{" "}
              and use Add to collection in a row&rsquo;s menu.
            </p>
          ) : (
            <CollectionIndicators collection={collection} />
          )}
        </TabsContent>

        <TabsContent value="api" className="pt-2">
          <CollectionApi
            collection={collection}
            onOpenSettings={() => setTab("settings")}
          />
        </TabsContent>

        <TabsContent value="logs" className="pt-2">
          <CollectionLogs collection={collection} />
        </TabsContent>

        <TabsContent value="settings" className="pt-2">
          {/* Keyed on the collection's state so the form restarts from what
              was saved when another tab — or the server — changes it. */}
          <CollectionSettings
            key={`${collection.name}\u0000${collection.description ?? ""}`}
            collection={collection}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/collections"
      className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
    >
      <IconArrowLeft className="size-4" />
      Collections
    </Link>
  );
}

/** Who may open the collection, in the words of the button that changes it. */
function AccessLabel({ collection }: { collection: Collection }) {
  const members = collection.members?.length ?? 0;
  if (collection.role === "shared") {
    return (
      <>
        <IconUsers className="size-4" />
        Everyone
      </>
    );
  }
  if (collection.visibility === "public") {
    return (
      <>
        <IconWorld className="size-4" />
        Public
      </>
    );
  }
  if (collection.role === "member" || collection.role === "viewer") {
    return (
      <>
        <IconUsers className="size-4" />
        {personLabel(collection.owner)}&rsquo;s
      </>
    );
  }
  return (
    <>
      {members ? <IconUsers className="size-4" /> : <IconLock className="size-4" />}
      {members ? `${members + 1} people` : "Only you"}
    </>
  );
}
