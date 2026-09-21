import {
  IconCheck,
  IconChevronDown,
  IconPlus,
  IconSearch,
  IconX,
} from "@tabler/icons-react";
import { useState } from "react";

import { Button } from "~/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "~/components/ui/popover";
import { cn } from "~/lib/utils";

/**
 * A filter as a pill: what is being filtered, and what it is set to.
 *
 * A row of chips says the whole filter state at a glance, where a row of empty
 * inputs says only what could be filtered. Set chips read darker and carry a
 * clear button; unset ones stay quiet.
 */
export function FilterChip({
  label,
  value,
  onClear,
  children,
}: {
  label: string;
  /** Present when the filter is set; its absence is what makes a chip quiet. */
  value?: string;
  onClear?: () => void;
  /** The control shown when the chip is opened. */
  children: React.ReactNode;
}) {
  const isSet = Boolean(value);

  return (
    <div
      className={cn(
        "flex h-8 items-center rounded-full border text-sm transition-colors",
        isSet ? "border-foreground/20 bg-muted" : "border-dashed hover:bg-muted/50",
      )}
    >
      <Popover>
        <PopoverTrigger
          render={
            <button
              type="button"
              className="flex h-8 items-center gap-1.5 rounded-full pr-2 pl-3 outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              <span className={isSet ? "text-muted-foreground" : ""}>{label}</span>
              {isSet ? <span className="font-medium">{value}</span> : null}
              <IconChevronDown className="size-4 text-muted-foreground" />
            </button>
          }
        />
        <PopoverContent align="start" className="w-64 p-2">
          {children}
        </PopoverContent>
      </Popover>

      {isSet && onClear ? (
        <button
          type="button"
          onClick={onClear}
          aria-label={`Clear ${label} filter`}
          className="mr-1 rounded-full p-1 text-muted-foreground hover:bg-background hover:text-foreground"
        >
          <IconX className="size-3.5" />
        </button>
      ) : null}
    </div>
  );
}

/** The chip that offers the filters not yet shown. */
export function AddFilterChip({ children }: { children: React.ReactNode }) {
  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            size="sm"
            className="h-8 rounded-full border border-dashed text-muted-foreground"
          >
            <IconPlus className="size-4" />
            Add filter
          </Button>
        }
      />
      <PopoverContent align="start" className="w-56 p-1">
        {children}
      </PopoverContent>
    </Popover>
  );
}

export type ChoiceOption = { value: string; label: string; hint?: string };

/**
 * The list inside a chip. Several values can be held at once.
 *
 * Multi-select because these questions are naturally plural: countries *and*
 * provinces, annual *and* monthly. Forcing one at a time makes the reader run
 * the query twice and add up the answers themselves.
 */
/**
 * Past this many options, scanning the list costs more than typing does — and
 * thirty-one commodities do not fit on screen at all, so the ones past the fold
 * are invisible rather than merely slow to find.
 */
const SEARCHABLE_FROM = 6;

export function ChoiceList({
  options,
  selected,
  onToggle,
  onClear,
  empty = "Nothing to choose from.",
  searchPlaceholder = "Search",
}: {
  options: ChoiceOption[];
  selected: string[];
  onToggle: (value: string) => void;
  onClear?: () => void;
  empty?: string;
  searchPlaceholder?: string;
}) {
  const [needle, setNeedle] = useState("");

  if (!options.length) {
    return <p className="px-2 py-3 text-sm text-muted-foreground">{empty}</p>;
  }

  const searchable = options.length >= SEARCHABLE_FROM;
  const query = needle.trim().toLowerCase();
  const shown =
    searchable && query
      ? options.filter((option) => option.label.toLowerCase().includes(query))
      : options;

  return (
    <div className="grid gap-0.5">
      {searchable ? (
        <div className="relative mb-1">
          <IconSearch className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            type="search"
            value={needle}
            onChange={(event) => setNeedle(event.target.value)}
            placeholder={searchPlaceholder}
            aria-label={searchPlaceholder}
            // Filters as you type rather than on submit: the list it narrows is
            // right there, so a round trip through a button would be a step
            // with nothing to show for it.
            className="h-8 w-full rounded-md border bg-transparent pl-7 pr-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring"
          />
        </div>
      ) : null}

      {/* Kept as a list with a message rather than an empty box: a filter that
          matches nothing should say so, not look broken. */}
      {shown.length === 0 ? (
        <p className="px-2 py-3 text-sm text-muted-foreground">
          Nothing matches “{needle.trim()}”.
        </p>
      ) : null}

      <div className="grid max-h-64 gap-0.5 overflow-y-auto">
        {shown.map((option) => {
          const isOn = selected.includes(option.value);
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => onToggle(option.value)}
              aria-pressed={isOn}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted"
            >
              {/* A tick in a box rather than a highlighted row: with several
                values held at once, the reader needs to see which, not just
                that something is on. */}
              <span
                className={cn(
                  "flex size-4 shrink-0 items-center justify-center rounded-[4px] border",
                  isOn
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-input",
                )}
              >
                {isOn ? <IconCheck className="size-3" /> : null}
              </span>
              <span className="truncate">{option.label}</span>
              {option.hint ? (
                <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                  {option.hint}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>

      {selected.length && onClear ? (
        <>
          <div className="my-1 border-t" />
          <button
            type="button"
            onClick={onClear}
            className="rounded-md px-2 py-1.5 text-left text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            Clear selection
          </button>
        </>
      ) : null}
    </div>
  );
}

/** How a multi-value filter reads on its chip. */
export function summarise(
  selected: string[],
  labelFor?: (value: string) => string,
): string | undefined {
  if (!selected.length) return undefined;
  const first = selected[0] as string;
  const label = labelFor ? labelFor(first) : first;
  // Naming every value makes the chip wider than the table; the count carries
  // the rest.
  return selected.length === 1 ? label : `${label} +${selected.length - 1}`;
}
