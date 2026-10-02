import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  IconAlertTriangle,
  IconArrowBackUp,
  IconCheck,
  IconDatabase,
  IconDatabasePlus,
  IconExternalLink,
  IconPackageImport,
  IconTrash,
  IconX,
} from "@tabler/icons-react";
import { useState } from "react";
import { z } from "zod";

import { ChoiceList, FilterChip, summarise } from "~/components/filter-chip";
import { PageHeader } from "~/components/page-header";
import { SearchInput } from "~/components/search-input";
import { StatRow } from "~/components/stat-row";
import { StickyHeader } from "~/components/sticky-header";
import { SuggestData } from "~/components/suggest-data";
import { TablePagination } from "~/components/table-pagination";
import { TableToolbar } from "~/components/table-toolbar";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "~/components/ui/dialog";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import { Textarea } from "~/components/ui/textarea";
import {
  ApiRequestError,
  api,
  type Dataset,
  type Suggestion,
  type SuggestionDecision,
} from "~/lib/api";
import { formatCount, formatDate, formatRelative } from "~/lib/format";
import { datasetLabel } from "~/lib/labels";
import { toggle } from "~/lib/multi";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";
import { capabilitiesQuery, useSessionState } from "~/lib/session";
import {
  REASONS,
  STATUSES,
  cadenceLabel,
  reasonLabel,
  statusLabel,
} from "~/lib/suggestions";

/**
 * Every request to collect a source, and what became of it.
 *
 * The dialog in the top bar files a request and shows the last few; this is
 * where an asker comes back to find the answer, and where an administrator
 * works through the queue. Both read the same list on purpose: an answer is
 * only worth giving if the person who asked can find it, and the next person
 * about to ask for the same source should find it too.
 *
 * Only an administrator may accept or decline (the API refuses anybody else),
 * and a request declined because the warehouse already holds the data shows
 * that dataset — what it covers and a link to open it — because "we already
 * have this" is an answer only when it says where.
 */

const searchSchema = z.object({
  status: listParam,
  mine: textParam,
  q: textParam,
  page: z.number().int().min(0).optional(),
});

export const Route = createFileRoute("/suggestions")({
  validateSearch: searchSchema,
  component: Suggestions,
});

const PAGE_SIZE = 25;

