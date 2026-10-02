import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { IconPencil } from "@tabler/icons-react";

import { article as builtArticle } from "~/articles";
import { ArticleBlocks, DashboardGrid } from "~/components/article-blocks";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { ApiRequestError, api } from "~/lib/api";
import { formatDate } from "~/lib/format";
import { datasetLabel } from "~/lib/labels";
import { useSessionState } from "~/lib/session";

export const Route = createFileRoute("/portal/$slug")({
  component: ArticlePage,
});

/**
 * One of the Portal's articles. A code-built one (src/articles) is drawn from
 * the build; any other slug is asked of the API, which holds the articles
 * written in the portal's editor.
 */
function ArticlePage() {
  const { slug } = Route.useParams();
  const built = builtArticle(slug);
  return built ? <BuiltArticle slug={slug} /> : <WrittenArticle slug={slug} />;
}

function Header({
  title,
  dek,
  byline,
  badge,
  actions,
  kind = "Article",
}: {
  kind?: string;
  title: string;
  dek?: string;
  byline: string;
  badge?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-medium tracking-widest text-muted-foreground uppercase">
          <Link to="/portal" className="hover:text-foreground">
            Portal
          </Link>{" "}
          · {kind} {badge}
        </p>
        {actions}
      </div>
      <h1 className="font-heading text-3xl font-semibold tracking-tight text-balance sm:text-4xl">
        {title}
      </h1>
      {dek ? <p className="text-lg leading-relaxed text-balance">{dek}</p> : null}
      <p className="text-sm text-muted-foreground">{byline}</p>
    </header>
  );
}

function BuiltArticle({ slug }: { slug: string }) {
  const entry = builtArticle(slug);
  if (!entry) return null;
  const { Body } = entry;
  return (
    <article className="mx-auto max-w-3xl space-y-8 pb-16">
      <Header
        title={entry.title}
        dek={entry.dek}
        byline={`${entry.publisher} · ${formatDate(entry.published)}`}
      />
      <div className="markdown article">
        <Body />
      </div>
    </article>
  );
}

function WrittenArticle({ slug }: { slug: string }) {
  const { session } = useSessionState();
  const isAdmin = session?.user.role === "admin";
  const found = useQuery({
    queryKey: ["articles", slug],
    queryFn: () => api.article(slug),
    retry: (count, error) =>
      !(error instanceof ApiRequestError && error.status === 404) && count < 2,
  });
  const catalogue = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });

  if (found.isLoading) {
    return (
      <div className="mx-auto max-w-3xl space-y-4">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (found.error || !found.data) {
    const missing =
      found.error instanceof ApiRequestError && found.error.status === 404;
    return (
      <p className="mx-auto max-w-3xl rounded-sm border border-dashed px-4 py-12 text-center text-sm text-muted-foreground">
        {missing ? "There is no article here." : (found.error as Error).message}{" "}
        <Link to="/portal" className="underline underline-offset-4">
          See everything on the Portal
        </Link>
        .
      </p>
    );
  }

  const entry = found.data.data;
  const titles = new Map((catalogue.data?.data ?? []).map((d) => [d.dataset_id, d]));
  const byline = [
    entry.publisher,
    entry.published_on ? formatDate(entry.published_on) : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const dashboard = entry.layout === "dashboard";
  return (
    <article
      className={`mx-auto space-y-8 pb-16 ${dashboard ? "max-w-6xl" : "max-w-3xl"}`}
    >
      <Header
        title={entry.title}
        dek={entry.dek}
        byline={byline || "Undated"}
        kind={dashboard ? "Dashboard" : "Article"}
        badge={
          entry.status === "draft" ? (
            <Badge variant="outline" className="ml-1 align-middle">
              Draft
            </Badge>
          ) : null
        }
        actions={
          isAdmin ? (
            <Button
              size="sm"
              variant="outline"
              render={<Link to="/portal/edit/$slug" params={{ slug: entry.slug }} />}
            >
              <IconPencil className="size-4" />
              Edit
            </Button>
          ) : null
        }
      />
      {dashboard ? (
        <DashboardGrid blocks={entry.blocks} figures={entry.figures} />
      ) : (
        <ArticleBlocks blocks={entry.blocks} figures={entry.figures} />
      )}
      {entry.datasets.length ? (
        <footer className="space-y-2 border-t pt-6 text-sm">
          <p className="font-medium">Data</p>
          <ul className="flex flex-wrap gap-1.5">
            {entry.datasets.map((id) => {
              const d = titles.get(id);
              return (
                <li key={id}>
                  <Link
                    to="/datasets/$datasetId"
                    params={{ datasetId: id }}
                    className="inline-flex rounded-md bg-muted px-2 py-0.5 text-xs hover:underline"
                  >
                    {d ? datasetLabel(d) : id}
                  </Link>
                </li>
              );
            })}
          </ul>
        </footer>
      ) : null}
    </article>
  );
}
