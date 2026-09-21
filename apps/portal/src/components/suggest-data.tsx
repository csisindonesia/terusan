import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  IconAlertTriangle,
  IconCheck,
  IconDatabasePlus,
  IconExternalLink,
} from "@tabler/icons-react";
import { useState } from "react";

import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "~/components/ui/dialog";
import { Alert, AlertDescription } from "~/components/ui/alert";
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
import { ApiRequestError, api, type Suggestion } from "~/lib/api";
import { CADENCES, cadenceLabel, statusLabel } from "~/lib/suggestions";
import { formatRelative } from "~/lib/format";
import { capabilitiesQuery, useSessionState } from "~/lib/session";

/**
 * "Collect this too" — asking the warehouse for a source it does not have.
 *
 * The most common thing a research portal is missing is a source nobody has
 * ingested yet, and the person who notices is almost never the person who runs
 * the pipelines. This is the shortest path between those two people.
 *
 * Four fields and no more, chosen for what a maintainer needs before they can
 * act: what it is, where it is, how often it changes, and why it is worth
 * having. A URL is required because a title can be searched for and an address
 * can be opened — everything else about a source is discoverable from it.
 *
 * It posts to the API rather than composing an email, unlike the portal's
 * other forms. A request to ingest a source is a work item, not a message: it
 * wants a status, and the next person about to ask for the same source should
 * be able to see that somebody already did — which is what the list at the
 * bottom of this dialog is for.
 */
export function SuggestData() {
  const [open, setOpen] = useState(false);
  const { session, hasAuth } = useSessionState();
  const capabilities = useQuery(capabilitiesQuery);

  const available = capabilities.data?.suggestions ?? false;
  const signedOut = hasAuth && !session;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {/* A word rather than a glyph: there is no icon anybody reads as "ask
          us to collect a source", and a tooltip nobody hovers is a label
          nobody sees. */}
      <DialogTrigger
        render={
          <Button variant="ghost" size="sm" className="hidden sm:inline-flex">
            <IconDatabasePlus className="size-4" />
            Suggest data
          </Button>
        }
      />
      {/* On a phone the bar has no room for the word. */}
      <DialogTrigger
        render={
          <Button
            variant="ghost"
            size="icon"
            aria-label="Suggest data"
            className="sm:hidden"
          >
            <IconDatabasePlus />
          </Button>
        }
      />

      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Suggest data to collect</DialogTitle>
          <DialogDescription>
            Something this warehouse should be ingesting and is not. It goes on a queue
            whoever runs the pipelines works through.
          </DialogDescription>
        </DialogHeader>

        {!available ? (
          <Alert>
            <IconAlertTriangle />
            <AlertDescription>
              {/* A deployment with nowhere to keep the queue would swallow the
                  request, which is worse than not offering the form. */}
              This deployment keeps no suggestions queue. Write to the address on the{" "}
              <Link to="/contact" className="underline underline-offset-4">
                contact page
              </Link>{" "}
              instead.
            </AlertDescription>
          </Alert>
        ) : signedOut ? (
          <Alert>
            <IconAlertTriangle />
            <AlertDescription>
              {/* Named rather than anonymous, because a request nobody can ask
                  a follow-up question about is a dead end. */}
              <Link to="/login" className="underline underline-offset-4">
                Log in
              </Link>{" "}
              to suggest a source — a request is worth acting on only if somebody can be
              asked what they meant by it.
            </AlertDescription>
          </Alert>
        ) : (
          <SuggestForm onDone={() => setOpen(false)} />
        )}

        {available ? <RecentSuggestions /> : null}
      </DialogContent>
    </Dialog>
  );
}

