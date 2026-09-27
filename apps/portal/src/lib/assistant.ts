/**
 * The assistant: asking the catalogue in words.
 *
 * The API answers `POST /v1/assistant/chat` with a stream of its own small
 * events (see services/api/internal/httpapi/assistant.go) — which conversation
 * this is, what the answer was allowed to use, then the reply a piece at a
 * time.
 *
 * Where the deployment keeps conversations (`assistant_history`), each chat is
 * a UUID on the server, which records every turn itself: the portal sends the
 * new question and nothing else, and a chat's URL reopens it anywhere. Where
 * it does not, the portal sends the whole conversation each time and keeps it
 * in this browser.
 */

import { Marked, type Tokens } from "marked";

import { ApiRequestError, apiBaseUrl, type ApiError } from "~/lib/api";
import type { NewItem } from "~/lib/workspace";

export type ChatRole = "user" | "assistant";

/**
 * A chart the assistant drew beside a reply: the series it chose, read by the
 * server at one granularity and lined up period by period.
 */
export type ChartKind = "line" | "dual_axis" | "indexed" | "scatter" | "bar";

export type ChartSpec = {
  kind: ChartKind;
  title: string;
  /** Why this kind of chart, in the reader's language. */
  reason?: string;
  granularity: "month" | "quarter" | "year";
  periods: string[];
  series: {
    id: string;
    label: string;
    unit?: string;
    /** A short name for the headline and the key figures. */
    short?: string;
    /** The place or commodity drawn, where the series has several. */
    member?: string;
    /** Who publishes the figures. Absent on charts kept from before. */
    source?: string;
    /** One per period; null where the period has no figure. */
    values: (number | null)[];
  }[];
  /** Pearson's r where there are two series, and over how many periods. */
  correlation?: number;
  overlap?: number;
  /**
   * What the chart shows, worked out by the server from the same figures:
   * the headline finding, each series' key figure, and the few points worth
   * marking. Absent on charts kept from before it was sent.
   */
  story?: ChartStory;
};

export type ChartStory = {
  language: "id" | "en";
  headline: string;
  figures: {
    /** Index into the chart's series. */
    series: number;
    last: number;
    last_period: string;
    change: number;
    change_unit: "percent" | "points";
    from: string;
    /** As the headline writes them, so the two never round differently. */
    last_text: string;
    change_text: string;
  }[];
  annotations?: ChartAnnotation[];
};

export type ChartAnnotation = {
  /** Index into the chart's series, and into its periods. */
  series: number;
  index: number;
  kind: "peak" | "low" | "jump" | "drop";
  label: string;
};

/**
 * A chart the assistant proposes before drawing: which series, for which part
 * of the question, and which kind of chart and why. The reader confirms it,
 * or unticks a series first, and only then is it drawn.
 */
export type ChartProposal = {
  proposed: true;
  kind: ChartKind;
  title: string;
  reason?: string;
  granularity: ChartSpec["granularity"];
  from: string;
  to: string;
  series: {
    id: string;
    label: string;
    short?: string;
    unit?: string;
    member?: string;
    source?: string;
    /** The part of the question it stands for, in the reader's words. */
    for?: string;
    from: string;
    to: string;
  }[];
  /** The reader's message when they confirm, in their language. */
  confirm_text: string;
};

/** The reader's answer to a proposal: the series to draw. */
export type ChartConfirm = {
  series: string[];
  members: Record<string, string>;
  names: Record<string, string>;
  kind: ChartKind;
  title?: string;
  reason?: string;
};

export type ChatMessage = {
  role: ChatRole;
  content: string;
  /** Its number in a conversation the server keeps. */
  seq?: number;
  /** Set on a reply that stopped before it finished. */
  error?: string;
  /** The collection this reply's suggestions were saved to, once they were. */
  collectionId?: string;
  /**
   * What the server let this reply link to, as `kind:id`. A record link that
   * is not in here was made up by the model, and is shown as text. Absent on
   * replies kept from before the server sent it.
   */
  sources?: string[];
  /** The chart drawn beside this reply, where the reader confirmed one. */
  chart?: ChartSpec;
  /** The chart this reply proposes, awaiting the reader's confirmation. */
  proposal?: ChartProposal;
};

export type Conversation = {
  id: string;
  title: string;
  messages: ChatMessage[];
  updatedAt: number;
};

/** A conversation in the history list, without its turns. */
export type ChatSummary = { id: string; title: string; updatedAt: number };