function Suggestions() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const { session, hasAuth, isLoading: sessionLoading } = useSessionState();
  const capabilities = useQuery(capabilitiesQuery);
  const isAdmin = session?.user.role === "admin";
  const available = capabilities.data?.suggestions ?? false;

  const query = useQuery({
    queryKey: ["suggestions", "all"],
    // The API's ceiling. A queue longer than that is one to prune, not page.
    queryFn: async () => (await api.suggestions(200)).data,
    enabled: available,
    retry: false,
  });
  // The whole catalogue, already cached by every page that lists datasets,
  // so a declined request can show the dataset that answers it.
  const catalogue = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });
  const datasets = new Map(
    (catalogue.data?.data ?? []).map((dataset) => [dataset.dataset_id, dataset]),
  );

  const all = query.data ?? [];
  const statuses = asTextList(search.status);
  const mine = asText(search.mine) === "1";
  const needle = asText(search.q)?.toLowerCase() ?? "";

  const rows = all.filter(
    (row) =>
      (!statuses.length || statuses.includes(row.status)) &&
      (!mine || row.requested_by === session?.user.id) &&
      (!needle ||
        row.title.toLowerCase().includes(needle) ||
        row.url.toLowerCase().includes(needle) ||
        (row.description ?? "").toLowerCase().includes(needle) ||
        row.requested_by_email.toLowerCase().includes(needle)),
  );
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const page = Math.min(search.page ?? 0, pageCount - 1);
  const visible = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const counted = (status: string) => all.filter((row) => row.status === status).length;
  const waiting = counted("open");
  const show = (status: string) =>
    navigate({ search: (prev) => ({ ...prev, status: [status], page: 0 }) });
  const clear = (key: "status" | "mine") =>
    navigate({ search: (prev) => ({ ...prev, [key]: undefined, page: 0 }) });

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Data Requests"
            count={rows.length}
            isLoading={query.isLoading}
            description="Sources readers have asked the warehouse to collect, and the answer to each. An administrator accepts or declines them; a request declined because the data is already here links to where it is."
            actions={
              available ? (
                <SuggestData
                  trigger={
                    <Button size="sm">
                      <IconDatabasePlus className="size-4" />
                      Suggest data
                    </Button>
                  }
                />
              ) : null
            }
          />
        }
        filters={
          <TableToolbar
            filters={
              <>
                <FilterChip
                  label="Status"
                  value={summarise(statuses.map(statusLabel))}
                  onClear={() => clear("status")}
                >
                  <ChoiceList
                    options={STATUSES.map((value) => ({
                      value,
                      label: statusLabel(value),
                    }))}
                    selected={statuses}
                    onToggle={(value) =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          status: toggle(statuses, value),
                          page: 0,
                        }),
                      })
                    }
                    onClear={() => clear("status")}
                  />
                </FilterChip>
                {session ? (
                  <Button
                    variant={mine ? "secondary" : "outline"}
                    size="sm"
                    className="h-8 border-dashed"
                    aria-pressed={mine}
                    onClick={() =>
                      navigate({
                        search: (prev) => ({
                          ...prev,
                          mine: mine ? undefined : "1",
                          page: 0,
                        }),
                      })
                    }
                  >
                    Asked by me
                  </Button>
                ) : null}
                {statuses.length || mine || search.q ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8"
                    onClick={() => navigate({ search: {} })}
                  >
                    Clear all
                  </Button>
                ) : null}
              </>
            }
            search={
              <SearchInput
                value={asText(search.q)}
                placeholder="Search titles, addresses and who asked"
                onSearch={(q) =>
                  navigate({ search: (prev) => ({ ...prev, q, page: 0 }) })
                }
              />
            }
          />
        }
      />

      {!capabilities.isLoading && !available ? (
        <Alert>
          <IconAlertTriangle />
          <AlertDescription>
            This deployment keeps no requests queue. Write to the address on the{" "}
            <Link to="/contact" className="underline underline-offset-4">
              contact page
            </Link>{" "}
            instead.
          </AlertDescription>
        </Alert>
      ) : hasAuth && !sessionLoading && !session ? (
        <Alert>
          <IconAlertTriangle />
          <AlertDescription>
            <Link to="/login" className="underline underline-offset-4">
              Log in
            </Link>{" "}
            to see what has been asked for and what became of it.
          </AlertDescription>
        </Alert>
      ) : (
        <>
          <StatRow
            isLoading={query.isLoading}
            stats={STATUSES.map((status) => ({
              label: statusLabel(status),
              value: formatCount(counted(status)),
              // What is waiting is a problem only for the person who can
              // clear it.
              attention: status === "open" && isAdmin && waiting > 0,
              onSelect: () => show(status),
            }))}
          />

          {query.isError ? (
            <Alert variant="destructive">
              <IconAlertTriangle />
              <AlertDescription>{errorText(query.error)}</AlertDescription>
            </Alert>
          ) : null}

          {query.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading requests…</p>
          ) : visible.length ? (
            <ul className="space-y-3">
              {visible.map((row) => (
                <RequestCard
                  key={row.id}
                  suggestion={row}
                  dataset={row.dataset_id ? datasets.get(row.dataset_id) : undefined}
                  catalogueLoading={catalogue.isLoading}
                  isAdmin={isAdmin}
                  isMine={row.requested_by === session?.user.id}
                />
              ))}
            </ul>
          ) : (
            <p className="rounded-lg border px-4 py-8 text-center text-sm text-muted-foreground">
              {all.length
                ? "No request matches this filter."
                : "Nobody has asked for anything yet."}
            </p>
          )}

          <TablePagination
            page={page}
            total={rows.length}
            pageSize={PAGE_SIZE}
            onPage={(next) => navigate({ search: (prev) => ({ ...prev, page: next }) })}
            summary={
              rows.length ? (
                <>
                  {formatCount(page * PAGE_SIZE + 1)}–
                  {formatCount(Math.min((page + 1) * PAGE_SIZE, rows.length))} of{" "}
                  {formatCount(rows.length)} request{rows.length === 1 ? "" : "s"}
                  {rows.length !== all.length
                    ? ` (${formatCount(all.length)} total)`
                    : null}
                </>
              ) : null
            }
          />
        </>
      )}
    </div>
  );
}

function errorText(error: unknown): string {
  return error instanceof ApiRequestError
    ? error.message
    : "The API could not be reached.";
}

