import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  IconAlertTriangle,
  IconExternalLink,
  IconSettings,
  IconTrash,
} from "@tabler/icons-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { z } from "zod";

import { resolverFor } from "~/components/article-blocks";
import {
  BlockEditor,
  savedBlocks,
  textsOf,
  uid,
  type Item,
} from "~/components/article-editor";
import { DashboardEditor } from "~/components/dashboard-editor";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "~/components/ui/popover";
import { ApiRequestError, api, type ArticleInput } from "~/lib/api";
import { tokensIn } from "~/lib/article-markdown";
import { formatRelative } from "~/lib/format";
import { useSessionState } from "~/lib/session";

export const Route = createFileRoute("/portal/edit/$slug")({
  // Which kind a new one starts as; an existing one keeps its own.
  validateSearch: z.object({ layout: z.enum(["article", "dashboard"]).optional() }),
  component: EditorPage,
});

type Meta = Omit<ArticleInput, "blocks">;

const BLANK: Meta = {
  slug: "",
  title: "",
  dek: "",
  publisher: "",
  published_on: "",
  status: "draft",
  layout: "article",
};

/** A title as a slug: `Rice After the Dry Season` → `rice-after-the-dry-season`. */
function slugify(title: string): string {
  return title
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

/** A borderless line that grows with its text: the title and the standfirst. */
function Grow({
  value,
  onChange,
  className,
  placeholder,
  onEnter,
  maxLength,
}: {
  value: string;
  onChange: (value: string) => void;
  className: string;
  placeholder: string;
  onEnter?: () => void;
  maxLength: number;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${node.scrollHeight}px`;
  }, [value]);
  return (
    <textarea
      ref={ref}
      rows={1}
      value={value}
      maxLength={maxLength}
      placeholder={placeholder}
      className={`block w-full resize-none overflow-hidden bg-transparent outline-none placeholder:text-muted-foreground/50 ${className}`}
      onChange={(e) => onChange(e.target.value.replace(/\n/g, " "))}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          onEnter?.();
        }
      }}
    />
  );
}

/**
 * Writing an article for the Portal, on the page itself: the title, the
 * standfirst and the blocks are edited where a reader will read them (see
 * components/article-editor.tsx). `slug` is `new` for one not yet saved.
 */
function EditorPage() {
  const { slug } = Route.useParams();
  const search = Route.useSearch();
  const isNew = slug === "new";
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { session, isLoading: sessionLoading } = useSessionState();

  const existing = useQuery({
    queryKey: ["articles", slug],
    queryFn: () => api.article(slug),
    enabled: !isNew,
  });
  const [meta, setMeta] = useState<Meta>({
    ...BLANK,
    layout: search.layout ?? "article",
  });
  const [items, setItems] = useState<Item[]>([
    { uid: uid(), block: { type: "text", markdown: "" } },
  ]);
  const [slugTouched, setSlugTouched] = useState(!isNew);
  const [loaded, setLoaded] = useState(isNew);
  const [dirty, setDirty] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [focus, setFocus] = useState<{
    uid: string;
    at: "start" | "end" | number;
  } | null>(null);

  useEffect(() => {
    const data = existing.data?.data;
    if (!data || loaded) return;
    setMeta({
      slug: data.slug,
      title: data.title,
      dek: data.dek ?? "",
      publisher: data.publisher ?? "",
      published_on: data.published_on ?? "",
      status: data.status,
      layout: data.layout ?? "article",
    });
    setItems(
      (data.blocks.length
        ? data.blocks
        : [{ type: "text", markdown: "" } as const]
      ).map((block) => ({ uid: uid(), block })),
    );
    setSavedAt(data.updated_at);
    setLoaded(true);
  }, [existing.data, loaded]);

  const change = (next: Item[]) => {
    setItems(next);
    setDirty(true);
  };
  const setField = (patch: Partial<Meta>) => {
    setMeta((m) => ({ ...m, ...patch }));
    setDirty(true);
  };

  const save = useMutation({
    mutationFn: (status: Meta["status"]) => {
      const body: ArticleInput = { ...meta, status, blocks: savedBlocks(items) };
      return isNew ? api.createArticle(body) : api.updateArticle(slug, body);
    },
    onSuccess: async (saved) => {
      setMeta((m) => ({ ...m, status: saved.data.status }));
      setDirty(false);
      setSavedAt(saved.data.updated_at);
      await queryClient.invalidateQueries({ queryKey: ["articles"] });
      await queryClient.invalidateQueries({ queryKey: ["showcase"] });
      if (saved.data.slug !== slug) {
        void navigate({
          to: "/portal/edit/$slug",
          params: { slug: saved.data.slug },
          replace: true,
        });
      }
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteArticle(slug),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["articles"] });
      await queryClient.invalidateQueries({ queryKey: ["showcase"] });
      void navigate({ to: "/portal" });
    },
  });
  const ready = Boolean(meta.title.trim() && meta.slug.trim());

  // ⌘S / Ctrl+S saves, as a draft stays a draft and a published one updates.
  const saveNow = useCallback(() => {
    if (ready && !save.isPending) save.mutate(meta.status);
  }, [ready, save, meta.status]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        saveNow();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [saveNow]);
  // Leaving with unsaved work asks first.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  // The figures, asked for once the writing pauses.
  const texts = [meta.dek ?? "", ...textsOf(items)];
  const textsKey = JSON.stringify(texts.filter((t) => tokensIn(t).length));
  const [settled, setSettled] = useState<string[]>([]);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(JSON.parse(textsKey) as string[]), 500);
    return () => clearTimeout(timer);
  }, [textsKey]);
  const figures = useQuery({
    queryKey: ["article-figures", settled],
    queryFn: () => api.articleFigures(settled),
    enabled: settled.length > 0,
    placeholderData: (previous) => previous,
  });
  const resolve = resolverFor(figures.data?.data ?? {});
  const footnotes = new Map<string, number>();
  items.forEach((i) => {
    if (i.block.type === "footnote") footnotes.set(i.uid, footnotes.size + 1);
  });

  if (sessionLoading || (!isNew && !loaded && existing.isLoading)) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  if (session?.user.role !== "admin") {
    return (
      <Alert>
        <IconAlertTriangle />
        <AlertDescription>
          Only an administrator can write articles.{" "}
          <Link to="/portal" className="underline underline-offset-4">
            Back to the Portal
          </Link>
        </AlertDescription>
      </Alert>
    );
  }
  if (!isNew && existing.error) {
    return (
      <p className="rounded-lg bg-destructive/5 px-4 py-3 text-sm">
        {(existing.error as Error).message}
      </p>
    );
  }

  const error = (save.error ?? remove.error) as Error | null;

  return (
    <div className="pb-24">
      <div className="sticky top-(--app-header) z-20 -mx-4 mb-8 flex flex-wrap items-center justify-between gap-2 border-b bg-background/95 px-4 py-2 backdrop-blur">
        <div className="flex min-w-0 items-center gap-2 text-sm text-muted-foreground">
          <Link to="/portal" className="hover:text-foreground">
            Portal
          </Link>
          <span>/</span>
          <span className="truncate text-foreground">{meta.title || "Untitled"}</span>
          <Badge variant={meta.status === "published" ? "secondary" : "outline"}>
            {meta.status === "published" ? "Published" : "Draft"}
          </Badge>
          <span className="hidden text-xs sm:inline">
            {save.isPending
              ? "Saving…"
              : dirty
                ? "Unsaved changes"
                : savedAt
                  ? `Saved ${formatRelative(savedAt)}`
                  : null}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {meta.status === "published" && !isNew ? (
            <Button
              size="sm"
              variant="ghost"
              render={<Link to="/portal/$slug" params={{ slug: meta.slug }} />}
            >
              <IconExternalLink className="size-4" />
              View
            </Button>
          ) : null}
          <Popover>
            <PopoverTrigger
              render={<Button size="sm" variant="ghost" aria-label="Page settings" />}
            >
              <IconSettings className="size-4" />
            </PopoverTrigger>
            <PopoverContent align="end" className="w-80 space-y-3">
              <div className="space-y-1">
                <span className="text-xs text-muted-foreground">Laid out as</span>
                <div className="flex gap-1">
                  {(["article", "dashboard"] as const).map((layout) => (
                    <Button
                      key={layout}
                      size="sm"
                      variant={meta.layout === layout ? "secondary" : "ghost"}
                      onClick={() => setField({ layout })}
                    >
                      {layout === "article" ? "An article" : "A dashboard"}
                    </Button>
                  ))}
                </div>
              </div>
              <label className="block space-y-1">
                <span className="text-xs text-muted-foreground">Address</span>
                <input
                  value={meta.slug}
                  className="h-8 w-full rounded-md border bg-transparent px-2 text-sm outline-none"
                  onChange={(e) => {
                    setSlugTouched(true);
                    setField({ slug: e.target.value });
                  }}
                />
                <span className="block text-xs text-muted-foreground">
                  /portal/{meta.slug || "…"}
                </span>
              </label>
              <label className="block space-y-1">
                <span className="text-xs text-muted-foreground">Who wrote it</span>
                <input
                  value={meta.publisher ?? ""}
                  placeholder="CSIS · Terusan"
                  className="h-8 w-full rounded-md border bg-transparent px-2 text-sm outline-none"
                  onChange={(e) => setField({ publisher: e.target.value })}
                />
              </label>
              <label className="block space-y-1">
                <span className="text-xs text-muted-foreground">Published</span>
                <input
                  type="date"
                  value={meta.published_on ?? ""}
                  className="h-8 w-full rounded-md border bg-transparent px-2 text-sm outline-none"
                  onChange={(e) => setField({ published_on: e.target.value })}
                />
              </label>
              {!isNew ? (
                <Button
                  size="sm"
                  variant="ghost"
                  className="w-full justify-start text-destructive"
                  onClick={() => {
                    if (
                      window.confirm(
                        `Delete “${meta.title}”? It leaves the Portal too.`,
                      )
                    )
                      remove.mutate();
                  }}
                >
                  <IconTrash className="size-4" />
                  Delete this article
                </Button>
              ) : null}
            </PopoverContent>
          </Popover>
          <Button
            size="sm"
            variant="outline"
            disabled={!ready || save.isPending}
            onClick={() => save.mutate("draft")}
          >
            {meta.status === "published" ? "Unpublish" : "Save draft"}
          </Button>
          <Button
            size="sm"
            disabled={!ready || save.isPending}
            onClick={() => save.mutate("published")}
          >
            {meta.status === "published" ? "Update" : "Publish"}
          </Button>
        </div>
      </div>

      <article
        className={
          meta.layout === "dashboard"
            ? "mx-auto max-w-6xl space-y-4"
            : "mx-auto max-w-3xl space-y-4 pl-14"
        }
      >
        {error ? (
          <Alert variant="destructive">
            <IconAlertTriangle />
            <AlertDescription>
              {error instanceof ApiRequestError
                ? error.message
                : "The API could not be reached."}
            </AlertDescription>
          </Alert>
        ) : null}
        <Grow
          value={meta.title}
          maxLength={300}
          placeholder="Untitled"
          className="font-heading text-3xl font-semibold tracking-tight sm:text-4xl"
          onChange={(title) =>
            setField({ title, ...(slugTouched ? {} : { slug: slugify(title) }) })
          }
          onEnter={() => {
            const first = items[0];
            if (first) setFocus({ uid: first.uid, at: "start" });
          }}
        />
        <Grow
          value={meta.dek ?? ""}
          maxLength={1000}
          placeholder="Add a standfirst: the finding, in two sentences"
          className="text-lg leading-relaxed"
          onChange={(dek) => setField({ dek })}
          onEnter={() => {
            const first = items[0];
            if (first) setFocus({ uid: first.uid, at: "start" });
          }}
        />
        <p className="text-sm text-muted-foreground">
          {[meta.publisher, meta.published_on].filter(Boolean).join(" · ") || (
            <span className="text-muted-foreground/60">
              Byline and date are in the page settings.
            </span>
          )}
        </p>
        <div className="pt-4">
          {meta.layout === "dashboard" ? (
            <DashboardEditor items={items} onChange={change} resolve={resolve} />
          ) : (
            <BlockEditor
              items={items}
              onChange={change}
              resolve={resolve}
              footnotes={footnotes}
              focus={focus}
              setFocus={setFocus}
            />
          )}
        </div>
      </article>
    </div>
  );
}
