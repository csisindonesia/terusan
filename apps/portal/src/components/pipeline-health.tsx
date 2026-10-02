import { Badge } from "~/components/ui/badge";
import type { Pipeline } from "~/lib/api";
import { formatCount } from "~/lib/format";
import { HEALTH_LABELS, emptyNote, healthOf } from "~/lib/pipelines";

/**
 * A pipeline's state as one badge.
 *
 * A failure says how many in a row, because one bad night and a week of them
 * call for different responses and look identical as a bare "Failed".
 */
export function PipelineHealthBadge({ pipeline }: { pipeline: Pipeline }) {
  const health = healthOf(pipeline);
  if (health === "failing") {
    return (
      <Badge variant="destructive" title={pipeline.last_error}>
        Failing
        {pipeline.failing_streak > 1
          ? ` · ${formatCount(pipeline.failing_streak)} in a row`
          : null}
      </Badge>
    );
  }
  if (health === "empty") {
    return (
      <Badge variant="outline" title={emptyNote(pipeline.kind)}>
        {pipeline.kind === "ingest" ? "Nothing new" : HEALTH_LABELS.empty}
      </Badge>
    );
  }
  if (health === "dry-run") return <Badge variant="outline">Dry run</Badge>;
  return <Badge variant="secondary">Succeeded</Badge>;
}
