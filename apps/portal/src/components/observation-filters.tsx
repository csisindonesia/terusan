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

export type ObservationFilters = {
  indicator?: string[];
  geo?: string;
  geo_type?: string[];
  period_start?: string;
  period_end?: string;
};

/**
 * Canonical period labels, as Silver writes them. The API rejects anything
 * else, so it is checked here too — a message beside the field beats a 400
 * after a round trip.
 */
const PERIOD_PATTERN = /^\d{4}(-(\d{2}|Q[1-4]|S[12])(-\d{2})?)?$/;

const PLACE_TYPES = [
  { value: "country", label: "Countries" },
  // Aggregates are sums of the countries beside them, so a total over
  // everything counts most places more than once.
  { value: "region", label: "Aggregates" },
  { value: "province", label: "Provinces" },
];

type Props = {
  value: ObservationFilters;
  onChange: (next: ObservationFilters) => void;
  onClear: () => void;
};

export function ObservationFilterBar({ value, onChange, onClear }: Props) {
  const indicators = useQuery({
    queryKey: ["indicators"],
    queryFn: () => api.indicators(),
  });

  const chosenIndicators = value.indicator ?? [];
  const chosenTypes = value.geo_type ?? [];
  const active =
    chosenIndicators.length ||
    chosenTypes.length ||
    value.geo ||
    value.period_start ||
    value.period_end;

  const periodLabel =
    value.period_start && value.period_end
      ? `${value.period_start}–${value.period_end}`
      : (value.period_start ?? value.period_end);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <FilterChip
        label="Indicator"
        value={summarise(chosenIndicators)}
        onClear={() => onChange({ indicator: undefined })}
      >
        <ChoiceList
          options={(indicators.data?.data ?? []).map((indicator) => ({
            value: indicator.indicator_id,
            label: indicator.indicator_id,
            hint: `${indicator.period_start}–${indicator.period_end}`,
          }))}
          selected={chosenIndicators}
          onToggle={(id) => onChange({ indicator: toggle(chosenIndicators, id) })}
          onClear={() => onChange({ indicator: undefined })}
          empty="No indicators yet."
        />
      </FilterChip>

      <FilterChip
        label="Place type"
        value={summarise(
          chosenTypes,
          (v) => PLACE_TYPES.find((t) => t.value === v)?.label ?? v,
        )}
        onClear={() => onChange({ geo_type: undefined })}
      >
        <ChoiceList
          options={PLACE_TYPES}
          selected={chosenTypes}
          onToggle={(type) => onChange({ geo_type: toggle(chosenTypes, type) })}
          onClear={() => onChange({ geo_type: undefined })}
        />
      </FilterChip>

      <FilterChip
        label="Place"
        value={value.geo}
        onClear={() => onChange({ geo: undefined })}
      >
        <TextFilter
          initial={value.geo ?? ""}
          placeholder="IDN, ID-32, WLD"
          hint="A geography identifier, as the table shows it."
          onApply={(geo) => onChange({ geo: geo || undefined })}
        />
      </FilterChip>

      <FilterChip
        label="Period"
        value={periodLabel}
        onClear={() => onChange({ period_start: undefined, period_end: undefined })}
      >
        <PeriodFilter
          from={value.period_start ?? ""}
          until={value.period_end ?? ""}
          onApply={(period_start, period_end) =>
            onChange({
              period_start: period_start || undefined,
              period_end: period_end || undefined,
            })
          }
        />
      </FilterChip>

      <AddFilterChip>
        <p className="px-2 py-3 text-sm text-muted-foreground">
          Every filter the API supports is already shown. Source and status
          filters arrive with the datasets that need them.
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

function TextFilter({
  initial,
  placeholder,
  hint,
  onApply,
}: {
  initial: string;
  placeholder: string;
  hint: string;
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
        placeholder={placeholder}
        autoFocus
      />
      <p className="text-xs text-muted-foreground">{hint}</p>
      <Button type="submit" size="sm">
        Apply
      </Button>
    </form>
  );
}

function PeriodFilter({
  from,
  until,
  onApply,
}: {
  from: string;
  until: string;
  onApply: (from: string, until: string) => void;
}) {
  const [start, setStart] = useState(from);
  const [end, setEnd] = useState(until);

  useEffect(() => setStart(from), [from]);
  useEffect(() => setEnd(until), [until]);

  const invalid =
    (start !== "" && !PERIOD_PATTERN.test(start)) ||
    (end !== "" && !PERIOD_PATTERN.test(end));

  return (
    <form
      className="grid gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (!invalid) onApply(start.trim(), end.trim());
      }}
    >
      <div className="grid grid-cols-2 gap-2">
        <Input
          value={start}
          onChange={(event) => setStart(event.target.value)}
          placeholder="From"
          aria-invalid={invalid}
          autoFocus
        />
        <Input
          value={end}
          onChange={(event) => setEnd(event.target.value)}
          placeholder="To"
          aria-invalid={invalid}
        />
      </div>
      <p className={`text-xs ${invalid ? "text-destructive" : "text-muted-foreground"}`}>
        A year, or a year with a month, quarter, half or day — 2026, 2026-01,
        2026-Q1.
      </p>
      <Button type="submit" size="sm" disabled={invalid}>
        Apply
      </Button>
    </form>
  );
}
