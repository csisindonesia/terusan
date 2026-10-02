import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { IconDatabase, IconGavel, IconInfoCircle } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { article as builtArticle, builtFigure, isBuiltFigure } from "~/articles";
import { TimeSeriesChart } from "~/components/time-series-chart";
import { Skeleton } from "~/components/ui/skeleton";
import {
  api,
  type ArticleBlock,
  type ArticleFigure,
  type ArticleQuery,
} from "~/lib/api";
import { renderArticleInline, renderArticleMarkdown } from "~/lib/article-markdown";
import { linesFrom } from "~/lib/chart-lines";
import { formatCount, formatDate } from "~/lib/format";
import { datasetLabel } from "~/lib/labels";

/**
 * An article written in the portal, block by block. The article page draws
 * with `ArticleBlocks`; the editor draws each block with `BlockView`, so what
 * an editor sees while writing is what a reader sees.
 */

export type Resolve = (raw: string) => ArticleFigure | undefined;

/** A token's figure: from a code-built article's file, or from the API. */
export function resolverFor(figures: Record<string, ArticleFigure>): Resolve {
  return (raw) => (isBuiltFigure(raw) ? builtFigure(raw) : figures[raw]);
}

/** Footnotes are numbered in the order they appear. */
export function footnoteNumbers(blocks: ArticleBlock[]): Map<number, number> {
  const out = new Map<number, number>();
  let n = 0;
  blocks.forEach((block, i) => {
    if (block.type === "footnote") out.set(i, ++n);
  });
  return out;
}

