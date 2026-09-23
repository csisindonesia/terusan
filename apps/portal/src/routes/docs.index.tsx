import { Link, createFileRoute } from "@tanstack/react-router";
import { IconFileText } from "@tabler/icons-react";

import { PageHeader } from "~/components/page-header";
import { allDocs } from "~/lib/docs";
import { formatCount } from "~/lib/format";

export const Route = createFileRoute("/docs/")({
  loader: () => allDocs(),
  component: Docs,
});

/**
 * The documentation shelf.
 *
 * These are the repository's own `docs/`, which until now were readable only
 * with a checkout — how the lake is run, how a source is added, which
 * Indonesian portals are collected and which cannot be. A reader asking where
 * a figure came from is asking something these answer.
 *
 * A list of cards rather than a table: there are six of them, each is a
 * document rather than a row, and what decides which one to open is the
 * sentence underneath the title.
 */
function Docs() {
  const docs = Route.useLoaderData();

  return (
    <div className="space-y-6">
      <PageHeader
        title="Documentation"
        count={docs.length}
        description="How this warehouse is built, run and extended — the repository's own documentation, as published with the version you are looking at."
      />

      <div className="grid gap-3 sm:grid-cols-2">
        {docs.map((doc) => (
          <Link
            key={doc.slug}
            to="/docs/$slug"
            params={{ slug: doc.slug }}
            className="group flex flex-col gap-2 rounded-lg border bg-card p-4 transition-colors hover:border-foreground/20 hover:bg-accent/40"
          >
            <div className="flex items-start gap-3">
              <IconFileText
                className="mt-0.5 size-4 shrink-0 text-muted-foreground"
                aria-hidden
              />
              <div className="space-y-1">
                <h2 className="font-heading leading-tight font-medium group-hover:underline">
                  {doc.title}
                </h2>
                <p className="line-clamp-3 text-sm text-muted-foreground">
                  {doc.summary}
                </p>
              </div>
            </div>
            <div className="mt-auto pl-7 text-xs text-muted-foreground tabular-nums">
              {formatCount(doc.words)} words
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
