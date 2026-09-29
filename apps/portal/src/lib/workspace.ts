/**
 * The reader's own shelf: collections of records, and queries worth keeping.
 *
 * Two homes, and which one is in use is a fact about the deployment rather
 * than a setting anyone chooses here. Where the API keeps a shelf
 * (`COLLECTIONS_DB` is set, and `/v1/capabilities` says so), this is a cache
 * in front of it and a folder has a URL that opens anywhere. Where it does
 * not, the browser is the shelf — which works, survives a reload, and cannot
 * be shared with anyone, because localStorage has no address.
 *
 * Everything above this file is the same either way. Pages read a synchronous
 * snapshot and call synchronous mutators; the server round trip happens behind
 * them, applied locally first so a click never waits on the network. A write
 * that fails says so through `useShelf()` and re-reads the server rather than
 * leaving the screen disagreeing with it.
 *
 * Nothing here stores figures. A collection holds identifiers and a saved
 * query holds filters, so both resolve against the warehouse as it is now
 * rather than as it was the day they were made — which is the whole point of
 * saving a query rather than a spreadsheet.
 */

import { useSyncExternalStore } from "react";

import {
  api,
  type ShelfCollection,
  type ShelfPerson,
  type ShelfQuery,
  type ShelfRole,
} from "~/lib/api";

export const WORKSPACE_STORAGE_KEY = "terusan.workspace";

/** Bumped when a stored shape stops being readable by this code. */
const VERSION = 1;

/**
 * The kinds of record the portal can offer to file. Pages still describe what
 * they show in these terms, but a collection holds only the first: it is a
 * set of series, whose figures it opens, combines and serves under its own
 * API, and a document has no figures to serve.
 */
export type ItemKind =
  "indicator" | "dataset" | "document" | "regulation" | "commodity" | "topic";

/** What a collection holds. */
export const ITEM_KINDS: ItemKind[] = ["indicator"];

/** Whether a record can go in a collection. */
export function collectable(item: { kind: ItemKind }): boolean {
  return ITEM_KINDS.includes(item.kind);
}

/** What to call a kind in a heading, singular and plural. */
export const ITEM_KIND_LABELS: Record<ItemKind, { one: string; many: string }> = {
  indicator: { one: "Indicator", many: "Indicators" },
  dataset: { one: "Dataset", many: "Datasets" },
  document: { one: "Document", many: "Documents" },
  regulation: { one: "Regulation", many: "Regulations" },
  commodity: { one: "Commodity", many: "Commodities" },
  topic: { one: "Topic", many: "Topics" },
};

export type CollectionItem = {
  kind: ItemKind;
  /**
   * The identifier the API takes for that kind: `indicator_id`, `dataset_id`,
   * `document_id`, a regulation key, a commodity's printed name, a tag.
   */
  id: string;
  /** What it was called when it was filed, so a folder reads without fetching. */
  label: string;
  /** Why it is in here, in the reader's words. */
  note?: string;
  added_at: string;
};

export type Collection = {
  id: string;
  name: string;
  description?: string;
  created_at: string;
  updated_at: string;
  items: CollectionItem[];
  /** Whether its figures are served through its own API path. */
  api?: boolean;
  /**
   * The reader's part in it, where the deployment has owners. Absent in a
   * browser-kept shelf, where everything is the reader's own.
   */
  role?: ShelfRole;
  owner?: ShelfPerson;
  members?: CollectionMember[];
};

export type CollectionMember = ShelfPerson & { added_at: string };

/** Whether the reader may rename or delete it — not only file into it. */
export function managesCollection(collection: Collection): boolean {
  return collection.role !== "member";
}

/** A person as a list shows them: their name, or the address they sign in with. */
export function personLabel(person: ShelfPerson | undefined): string {
  return person?.name || person?.email || "a deleted account";
}

/**
 * The page a saved query reopens.
 *
 * `observations` is the one the combiner can run on its own, because the
 * figures it returns are comparable across queries. The rest are saved so a
 * reader can get back to a filtered catalogue; combining a list of documents
 * with a list of regulations would produce a table of nothing in particular.
 */
