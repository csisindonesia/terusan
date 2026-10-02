import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { IconPlayerPlay } from "@tabler/icons-react";

import { Button } from "~/components/ui/button";
import { ApiRequestError, api } from "~/lib/api";
import { capabilitiesQuery } from "~/lib/session";

/**
 * Start an ingestion of one source from a table row.
 *
 * The indicator page has `useIngestion`, which watches the run to the end; a
 * table of a hundred sources cannot poll a hundred jobs, so this only starts
 * one and points at the Jobs page, which shows everything running live. The
 * button is disabled, with the reason as its title, where the serving layer
 * does not run pipelines or the source is gated — never one that fails on
 * click.
 */
export function RunSourceButton({
  sourceId,
  active = true,
}: {
  sourceId: string;
  active?: boolean;
}) {
  const client = useQueryClient();
  const capabilities = useQuery(capabilitiesQuery);
  const available = capabilities.data?.run_pipelines ?? false;

  const start = useMutation({
    mutationFn: () => api.runSource(sourceId),
    // The Jobs page's running list is the one cache entry that changes the
    // moment a run starts; the journal changes only once it finishes.
    onSuccess: () => void client.invalidateQueries({ queryKey: ["jobs"] }),
  });

  if (start.isSuccess) {
    return (
      <Button
        variant="outline"
        size="sm"
        nativeButton={false}
        render={<Link to="/jobs" />}
      >
        Started — watch
      </Button>
    );
  }

  const reason = !available
    ? "This serving layer does not run pipelines — run it from a terminal"
    : !active
      ? "This source is gated and has no working fetcher"
      : start.error
        ? describe(start.error)
        : undefined;

  return (
    <Button
      variant="outline"
      size="sm"
      disabled={!available || !active || start.isPending}
      title={reason ?? `Ingest ${sourceId} now`}
      onClick={() => start.mutate()}
      className={start.error ? "border-destructive/50 text-destructive" : undefined}
    >
      <IconPlayerPlay className="size-4" />
      {start.isPending ? "Starting" : start.error ? "Retry" : "Run"}
    </Button>
  );
}

function describe(error: unknown): string {
  if (error instanceof ApiRequestError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}
