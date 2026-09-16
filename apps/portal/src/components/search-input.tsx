import { IconSearch, IconX } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { Input } from "~/components/ui/input";

/**
 * Free text over the table.
 *
 * Committed on submit rather than on keystroke: each change is a query and a
 * history entry, and a search that fires per letter puts five requests behind
 * a five-letter word.
 */
export function SearchInput({
  value,
  onSearch,
  placeholder = "Search",
  className,
}: {
  value?: string;
  onSearch: (value: string | undefined) => void;
  placeholder?: string;
  className?: string;
}) {
  const [draft, setDraft] = useState(value ?? "");

  // Follow the URL when it changes from outside — a cleared filter, a pasted
  // link, the back button.
  useEffect(() => setDraft(value ?? ""), [value]);

  return (
    <form
      className={className}
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        onSearch(draft.trim() || undefined);
      }}
    >
      <div className="relative">
        <IconSearch className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          type="search"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={placeholder}
          aria-label={placeholder}
          className="pl-8"
        />
        {draft ? (
          <button
            type="button"
            aria-label="Clear search"
            onClick={() => {
              setDraft("");
              onSearch(undefined);
            }}
            className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
          >
            <IconX className="size-4" />
          </button>
        ) : null}
      </div>
    </form>
  );
}
