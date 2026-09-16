import { useQuery } from "@tanstack/react-query";
import { IconX } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import { api } from "~/lib/api";

/**
 * What the observations page can narrow by. Mirrors the API's parameters, and
 * the route's search schema, so a filtered view is a link someone can send.
 */
export type ObservationFilters = {
  indicator?: string;
  geo?: string;
  geo_type?: string;
  period_start?: string;
  period_end?: string;
};

/**
 * Canonical period labels, as Silver writes them. The API rejects anything
 * else, so it is checked here too — a message beside the field beats a 400
 * after a round trip.
 */
const PERIOD_PATTERN = /^\d{4}(-(\d{2}|Q[1-4]|S[12])(-\d{2})?)?$/;

/**
 * The value of the "no filter" option. A Select item cannot hold an empty
 * string, but an empty *trigger* value is what makes the placeholder show —
 * including server-rendered, before hydration resolves the item's label. So the
 * item carries this and the trigger is given "" when nothing is chosen.
 */
const ANY = "__any__";

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

  // Text fields hold their own value while being typed and commit on submit;
  // navigating on every keystroke would put a history entry behind each letter.
  const [geo, setGeo] = useState(value.geo ?? "");
  const [from, setFrom] = useState(value.period_start ?? "");
  const [until, setUntil] = useState(value.period_end ?? "");

  // Keep the fields in step when the URL changes from outside — a back button,
  // or a link someone opened with filters already in it.
  useEffect(() => setGeo(value.geo ?? ""), [value.geo]);
  useEffect(() => setFrom(value.period_start ?? ""), [value.period_start]);
  useEffect(() => setUntil(value.period_end ?? ""), [value.period_end]);

  const fromInvalid = from !== "" && !PERIOD_PATTERN.test(from);
  const untilInvalid = until !== "" && !PERIOD_PATTERN.test(until);
  const active = Object.values(value).some(Boolean);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (fromInvalid || untilInvalid) return;
    onChange({
      geo: geo.trim() || undefined,
      period_start: from.trim() || undefined,
      period_end: until.trim() || undefined,
    });
  }

  const selected = indicators.data?.data.find((i) => i.indicator_id === value.indicator);

  return (
    <div className="space-y-3">
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
        <Field label="Indicator">
          <Select
            value={value.indicator ?? ""}
            onValueChange={(next) =>
              onChange({ indicator: !next || next === ANY ? undefined : next })
            }
            disabled={indicators.isLoading}
          >
            <SelectTrigger className="w-64">
              <SelectValue placeholder="All indicators" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>All indicators</SelectItem>
              {indicators.data?.data.map((indicator) => (
                <SelectItem key={indicator.indicator_id} value={indicator.indicator_id}>
                  {indicator.indicator_id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>

        <Field label="Place">
          <Input
            value={geo}
            onChange={(event) => setGeo(event.target.value)}
            placeholder="IDN, ID-32, WLD"
            className="w-44"
          />
        </Field>

        <Field label="Place type">
          <Select
            value={value.geo_type ?? ""}
            onValueChange={(next) =>
              onChange({ geo_type: !next || next === ANY ? undefined : next })
            }
          >
            <SelectTrigger className="w-40">
              <SelectValue placeholder="All places" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>All places</SelectItem>
              {PLACE_TYPES.map((type) => (
                <SelectItem key={type.value} value={type.value}>
                  {type.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>

        <Field label="From" error={fromInvalid}>
          <Input
            value={from}
            onChange={(event) => setFrom(event.target.value)}
            placeholder="2020"
            className="w-28"
            aria-invalid={fromInvalid}
          />
        </Field>

        <Field label="To" error={untilInvalid}>
          <Input
            value={until}
            onChange={(event) => setUntil(event.target.value)}
            placeholder="2024"
            className="w-28"
            aria-invalid={untilInvalid}
          />
        </Field>

        <Button type="submit" variant="secondary" disabled={fromInvalid || untilInvalid}>
          Apply
        </Button>

        {active ? (
          <Button type="button" variant="ghost" onClick={onClear}>
            <IconX className="size-4" />
            Clear
          </Button>
        ) : null}
      </form>

      {fromInvalid || untilInvalid ? (
        <p className="text-sm text-destructive">
          A period is a year, or a year with a month, quarter, half or day —
          <code className="mx-1 rounded bg-muted px-1 py-0.5">2026</code>
          <code className="mx-1 rounded bg-muted px-1 py-0.5">2026-01</code>
          <code className="mx-1 rounded bg-muted px-1 py-0.5">2026-Q1</code>
        </p>
      ) : null}

      {selected ? (
        <p className="text-sm text-muted-foreground">
          {selected.indicator_id} covers {selected.period_start} to{" "}
          {selected.period_end}, {selected.temporal_resolution}, across{" "}
          {selected.geographies.toLocaleString("en-US")} places
          {selected.unit ? `, in ${selected.unit}` : ""}.
        </p>
      ) : null}
    </div>
  );
}

function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span
        className={`text-xs font-medium ${error ? "text-destructive" : "text-muted-foreground"}`}
      >
        {label}
      </span>
      {children}
    </label>
  );
}
