import { Link, createFileRoute, notFound } from "@tanstack/react-router";
import { IconCopy, IconDownload } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { Button } from "~/components/ui/button";
import { copyToClipboard } from "~/components/row-actions";
import { docTitles, findDoc, type Doc } from "~/lib/docs";
import { cn } from "~/lib/utils";

export const Route = createFileRoute("/docs/$slug")({
  loader: async ({ params }) => {
    const [doc, others] = await Promise.all([findDoc(params.slug), docTitles()]);
    if (!doc) throw notFound();
    return { doc, others: others.filter((other) => other.slug !== doc.slug) };
  },
  component: DocPage,
  notFoundComponent: NotFound,
});

/**
 * One documentation file.
 *
 * Laid out like `/help`, because it is the same kind of reading: the contents
 * down the side and the whole text beside it, so "is my question in here" is
 * answered before any of the prose is read. The heading anchors are the ones
 * the markdown's own cross-references point at, which is what makes a link
 * between two of these files land in the right place.
 */
function DocPage() {
  const { doc, others } = Route.useLoaderData();
  const active = useActiveSection(doc.headings.map((heading) => heading.id));

  return (
    <div className="grid gap-8 lg:grid-cols-[15rem_minmax(0,1fr)] lg:gap-12">
      <aside className="lg:sticky lg:top-[calc(var(--app-header)+1.5rem)] lg:self-start">
        <nav aria-label="On this page" className="space-y-1">
          <SidebarLabel>On this page</SidebarLabel>
          {doc.headings.map((heading) => (
            <a
              key={heading.id}
              href={`#${heading.id}`}
              aria-current={active === heading.id ? "true" : undefined}
              className={cn(
                "block rounded-md py-1 text-sm transition-colors hover:bg-accent hover:text-foreground",
                heading.depth === 3 ? "pr-2 pl-5" : "px-2",
                active === heading.id
                  ? "bg-accent font-medium text-foreground"
                  : "text-muted-foreground",
              )}
            >
              {heading.text}
            </a>
          ))}

          <div className="mt-8 space-y-1 border-t pt-6">
            <SidebarLabel>Other documents</SidebarLabel>
            {others.map((other) => (
              <Link
                key={other.slug}
                to="/docs/$slug"
                params={{ slug: other.slug }}
                className="block rounded-md px-2 py-1 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                {other.title}
              </Link>
            ))}
          </div>
        </nav>
      </aside>

      <article className="max-w-3xl space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <h1 className="font-heading text-3xl font-semibold tracking-tight">
              {doc.title}
            </h1>
            <p className="text-sm text-muted-foreground">
              <Link to="/docs" className="underline underline-offset-4">
                Documentation
              </Link>
              {` · docs/${doc.slug}.md`}
            </p>
          </div>
          <DocActions doc={doc} />
        </div>

        {/* The repository's own markup, rendered. It is written by the people
            who deploy this and is not sanitized; see lib/docs.ts. */}
        <div className="markdown" dangerouslySetInnerHTML={{ __html: doc.html }} />
      </article>
    </div>
  );
}

/**
 * A heading over one list in the side menu.
 *
 * Bold, in the text colour, with room under it. Set in muted grey at the same
 * weight as the links beneath, these read as the first item of their own list
 * rather than as the thing naming it — which is the one job they have.
 */
function SidebarLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-2 pt-1 pb-3 text-xs font-bold tracking-widest text-foreground uppercase">
      {children}
    </div>
  );
}

/**
 * The file itself, for a reader who would rather have the source than the
 * page — pasting it into an editor, or into a question somewhere else.
 */
function DocActions({ doc }: { doc: Doc }) {
  function download() {
    const blob = new Blob([doc.markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${doc.slug}.md`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="flex items-center gap-2">
      <Button variant="outline" size="sm" onClick={() => copyToClipboard(doc.markdown)}>
        <IconCopy className="size-4" aria-hidden />
        Copy
      </Button>
      <Button variant="outline" size="sm" onClick={download}>
        <IconDownload className="size-4" aria-hidden />
        Markdown
      </Button>
    </div>
  );
}

function NotFound() {
  return (
    <div className="max-w-2xl space-y-3">
      <h1 className="font-heading text-2xl font-semibold tracking-tight">
        No such document
      </h1>
      <p className="text-sm text-muted-foreground">
        This documentation is built from the repository, so a page exists only while its
        file does.{" "}
        <Link to="/docs" className="underline underline-offset-4">
          See what there is
        </Link>
        .
      </p>
    </div>
  );
}

/**
 * Which section the reader is looking at, for the contents menu.
 *
 * The same observer `/help` uses: a band across the upper middle of the
 * viewport, and the first heading inside it in document order — so a scroll
 * revealing two at once names the upper one rather than whichever fired last.
 */
function useActiveSection(ids: string[]): string | undefined {
  const [active, setActive] = useState<string>();

  useEffect(() => {
    const seen = new Map<string, boolean>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) seen.set(entry.target.id, entry.isIntersecting);
        const first = ids.find((id) => seen.get(id));
        if (first) setActive(first);
      },
      { rootMargin: "-25% 0px -70% 0px" },
    );

    for (const id of ids) {
      const element = document.getElementById(id);
      if (element) observer.observe(element);
    }
    return () => observer.disconnect();
  }, [ids.join(",")]);

  return active;
}
