import { IconChevronDown, IconPlus, IconX } from "@tabler/icons-react";
import type { Icon } from "@tabler/icons-react";

import { Button } from "~/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "~/components/ui/popover";
import { cn } from "~/lib/utils";

/**
 * A filter as a pill: what is being filtered, and what it is set to.
 *
 * A row of chips says the whole filter state at a glance, where a row of empty
 * inputs says only what could be filtered. Set chips read darker and carry a
 * clear button; unset ones stay quiet.
 */
export function FilterChip({
  icon: ChipIcon,
  label,
  value,
  onClear,
  children,
}: {
  icon?: Icon;
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
              {ChipIcon ? (
                <ChipIcon className="size-4 shrink-0 text-muted-foreground" />
              ) : null}
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