/** The status, coloured by whether the asker got what they wanted. */
function StatusBadge({ status }: { status: string }) {
  const tone: Record<string, string> = {
    open: "border-amber-500/50 text-amber-700 dark:text-amber-400",
    planned: "border-sky-500/50 text-sky-700 dark:text-sky-400",
    ingested: "border-emerald-500/50 text-emerald-700 dark:text-emerald-400",
  };
  return status === "declined" ? (
    <Badge variant="destructive">{statusLabel(status)}</Badge>
  ) : (
    <Badge variant="outline" className={tone[status]}>
      {statusLabel(status)}
    </Badge>
  );
}

type Dialogue = "decline" | "ingested" | null;

function RequestCard({
  suggestion,
  dataset,
  catalogueLoading,
  isAdmin,
  isMine,
}: {
  suggestion: Suggestion;
  dataset?: Dataset;
  catalogueLoading: boolean;
  isAdmin: boolean;
  isMine: boolean;
}) {
  const queryClient = useQueryClient();
  const [dialogue, setDialogue] = useState<Dialogue>(null);

  const decide = useMutation({
    mutationFn: (decision: SuggestionDecision) =>
      api.updateSuggestion(suggestion.id, decision),
    onSuccess: async () => {
      setDialogue(null);
      await queryClient.invalidateQueries({ queryKey: ["suggestions"] });
    },
  });
  const withdraw = useMutation({
    mutationFn: () => api.deleteSuggestion(suggestion.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["suggestions"] }),
  });

  const asked =
    formatRelative(suggestion.created_at) ?? formatDate(suggestion.created_at);
  const reviewed = suggestion.reviewed_at
    ? (formatRelative(suggestion.reviewed_at) ?? formatDate(suggestion.reviewed_at))
    : undefined;

  return (
    <li className="space-y-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex items-center gap-1.5">
            <h2 className="font-medium text-balance">{suggestion.title}</h2>
            <a
              href={suggestion.url}
              target="_blank"
              rel="noreferrer noopener"
              className="shrink-0 text-muted-foreground hover:text-foreground"
              aria-label={`Open ${suggestion.title} at its source`}
            >
              <IconExternalLink className="size-4" />
            </a>
          </div>
          <p className="truncate text-xs text-muted-foreground" title={suggestion.url}>
            {suggestion.url}
          </p>
          <p className="text-xs text-muted-foreground">
            {cadenceLabel(suggestion.cadence)} · asked by{" "}
            {isMine ? "you" : suggestion.requested_by_email} · {asked}
          </p>
        </div>
        <StatusBadge status={suggestion.status} />
      </div>

      {suggestion.description ? (
        <p className="text-sm whitespace-pre-line">{suggestion.description}</p>
      ) : null}

      {suggestion.status !== "open" ? (
        <Answer
          suggestion={suggestion}
          dataset={dataset}
          catalogueLoading={catalogueLoading}
          reviewed={reviewed}
        />
      ) : null}

      {decide.isError || withdraw.isError ? (
        <Alert variant="destructive">
          <IconAlertTriangle />
          <AlertDescription>
            {errorText(decide.error ?? withdraw.error)}
          </AlertDescription>
        </Alert>
      ) : null}

      {isAdmin || isMine ? (
        <div className="flex flex-wrap items-center gap-2 border-t pt-3">
          {isAdmin && suggestion.status === "open" ? (
            <>
              <Button
                size="sm"
                disabled={decide.isPending}
                onClick={() => decide.mutate({ status: "planned" })}
              >
                <IconCheck className="size-4" />
                Accept
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={decide.isPending}
                onClick={() => setDialogue("decline")}
              >
                <IconX className="size-4" />
                Decline…
              </Button>
            </>
          ) : null}
          {isAdmin && suggestion.status === "planned" ? (
            <Button
              size="sm"
              disabled={decide.isPending}
              onClick={() => setDialogue("ingested")}
            >
              <IconPackageImport className="size-4" />
              Mark ingested…
            </Button>
          ) : null}
          {isAdmin && suggestion.status !== "open" ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ status: "open" })}
            >
              <IconArrowBackUp className="size-4" />
              Reopen
            </Button>
          ) : null}
          {/* Withdrawing is the asker's own call as much as an
              administrator's, which the API enforces the same way. */}
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto text-muted-foreground"
            disabled={withdraw.isPending}
            onClick={() => {
              if (window.confirm(`Delete the request “${suggestion.title}”?`)) {
                withdraw.mutate();
              }
            }}
          >
            <IconTrash className="size-4" />
            {isMine ? "Withdraw" : "Delete"}
          </Button>
        </div>
      ) : null}

      {isAdmin ? (
        <DecisionDialog
          mode={dialogue}
          suggestion={suggestion}
          isPending={decide.isPending}
          error={decide.isError ? errorText(decide.error) : undefined}
          onClose={() => {
            setDialogue(null);
            decide.reset();
          }}
          onDecide={(decision) => decide.mutate(decision)}
        />
      ) : null}
    </li>
  );
}