function SuggestForm({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [cadence, setCadence] = useState<string>("unknown");
  const [description, setDescription] = useState("");

  const submit = useMutation({
    mutationFn: () =>
      api.suggest({ title: title.trim(), url: url.trim(), cadence, description }),
    onSuccess: async () => {
      setTitle("");
      setUrl("");
      setCadence("unknown");
      setDescription("");
      await queryClient.invalidateQueries({ queryKey: ["suggestions"] });
    },
  });

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!title.trim() || !url.trim()) return;
        submit.mutate();
      }}
    >
      <FieldGroup>
        <Field>
          <FieldLabel htmlFor="suggestion-title">What is it</FieldLabel>
          <Input
            id="suggestion-title"
            required
            maxLength={200}
            value={title}
            placeholder="Fuel subsidy realisation, monthly"
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>

        <Field>
          <FieldLabel htmlFor="suggestion-url">Where it is published</FieldLabel>
          <Input
            id="suggestion-url"
            type="url"
            required
            maxLength={2000}
            value={url}
            placeholder="https://www.esdm.go.id/…"
            onChange={(event) => setUrl(event.target.value)}
          />
          <FieldDescription>
            The page or file itself, if you have it — a landing page is fine too.
          </FieldDescription>
        </Field>

        <Field>
          <FieldLabel htmlFor="suggestion-cadence">How often it changes</FieldLabel>
          <Select
            value={cadence}
            onValueChange={(value) => setCadence(String(value))}
            items={CADENCES.map((entry) => ({
              value: entry,
              label: cadenceLabel(entry),
            }))}
          >
            <SelectTrigger id="suggestion-cadence" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CADENCES.map((entry) => (
                <SelectItem key={entry} value={entry}>
                  {cadenceLabel(entry)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <FieldDescription>
            {/* What this is actually for: the cadence becomes the schedule the
                pipeline runs on. */}
            It becomes the schedule the ingestion would run on.
          </FieldDescription>
        </Field>

        <Field>
          <FieldLabel htmlFor="suggestion-description">
            What it covers, and why
          </FieldLabel>
          <Textarea
            id="suggestion-description"
            rows={3}
            maxLength={4000}
            value={description}
            placeholder="Which years, which places, what it would let you answer — and anything awkward about how it is published."
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
      </FieldGroup>

      {submit.isError ? (
        <Alert variant="destructive">
          <IconAlertTriangle />
          <AlertDescription>
            {submit.error instanceof ApiRequestError
              ? submit.error.message
              : "The API could not be reached."}
          </AlertDescription>
        </Alert>
      ) : null}

      {submit.isSuccess ? (
        <Alert>
          <IconCheck />
          <AlertDescription>
            Filed. It is on the queue below — suggest another, or close this.
          </AlertDescription>
        </Alert>
      ) : null}

      <DialogFooter>
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Close
        </Button>
        <Button
          type="submit"
          size="sm"
          disabled={submit.isPending || !title.trim() || !url.trim()}
        >
          {submit.isPending ? "Filing…" : "Suggest it"}
        </Button>
      </DialogFooter>
    </form>
  );
}

/**
 * What has already been asked for.
 *
 * Here rather than on a page of its own because its job is to be read *while*
 * filling the form: the second request for the same source costs a maintainer
 * a duplicate to close, and costs the asker the answer they could have had
 * from the status.
 */
function RecentSuggestions() {
  const suggestions = useQuery({
    queryKey: ["suggestions"],
    queryFn: async () => (await api.suggestions(8)).data,
    retry: false,
  });

  const rows = suggestions.data ?? [];
  if (!rows.length) return null;

  return (
    <div className="space-y-2 border-t pt-4">
      <p className="text-xs font-medium text-muted-foreground">Already asked for</p>
      <ul className="divide-y rounded-lg border">
        {rows.map((row) => (
          <SuggestionRow key={row.id} suggestion={row} />
        ))}
      </ul>
    </div>
  );
}

function SuggestionRow({ suggestion }: { suggestion: Suggestion }) {
  return (
    <li className="flex items-center gap-2 px-3 py-2 text-sm">
      <div className="min-w-0 flex-1 leading-tight">
        <div className="flex items-center gap-1.5">
          <span className="truncate font-medium" title={suggestion.title}>
            {suggestion.title}
          </span>
          <a
            href={suggestion.url}
            target="_blank"
            rel="noreferrer noopener"
            className="shrink-0 text-muted-foreground hover:text-foreground"
            aria-label={`Open ${suggestion.title}`}
          >
            <IconExternalLink className="size-3.5" />
          </a>
        </div>
        <div className="truncate text-xs text-muted-foreground">
          {cadenceLabel(suggestion.cadence)} · asked by {suggestion.requested_by_email}
          {formatRelative(suggestion.created_at)
            ? ` · ${formatRelative(suggestion.created_at)}`
            : ""}
        </div>
      </div>
      <Badge
        variant={suggestion.status === "open" ? "secondary" : "outline"}
        className="shrink-0"
      >
        {statusLabel(suggestion.status)}
      </Badge>
    </li>
  );
}
