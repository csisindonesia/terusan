import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate, useRouter } from "@tanstack/react-router";
import {
  IconArrowRight,
  IconArrowUp,
  IconCheck,
  IconCopy,
  IconFolderPlus,
  IconHistory,
  IconPlayerStopFilled,
  IconPlus,
  IconRefresh,
  IconSparkles,
  IconTrash,
} from "@tabler/icons-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type RefObject,
} from "react";

import { AssistantChart } from "~/components/assistant-chart";
import { ChartProposalCard } from "~/components/chart-proposal";
import { CollectButton } from "~/components/collect-button";
import { Button } from "~/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "~/components/ui/tooltip";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "~/components/ui/sheet";
import {
  confirmFor,
  confirmsProposal,
  deleteConversation,
  describeChatError,
  fetchConversation,
  fetchConversations,
  forgetRecent,
  loadConversations,
  loadRecent,
  newConversationId,
  recordCollection,
  rememberRecent,
  renderReply,
  saveConversations,
  streamTurn,
  suggestedItems,
  titleFor,
  type ChartConfirm,
  type ChatMessage,
  type ChatSummary,
  type Conversation,
  type TurnRequest,
} from "~/lib/assistant";
import { capabilitiesQuery, useUser } from "~/lib/session";
import {
  addToCollection,
  createCollection,
  useShelf,
  useWorkspace,
  type NewItem,
} from "~/lib/workspace";
import { cn } from "~/lib/utils";

/**
 * The assistant, at `/assistant` for a new chat and `/assistant/<uuid>` for one
 * already started. One route with an optional id rather than two, so the page
 * stays mounted when a new chat is given its id mid-reply and the stream
 * carries on.
 */
export const Route = createFileRoute("/assistant/{-$chatId}")({
  head: () => ({ meta: [{ title: "Assistant — Terusan" }] }),
  component: Assistant,
});

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Where a first-time reader starts. Each is a question the catalogue can
 * actually answer today, so the first reply shows what the thing is for.
 */
const STARTERS = [
  {
    title: "Inflation and rice prices",
    prompt:
      "What do you have on inflation and rice prices, and how far back does it go?",
  },
  {
    title: "Palm oil exports",
    prompt: "Can I compare palm oil exports against the world price?",
  },
  {
    title: "Data per provinsi",
    prompt: "Data apa saja yang tersedia per provinsi?",
  },
  {
    title: "Regional budgets",
    prompt: "Which datasets cover regional government budgets (APBD)?",
  },
];

type Chat = { id: string | null; title: string; messages: ChatMessage[] };
const EMPTY: Chat = { id: null, title: "", messages: [] };