export function ArticleBlocks({
  blocks,
  figures,
}: {
  blocks: ArticleBlock[];
  /** The API's answers for the tokens, keyed by the token as written. */
  figures: Record<string, ArticleFigure>;
}) {
  const resolve = resolverFor(figures);
  const numbers = footnoteNumbers(blocks);
  const notes = blocks.flatMap((block, i) =>
    block.type === "footnote"
      ? [{ n: numbers.get(i) ?? 0, markdown: block.markdown }]
      : [],
  );
  return (
    <div className="markdown article">
      {blocks.map((block, n) =>
        block.type === "footnote" ? null : (
          // Position is the identity: blocks carry no ids of their own.
          <BlockView key={n} block={block} resolve={resolve} />
        ),
      )}
      {notes.length ? (
        <section className="mt-10 border-t pt-4">
          <h2 className="!mt-0 !border-0 !pb-0 text-sm">Notes</h2>
          <ol className="!list-none !pl-0 text-sm">
            {notes.map((note) => (
              <li key={note.n} id={`fn-${note.n}`} className="flex gap-2">
                <a href={`#fnref-${note.n}`} className="tabular-nums !no-underline">
                  {note.n}.
                </a>
                <Prose markdown={note.markdown} resolve={resolve} inline />
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </div>
  );
}

/** One block, as a reader sees it. */
export function BlockView({
  block,
  resolve,
  footnote,
}: {
  block: ArticleBlock;
  resolve: Resolve;
  /** A footnote's number, where the block is one. */
  footnote?: number;
}) {
  switch (block.type) {
    case "text":
      return <Prose markdown={block.markdown} resolve={resolve} />;
    case "heading":
      return block.level === 3 ? (
        <h3>
          <Prose markdown={block.markdown} resolve={resolve} inline />
        </h3>
      ) : (
        <h2>
          <Prose markdown={block.markdown} resolve={resolve} inline />
        </h2>
      );
    case "quote":
      return (
        <blockquote className="!not-italic">
          <Prose markdown={block.markdown} resolve={resolve} />
          {block.note ? (
            <footer className="mt-2 text-sm text-muted-foreground">
              — {block.note}
            </footer>
          ) : null}
        </blockquote>
      );
    case "callout":
      return (
        <div className="flex gap-3 rounded-lg bg-muted/60 px-4 py-3">
          <IconInfoCircle className="mt-1 size-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <Prose markdown={block.markdown} resolve={resolve} />
          </div>
        </div>
      );
    case "footnote":
      return (
        <div className="flex gap-2 text-sm text-muted-foreground">
          <span className="tabular-nums">{footnote ?? "·"}.</span>
          <Prose markdown={block.markdown} resolve={resolve} inline />
        </div>
      );
    case "divider":
      return <hr />;
    case "chart":
      return <ChartBlock title={block.title} note={block.note} query={block.query} />;
    case "embed":
      return <EmbedBlock article={block.article} chart={block.chart} />;
    case "table":
      return <TableBlock block={block} resolve={resolve} />;
    case "regulation":
      return <RegulationBlock regulationKey={block.key} />;
    case "dataset":
      return <DatasetBlock id={block.dataset} />;
    case "stat":
      return <StatBlock block={block} resolve={resolve} />;
  }
}

/** A dashboard's headline number, with what it is and what qualifies it. */
function StatBlock({
  block,
  resolve,
}: {
  block: Extract<ArticleBlock, { type: "stat" }>;
  resolve: Resolve;
}) {
  return (
    <div className="stat flex h-full min-h-28 flex-col justify-between gap-3">
      {block.title ? (
        <div className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          <Prose markdown={block.title} resolve={resolve} inline />
        </div>
      ) : null}
      <div className="font-heading text-3xl leading-none font-semibold tracking-tight tabular-nums">
        <Prose markdown={block.markdown} resolve={resolve} inline />
      </div>
      {block.note ? (
        <div className="text-sm text-muted-foreground">
          <Prose markdown={block.note} resolve={resolve} inline />
        </div>
      ) : null}
    </div>
  );
}

/** A tile's size where the writer has not set one: what each kind reads best at. */
export function defaultSize(block: ArticleBlock): { width: number; height: number } {
  switch (block.type) {
    case "stat":
      return { width: 1, height: 1 };
    case "chart":
    case "embed":
      return { width: 2, height: 2 };
    case "heading":
    case "divider":
      return { width: 4, height: 1 };
    default:
      return { width: 2, height: 1 };
  }
}

export function sizeOf(block: ArticleBlock): { width: number; height: number } {
  const fallback = defaultSize(block);
  return {
    width: block.width || fallback.width,
    height: block.height || fallback.height,
  };
}

// Written out whole, so Tailwind sees every class it has to generate. On a
// phone the grid is two columns: a stat keeps half of it, anything else
// takes the row.
const SPAN_W: Record<number, string> = {
  1: "col-span-2 md:col-span-1",
  2: "col-span-2 md:col-span-2",
  3: "col-span-2 md:col-span-3",
  4: "col-span-2 md:col-span-4",
};
const SPAN_W_STAT: Record<number, string> = {
  ...SPAN_W,
  1: "col-span-1 md:col-span-1",
};
const SPAN_H: Record<number, string> = {
  1: "",
  2: "md:row-span-2",
  3: "md:row-span-3",
};

/** The classes that place a tile on the grid. */
export function tileClass(block: ArticleBlock): string {
  const { width, height } = sizeOf(block);
  const w = (block.type === "stat" ? SPAN_W_STAT : SPAN_W)[width] ?? SPAN_W[2];
  const surface =
    block.type === "heading" || block.type === "divider" ? "" : "dashboard-tile";
  return `${w} ${SPAN_H[height] ?? ""} ${surface} min-w-0`;
}

/** The grid a dashboard's tiles are laid on. */
export const DASHBOARD_GRID =
  "grid grid-cols-2 gap-3 md:grid-cols-4 md:auto-rows-[minmax(9rem,auto)]";

/** A dashboard: its blocks as tiles on a bento grid. */
export function DashboardGrid({
  blocks,
  figures,
}: {
  blocks: ArticleBlock[];
  figures: Record<string, ArticleFigure>;
}) {
  const resolve = resolverFor(figures);
  return (
    <div className={`markdown article dashboard ${DASHBOARD_GRID}`}>
      {blocks.map((block, n) => (
        <div key={n} className={tileClass(block)}>
          <BlockView block={block} resolve={resolve} />
        </div>
      ))}
    </div>
  );
}

/** Markdown with its tokens filled in, rendered safely (lib/article-markdown). */
export function Prose({
  markdown,
  resolve,
  inline = false,
}: {
  markdown: string;
  resolve: Resolve;
  inline?: boolean;
}) {
  const [html, setHtml] = useState<string | null>(null);
  // What the tokens currently resolve to, as a key: the render reruns when a
  // figure arrives or changes, not on every render of the page around it.
  const answers = JSON.stringify(
    (markdown.match(/\{\{[^{}]*\}\}/g) ?? []).map((raw) => resolve(raw) ?? null),
  );
  useEffect(() => {
    let live = true;
    void (inline ? renderArticleInline : renderArticleMarkdown)(markdown, resolve).then(
      (out) => {
        if (live) setHtml(out);
      },
    );
    return () => {
      live = false;
    };
  }, [markdown, answers, inline]);
  if (html === null)
    return inline ? <span>{markdown}</span> : <Skeleton className="h-6" />;
  // Escaped and link-checked in lib/article-markdown.ts: the markdown comes
  // from a form, not from the repository.
  return inline ? (
    <span dangerouslySetInnerHTML={{ __html: html }} />
  ) : (
    <div className="space-y-4" dangerouslySetInnerHTML={{ __html: html }} />
  );
}

function Caption({ title, note }: { title?: string; note?: string }) {
  if (!title && !note) return null;
  return (
    <figcaption className="space-y-0.5">
      {title ? <div className="font-heading text-sm font-semibold">{title}</div> : null}
      {note ? <div className="text-xs text-muted-foreground">{note}</div> : null}
    </figcaption>
  );
}

function Placeholder({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-sm border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
      {children}
    </p>
  );
}

/** The series filters, in the shape the observations routes take. */
function seriesParams(query: ArticleQuery) {
  return {
    indicator: query.indicator,
    geo: query.geo,
    geo_type: query.geo_type,
    commodity: query.commodity,
    period_start: query.period_start,
    period_end: query.period_end,
    dimension: query.dimension || undefined,
    members: query.members || undefined,
  };
}

function ChartBlock({
  title,
  note,
  query,
}: {
  title?: string;
  note?: string;
  query: ArticleQuery;
}) {
  const series = useQuery({
    queryKey: ["article-chart", query],
    queryFn: () =>
      api.observationSeries(
        seriesParams(query) as Parameters<typeof api.observationSeries>[0],
      ),
    enabled: query.indicator.length > 0,
  });
  const lines = series.data
    ? linesFrom(series.data.data.series, title || "Series")
    : [];

  return (
    <figure className="article-card space-y-3">
      <Caption title={title} note={note} />
      {!query.indicator.length ? (
        <Placeholder>Choose the series this chart draws.</Placeholder>
      ) : series.isLoading ? (
        <Skeleton className="h-64" />
      ) : series.error ? (
        <p className="rounded-lg bg-destructive/5 px-4 py-3 text-sm">
          {(series.error as Error).message}
        </p>
      ) : lines.length ? (
        <TimeSeriesChart framed={false} dashed series={lines} />
      ) : (
        <Placeholder>No figures match this chart's series.</Placeholder>
      )}
    </figure>
  );
}

function EmbedBlock({ article, chart }: { article: string; chart: string }) {
  const spec = builtArticle(article)?.charts[chart];
  if (!spec)
    return (
      <Placeholder>
        The chart {chart} from {article} is not in this build of the portal.
      </Placeholder>
    );
  return spec.render();
}

function TableBlock({
  block,
  resolve,
}: {
  block: Extract<ArticleBlock, { type: "table" }>;
  resolve: Resolve;
}) {
  const [head, ...body] = block.header ? block.rows : [undefined, ...block.rows];
  return (
    <figure className="article-card space-y-2">
      <Caption title={block.title} note={block.note} />
      <div className="overflow-x-auto">
        <table className="!table w-full">
          {head ? (
            <thead>
              <tr>
                {head.map((cell, c) => (
                  <th key={c}>
                    <Prose markdown={cell} resolve={resolve} inline />
                  </th>
                ))}
              </tr>
            </thead>
          ) : null}
          <tbody>
            {body.map((row, r) =>
              row ? (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td
                      key={c}
                      className={
                        /^[−+\-\d.,% Rp]+$/.test(cell) ? "text-right tabular-nums" : ""
                      }
                    >
                      {cell ? <Prose markdown={cell} resolve={resolve} inline /> : null}
                    </td>
                  ))}
                </tr>
              ) : null,
            )}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

/** A regulation from the warehouse, as a card that opens it. */
function RegulationBlock({ regulationKey }: { regulationKey: string }) {
  const found = useQuery({
    queryKey: ["regulation", regulationKey],
    queryFn: () => api.regulation(regulationKey),
    enabled: Boolean(regulationKey),
  });
  if (!regulationKey) return <Placeholder>Choose the regulation to cite.</Placeholder>;
  if (found.isLoading) return <Skeleton className="h-20" />;
  const r = found.data?.data;
  if (!r)
    return (
      <Placeholder>The regulation {regulationKey} is not in the warehouse.</Placeholder>
    );
  const name = [
    r.instrument,
    r.number ? `No. ${r.number}` : null,
    r.year ? `Tahun ${r.year}` : null,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <Link
      to="/regulations/$key"
      params={{ key: r.key }}
      className="article-card !no-underline flex gap-3"
    >
      <IconGavel className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 space-y-0.5">
        <span className="block text-xs text-muted-foreground">
          {[name, r.region_name].filter(Boolean).join(" · ")}
        </span>
        <span className="block font-medium text-foreground">{r.title}</span>
        <span className="block text-xs text-muted-foreground">
          {[r.status, r.enacted_date ? `enacted ${formatDate(r.enacted_date)}` : null]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </span>
    </Link>
  );
}

/** A dataset from the warehouse, as a card that opens it. */
function DatasetBlock({ id }: { id: string }) {
  const catalogue = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });
  if (!id) return <Placeholder>Choose the dataset to point to.</Placeholder>;
  if (catalogue.isLoading) return <Skeleton className="h-20" />;
  const d = catalogue.data?.data.find((entry) => entry.dataset_id === id);
  if (!d) return <Placeholder>The dataset {id} is not in the warehouse.</Placeholder>;
  return (
    <Link
      to="/datasets/$datasetId"
      params={{ datasetId: d.dataset_id }}
      className="article-card !no-underline flex gap-3"
    >
      <IconDatabase className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 space-y-0.5">
        <span className="block font-medium text-foreground">{datasetLabel(d)}</span>
        {d.description ? (
          <span className="line-clamp-2 block text-sm text-muted-foreground">
            {d.description}
          </span>
        ) : null}
        <span className="block text-xs text-muted-foreground">
          {[
            d.organization ?? d.source_name ?? d.source_id,
            d.series ? `${formatCount(d.series)} series` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </span>
    </Link>
  );
}
