/**
 * An article's prose, rendered: markdown, with its figure tokens replaced by
 * the figures they ask for.
 *
 * Unlike the documentation (lib/docs.ts), this markdown is written through a
 * form and stored in the application database, so it is not trusted as HTML.
 * Raw HTML in it is printed as text, links go only to http(s), mail and the
 * portal's own paths, and images are not rendered at all. What is left is the
 * markup marked produces from markdown itself, which carries no script.
 *
 * Tokens are swapped for private-use markers before the markdown is parsed,
 * so a figure can never be read as markup, and the markers for the figures'
 * own HTML afterwards. A token without a figure is printed as a marked gap
 * with its reason, never as a blank and never as zero.
 *
 * The markers must only ever stand in text, never in an attribute, where a
 * figure's HTML would end the attribute early. So any marker characters the
 * author typed are removed before the swap, and a token written into a link's
 * address is put back as the token's own text before the address is escaped.
 */

import type { ArticleFigure } from "~/lib/api";
import { isPortalPath } from "~/lib/paths";

const TOKEN = /\{\{[^{}]*\}\}/g;
const OPEN = "\uE000";
const CLOSE = "\uE001";
const MARKERS = /[\uE000\uE001]/g;
const MARKER = new RegExp(`${OPEN}(\\d+)${CLOSE}`, "g");

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function safeHref(href: string): string | null {
  const trimmed = href.trim();
  if (/^(https?:|mailto:)/i.test(trimmed)) return trimmed;
  if (isPortalPath(trimmed)) return trimmed;
  if (trimmed.startsWith("#")) return trimmed;
  return null;
}

/** Every token in a text, as written. */
export function tokensIn(text: string): string[] {
  return [...new Set(text.match(TOKEN) ?? [])];
}

export function figureHtml(figure: ArticleFigure | undefined, raw: string): string {
  if (figure?.display && !figure.error) {
    const title = figure.period ? ` title="${escapeHtml(figure.period)}"` : "";
    return `<span class="article-figure"${title}>${escapeHtml(figure.display)}</span>`;
  }
  const why = figure?.error ?? "not evaluated yet";
  return `<span class="article-figure-missing" title="${escapeHtml(`${raw}: ${why}`)}">figure unavailable</span>`;
}

const FOOTNOTE_REF = /\[\^(\d{1,3})\]/g;

/** A footnote reference, `[^2]`, as the superscript that links to its note. */
function footnoteRefs(html: string): string {
  return html.replace(
    FOOTNOTE_REF,
    (_, n: string) =>
      `<sup class="article-footnote-ref"><a href="#fn-${n}" id="fnref-${n}">${n}</a></sup>`,
  );
}

async function renderer(
  markdown: string,
  resolve: (raw: string) => ArticleFigure | undefined,
  inline: boolean,
): Promise<string> {
  const { Marked } = await import("marked");
  const tokens: string[] = [];
  const marked_ = markdown.replace(MARKERS, "").replace(TOKEN, (raw) => {
    tokens.push(raw);
    return `${OPEN}${tokens.length - 1}${CLOSE}`;
  });

  const marked = new Marked({ gfm: true });
  marked.use({
    renderer: {
      html(token) {
        return escapeHtml(token.text);
      },
      image(token) {
        return escapeHtml(token.text);
      },
      link(token) {
        const text = this.parser.parseInline(token.tokens);
        const href = safeHref(
          token.href.replace(MARKER, (_, n: string) => tokens[Number(n)] ?? ""),
        );
        if (!href) return text;
        const external = /^https?:/i.test(href);
        const attrs = external ? ' target="_blank" rel="noreferrer noopener"' : "";
        return `<a href="${escapeHtml(href)}"${attrs}>${text}</a>`;
      },
    },
  });

  const html = (inline ? marked.parseInline(marked_) : marked.parse(marked_)) as string;
  return footnoteRefs(
    html.replace(MARKER, (_, n: string) => {
      const raw = tokens[Number(n)] ?? "";
      return figureHtml(resolve(raw), raw);
    }),
  );
}

/**
 * The HTML for one block of prose.
 *
 * `resolve` gives each token's figure; the caller merges the API's answers
 * with the figures of code-built articles.
 */
export function renderArticleMarkdown(
  markdown: string,
  resolve: (raw: string) => ArticleFigure | undefined,
): Promise<string> {
  return renderer(markdown, resolve, false);
}

/** The same, for a line with no paragraphs of its own: a heading, a cell. */
export function renderArticleInline(
  markdown: string,
  resolve: (raw: string) => ArticleFigure | undefined,
): Promise<string> {
  return renderer(markdown, resolve, true);
}