/** What the administrator said, and the dataset that answers it, if one does. */
function Answer({
  suggestion,
  dataset,
  catalogueLoading,
  reviewed,
}: {
  suggestion: Suggestion;
  dataset?: Dataset;
  catalogueLoading: boolean;
  reviewed?: string;
}) {
  const heading =
    suggestion.status === "declined"
      ? suggestion.reason
        ? `Declined: ${reasonLabel(suggestion.reason).toLowerCase()}`
        : // Declined before a reason was required.
          "Declined, no reason recorded"
      : suggestion.status === "planned"
        ? "Accepted — it will be collected"
        : "Ingested — it is in the warehouse";

  return (
    <div className="space-y-2 rounded-md bg-muted/50 p-3">
      <p className="text-sm font-medium">{heading}</p>
      {suggestion.note ? (
        <p className="text-sm whitespace-pre-line">{suggestion.note}</p>
      ) : null}
      {suggestion.dataset_id ? (
        <DatasetAnswer
          id={suggestion.dataset_id}
          dataset={dataset}
          isLoading={catalogueLoading}
        />
      ) : null}
      <p className="text-xs text-muted-foreground">
        {suggestion.reviewed_by_email
          ? `By ${suggestion.reviewed_by_email}${reviewed ? ` · ${reviewed}` : ""}`
          : // Moved before the reviewer was recorded.
            "Reviewer not recorded"}
      </p>
    </div>
  );
}

/**
 * The dataset a request was answered with: enough of it to tell whether it is
 * what was asked for, and the way in.
 */
function DatasetAnswer({
  id,
  dataset,
  isLoading,
}: {
  id: string;
  dataset?: Dataset;
  isLoading: boolean;
}) {
  if (!dataset) {
    return (
      <p className="text-sm text-muted-foreground">
        {isLoading ? (
          "Looking up the dataset…"
        ) : (
          // Named when it was decided, and gone from the catalogue since.
          <>
            Dataset <span className="font-mono">{id}</span> is no longer in the
            catalogue.
          </>
        )}
      </p>
    );
  }
  const period =
    dataset.period_start && dataset.period_end
      ? `${dataset.period_start} – ${dataset.period_end}`
      : "Period not recorded";
  return (
    <Link
      to="/datasets/$datasetId"
      params={{ datasetId: dataset.dataset_id }}
      className="flex items-start gap-3 rounded-md border bg-background p-3 transition-colors hover:bg-muted/50"
    >
      <IconDatabase className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-sm font-medium">{datasetLabel(dataset)}</p>
        <p className="text-xs text-muted-foreground">
          {dataset.source_name || dataset.source_id} · {period} ·{" "}
          {formatCount(dataset.series ?? dataset.indicators.length)} series ·{" "}
          {formatCount(dataset.observations)} observations
        </p>
        {dataset.description ? (
          <p className="line-clamp-2 text-xs text-muted-foreground">
            {dataset.description}
          </p>
        ) : null}
      </div>
      <span className="shrink-0 text-xs underline-offset-4">Open</span>
    </Link>
  );
}

/**
 * Declining, or marking a request ingested — the two decisions that need more
 * than a click: a reason, and the dataset that answers it.
 */
