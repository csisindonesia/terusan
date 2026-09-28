import { useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import {
  IconChartBar,
  IconDatabase,
  IconFileText,
  IconGavel,
  IconSearch,
} from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";

import {
  Command,
  CommandDialog,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "~/components/ui/command";
import { useDebounced } from "~/hooks/use-debounced";
import { api } from "~/lib/api";
import { datasetLabel, indicatorLabel } from "~/lib/labels";
import { NAVIGATION } from "~/lib/navigation";

/** How many rows one group may contribute, so no single kind fills the list. */
const PER_GROUP = 6;

/**
 * Below this a query matches most of the catalogue, and the list is noise.
 * Pages are still matched from the first letter — there are forty of them.
 */
const MIN_QUERY = 2;

/** Long enough that a typed word is one request, short enough to feel live. */
const DEBOUNCE_MS = 200;

/**
 * Every token has to appear somewhere in the row's text.
 *
 * Tokenised rather than a plain substring because the words a reader
 * remembers rarely arrive in the order the publisher wrote them: "bi rate"
 * should find "BI 7-Day Reverse Repo Rate".
 */
function matches(tokens: string[], ...fields: (string | undefined)[]): boolean {
  if (!tokens.length) return false;
  const haystack = fields.filter(Boolean).join(" ").toLowerCase();
  return tokens.every((token) => haystack.includes(token));
}

/**
 * The navbar's search box: a button dressed as a field, plus ⌘K.
 *
 * A real input here would need somewhere to put the results, and the field is
 * a poor place for them — so the field is the affordance and the dialog is the
 * search.
 *
 * What it searches is the catalogue, not only the navigation: a reader looking
 * for "inflation" wants the series, and a reader who types "perda" wants the
 * regulations. Datasets are few enough to hold whole and filter here — and the
 * list is the same one the pages already load, so that group is usually free.
 * Indicators, documents and regulations are too many for that, so those are
 * asked of the API under the typed query.
 */
export function CommandPalette({ className }: { className?: string }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "k" || !(event.metaKey || event.ctrlKey)) return;
      event.preventDefault();
      setOpen((current) => !current);
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const trimmed = query.trim();
  const tokens = useMemo(
    () => trimmed.toLowerCase().split(/\s+/).filter(Boolean),
    [trimmed],
  );
  const searching = trimmed.length >= MIN_QUERY;

  // The debounce sits on the server-backed groups alone: filtering a list
  // already in memory costs nothing, and delaying it only makes the palette
  // feel slower than it is.
  const debounced = useDebounced(trimmed, DEBOUNCE_MS);
  const remote = debounced.length >= MIN_QUERY ? debounced : "";

  // Asked of the server: the full series list is tens of thousands of rows,
  // far too much to fetch for the six a palette shows.
  const indicators = useQuery({
    queryKey: ["command-indicators", remote],
    queryFn: () => api.indicators({ q: remote, fold: "ohlc", limit: PER_GROUP }),
    enabled: open && remote !== "",
  });
  const datasets = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.datasets(),
    enabled: open,
  });
  const documents = useQuery({
    queryKey: ["command-documents", remote],
    queryFn: () => api.documents({ q: remote, limit: PER_GROUP }),
    enabled: open && remote !== "",
  });
  const regulations = useQuery({
    queryKey: ["command-regulations", remote],
    queryFn: () => api.regulations({ q: remote, limit: PER_GROUP }),
    enabled: open && remote !== "",
  });

  const pages = useMemo(() => {
    const rows = NAVIGATION.flatMap((section) =>
      section.groups.flatMap((group) =>
        group.items
          .filter((item) => item.to)
          .map((item) => ({
            item,
            heading: section.label ? `${section.label} · ${group.label}` : group.label,
            group: group.label,
          })),
      ),
    );

    // Unfiltered when nothing is typed: an empty palette is a dead end, and
    // the navigation is what it used to be for.
    if (!tokens.length) return rows;
    return rows.filter((row) => matches(tokens, row.group, row.item.label));
  }, [tokens]);

  const datasetRows = useMemo(() => {
    if (!searching) return [];
    return (datasets.data?.data ?? [])
      .filter((dataset) =>
        matches(
          tokens,
          datasetLabel(dataset),
          dataset.dataset_id,
          dataset.slug,
          dataset.organization,
          dataset.tags.join(" "),
        ),
      )
      .slice(0, PER_GROUP);
  }, [datasets.data, searching, tokens]);

  const indicatorRows = searching ? (indicators.data?.data ?? []) : [];
  const documentRows = searching ? (documents.data?.data ?? []) : [];
  const regulationRows = searching ? (regulations.data?.data ?? []) : [];

  // The debounce means a typed letter leaves the old results on screen for a
  // moment; saying so is better than a list that silently lags the field.
  const pending =
    searching &&
    (remote !== trimmed ||
      indicators.isFetching ||
      documents.isFetching ||
      regulations.isFetching ||
      datasets.isLoading);

  const empty =
    !pages.length &&
    !indicatorRows.length &&
    !datasetRows.length &&
    !documentRows.length &&
    !regulationRows.length;

  function go(to: () => void) {
    setOpen(false);
    setQuery("");
    to();
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn(
          "flex h-8 w-full items-center gap-2 rounded-lg border border-input/60 bg-input/30 px-2.5 text-sm text-muted-foreground transition-colors hover:bg-input/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden",
          className,
        )}
      >
        <IconSearch className="size-4 shrink-0" />
        <span className="truncate">Search</span>
        <kbd className="ml-auto hidden shrink-0 rounded border bg-background px-1.5 font-sans text-[0.7rem] text-muted-foreground sm:inline-block">
          ⌘K
        </kbd>
      </button>

      <CommandDialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setQuery("");
        }}
        title="Search"
        description="Search indicators, datasets, documents, regulations and pages."
      >
        {/* Filtered here rather than by cmdk: half the rows come from the API
            already matched, and a second pass over them would drop rows the
            server found by a field the label does not show. */}
        <Command shouldFilter={false}>
          <CommandInput
            placeholder="Search indicators, documents, regulations…"
            value={query}
            onValueChange={setQuery}
          />
          <CommandList>
            {empty ? (
              <div className="py-6 text-center text-sm text-muted-foreground">
                {pending ? "Searching…" : "No match."}
              </div>
            ) : null}

            {indicatorRows.length ? (
              <CommandGroup heading="Indicators">
                {indicatorRows.map((indicator) => (
                  <CommandItem
                    key={indicator.indicator_id}
                    value={`indicator:${indicator.indicator_id}`}
                    onSelect={() =>
                      go(
                        () =>
                          void navigate({
                            to: "/indicators/$indicatorId",
                            params: { indicatorId: indicator.indicator_id },
                          }),
                      )
                    }
                  >
                    <IconChartBar />
                    <span className="truncate">{indicatorLabel(indicator)}</span>
                    <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                      {indicator.sources.join(", ")}
                    </span>
                  </CommandItem>
                ))}
              </CommandGroup>
            ) : null}

            {datasetRows.length ? (
              <CommandGroup heading="Datasets">
                {datasetRows.map((dataset) => (
                  <CommandItem
                    key={dataset.dataset_id}
                    value={`dataset:${dataset.dataset_id}`}
                    onSelect={() =>
                      go(
                        () =>
                          void navigate({
                            to: "/datasets/$datasetId",
                            params: { datasetId: dataset.dataset_id },
                          }),
                      )
                    }
                  >
                    <IconDatabase />
                    <span className="truncate">{datasetLabel(dataset)}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            ) : null}

            {documentRows.length ? (
              <CommandGroup heading="Documents">
                {documentRows.map((document) => (
                  <CommandItem
                    key={document.document_id}
                    value={`document:${document.document_id}`}
                    onSelect={() =>
                      go(
                        () =>
                          void navigate({
                            to: "/documents/$documentId",
                            params: { documentId: document.document_id },
                          }),
                      )
                    }
                  >
                    <IconFileText />
                    <span className="truncate">{document.title}</span>
                    {document.publisher ? (
                      <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                        {document.publisher}
                      </span>
                    ) : null}
                  </CommandItem>
                ))}
              </CommandGroup>
            ) : null}

            {regulationRows.length ? (
              <CommandGroup heading="Regulations">
                {regulationRows.map((regulation) => (
                  <CommandItem
                    key={regulation.key}
                    value={`regulation:${regulation.key}`}
                    onSelect={() =>
                      go(
                        () =>
                          void navigate({
                            to: "/regulations/$key",
                            params: { key: regulation.key },
                          }),
                      )
                    }
                  >
                    <IconGavel />
                    <span className="truncate">{regulation.title}</span>
                    {regulation.year ? (
                      <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                        {regulation.year}
                      </span>
                    ) : null}
                  </CommandItem>
                ))}
              </CommandGroup>
            ) : null}

            {/* Pages last once something is typed: a reader who types a series
                name wants the series, not the page that lists them. Only pages
                that exist are listed — the sidebar shows the unbuilt ones
                because the shape of the platform is worth seeing, but a
                palette is for going somewhere. */}
            {pages.length ? (
              <CommandGroup heading="Pages">
                {pages.map(({ item, heading }) => (
                  <CommandItem
                    key={item.to}
                    value={`page:${item.to}`}
                    onSelect={() => go(() => void navigate({ to: item.to! }))}
                  >
                    <item.icon />
                    <span>{item.label}</span>
                    <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                      {heading}
                    </span>
                  </CommandItem>
                ))}
              </CommandGroup>
            ) : null}
          </CommandList>
        </Command>
      </CommandDialog>
    </>
  );
}
