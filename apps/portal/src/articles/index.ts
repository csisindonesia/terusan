/**
 * The articles built in code, by the slug `/portal/$slug` routes on.
 *
 * Written in the repository, like the docs, because each is an analysis as
 * much as prose: its charts and sentences read the figures file that
 * `terusan articles build <slug>` writes from the lake. Articles written in
 * the portal's editor live in the application database instead, and can
 * borrow from these two ways: a chart block naming one of `charts`, and a
 * figure token, `{{figure article=<slug> key=<path>}}`, naming a value in
 * `figures`.
 *
 * The Portal list comes from the showcase. An editor's article is filed there
 * when published; a code-built one is filed by an administrator with the path
 * `/portal/<slug>`.
 */

import type { ComponentType, ReactElement } from "react";

import type { ArticleFigure } from "~/lib/api";

import * as rice from "./rice-after-the-dry-season";
import riceFigures from "./rice-after-the-dry-season.json";

export type BuiltChart = { title: string; render: () => ReactElement };

export type Article = {
  slug: string;
  title: string;
  dek: string;
  publisher: string;
  /** YYYY-MM-DD. */
  published: string;
  datasets: string[];
  Body: ComponentType;
  charts: Record<string, BuiltChart>;
  figures: unknown;
};

export const ARTICLES: Article[] = [
  {
    ...rice.meta,
    Body: rice.RiceAfterTheDrySeason,
    charts: rice.charts,
    figures: riceFigures,
  },
];

export function article(slug: string): Article | undefined {
  return ARTICLES.find((entry) => entry.slug === slug);
}

const FIGURE_TOKEN =
  /^\{\{\s*figure\s+article=([a-z0-9-]+)\s+key=([A-Za-z0-9_.]+)\s*\}\}$/;

/** Is this token one a code-built article answers? */
export function isBuiltFigure(raw: string): boolean {
  return /^\{\{\s*figure\s/.test(raw);
}

/**
 * The value at a dotted path in a code-built article's figures file:
 * `y2023.rho`, `provinces.3.rice_change`.
 */
export function builtFigure(raw: string): ArticleFigure {
  const match = FIGURE_TOKEN.exec(raw.trim());
  if (!match) return { error: "write it as {{figure article=<slug> key=<path>}}" };
  const [, slug, key] = match;
  const source = article(slug ?? "");
  if (!source) return { error: `no built article ${slug}` };
  let value: unknown = source.figures;
  for (const part of (key ?? "").split(".")) {
    if (value === null || typeof value !== "object") {
      value = undefined;
      break;
    }
    value = (value as Record<string, unknown>)[part];
  }
  if (typeof value === "number") {
    return {
      display: value.toLocaleString("en-US", { maximumFractionDigits: 2 }),
      value: String(value),
    };
  }
  if (typeof value === "string") return { display: value };
  return { error: `${key} is not a figure in ${slug}` };
}

/** Every chart the code-built articles offer an editor, for the block picker. */
export function builtCharts(): {
  article: string;
  articleTitle: string;
  chart: string;
  title: string;
}[] {
  return ARTICLES.flatMap((entry) =>
    Object.entries(entry.charts).map(([chart, spec]) => ({
      article: entry.slug,
      articleTitle: entry.title,
      chart,
      title: spec.title,
    })),
  );
}
