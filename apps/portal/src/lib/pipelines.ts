/**
 * How a pipeline is doing, read off its journal summary.
 *
 * The administration pages share one vocabulary for this so that "failing" on
 * the ingestion page and "failing" on the pipelines page mean the same rows.
 * Only the newest run decides the state; the streak behind it decides how loud
 * to be about it.
 */

import type { Pipeline } from "~/lib/api";

export type Health = "failing" | "empty" | "dry-run" | "ok";

export const HEALTH_LABELS: Record<Health, string> = {
  failing: "Failing",
  empty: "Nothing written",
  "dry-run": "Dry run",
  ok: "Succeeded",
};

/** Failing rows first: they are what someone opened the page to find. */
export const HEALTH_ORDER: Record<Health, number> = {
  failing: 0,
  empty: 1,
  "dry-run": 2,
  ok: 3,
};

export function healthOf(pipeline: Pipeline): Health {
  if (pipeline.last_status === "failed") return "failing";
  // A dry run lands nothing on purpose, so its zero is not the quiet failure.
  if (pipeline.last_dry_run) return "dry-run";
  if (pipeline.last_records_out === 0) return "empty";
  return "ok";
}

/**
 * Why a run that succeeded wrote nothing, in the terms of its stage.
 *
 * An ingestion of a source whose agency has published nothing new writes
 * nothing, because RAW is content-addressed — that is the system working. A
 * normalization that writes nothing has produced an empty series, which is not.
 */
export function emptyNote(kind: string): string {
  return kind === "ingest"
    ? "Fetched, and found nothing it had not already landed"
    : "Succeeded and wrote no records";
}

/** Pipelines by the source they run for, newest summary kept where several do. */
export function bySource(pipelines: Pipeline[]): Map<string, Pipeline> {
  const out = new Map<string, Pipeline>();
  for (const pipeline of pipelines) {
    if (!pipeline.source_id) continue;
    const held = out.get(pipeline.source_id);
    if (!held || held.last_started_at < pipeline.last_started_at) {
      out.set(pipeline.source_id, pipeline);
    }
  }
  return out;
}

/** Pipelines by the dataset slug they normalize into, several per dataset. */
export function byDataset(pipelines: Pipeline[]): Map<string, Pipeline[]> {
  const out = new Map<string, Pipeline[]>();
  for (const pipeline of pipelines) {
    if (!pipeline.dataset) continue;
    const held = out.get(pipeline.dataset) ?? [];
    held.push(pipeline);
    out.set(pipeline.dataset, held);
  }
  return out;
}
