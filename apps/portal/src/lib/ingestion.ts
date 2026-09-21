/**
 * Starting an ingestion from the page that shows its figures.
 *
 * The serving layer answers questions and does not change data, with one
 * deliberate exception: it will ask a pipeline to run. Nothing here writes a
 * figure — the scraper lands what it finds in RAW and records the run in the
 * journal, exactly as it does from a terminal. What the browser gets is a way
 * to start it and something to watch while it goes.
 *
 * That exception is switched off by default and is not on in every deployment,
 * so the capability is asked for rather than assumed: a button that works, or
 * a disabled one that says why, but never one that fails on click.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiRequestError, api, type Job } from "~/lib/api";
import { capabilitiesQuery } from "~/lib/session";

/** How often a running job is asked about. Fast enough to read as live. */
const POLL_MS = 1500;

export type Ingestion = {
  /** Whether this serving layer will run a pipeline at all. */
  available: boolean;
  /** Why the button is disabled, when it is. */
  unavailable?: string;
  /**
   * The run being watched: the one just started, or one already going that
   * this page adopted when it loaded. Undefined before anything has run.
   */
  job?: Job;
  running: boolean;
  /** Start one, unless one is already going. */
  start: () => void;
  /** The request to start it is in flight — not the run itself. */
  starting: boolean;
  /** What went wrong asking for the run. A failed *run* is on the job. */
  error?: string;
};

/**
 * Watch, and optionally start, the ingestion behind one series.
 *
 * `sourceId` is what actually runs: a series is produced from a source, and
 * "ingest this indicator" means "run the scraper that feeds it". A series
 * whose source is not recorded therefore cannot be ingested from here, which
 * the returned reason says.
 */
export function useIngestion(sourceId?: string, indicatorId?: string): Ingestion {
  const client = useQueryClient();
  const [watching, setWatching] = useState<string | undefined>();

  // `capabilitiesQuery` rather than a second declaration of the same key. Two
  // queries under one key share one cache entry, and these two did not agree
  // on its shape: one cached the envelope, the other what is inside it.
  // Whichever mounted first won, and the loser read a field off undefined —
  // which is what made an indicator page render as "Something went wrong"
  // whenever the top bar had asked first.
  const capabilities = useQuery(capabilitiesQuery);
  const available = capabilities.data?.run_pipelines ?? false;

  // What is already going for this source. Without this, reloading the page
  // during a ten-minute ingestion loses sight of it entirely and the only
  // honest thing left to show is "nothing is running", which is false.
  const recent = useQuery({
    queryKey: ["jobs", sourceId],
    queryFn: () => api.jobs({ source: sourceId }),
    enabled: available && Boolean(sourceId),
  });

  const jobId = watching ?? recent.data?.data[0]?.job_id;

  const watched = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.job(jobId as string),
    enabled: Boolean(jobId),
    // Only while it is going: a finished job never changes again, and polling
    // one forever is a request every second and a half for nothing.
    refetchInterval: (query) =>
      query.state.data?.data.status === "running" ? POLL_MS : false,
  });

  const job = watched.data?.data;
  const running = job?.status === "running";

  // A finished run means the lake has changed under every answer this page is
  // holding — the run history most obviously, but the figures too. The whole
  // cache goes rather than a named few: guessing which queries a pipeline
  // touched is how a page ends up showing a stale number beside a fresh one.
  const finishedAt = running ? undefined : job?.finished_at;
  useEffect(() => {
    if (!finishedAt) return;
    void client.invalidateQueries();
  }, [client, finishedAt]);

  const start = useMutation({
    mutationFn: () => api.runSource(sourceId as string, { indicator: indicatorId }),
    onSuccess: (page) => setWatching(page.data.job_id),
  });

  return {
    available,
    unavailable: reason(available, sourceId, running),
    job,
    running,
    start: () => {
      if (!available || !sourceId || running || start.isPending) return;
      start.mutate();
    },
    starting: start.isPending,
    error: start.error ? describe(start.error) : undefined,
  };
}

/** Why the button is disabled, in the terms of whoever is reading it. */
function reason(
  available: boolean,
  sourceId?: string,
  running?: boolean,
): string | undefined {
  if (!sourceId) return "No source is recorded for this series";
  if (!available) {
    return "This serving layer does not run pipelines — run it from a terminal";
  }
  if (running) return "An ingestion of this source is already going";
  return undefined;
}

function describe(error: unknown): string {
  if (error instanceof ApiRequestError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}