function DecisionDialog({
  mode,
  suggestion,
  isPending,
  error,
  onClose,
  onDecide,
}: {
  mode: Dialogue;
  suggestion: Suggestion;
  isPending: boolean;
  error?: string;
  onClose: () => void;
  onDecide: (decision: SuggestionDecision) => void;
}) {
  const [reason, setReason] = useState<string>("already-collected");
  const [datasetId, setDatasetId] = useState("");
  const [note, setNote] = useState("");

  const declining = mode === "decline";
  const needsDataset = declining && reason === "already-collected";
  const ready = !needsDataset || datasetId !== "";

  return (
    <Dialog open={mode !== null} onOpenChange={(open) => (open ? null : onClose())}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {declining ? "Decline this request" : "Mark it ingested"}
          </DialogTitle>
          <DialogDescription>{suggestion.title}</DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!ready) return;
            onDecide(
              declining
                ? {
                    status: "declined",
                    reason,
                    dataset_id: needsDataset ? datasetId : undefined,
                    note,
                  }
                : { status: "ingested", dataset_id: datasetId || undefined, note },
            );
          }}
        >
          <FieldGroup>
            {declining ? (
              <Field>
                <FieldLabel htmlFor="decision-reason">Why</FieldLabel>
                <Select
                  value={reason}
                  onValueChange={(value) => setReason(String(value))}
                  items={REASONS.map((value) => ({
                    value,
                    label: reasonLabel(value),
                  }))}
                >
                  <SelectTrigger id="decision-reason" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {REASONS.map((value) => (
                      <SelectItem key={value} value={value}>
                        {reasonLabel(value)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            ) : null}

            {needsDataset || !declining ? (
              <DatasetChoice
                selected={datasetId}
                onChange={setDatasetId}
                label={needsDataset ? "Where it already is" : "The dataset it became"}
                hint={
                  needsDataset
                    ? "The asker is shown this dataset and a link to it."
                    : "Optional, and shown to the asker as the answer."
                }
              />
            ) : null}

            <Field>
              <FieldLabel htmlFor="decision-note">Note to the asker</FieldLabel>
              <Textarea
                id="decision-note"
                rows={3}
                maxLength={4000}
                value={note}
                placeholder={
                  needsDataset
                    ? "Which part of it covers what they asked for."
                    : "Anything they should know."
                }
                onChange={(event) => setNote(event.target.value)}
              />
            </Field>
          </FieldGroup>

          {error ? (
            <Alert variant="destructive">
              <IconAlertTriangle />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          <DialogFooter>
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              size="sm"
              variant={declining ? "destructive" : "default"}
              disabled={isPending || !ready}
            >
              {isPending ? "Saving…" : declining ? "Decline" : "Mark ingested"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

const PICKER_ROWS = 50;

/** One dataset out of the catalogue, searched by title, id or source. */
function DatasetChoice({
  selected,
  onChange,
  label,
  hint,
}: {
  selected: string;
  onChange: (id: string) => void;
  label: string;
  hint: string;
}) {
  const catalogue = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });
  const [needle, setNeedle] = useState("");

  const all = catalogue.data?.data ?? [];
  const chosen = all.find((d) => d.dataset_id === selected);
  const term = needle.trim().toLowerCase();
  const matches = all
    .filter(
      (d) =>
        !term ||
        datasetLabel(d).toLowerCase().includes(term) ||
        d.dataset_id.toLowerCase().includes(term) ||
        d.source_id.toLowerCase().includes(term),
    )
    .slice(0, PICKER_ROWS);

  return (
    <Field>
      <FieldLabel htmlFor="decision-dataset">{label}</FieldLabel>
      {selected ? (
        <div className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
          <IconDatabase className="size-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate">
            {chosen ? datasetLabel(chosen) : selected}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-6"
            aria-label="Choose another dataset"
            onClick={() => onChange("")}
          >
            <IconX className="size-3.5" />
          </Button>
        </div>
      ) : (
        <>
          <Input
            id="decision-dataset"
            value={needle}
            placeholder="Search datasets by title, id or source"
            onChange={(event) => setNeedle(event.target.value)}
          />
          <div className="max-h-48 overflow-y-auto rounded-md border">
            {catalogue.isLoading ? (
              <p className="px-3 py-2 text-sm text-muted-foreground">
                Loading datasets…
              </p>
            ) : matches.length ? (
              matches.map((d) => (
                <button
                  key={d.dataset_id}
                  type="button"
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-muted"
                  onClick={() => onChange(d.dataset_id)}
                >
                  <span className="min-w-0 flex-1 truncate">{datasetLabel(d)}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {d.dataset_id}
                  </span>
                </button>
              ))
            ) : (
              <p className="px-3 py-2 text-sm text-muted-foreground">
                No dataset matches “{needle}”.
              </p>
            )}
          </div>
        </>
      )}
      <FieldDescription>{hint}</FieldDescription>
    </Field>
  );
}
