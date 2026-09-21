import { useEffect, useState } from "react";

import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";

/**
 * A bounded stretch of time, given as two canonical period labels.
 *
 * Typed rather than picked from a calendar: the warehouse holds annual,
 * quarterly, monthly and daily series, and a date picker would ask for a day
 * from someone reading a series that has none.
 */
export const PERIOD_PATTERN = /^\d{4}(-(\d{2}|Q[1-4]|S[12])(-\d{2})?)?$/;

export function PeriodFilter({
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
      <p
        className={`text-xs ${invalid ? "text-destructive" : "text-muted-foreground"}`}
      >
        A year, or a year with a month, quarter, half or day — 2026, 2026-01, 2026-Q1.
      </p>
      <Button type="submit" size="sm" disabled={invalid}>
        Apply
      </Button>
    </form>
  );
}