type StreamEvent =
  | { type: "conversation"; id: string; title: string }
  | { type: "sources"; sources: { kind: string; id: string }[] }
  | { type: "chart"; chart: ChartSpec }
  | { type: "proposal"; proposal: ChartProposal }
  | { type: "delta"; text: string }
  /** The server stopped the reply and put this in place of all of it. */
  | { type: "replace"; text: string }
  | { type: "error"; message: string }
  | { type: "done" };

/** What a turn is asked with: a recorded conversation, or the whole of one. */
export type TurnRequest =
  | { conversationId?: string; message: string; confirm?: ChartConfirm }
  | { conversationId: string; regenerate: true }
  | { messages: ChatMessage[]; confirm?: ChartConfirm };

export type TurnCallbacks = {
  onText: (text: string) => void;
  onConversation?: (id: string, title: string) => void;
  onSources?: (sources: string[]) => void;
  onChart?: (chart: ChartSpec) => void;
  onProposal?: (proposal: ChartProposal) => void;
  onReplace?: (text: string) => void;
};

/**
 * Ask, and hear the reply as it is written.
 *
 * Resolves when the reply is finished; rejects with an ApiRequestError when the
 * API refused the question outright (too many, no model, no such chat).
 */
export async function streamTurn(
  turn: TurnRequest,
  callbacks: TurnCallbacks,
  signal?: AbortSignal,
): Promise<{ error?: string }> {
  const body =
    "messages" in turn
      ? {
          messages: turn.messages.map(({ role, content }) => ({ role, content })),
          confirm: turn.confirm,
        }
      : "regenerate" in turn
        ? { conversation_id: turn.conversationId, regenerate: true }
        : {
            conversation_id: turn.conversationId,
            message: turn.message,
            confirm: turn.confirm,
          };

  const response = await fetch(new URL("/v1/assistant/chat", apiBaseUrl), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    credentials: "include",
    signal,
    body: JSON.stringify(body),
  });

  if (!response.ok || !response.body) {
    throw await requestError(response);
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let failure: string | undefined;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    // Events end at a blank line; the last piece may be half of one.
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const raw of events) {
      const line = raw.split("\n").find((part) => part.startsWith("data:"));
      if (!line) continue;
      const event = JSON.parse(line.slice(5)) as StreamEvent;
      if (event.type === "delta") callbacks.onText(event.text);
      else if (event.type === "conversation")
        callbacks.onConversation?.(event.id, event.title);
      else if (event.type === "replace") callbacks.onReplace?.(event.text);
      else if (event.type === "chart") callbacks.onChart?.(event.chart);
      else if (event.type === "proposal") callbacks.onProposal?.(event.proposal);
      else if (event.type === "sources")
        callbacks.onSources?.(
          event.sources.map((source) => `${source.kind}:${source.id}`),
        );
      else if (event.type === "error") failure = event.message;
    }
  }
  return { error: failure };
}

async function requestError(response: Response): Promise<ApiRequestError> {
  let error: ApiError = { code: "unknown", message: response.statusText };
  try {
    error = ((await response.json()) as { error?: ApiError }).error ?? error;
  } catch {
    // Not the envelope — a proxy's page, most likely. The status says enough.
  }
  return new ApiRequestError(response.status, error);
}

// ---- conversations the server keeps ----------------------------------------

type ServerMessage = {
  seq: number;
  role: ChatRole;
  content: string;
  error?: string;
  sources?: string[];
  collection_id?: string;
  /** A drawn chart, or a proposal for one (`proposed: true`). */
  chart?: ChartSpec | ChartProposal;
};

type ServerConversation = {
  id: string;
  title: string;
  updated_at: string;
  messages?: ServerMessage[];
};

async function conversationsApi<T>(
  path: string,
  init?: RequestInit,
): Promise<T | null> {
  const response = await fetch(
    new URL(`/v1/assistant/conversations${path}`, apiBaseUrl),
    {
      credentials: "include",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      ...init,
    },
  );
  if (response.status === 404) return null;
  if (!response.ok) throw await requestError(response);
  if (response.status === 204) return null;
  return ((await response.json()) as { data: T }).data;
}

/** The signed-in reader's chats, the latest first. */
export async function fetchConversations(): Promise<ChatSummary[]> {
  const list = (await conversationsApi<ServerConversation[]>("")) ?? [];
  return list.map((c) => ({
    id: c.id,
    title: c.title,
    updatedAt: Date.parse(c.updated_at),
  }));
}

