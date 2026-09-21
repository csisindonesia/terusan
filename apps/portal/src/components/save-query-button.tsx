import { IconDeviceFloppy } from "@tabler/icons-react";
import { useState } from "react";

import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "~/components/ui/popover";
import { describeSearch, saveQuery, type QueryKind } from "~/lib/workspace";

/**
 * Keep the filters currently on screen.
 *
 * Named rather than saved bare: a shelf of four queries called "observations"
 * is a shelf nobody reads, and by the time someone comes back the name is the
 * only thing left that says what they were asking. The filters are shown under
 * the field so the name is chosen against what it will reopen.
 *
 * What is saved is the filters, never the rows — the point of a saved query is
 * that it re-runs.
 */
export function SaveQueryButton({
  kind,
  path,
  search,
  suggestion,
  disabled = false,
  label = "Save query",
}: {
  kind: QueryKind;
  /** The route it reopens — the same path the page is on. */
  path: string;
  search: Record<string, unknown>;
  /** What the name field starts at. */
  suggestion?: string;
  disabled?: boolean;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [saved, setSaved] = useState(false);

  const summary = describeSearch(search);

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setName(suggestion ?? "");
          setSaved(false);
        }
      }}
    >
      <PopoverTrigger
        render={
          <Button variant="outline" size="sm" disabled={disabled}>
            <IconDeviceFloppy className="size-4" />
            {saved ? "Saved" : label}
          </Button>
        }
      />
      <PopoverContent align="end" className="w-80 p-3">
        <form
          className="grid gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            saveQuery({
              name: name.trim() || suggestion || summary,
              kind,
              path,
              search,
              summary,
            });
            setName("");
            setSaved(true);
            setOpen(false);
          }}
        >
          <label className="text-sm font-medium" htmlFor="save-query-name">
            Name this query
          </label>
          <Input
            id="save-query-name"
            autoFocus
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={suggestion ?? "What you are asking"}
          />
          <p className="text-xs break-words text-muted-foreground">{summary}</p>
          <Button type="submit" size="sm">
            Save
          </Button>
        </form>
      </PopoverContent>
    </Popover>
  );
}
