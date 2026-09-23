/**
 * The repository's documentation, rendered into the portal.
 *
 * `docs/` is where the operators' documentation already lives, and it was
 * readable only to someone with a checkout. Nothing about it is private —
 * how the lake is run, how a source is added, which Indonesian portals are
 * collected and which cannot be — and a reader who wants to know where a
 * figure came from should not have to clone a repository to find out.
 *
 * The files are read at build time rather than fetched from the API. They are
 * part of the repository the portal is built from, so they are as current as
 * the deployment is, and a documentation page that cannot fail to load is
 * worth more than one that is always the very latest. The build needs the
 * `docs/` directory beside `apps/`; `apps/portal/Dockerfile` copies it in.
 *
 * Everything here loads on demand — the files through a lazy glob, the
 * markdown parser through a dynamic import. Eagerly, both end up in the chunk
 * every page shares, which would put two thousand lines of documentation and a
 * parser into the download for a reader who only ever opens a chart.
 *
 * The markup is ours, written by the people who deploy this, which is why the
 * rendered HTML is inserted as HTML. Do not point this at anything a reader
 * can write — that would need sanitizing, and this does none.
 */

/** Each documentation file, as a loader that fetches its text. */
const FILES = import.meta.glob("../../../../docs/*.md", {
  query: "?raw",
  import: "default",
}) as Record<string, () => Promise<string>>;

/**
 * The order they are listed in: the way someone meets them, not alphabetical.
 * Anything not named here follows, alphabetically, so a new file in `docs/`
 * appears without editing this.
 */
const ORDER = [
  "running-it",
  "design",
  "adding-a-source",
  "indonesia-sources",
  "docker",
  "nas-deployment",
];

export type DocHeading = {
  id: string;
  text: string;
  /** 2 for a section, 3 for a subsection. Deeper headings are not listed. */
  depth: number;
};

/** What the index needs: enough to choose a document, without rendering one. */
export type DocSummary = {
  slug: string;
  /** The file's `# ` heading, which every one of these carries. */
  title: string;
  /** The first line of prose under the title. */
  summary: string;
  words: number;
};

export type Doc = DocSummary & {
  /** The file, as published. Offered for download and for copying. */
  markdown: string;
  html: string;
  headings: DocHeading[];
};

/** `../../../../docs/adding-a-source.md` → `adding-a-source`. */
function slugOf(path: string): string {
  return path.split("/").pop()?.replace(/\.md$/, "") ?? path;
}

/** The files, in reading order, as `[slug, load]`. */
function ordered(): Array<[string, () => Promise<string>]> {
  return Object.entries(FILES)
    .map(([path, load]) => [slugOf(path), load] as [string, () => Promise<string>])
    .sort(([left], [right]) => {
      const a = ORDER.indexOf(left);
      const b = ORDER.indexOf(right);
      if (a !== -1 && b !== -1) return a - b;
      if (a !== -1) return -1;
      if (b !== -1) return 1;
      return left.localeCompare(right);
    });
}

/**
 * A heading's anchor, matching what the contents menu links to.
 *
 * Deliberately plain: lowercase, words joined by hyphens. Two headings with
 * the same text would collide, so the render counts them and suffixes.
 */
function anchor(text: string): string {
  return (
    text
      .toLowerCase()
      .replace(/[`*_]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "section"
  );
}

/** The first prose line of a document, as a summary for the index. */
function firstLine(markdown: string): string {
  let started = false;
  for (const line of markdown.split("\n")) {
    const text = line.trim();
    if (!started) {
      if (text.startsWith("# ")) started = true;
      continue;
    }
    if (!text || text.startsWith("#") || text.startsWith("```")) continue;
    return text.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").replace(/[`*]/g, "");
  }
  return "";
}

function summarize(slug: string, markdown: string): DocSummary {
  return {
    slug,
    title: markdown.match(/^#\s+(.+)$/m)?.[1]?.trim() ?? slug,
    summary: firstLine(markdown),
    words: markdown.split(/\s+/).filter(Boolean).length,
  };
}

/**
 * Where a link in a document should point once it is on the web.
 *
 * Three kinds arrive. A link to another documentation file becomes a link to
 * that page here. A link to somewhere on the internet stays one, opened in a
 * new tab so a reader does not lose their place. Everything else addresses a
 * file in the repository — `../pipelines/src/...` — which the browser cannot
 * open and which is still worth reading: those are rendered as the path they
 * are, in code, rather than as a link that 404s.
 */
function rewriteHref(href: string): { href?: string; external?: boolean } {
  if (/^https?:\/\//.test(href)) return { href, external: true };
  if (href.startsWith("#")) return { href };

  const [path, hash] = href.split("#");
  if (path && /\.md$/.test(path) && !path.includes("../")) {
    return { href: `/docs/${slugOf(path)}${hash ? `#${hash}` : ""}` };
  }
  if (!path && hash) return { href: `#${hash}` };
  return {};
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Renders one file, collecting its headings on the way through. */
async function render(
  markdown: string,
): Promise<{ html: string; headings: DocHeading[] }> {
  const { Marked } = await import("marked");

  const headings: DocHeading[] = [];
  const used = new Map<string, number>();

  const marked = new Marked({ gfm: true });
  marked.use({
    renderer: {
      heading(token) {
        const text = this.parser.parseInline(token.tokens);
        const base = anchor(token.text);
        const seen = used.get(base) ?? 0;
        used.set(base, seen + 1);
        const id = seen ? `${base}-${seen + 1}` : base;

        // The title is the page heading and is rendered above the body, so it
        // is dropped here rather than repeated.
        if (token.depth === 1) return "";
        if (token.depth <= 3)
          headings.push({ id, text: token.text, depth: token.depth });
        return `<h${token.depth} id="${id}">${text}</h${token.depth}>\n`;
      },
      link(token) {
        const text = this.parser.parseInline(token.tokens);
        const target = rewriteHref(token.href);
        if (!target.href) return `<code>${escapeHtml(token.text)}</code>`;
        const attrs = target.external
          ? ' target="_blank" rel="noreferrer noopener"'
          : "";
        return `<a href="${escapeHtml(target.href)}"${attrs}>${text}</a>`;
      },
    },
  });

  return { html: marked.parse(markdown) as string, headings };
}

/** Every document, described but not rendered. For the index. */
export async function allDocs(): Promise<DocSummary[]> {
  return Promise.all(
    ordered().map(async ([slug, load]) => summarize(slug, await load())),
  );
}

/** One document, rendered. Undefined where no such file is published. */
export async function findDoc(slug: string): Promise<Doc | undefined> {
  const found = ordered().find(([candidate]) => candidate === slug);
  if (!found) return undefined;

  const markdown = await found[1]();
  const { html, headings } = await render(markdown);
  return { ...summarize(slug, markdown), markdown, html, headings };
}

/** The other documents' titles, for the menu beside one of them. */
export async function docTitles(): Promise<Array<{ slug: string; title: string }>> {
  const docs = await allDocs();
  return docs.map(({ slug, title }) => ({ slug, title }));
}