/** One chat whole, or null where it does not exist or is not the reader's. */
export async function fetchConversation(id: string): Promise<Conversation | null> {
  const c = await conversationsApi<ServerConversation>(`/${encodeURIComponent(id)}`);
  if (!c) return null;
  return {
    id: c.id,
    title: c.title,
    updatedAt: Date.parse(c.updated_at),
    messages: (c.messages ?? []).map((m) => ({
      seq: m.seq,
      role: m.role,
      content: m.content,
      error: m.error || undefined,
      sources: m.sources ?? [],
      collectionId: m.collection_id || undefined,
      chart: m.chart && !("proposed" in m.chart) ? m.chart : undefined,
      proposal: m.chart && "proposed" in m.chart ? m.chart : undefined,
    })),
  };
}

export async function deleteConversation(id: string): Promise<void> {
  await conversationsApi(`/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** Record the collection a reply was saved into, so it is there on reopening. */
export async function recordCollection(
  id: string,
  seq: number,
  collectionId: string,
): Promise<void> {
  await conversationsApi(`/${encodeURIComponent(id)}/messages/${seq}`, {
    method: "PATCH",
    body: JSON.stringify({ collection_id: collectionId }),
  });
}

// ---- chats this browser started without an account -------------------------

const RECENT_KEY = "terusan.assistant.recent";

/**
 * The chats this browser started signed out. They live on the server; only
 * their ids and names are kept here, since the server lists nobody's chats to
 * a reader without an account.
 */
export function loadRecent(): ChatSummary[] {
  try {
    const stored = JSON.parse(
      localStorage.getItem(RECENT_KEY) ?? "[]",
    ) as ChatSummary[];
    return Array.isArray(stored) ? stored : [];
  } catch {
    return [];
  }
}

export function rememberRecent(summary: ChatSummary): ChatSummary[] {
  const next = [summary, ...loadRecent().filter((c) => c.id !== summary.id)].slice(
    0,
    50,
  );
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    // Storage blocked: the chat still has its URL.
  }
  return next;
}

export function forgetRecent(id: string): ChatSummary[] {
  const next = loadRecent().filter((c) => c.id !== id);
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    // As above.
  }
  return next;
}

/** What a failed question tells the reader. */
export function describeChatError(error: unknown): string {
  if (error instanceof ApiRequestError) {
    if (error.status === 401) return "Sign in to ask the assistant.";
    if (error.status === 404) return "This chat no longer exists. Start a new one.";
    if (error.status === 429)
      return `Too many questions — ${error.error.detail ?? "wait a minute"}.`;
    if (error.status === 403)
      return "The assistant is not switched on for this deployment.";
    return error.message;
  }
  return "The assistant could not be reached. Check your connection and try again.";
}

// ---- conversations kept wholly in this browser -----------------------------
//
// Where the deployment keeps none.

const STORAGE_KEY = "terusan.assistant.conversations";
const MAX_KEPT = 50;

export function loadConversations(): Conversation[] {
  try {
    const stored = JSON.parse(
      localStorage.getItem(STORAGE_KEY) ?? "[]",
    ) as Conversation[];
    return Array.isArray(stored) ? stored : [];
  } catch {
    return [];
  }
}

export function saveConversations(conversations: Conversation[]): void {
  try {
    const kept = [...conversations]
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, MAX_KEPT);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(kept));
  } catch {
    // Storage full or blocked: the conversation still works, it is only not kept.
  }
}

export function newConversationId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** A conversation's name in the list: its first question, cut short. */
export function titleFor(question: string): string {
  const flat = question.replace(/\s+/g, " ").trim();
  return flat.length > 60 ? `${flat.slice(0, 57)}…` : flat;
}

// ---- rendering a reply -----------------------------------------------------

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * The record a link on this site points at, as `kind:id`, or null for a page
 * that is not one record (a search, the explorer, a commodity's listing).
 */
function recordOf(href: string): string | null {
  const match = /^\/(datasets|indicators|regulations)\/([^/?#]+)$/.exec(href);
  if (!match) return null;
  const kind = {
    datasets: "dataset",
    indicators: "indicator",
    regulations: "regulation",
  }[match[1] as "datasets" | "indicators" | "regulations"];
  return `${kind}:${decodeURIComponent(match[2]!)}`;
}

/** What the link renderer checks record links against, for the current parse. */
let allowedRecords: Set<string> | null = null;

/**
 * Markdown for a reply, with the model kept out of the page.
 *
 * Raw HTML is shown as text rather than rendered, and images are dropped. A
 * link goes only to a page on this site: an outside address in a reply is one
 * the model was talked into writing, since nothing it is given has one. And a
 * link to a record the server did not offer is shown as its words alone — the
 * model invented it, and a link that opens a 404 or the wrong series reads as
 * the portal vouching for it.
 */
const markdown = new Marked({
  gfm: true,
  breaks: true,
  renderer: {
    html({ text }: Tokens.HTML | Tokens.Tag) {
      return escapeHtml(text);
    },
    image({ text }: Tokens.Image) {
      return escapeHtml(text);
    },
    link(
      this: { parser: { parseInline(tokens: Tokens.Generic[]): string } },
      token: Tokens.Link,
    ) {
      const label = this.parser.parseInline(token.tokens);
      const href = token.href.trim();
      if (!href.startsWith("/") || href.startsWith("//")) return label;
      const record = recordOf(href);
      if (record && allowedRecords && !allowedRecords.has(record)) {
        return `<span class="unverified-link" title="Not found in the catalogue">${label}</span>`;
      }
      return `<a href="${escapeHtml(href)}" data-internal="true">${label}</a>`;
    },
  },
});

export function renderReply(text: string, sources?: string[]): string {
  allowedRecords = sources ? new Set(sources) : null;
  try {
    return markdown.parse(text, { async: false }) as string;
  } finally {
    allowedRecords = null;
  }
}

// ---- what a reply suggested ------------------------------------------------

/**
 * The records a reply links to, as collection items, in the order it gave them.
 *
 * Read off the links rather than asked of the model: every suggestion is a
 * link by instruction, and the link carries the identifier the collection
 * needs. A commodity is filed under its printed name, which is what its link
 * searches for.
 */
export function suggestedItems(reply: string, sources?: string[]): NewItem[] {
  const allowed = sources ? new Set(sources) : null;
  const items: NewItem[] = [];
  const seen = new Set<string>();
  const link = /\[([^\]]+)\]\((\/[^)\s]+)\)/g;
  for (const [, rawLabel, href] of reply.matchAll(link)) {
    const label = rawLabel!.replace(/[*_`]/g, "").trim();
    let item: NewItem | null = null;
    const record = /^\/(datasets|indicators|regulations)\/([^/?#]+)$/.exec(href!);
    if (record) {
      const kinds = {
        datasets: "dataset",
        indicators: "indicator",
        regulations: "regulation",
      } as const;
      item = {
        kind: kinds[record[1] as keyof typeof kinds],
        id: decodeURIComponent(record[2]!),
        label,
      };
    } else if (href!.startsWith("/commodities?")) {
      const name = new URLSearchParams(href!.slice("/commodities?".length)).get("q");
      if (name) item = { kind: "commodity", id: name, label: name };
    }
    if (!item) continue;
    const key = `${item.kind}:${item.id}`;
    // Only what the server offered: a made-up record is not worth keeping.
    if (allowed && item.kind !== "commodity" && !allowed.has(key)) continue;
    if (seen.has(key)) continue;
    seen.add(key);
    items.push(item);
  }
  return items;
}

/** A message that says yes to a proposal, typed rather than clicked. */
export function confirmsProposal(text: string): boolean {
  return /^(ya|iya|yap|oke|ok|okay|lanjut|lanjutkan|boleh|setuju|silakan|yes|yep|sure|go ahead|draw it)\b[\s.!,]*(buat(kan)?( grafik(nya)?| chart(nya)?)?)?[\s.!]*$/i.test(
    text.trim(),
  );
}

/** The confirmation for a proposal, with the series the reader kept. */
export function confirmFor(proposal: ChartProposal, keep: string[]): ChartConfirm {
  const kept = proposal.series.filter((s) => keep.includes(s.id));
  const members: Record<string, string> = {};
  const names: Record<string, string> = {};
  for (const s of kept) {
    if (s.member) members[s.id] = s.member;
    if (s.short) names[s.id] = s.short;
  }
  return {
    series: kept.map((s) => s.id),
    members,
    names,
    kind: proposal.kind,
    // The title names every proposed series; with one dropped the server
    // names the chart from what is left.
    title: kept.length === proposal.series.length ? proposal.title : undefined,
    reason: proposal.reason,
  };
}
