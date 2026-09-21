/**
 * What narrows a run history.
 *
 * The journal has no facets endpoint, so the options come from what the
 * registry knows rather than from what the journal holds: the stages are a
 * closed set the pipelines write, the sources are the registry's own list, and
 * a pipeline name is typed because there are one per series and no list of
 * them is served.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  AddFilterChip,
  ChoiceList,
  FilterChip,
  summarise,
} from "~/components/filter-chip";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { api } from "~/lib/api";
import { toggle } from "~/lib/multi";

export type RunFilters = {
  kind?: string[];
  status?: string[];
  source?: string[];
  pipeline?: string[];
};

/** The stages a run belongs to, as `terusan` names them. */
const KINDS = [
  { value: "ingest", label: "Ingest", hint: "Fetching from the agency" },
  { value: "extract", label: "Extract", hint: "Reading RAW into Bronze" },
  { value: "normalize", label: "Normalize", hint: "Bronze into Silver" },
];

const STATUSES = [
  { value: "succeeded", label: "Succeeded" },
  // A run that died mid-flight writes no row at all, so "failed" here means
  // failed *and recorded* — the gap between runs is the other symptom.
  { value: "failed", label: "Failed" },
];

type Props = {
  value: RunFilters;
  onChange: (next: RunFilters) => void;
  onClear: () => void;
};

export function RunFilterBar({ value, onChange, onClear }: Props) {
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: () => api.sources(),
  });

  const kinds = value.kind ?? [];
  const statuses = value.status ?? [];
  const chosenSources = value.source ?? [];
  const pipelines = value.pipeline ?? [];
  const active =
    kinds.length || statuses.length || chosenSources.length || pipelines.length;

  const sourceName = (id: string) =>
    (sources.data?.data ?? []).find((source) => source.source_id === id)?.name ?? id;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <FilterChip
        label="Stage"
        value={summarise(
          kinds,
          (v) => KINDS.find((kind) => kind.value === v)?.label ?? v,
        )}
        onClear={() => onChange({ kind: undefined })}
      >
        <ChoiceList
          options={KINDS}
          selected={kinds}
          onToggle={(kind) => onChange({ kind: toggle(kinds, kind) })}
          onClear={() => onChange({ kind: undefined })}
        />
      </FilterChip>

      <FilterChip
        label="Status"
        value={summarise(
          statuses,
          (v) => STATUSES.find((status) => status.value === v)?.label ?? v,
        )}
        onClear={() => onChange({ status: undefined })}
      >
        <ChoiceList
          options={STATUSES}
          selected={statuses}
          onToggle={(status) => onChange({ status: toggle(statuses, status) })}
          onClear={() => onChange({ status: undefined })}
        />
      </FilterChip>

      <FilterChip
        label="Source"
        value={summarise(chosenSources, sourceName)}
        onClear={() => onChange({ source: undefined })}
      >
        <ChoiceList
          // Named rather than slugged: the slug is what the filter sends and
          // the name is what the reader recognises.
          options={(sources.data?.data ?? []).map((source) => ({
            value: source.source_id,
            label: source.name,
            hint: source.source_id,
          }))}
          selected={chosenSources}
          onToggle={(id) => onChange({ source: toggle(chosenSources, id) })}
          onClear={() => onChange({ source: undefined })}
          empty="The source registry could not be read."
        />
      </FilterChip>

      <FilterChip
        label="Pipeline"
        value={summarise(pipelines)}
        onClear={() => onChange({ pipeline: undefined })}
      >
        <PipelineFilter
          initial={pipelines[0] ?? ""}
          onApply={(name) => onChange({ pipeline: name ? [name] : undefined })}
        />
      </FilterChip>

      <AddFilterChip>
        <p className="px-2 py-3 text-sm text-muted-foreground">
          The journal is filtered by stage, status, source and pipeline. A date range
          arrives when the endpoint accepts one; until then the history is newest first
          and paged.
        </p>
      </AddFilterChip>

      {active ? (
        <Button variant="ghost" size="sm" className="h-8" onClick={onClear}>
          Clear all
        </Button>
      ) : null}
    </div>
  );
}

/**
 * A pipeline is named exactly, not searched: `/v1/runs` matches the column
 * against the value it is given, and there is no endpoint listing the names.
 * The table's own Pipeline column is where a reader finds one to type.
 */
function PipelineFilter({
  initial,
  onApply,
}: {
  initial: string;
  onApply: (value: string) => void;
}) {
  const [draft, setDraft] = useState(initial);
  useEffect(() => setDraft(initial), [initial]);

  return (
    <form
      className="grid gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        onApply(draft.trim());
      }}
    >
      <Input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder="normalize-cocoa_price_close"
        autoFocus
      />
      <p className="text-xs text-muted-foreground">
        The exact pipeline name, as the table prints it.
      </p>
      <Button type="submit" size="sm">
        Apply
      </Button>
    </form>
  );
}