export type QueryKind =
  | "observations"
  | "documents"
  | "regulations"
  | "indicators"
  | "datasets"
  | "commodities"
  | "search";

export const QUERY_KIND_LABELS: Record<QueryKind, string> = {
  observations: "Figures",
  documents: "Documents",
  regulations: "Regulations",
  indicators: "Indicators",
  datasets: "Datasets",
  commodities: "Commodities",
  search: "Search",
};

export type SavedQuery = {
  id: string;
  name: string;
  kind: QueryKind;
  /** The route it reopens — `/observations`, `/documents`. */
  path: string;
  /** The search parameters, exactly as the route validated them. */
  search: Record<string, unknown>;
  /** What the filters said when it was saved, for the card. */
  summary?: string;
  created_at: string;
};

export type Workspace = {
  version: number;
  collections: Collection[];
  queries: SavedQuery[];
};

/**
 * Where the shelf lives, and whether it can be changed.
 *
 * On screen this is one sentence under a heading, and it is worth the sentence:
 * "this folder has a URL your colleague can open" and "this folder exists in
 * this browser only" are different promises, and a reader who assumes the
 * first while the second is true loses their work to a cleared cache.
 */
export type ShelfStatus = {
  /** `loading` until the API has been asked which it is. */
  mode: "loading" | "server" | "local";
  writable: boolean;
  /** Whether folders have owners and members, which needs accounts. */
  members: boolean;
  /** What the last write or sync said when it failed. */
  error?: string;
};

/**
 * What the server renders, and what a browser with storage blocked shows.
 *
 * One frozen object rather than a fresh one per call: `useSyncExternalStore`
 * compares snapshots by identity, and a new empty workspace every render is an
 * infinite loop.
 */
const EMPTY: Workspace = Object.freeze({
  version: VERSION,
  collections: [],
  queries: [],
}) as Workspace;

const LOADING: ShelfStatus = Object.freeze({
  mode: "loading",
  writable: false,
  members: false,
});

let cache: Workspace | null = null;
let status: ShelfStatus = LOADING;
let started = false;
const listeners = new Set<() => void>();

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * Whatever arrived, kept only where it still has the shape this expects.
 *
 * A stored workspace outlives the code that wrote it — a reader keeps a folder
 * for a year — and a served one is written by a deployment this build does not
 * control, so every field is checked rather than trusted. A row that no longer
 * parses is dropped instead of taking the whole shelf down with it.
 */
function parse(raw: string): Workspace {
  const parsed: unknown = JSON.parse(raw);
  if (!isRecord(parsed)) return EMPTY;
  return {
    version: VERSION,
    collections: Array.isArray(parsed.collections)
      ? parsed.collections.filter(isCollection)
      : [],
    queries: Array.isArray(parsed.queries) ? parsed.queries.filter(isQuery) : [],
  };
}

function isCollection(value: unknown): value is Collection {
  if (!isRecord(value)) return false;
  if (typeof value.id !== "string" || typeof value.name !== "string") return false;
  return Array.isArray(value.items) && value.items.every(isItem);
}

function isItem(value: unknown): value is CollectionItem {
  if (!isRecord(value)) return false;
  if (typeof value.id !== "string" || typeof value.label !== "string") return false;
  return ITEM_KINDS.includes(value.kind as ItemKind);
}

function isQuery(value: unknown): value is SavedQuery {
  if (!isRecord(value)) return false;
  if (typeof value.id !== "string" || typeof value.name !== "string") return false;
  if (typeof value.path !== "string") return false;
  return isRecord(value.search);
}

function readLocal(): Workspace {
  try {
    const raw = localStorage.getItem(WORKSPACE_STORAGE_KEY);
    return raw ? parse(raw) : EMPTY;
  } catch {
    // Storage unavailable, or a value that is not JSON at all. An unreadable
    // shelf is an empty one; it is not worth a blank page.
    return EMPTY;
  }
}