function Assistant() {
  const capabilities = useQuery(capabilitiesQuery);
  const user = useUser();
  const router = useRouter();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { chatId } = Route.useParams();

  // Kept on the server, or wholly in this browser. Unknown until the
  // deployment has said which.
  const recorded = capabilities.data?.assistant_history === true;
  const ready = capabilities.isSuccess;

  const [chat, setChat] = useState<Chat>(EMPTY);
  // The id the page is showing, read synchronously: a new chat is given its
  // id by the first event of its stream, and the URL changing to it must not
  // look like a request to load another chat.
  const shownRef = useRef<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const pinnedRef = useRef(true);

  // ---- the history list ---------------------------------------------------

  // Read after mount: the page is rendered on the server first, which has no
  // localStorage, and a list that differs between the two will not hydrate.
  const [localChats, setLocalChats] = useState<Conversation[]>([]);
  const [recent, setRecent] = useState<ChatSummary[]>([]);
  useEffect(() => {
    setLocalChats(loadConversations());
    setRecent(loadRecent());
  }, []);
  const accountChats = useQuery({
    queryKey: ["assistant", "conversations", user?.id],
    queryFn: fetchConversations,
    enabled: recorded && !!user,
  });

  const summaries: ChatSummary[] = useMemo(() => {
    const list = !recorded
      ? localChats.map(({ id, title, updatedAt }) => ({ id, title, updatedAt }))
      : user
        ? (accountChats.data ?? [])
        : recent;
    return [...list].sort((a, b) => b.updatedAt - a.updatedAt);
  }, [recorded, user, localChats, recent, accountChats.data]);

  function listed(summary: ChatSummary) {
    if (!recorded) return;
    if (user)
      void queryClient.invalidateQueries({ queryKey: ["assistant", "conversations"] });
    else setRecent(rememberRecent(summary));
  }

  // ---- opening the chat the URL names --------------------------------------

  useEffect(() => {
    if (!ready) return;
    const id = chatId && UUID.test(chatId) ? chatId : null;
    if (id === shownRef.current) return;
    abortRef.current?.abort();
    shownRef.current = id;
    setMissing(false);
    if (!id) {
      setChat(EMPTY);
      return;
    }
    if (!recorded) {
      const found = loadConversations().find((c) => c.id === id);
      if (found) setChat({ id, title: found.title, messages: found.messages });
      else {
        setChat(EMPTY);
        setMissing(true);
      }
      return;
    }
    setLoading(true);
    fetchConversation(id)
      .then((found) => {
        if (shownRef.current !== id) return;
        if (found) setChat({ id, title: found.title, messages: found.messages });
        else {
          setChat(EMPTY);
          setMissing(true);
        }
      })
      .catch(() => {
        if (shownRef.current === id) setMissing(true);
      })
      .finally(() => setLoading(false));
  }, [chatId, ready, recorded]);

  // Follow the reply as it grows, unless the reader has scrolled up to read.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [chat.messages]);

  // ---- changing the chat ---------------------------------------------------

  /** Change the chat on screen, and keep it where this deployment keeps it. */
  function change(update: (current: Chat) => Chat) {
    setChat((current) => {
      const next = update(current);
      if (!recorded && next.id) {
        const kept = loadConversations().filter((c) => c.id !== next.id);
        const saved = [
          {
            id: next.id,
            title: next.title,
            messages: next.messages,
            updatedAt: Date.now(),
          },
          ...kept,
        ];
        saveConversations(saved);
        setLocalChats(saved);
      }
      return next;
    });
  }

  const patchLast = (patch: (last: ChatMessage) => ChatMessage) =>
    change((current) => {
      const messages = [...current.messages];
      messages[messages.length - 1] = patch(messages[messages.length - 1]!);
      return { ...current, messages };
    });

  function openUrl(id: string) {
    void navigate({
      to: "/assistant/{-$chatId}",
      params: { chatId: id },
      replace: true,
    });
  }

  async function ask(turn: TurnRequest, shown: ChatMessage[]) {
    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    pinnedRef.current = true;
    // The empty reply the stream writes into.
    change((current) => ({
      ...current,
      messages: [...shown, { role: "assistant", content: "" }],
    }));

    let id = shownRef.current;
    try {
      const { error } = await streamTurn(
        turn,
        {
          onText: (text) =>
            patchLast((last) => ({ ...last, content: last.content + text })),
          onSources: (sources) => patchLast((last) => ({ ...last, sources })),
          onChart: (chart) => patchLast((last) => ({ ...last, chart })),
          onProposal: (proposal) => patchLast((last) => ({ ...last, proposal })),
          onReplace: (text) =>
            patchLast((last) => ({
              ...last,
              content: text,
              sources: [],
              chart: undefined,
            })),
          onConversation: (conversationId, title) => {
            id = conversationId;
            if (shownRef.current !== conversationId) {
              // A new chat, named by the server: its URL from now on.
              shownRef.current = conversationId;
              change((current) => ({ ...current, id: conversationId, title }));
              openUrl(conversationId);
            }
            listed({ id: conversationId, title, updatedAt: Date.now() });
          },
        },
        controller.signal,
      );
      if (error) patchLast((last) => ({ ...last, error }));
    } catch (error) {
      if (controller.signal.aborted) {
        patchLast((last) => (last.content ? last : { ...last, error: "Stopped." }));
      } else {
        patchLast((last) => ({ ...last, error: describeChatError(error) }));
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setStreaming(false);
      inputRef.current?.focus();
    }

    // What the server recorded, numbered: a reply is saved to a collection
    // by its number, and this is the copy another browser would open.
    if (recorded && id && shownRef.current === id) {
      const saved = await fetchConversation(id).catch(() => null);
      if (saved && shownRef.current === id) {
        setChat({ id, title: saved.title, messages: saved.messages });
      }
    }
  }

  function send(text: string, confirm?: ChartConfirm) {
    const question = text.trim();
    if (!question || streaming || missing) return;
    // "ya" or "lanjut" beneath a proposal is the same as its button, with
    // every series kept.
    const last = chat.messages[chat.messages.length - 1];
    if (!confirm && last?.proposal && confirmsProposal(question)) {
      confirm = confirmFor(
        last.proposal,
        last.proposal.series.map((s) => s.id),
      );
    }
    setDraft("");
    const shown = [
      // A failed reply is not sent back as if it were one.
      ...chat.messages.filter((m) => !(m.role === "assistant" && !m.content)),
      { role: "user" as const, content: question },
    ];

    if (recorded) {
      void ask(
        { conversationId: chat.id ?? undefined, message: question, confirm },
        shown,
      );
      return;
    }
    // Kept in this browser: the id is made here, and the whole conversation
    // goes with every question.
    if (!chat.id) {
      const id = newConversationId();
      shownRef.current = id;
      change(() => ({ id, title: titleFor(question), messages: shown }));
      openUrl(id);
    }
    void ask({ messages: shown, confirm }, shown);
  }

  function regenerate() {
    if (!chat.id || streaming) return;
    const lastQuestion = chat.messages.map((m) => m.role).lastIndexOf("user");
    if (lastQuestion < 0) return;
    const shown = chat.messages.slice(0, lastQuestion + 1);
    void ask(
      recorded ? { conversationId: chat.id, regenerate: true } : { messages: shown },
      shown,
    );
  }

  function startNew() {
    abortRef.current?.abort();
    setDraft("");
    setHistoryOpen(false);
    void navigate({ to: "/assistant/{-$chatId}", params: { chatId: undefined } });
    inputRef.current?.focus();
  }

  async function remove(id: string) {
    if (id === chat.id) startNew();
    if (!recorded) {
      const kept = loadConversations().filter((c) => c.id !== id);
      saveConversations(kept);
      setLocalChats(kept);
      return;
    }
    await deleteConversation(id).catch(() => undefined);
    if (user)
      void queryClient.invalidateQueries({ queryKey: ["assistant", "conversations"] });
    else setRecent(forgetRecent(id));
  }

  function saved(index: number, collectionId: string) {
    const message = chat.messages[index];
    change((current) => ({
      ...current,
      messages: current.messages.map((m, i) =>
        i === index ? { ...m, collectionId } : m,
      ),
    }));
    if (recorded && chat.id && message?.seq !== undefined) {
      void recordCollection(chat.id, message.seq, collectionId).catch(() => undefined);
    }
  }

  // Links in a reply go through the router, so following one keeps the page
  // state rather than reloading the whole portal.
  function followLink(event: MouseEvent<HTMLDivElement>) {
    const anchor = (event.target as HTMLElement).closest("a[data-internal]");
    if (!anchor || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    // Through history rather than `navigate`, which takes a path and would
    // drop a link's query string (`/commodities?q=Red%20chili`).
    router.history.push(anchor.getAttribute("href")!);
  }

  const unavailable = capabilities.data && !capabilities.data.assistant;
  const messages = chat.messages;

  const history = (
    <HistoryList
      conversations={summaries}
      activeId={chat.id}
      onPick={(id) => {
        setHistoryOpen(false);
        void navigate({ to: "/assistant/{-$chatId}", params: { chatId: id } });
      }}
      onRemove={(id) => void remove(id)}
      note={
        recorded
          ? user
            ? "Your chats are kept with your account."
            : "Chats open from their link. Sign in to keep them with your account."
          : "Your chats are kept in this browser."
      }
    />
  );

  return (
    // The whole height below the breadcrumb, so the composer stays put and
    // only the conversation scrolls.
    <div className="-mx-4 -my-6 flex h-[calc(100svh-var(--app-header)-3rem)] sm:-mx-6">
      <aside className="hidden w-64 shrink-0 flex-col md:flex">
        <div className="flex items-center justify-between py-3 pr-2 pl-4">
          <h2 className="text-sm font-medium">Chats</h2>
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="outline"
                  size="icon"
                  className="size-8"
                  aria-label="New chat"
                  onClick={startNew}
                >
                  <IconPlus className="size-4" />
                </Button>
              }
            />
            <TooltipContent>New chat</TooltipContent>
          </Tooltip>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">{history}</div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-1 px-3 pt-2 md:hidden">
          <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
            <SheetTrigger
              render={
                <Button variant="ghost" size="icon" aria-label="Chat history">
                  <IconHistory />
                </Button>
              }
            />
            <SheetContent side="left" className="w-72 gap-0 p-0">
              <SheetHeader className="border-b p-4">
                <SheetTitle>Chats</SheetTitle>
              </SheetHeader>
              <div className="overflow-y-auto p-2">{history}</div>
            </SheetContent>
          </Sheet>
          <Button variant="ghost" size="icon" aria-label="New chat" onClick={startNew}>
            <IconPlus />
          </Button>
        </div>

        <div
          ref={scrollRef}
          className="min-h-0 flex-1 overflow-y-auto"
          onScroll={(event) => {
            const el = event.currentTarget;
            pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          }}
        >
          {missing ? (
            <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center gap-3 px-4 text-center">
              <p className="font-medium">This chat can't be found</p>
              <p className="text-sm text-muted-foreground">
                It was deleted, or it belongs to another account.
              </p>
              <Button variant="outline" size="sm" onClick={startNew}>
                <IconPlus className="size-4" />
                New chat
              </Button>
            </div>
          ) : loading && messages.length === 0 ? (
            <p className="mt-10 text-center text-sm text-muted-foreground">
              Opening chat…
            </p>
          ) : messages.length === 0 ? (
            <EmptyState onPick={send} disabled={!!unavailable} />
          ) : (
            <div
              className="mx-auto w-full max-w-3xl space-y-8 px-4 py-8"
              onClick={followLink}
            >
              {messages.map((message, index) => (
                <Message
                  key={message.seq ?? `i${index}`}
                  message={message}
                  question={messages[index - 1]?.content ?? ""}
                  pending={streaming && index === messages.length - 1}
                  isLast={index === messages.length - 1}
                  onRegenerate={regenerate}
                  onSaved={(collectionId) => saved(index, collectionId)}
                  canConfirm={!streaming && index === messages.length - 1}
                  confirmed={Boolean(messages[index + 2]?.chart)}
                  onConfirm={(keep) =>
                    message.proposal &&
                    send(
                      message.proposal.confirm_text,
                      confirmFor(message.proposal, keep),
                    )
                  }
                />
              ))}
            </div>
          )}
        </div>

        <div className="mx-auto w-full max-w-3xl px-4 pb-4">
          <Composer
            inputRef={inputRef}
            value={draft}
            onChange={setDraft}
            onSend={() => send(draft)}
            onStop={() => abortRef.current?.abort()}
            streaming={streaming}
            disabled={!!unavailable || missing}
          />
          <p className="mt-2 text-center text-xs text-muted-foreground">
            {unavailable
              ? "The assistant is not switched on for this deployment."
              : "The assistant suggests from this portal's catalogue and can be wrong. Check a series' page before citing it."}
          </p>
        </div>
      </section>
    </div>
  );
}

function EmptyState({
  onPick,
  disabled,
}: {
  onPick: (prompt: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col items-center justify-center px-4 py-10">
      <span className="mb-4 flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary">
        <IconSparkles className="size-6" />
      </span>
      <h1 className="font-heading text-center text-2xl font-semibold tracking-tight sm:text-3xl">
        What data are you looking for?
      </h1>
      <p className="mt-2 text-center text-sm text-muted-foreground">
        Ask in English or Bahasa Indonesia. Answers link to datasets and series in this
        portal.
      </p>
      <div className="mt-8 grid w-full gap-2 sm:grid-cols-2">
        {STARTERS.map((starter) => (
          <button
            key={starter.title}
            type="button"
            disabled={disabled}
            onClick={() => onPick(starter.prompt)}
            className="rounded-xl border bg-card p-3 text-left transition-colors hover:bg-muted/60 disabled:pointer-events-none disabled:opacity-50"
          >
            <div className="text-sm font-medium">{starter.title}</div>
            <div className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
              {starter.prompt}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function Message({
  message,
  question,
  pending,
  isLast,
  onRegenerate,
  onSaved,
  canConfirm,
  confirmed,
  onConfirm,
}: {
  message: ChatMessage;
  /** What this reply answers, which names the collection it is saved to. */
  question: string;
  pending: boolean;
  isLast: boolean;
  onRegenerate: () => void;
  onSaved: (collectionId: string) => void;
  canConfirm: boolean;
  confirmed: boolean;
  onConfirm: (keep: string[]) => void;
}) {
  const [copied, setCopied] = useState(false);
  const suggested = useMemo(
    () =>
      message.role === "assistant" && !pending
        ? suggestedItems(message.content, message.sources)
        : [],
    [message.role, message.content, message.sources, pending],
  );

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-3xl bg-muted px-5 py-2.5 text-[0.9375rem] leading-relaxed whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="group flex gap-4">
      <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full border text-primary">
        <IconSparkles className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        {message.chart ? (
          <AssistantChart chart={message.chart} className="mb-4" />
        ) : null}
        {message.content ? (
          <div
            className="markdown chat-reply"
            dangerouslySetInnerHTML={{
              __html: renderReply(message.content, message.sources),
            }}
          />
        ) : pending ? (
          <span className="mt-2 inline-block size-3 animate-pulse rounded-full bg-foreground" />
        ) : null}

        {message.proposal && !pending ? (
          <ChartProposalCard
            proposal={message.proposal}
            active={canConfirm}
            confirmed={confirmed}
            onConfirm={onConfirm}
          />
        ) : null}

        {message.error ? (
          <p className="mt-2 text-sm text-destructive">{message.error}</p>
        ) : null}

        {!pending && message.content ? (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <div
              className={cn(
                "flex gap-0.5 text-muted-foreground transition-opacity",
                !isLast && "opacity-0 group-hover:opacity-100 focus-within:opacity-100",
              )}
            >
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                aria-label="Copy"
                onClick={() => {
                  void navigator.clipboard.writeText(message.content);
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1500);
                }}
              >
                {copied ? (
                  <IconCheck className="size-4" />
                ) : (
                  <IconCopy className="size-4" />
                )}
              </Button>
              {isLast ? (
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8"
                  aria-label="Regenerate"
                  onClick={onRegenerate}
                >
                  <IconRefresh className="size-4" />
                </Button>
              ) : null}
            </div>

            {/* On the right of the same row, and always shown: unlike copy, it
              is what a reader scrolling back through replies is looking for. */}
            {suggested.length ? (
              <SaveSuggestions
                items={suggested}
                question={question}
                collectionId={message.collectionId}
                onSaved={onSaved}
              />
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Keep what a reply suggested.
 *
 * A reply is mostly a list of records, and the next thing a reader does with a
 * good list is keep it — so one click files every record it links to into a
 * new collection named after the question, and the button becomes the way to
 * that collection. Filing into one that already exists is the usual picker.
 */
function SaveSuggestions({
  items,
  question,
  collectionId,
  onSaved,
}: {
  items: NewItem[];
  question: string;
  collectionId?: string;
  onSaved: (collectionId: string) => void;
}) {
  const workspace = useWorkspace();
  const shelf = useShelf();
  // Deleted since, or saved in another browser's shelf: offer it again.
  const saved = collectionId
    ? workspace.collections.find((collection) => collection.id === collectionId)
    : undefined;

  // A shelf served read-only takes nothing, and a button that files into it
  // would only fail.
  if (shelf.mode === "server" && !shelf.writable) return null;

  function save() {
    const collection = createCollection(
      titleFor(question) || "Assistant suggestions",
      question ? `Suggested by the assistant for: ${question}` : undefined,
    );
    addToCollection(collection.id, items);
    onSaved(collection.id);
  }

  return (
    <div className="ml-auto flex items-center gap-1.5">
      {saved ? (
        <>
          <span className="max-w-64 truncate text-sm text-muted-foreground">
            Saved to <span className="font-medium text-foreground">{saved.name}</span>
          </span>
          <Button
            size="sm"
            variant="ghost"
            nativeButton={false}
            render={
              <Link
                to="/collections/$collectionId"
                params={{ collectionId: saved.id }}
              />
            }
          >
            Open collection
            <IconArrowRight className="size-4" />
          </Button>
        </>
      ) : (
        <>
          <CollectButton items={items} label="Add to existing" variant="ghost" />
          <Button size="sm" onClick={save}>
            <IconFolderPlus className="size-4" />
            Save to new collection
          </Button>
        </>
      )}
    </div>
  );
}

function Composer({
  inputRef,
  value,
  onChange,
  onSend,
  onStop,
  streaming,
  disabled,
}: {
  inputRef: RefObject<HTMLTextAreaElement | null>;
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  streaming: boolean;
  disabled: boolean;
}) {
  return (
    <div className="flex items-end gap-2 rounded-3xl border bg-card p-2 pl-4 shadow-sm focus-within:ring-2 focus-within:ring-ring/30">
      <textarea
        ref={inputRef}
        rows={1}
        value={value}
        disabled={disabled}
        autoFocus
        placeholder="Ask about datasets, series or sources…"
        aria-label="Message the assistant"
        className="field-sizing-content max-h-52 min-h-10 flex-1 resize-none bg-transparent py-2 text-[0.9375rem] outline-none placeholder:text-muted-foreground disabled:opacity-50"
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (
            event.key === "Enter" &&
            !event.shiftKey &&
            !event.nativeEvent.isComposing
          ) {
            event.preventDefault();
            onSend();
          }
        }}
      />
      {streaming ? (
        <Button
          size="icon"
          className="size-9 shrink-0 rounded-full"
          aria-label="Stop"
          onClick={onStop}
        >
          <IconPlayerStopFilled className="size-4" />
        </Button>
      ) : (
        <Button
          size="icon"
          className="size-9 shrink-0 rounded-full"
          aria-label="Send"
          disabled={disabled || !value.trim()}
          onClick={onSend}
        >
          <IconArrowUp className="size-4" />
        </Button>
      )}
    </div>
  );
}

function HistoryList({
  conversations,
  activeId,
  onPick,
  onRemove,
  note,
}: {
  conversations: ChatSummary[];
  activeId: string | null;
  onPick: (id: string) => void;
  onRemove: (id: string) => void;
  /** Where these are kept, said where the list is empty. */
  note: string;
}) {
  if (!conversations.length) {
    return <p className="px-2 py-1 text-xs text-muted-foreground">{note}</p>;
  }
  return (
    <ul className="space-y-0.5">
      {conversations.map((c) => (
        <li key={c.id} className="group relative">
          <button
            type="button"
            onClick={() => onPick(c.id)}
            className={cn(
              "w-full truncate rounded-lg px-2 py-2 pr-8 text-left text-sm transition-colors hover:bg-muted",
              c.id === activeId && "bg-muted font-medium",
            )}
          >
            {c.title}
          </button>
          <button
            type="button"
            aria-label={`Delete “${c.title}”`}
            onClick={() => onRemove(c.id)}
            className="absolute top-1/2 right-1 hidden -translate-y-1/2 rounded-md p-1 text-muted-foreground hover:text-foreground group-hover:block focus-visible:block"
          >
            <IconTrash className="size-3.5" />
          </button>
        </li>
      ))}
    </ul>
  );
}
