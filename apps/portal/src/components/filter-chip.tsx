import { IconCheck, IconChevronDown, IconPlus, IconX } from "@tabler/icons-react";

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
export function ChoiceList({
  options,
  selected,
  onToggle,
  onClear,
  empty = "Nothing to choose from.",
}: {
  options: ChoiceOption[];
  selected: string[];
  onToggle: (value: string) => void;
  onClear?: () => void;
  empty?: string;
}) {
  if (!options.length) {
    return <p className="px-2 py-3 text-sm text-muted-foreground">{empty}</p>;
  }

  return (
    <div className="grid gap-0.5">
      {options.map((option) => {
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
                isOn ? "border-primary bg-primary text-primary-foreground" : "border-input",
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
export function summarise(selected: string[], labelFor?: (value: string) => string): string | undefined {
  if (!selected.length) return undefined;
  const first = selected[0] as string;
  const label = labelFor ? labelFor(first) : first;
  // Naming every value makes the chip wider than the table; the count carries
  // the rest.
  return selected.length === 1 ? label : `${label} +${selected.length - 1}`;
}