function persistLocal(next: Workspace): void {
  try {
    localStorage.setItem(WORKSPACE_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Quota, or a browser with storage switched off. The change stands for as
    // long as the page does rather than being silently refused on screen.
  }
}

function snapshot(): Workspace {
  if (!cache) cache = status.mode === "server" ? EMPTY : readLocal();
  return cache;
}

function statusSnapshot(): ShelfStatus {
  return status;
}

function emit(): void {
  for (const listener of listeners) listener();
}

function setStatus(next: Partial<ShelfStatus>): void {
  status = { ...status, ...next };
  emit();
}

/** Apply a change here, and persist it wherever this shelf lives. */
function apply(next: Workspace): void {
  cache = next;
  if (status.mode !== "server") persistLocal(next);
  emit();
}

/**
 * Changes waiting to reach the server, in the order they were made.
 *
 * They have to be ordered, not merely sent: making a collection and filing
 * something into it are two requests, and the second one 404s if it overtakes
 * the first. Fired in parallel they do overtake — the create carries a body
 * and the add is smaller — and the screen then shows items the server threw
 * away.
 */
let pending: Promise<unknown> = Promise.resolve();

/**
 * Send a change the server has to hear about.
 *
 * The local copy has already changed by the time this runs, which is what
 * makes a click feel instant. A failure is therefore not just a message: the
 * screen is now showing something the server does not hold, so the shelf is
 * re-read rather than left to drift.
 */
function push(run: () => Promise<unknown>): void {
  if (status.mode !== "server") return;
  pending = pending
    .then(run)
    .then(() => {
      if (status.error) setStatus({ error: undefined });
    })
    .catch((error: unknown) => {
      setStatus({
        error: error instanceof Error ? error.message : "the shelf did not save",
      });
      // Re-read rather than leaving the screen disagreeing with the server —
      // and awaited inside the chain, so whatever was queued behind this
      // failure applies to the shelf as it actually is.
      return pull().catch(() => {});
    });
}

/** Read the whole shelf from the API. */
async function pull(): Promise<void> {
  const [collections, queries] = await Promise.all([
    api.collections(),
    api.savedQueries(),
  ]);
  cache = {
    version: VERSION,
    collections: collections.data.map(fromServerCollection).filter(isCollection),
    queries: queries.data.map(fromServerQuery).filter(isQuery),
  };
  emit();
}

function fromServerCollection(collection: ShelfCollection): Collection {
  return {
    id: collection.id,
    name: collection.name,
    description: collection.description || undefined,
    created_at: collection.created_at,
    updated_at: collection.updated_at,
    items: (collection.items ?? [])
      .filter((item) => ITEM_KINDS.includes(item.kind as ItemKind))
      .map((item) => ({
        kind: item.kind as ItemKind,
        id: item.id,
        label: item.label,
        note: item.note || undefined,
        added_at: item.added_at,
      })),
    api: collection.api ?? false,
    role: collection.role,
    owner: collection.owner,
    members: collection.members ?? [],
  };
}

function fromServerQuery(query: ShelfQuery): SavedQuery {
  return {
    id: query.id,
    name: query.name,
    kind: query.kind as QueryKind,
    path: query.path,
    search: query.search ?? {},
    summary: query.summary || undefined,
    created_at: query.created_at,
  };
}

/**
 * Find out which shelf this deployment has, once.
 *
 * An API that cannot be reached is treated as one without a shelf rather than
 * as an error: the portal's other pages will say the API is down in their own
 * way, and a reader whose collections vanish because a fetch failed is worse
 * off than one whose collections are local.
 */
async function startOnce(): Promise<void> {
  if (started) return;
  started = true;

  try {
    const capabilities = await api.capabilities();
    if (capabilities.data.collections) {
      status = {
        mode: "server",
        writable: capabilities.data.collections_write,
        members: capabilities.data.collection_members ?? false,
      };
      await pull();
      return;
    }
  } catch {
    // Fall through to the browser.
  }

  status = { mode: "local", writable: true, members: false };
  cache = readLocal();
  emit();
}

function onStorage(event: StorageEvent): void {
  if (status.mode === "server") return;
  if (event.key !== null && event.key !== WORKSPACE_STORAGE_KEY) return;
  // Another tab wrote; re-read rather than trusting what this one holds.
  cache = null;
  emit();
}

// A server-kept shelf is shared, so another tab — or a colleague — can change
// it while this one sits open. Re-read when the window is looked at again,
// which is the cheapest moment that is also the one that matters.
function onFocus(): void {
  if (status.mode === "server") void pull().catch(() => {});
}

function subscribe(listener: () => void): () => void {
  if (!listeners.size) {
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", onFocus);
  }
  listeners.add(listener);
  void startOnce();

  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", onFocus);
    }
  };
}

/**
 * The shelf, re-rendering whatever reads it when it changes.
 *
 * The server snapshot is empty because the server rendering this page has no
 * shelf of its own to read; the real one arrives after hydration, which is the
 * same bargain the theme toggle makes.
 */
export function useWorkspace(): Workspace {
  return useSyncExternalStore(subscribe, snapshot, () => EMPTY);
}

/** Where this shelf lives, whether it accepts changes, and what last failed. */
export function useShelf(): ShelfStatus {
  return useSyncExternalStore(subscribe, statusSnapshot, () => LOADING);
}

/** One collection by id, for a detail page. */
export function useCollection(id: string): Collection | undefined {
  return useWorkspace().collections.find((collection) => collection.id === id);
}

function newId(): string {
  try {
    return crypto.randomUUID().replaceAll("-", "").slice(0, 12);
  } catch {
    // Older browsers, and any insecure origin — `crypto.randomUUID` is only
    // defined in a secure context.
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  }
}

function now(): string {
  return new Date().toISOString();
}

/**
 * Whether a change may be made at all.
 *
 * A deployment can serve a curated shelf read-only, and the honest thing then
 * is to refuse in one place and say so once, rather than letting every button
 * write to a cache the server will contradict on the next reload.
 */
function refused(): boolean {
  if (status.mode !== "server" || status.writable) return false;
  setStatus({ error: "this shelf is read-only" });
  return true;
}

export function createCollection(name: string, description?: string): Collection {
  const stamp = now();
  // The id is made here rather than taken from the server, so the folder drawn
  // on this click and the row written by it are the same thing — and so the
  // same call retried is the same collection rather than a second one.
  const collection: Collection = {
    id: `col-${newId()}`,
    name: name.trim() || "Untitled collection",
    description: description?.trim() || undefined,
    created_at: stamp,
    updated_at: stamp,
    items: [],
    // Drawn as the reader's until the server's copy, with their name on it,
    // arrives on the next read.
    role: status.members ? "owner" : status.mode === "server" ? "shared" : undefined,
    members: [],
  };
  if (refused()) return collection;

  const current = snapshot();
  apply({ ...current, collections: [collection, ...current.collections] });
  push(() =>
    api.createCollection({
      id: collection.id,
      name: collection.name,
      description: collection.description,
      items: [],
    }),
  );
  return collection;
}

export function updateCollection(
  id: string,
  patch: { name?: string; description?: string; api?: boolean },
): void {
  if (refused()) return;
  const current = snapshot();
  apply({
    ...current,
    collections: current.collections.map((collection) =>
      collection.id === id
        ? {
            ...collection,
            name: patch.name?.trim() || collection.name,
            // An emptied description is a removal, not an unchanged field, so
            // it is distinguished from one that was never given.
            description:
              patch.description === undefined
                ? collection.description
                : patch.description.trim() || undefined,
            api: patch.api ?? collection.api,
            updated_at: now(),
          }
        : collection,
    ),
  });
  push(() => api.updateCollection(id, patch));
}

export function deleteCollection(id: string): void {
  if (refused()) return;
  const current = snapshot();
  apply({
    ...current,
    collections: current.collections.filter((collection) => collection.id !== id),
  });
  push(() => api.deleteCollection(id));
}

export type NewItem = Omit<CollectionItem, "added_at">;

/**
 * File records under a collection, and say how many were new.
 *
 * Filing the same record twice is a thing readers do — from a search, then
 * from the record's own page — and a folder holding it twice is a bug, so the
 * second filing is dropped and the count tells the caller what happened.
 */
export function addToCollection(collectionId: string, items: NewItem[]): number {
  if (refused()) return 0;
  const current = snapshot();
  const target = current.collections.find(
    (collection) => collection.id === collectionId,
  );
  if (!target) return 0;

  const held = new Set(target.items.map((item) => `${item.kind}:${item.id}`));
  const fresh = items
    .filter(collectable)
    .filter((item) => !held.has(`${item.kind}:${item.id}`))
    .map((item) => ({ ...item, added_at: now() }));
  if (!fresh.length) return 0;

  apply({
    ...current,
    collections: current.collections.map((collection) =>
      collection.id === collectionId
        ? { ...collection, items: [...collection.items, ...fresh], updated_at: now() }
        : collection,
    ),
  });
  push(() => api.addCollectionItems(collectionId, fresh));
  return fresh.length;
}

export function removeFromCollection(
  collectionId: string,
  kind: ItemKind,
  id: string,
): void {
  if (refused()) return;
  const current = snapshot();
  apply({
    ...current,
    collections: current.collections.map((collection) =>
      collection.id === collectionId
        ? {
            ...collection,
            items: collection.items.filter(
              (item) => !(item.kind === kind && item.id === id),
            ),
            updated_at: now(),
          }
        : collection,
    ),
  });
  push(() => api.removeCollectionItem(collectionId, kind, id));
}

/**
 * Run a change after everything already queued, and hand back its answer.
 *
 * Membership is not drawn ahead of the server the way filing is: adding
 * someone by address can fail for reasons only the server knows — no account
 * has that address — and a name that appears and then vanishes is worse than
 * a button that waits a moment.
 */
function queue<T>(run: () => Promise<T>): Promise<T> {
  const result = pending.then(run);
  pending = result.catch(() => {});
  return result;
}

function replace(served: ShelfCollection): void {
  const next = fromServerCollection(served);
  const current = snapshot();
  apply({
    ...current,
    collections: current.collections.map((collection) =>
      collection.id === next.id ? next : collection,
    ),
  });
}

function readOnlyError(): Error | undefined {
  if (status.mode !== "server")
    return new Error("this deployment keeps no shared shelf");
  if (!status.writable) return new Error("this shelf is read-only");
  return undefined;
}

/** Let an account into a collection, by the address they sign in with. */
export async function addMember(collectionId: string, email: string): Promise<void> {
  const refusal = readOnlyError();
  if (refusal) throw refusal;
  const response = await queue(() =>
    api.addCollectionMember(collectionId, email.trim()),
  );
  replace(response.data);
}

/**
 * Take someone out of a collection. The reader taking themselves out is
 * leaving, and the folder then goes from their shelf.
 */
export async function removeMember(
  collectionId: string,
  userId: string,
): Promise<void> {
  const refusal = readOnlyError();
  if (refusal) throw refusal;
  const response = await queue(() => api.removeCollectionMember(collectionId, userId));
  if ("left" in response.data) {
    const current = snapshot();
    apply({
      ...current,
      collections: current.collections.filter(
        (collection) => collection.id !== collectionId,
      ),
    });
    return;
  }
  replace(response.data);
}

/** Make a folder from before there were owners the reader's, and private. */
export async function claimCollection(collectionId: string): Promise<void> {
  const refusal = readOnlyError();
  if (refusal) throw refusal;
  const response = await queue(() => api.claimCollection(collectionId));
  replace(response.data);
}

/** Which collections already hold a record — what the "add" menu ticks. */
export function holdersOf(
  workspace: Workspace,
  kind: ItemKind,
  id: string,
): Set<string> {
  const holders = new Set<string>();
  for (const collection of workspace.collections) {
    if (collection.items.some((item) => item.kind === kind && item.id === id)) {
      holders.add(collection.id);
    }
  }
  return holders;
}

/** Every item of one kind in a collection, in the order they were filed. */
export function itemsOfKind(collection: Collection, kind: ItemKind): CollectionItem[] {
  return collection.items.filter((item) => item.kind === kind);
}

export function saveQuery(input: Omit<SavedQuery, "id" | "created_at">): SavedQuery {
  const query: SavedQuery = {
    ...input,
    name: input.name.trim() || "Untitled query",
    id: `qry-${newId()}`,
    created_at: now(),
  };
  if (refused()) return query;

  const current = snapshot();
  apply({ ...current, queries: [query, ...current.queries] });
  push(() =>
    api.saveQuery({
      id: query.id,
      name: query.name,
      kind: query.kind,
      path: query.path,
      // Undefined values do not survive JSON, and the API takes an object of
      // set filters — which is what an unset filter not being there means.
      search: JSON.parse(JSON.stringify(query.search)) as Record<string, unknown>,
      summary: query.summary,
    }),
  );
  return query;
}

export function renameQuery(id: string, name: string): void {
  if (refused()) return;
  const current = snapshot();
  const trimmed = name.trim();
  if (!trimmed) return;
  apply({
    ...current,
    queries: current.queries.map((query) =>
      query.id === id ? { ...query, name: trimmed } : query,
    ),
  });
  push(() => api.renameQuery(id, trimmed));
}

export function deleteQuery(id: string): void {
  if (refused()) return;
  const current = snapshot();
  apply({ ...current, queries: current.queries.filter((query) => query.id !== id) });
  push(() => api.deleteSavedQuery(id));
}

/**
 * The whole shelf as a file, and back again.
 *
 * Still here with a server-kept shelf, and still worth having: the export is
 * how a shelf moves between deployments, how it is backed up before somebody
 * empties a database, and how a browser-kept one is carried to a deployment
 * that will give it a URL.
 */
export function exportWorkspace(): string {
  return JSON.stringify(snapshot(), null, 2);
}

/** Merge an exported shelf into this one. Returns what arrived. */
export function importWorkspace(raw: string): {
  collections: number;
  queries: number;
} {
  if (refused()) return { collections: 0, queries: 0 };

  const incoming = parse(raw);
  const current = snapshot();

  const heldCollections = new Set(current.collections.map((entry) => entry.id));
  const heldQueries = new Set(current.queries.map((entry) => entry.id));

  const collections = incoming.collections.filter(
    (entry) => !heldCollections.has(entry.id),
  );
  const queries = incoming.queries.filter((entry) => !heldQueries.has(entry.id));

  if (collections.length || queries.length) {
    apply({
      version: VERSION,
      collections: [...collections, ...current.collections],
      queries: [...queries, ...current.queries],
    });
    // The API merges by the same rule, so the whole file goes over rather than
    // the part this browser happened not to have: two tabs importing the same
    // file must not produce two shelves.
    push(() =>
      api.importShelf({
        collections: incoming.collections,
        queries: incoming.queries,
      }),
    );
  }

  return { collections: collections.length, queries: queries.length };
}

/**
 * How a set of filters reads on a card.
 *
 * The parameters themselves, not a sentence about them: a reader who saved two
 * queries over the same indicator needs to see which one had the period bound,
 * and any prose long enough to say that is longer than the parameters are.
 */
export function describeSearch(search: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(search)) {
    if (value === undefined || value === "" || key === "page") continue;
    const text = Array.isArray(value)
      ? value.length > 2
        ? `${value.slice(0, 2).join(", ")} +${value.length - 2}`
        : value.join(", ")
      : String(value);
    parts.push(`${key}: ${text}`);
  }
  return parts.join(" · ") || "No filters";
}
