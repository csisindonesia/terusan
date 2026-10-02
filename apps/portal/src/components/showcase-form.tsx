import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconAlertTriangle, IconX } from "@tabler/icons-react";
import { useState } from "react";

import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
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
  type ShowcaseEntry,
  type ShowcaseInput,
} from "~/lib/api";
import { datasetLabel } from "~/lib/labels";
import { SHOWCASE_KINDS, kindLabel } from "~/lib/showcase";

/** How many unpicked datasets the picker shows at once, before a search. */
const PICKER_ROWS = 40;

/**
 * Adding or changing one entry on the Portal page. Administrators only; the
 * API refuses anyone else, and the page offers this to nobody else.
 *
 * Opened with `entry` to edit it, without to add one.
 */
export function ShowcaseForm({
  open,
  onOpenChange,
  entry,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  entry?: ShowcaseEntry;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>
            {entry ? "Edit entry" : "Add work that uses our data"}
          </DialogTitle>
          <DialogDescription>
            A dashboard, article or report built on this warehouse's figures. It is
            listed on the Portal page and under every dataset it names.
          </DialogDescription>
        </DialogHeader>
        {/* Keyed on the entry so opening another one starts from its fields
            rather than the last one's. */}
        {open ? (
          <EntryFields
            key={entry?.id ?? "new"}
            entry={entry}
            onDone={() => onOpenChange(false)}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function EntryFields({ entry, onDone }: { entry?: ShowcaseEntry; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState(entry?.title ?? "");
  const [kind, setKind] = useState(entry?.kind ?? "dashboard");
  const [url, setUrl] = useState(entry?.url ?? "");
  const [publisher, setPublisher] = useState(entry?.publisher ?? "");
  const [publishedOn, setPublishedOn] = useState(entry?.published_on ?? "");
  const [description, setDescription] = useState(entry?.description ?? "");
  const [datasets, setDatasets] = useState<string[]>(
    entry?.datasets.map((d) => d.dataset_id) ?? [],
  );

  const save = useMutation({
    mutationFn: () => {
      const body: ShowcaseInput = {
        title: title.trim(),
        kind,
        url: url.trim(),
        publisher: publisher.trim(),
        published_on: publishedOn,
        description: description.trim(),
        datasets,
      };
      return entry ? api.updateShowcase(entry.id, body) : api.createShowcase(body);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["showcase"] });
      onDone();
    },
  });

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!title.trim() || !url.trim()) return;
        save.mutate();
      }}
    >
      <FieldGroup>
        <Field>
          <FieldLabel htmlFor="showcase-title">Title</FieldLabel>
          <Input
            id="showcase-title"
            required
            maxLength={300}
            value={title}
            placeholder="Fiscal monitoring dashboard"
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="showcase-kind">Kind</FieldLabel>
            <Select
              value={kind}
              onValueChange={(value) => setKind(String(value))}
              items={SHOWCASE_KINDS.map((value) => ({
                value,
                label: kindLabel(value),
              }))}
            >
              <SelectTrigger id="showcase-kind" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SHOWCASE_KINDS.map((value) => (
                  <SelectItem key={value} value={value}>
                    {kindLabel(value)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field>
            <FieldLabel htmlFor="showcase-date">Published</FieldLabel>
            <Input
              id="showcase-date"
              type="date"
              value={publishedOn}
              onChange={(event) => setPublishedOn(event.target.value)}
            />
            <FieldDescription>
              Leave empty for a dashboard kept up to date.
            </FieldDescription>
          </Field>
        </div>

        <Field>
          <FieldLabel htmlFor="showcase-url">Where it is</FieldLabel>
          <Input
            id="showcase-url"
            // Text rather than `url`: a Portal article is filed by its path,
            // which a url input refuses. The API checks both forms.
            type="text"
            inputMode="url"
            required
            maxLength={2000}
            value={url}
            placeholder="https://… or /portal/<article>"
            onChange={(event) => setUrl(event.target.value)}
          />
          <FieldDescription>
            A link to the work, or the path of one of the Portal's own articles.
          </FieldDescription>
        </Field>

        <Field>
          <FieldLabel htmlFor="showcase-publisher">Who made it</FieldLabel>
          <Input
            id="showcase-publisher"
            maxLength={200}
            value={publisher}
            placeholder="CSIS Economics, The Jakarta Post, …"
            onChange={(event) => setPublisher(event.target.value)}
          />
        </Field>

        <Field>
          <FieldLabel htmlFor="showcase-description">What it shows</FieldLabel>
          <Textarea
            id="showcase-description"
            rows={3}
            maxLength={4000}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>

        <DatasetPicker selected={datasets} onChange={setDatasets} />
      </FieldGroup>

      {save.isError ? (
        <Alert variant="destructive">
          <IconAlertTriangle />
          <AlertDescription>
            {save.error instanceof ApiRequestError
              ? save.error.message
              : "The API could not be reached."}
          </AlertDescription>
        </Alert>
      ) : null}

      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending || !title.trim() || !url.trim()}>
          {save.isPending ? "Saving…" : entry ? "Save" : "Add"}
        </Button>
      </DialogFooter>
    </form>
  );
}

/**
 * The datasets an entry uses, picked from the catalogue rather than typed, so
 * every link on a dataset page points at a dataset that exists.
 */
function DatasetPicker({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const catalogue = useQuery({ queryKey: ["datasets"], queryFn: () => api.datasets() });
  const [needle, setNeedle] = useState("");

  const all = catalogue.data?.data ?? [];
  const byId = new Map(all.map((d) => [d.dataset_id, d]));
  const query = needle.trim().toLowerCase();
  const matches = all
    .filter(
      (d) =>
        !query ||
        datasetLabel(d).toLowerCase().includes(query) ||
        d.dataset_id.toLowerCase().includes(query) ||
        d.source_id.toLowerCase().includes(query),
    )
    .slice(0, PICKER_ROWS);

  const toggle = (id: string) =>
    onChange(
      selected.includes(id) ? selected.filter((d) => d !== id) : [...selected, id],
    );

  return (
    <Field>
      <FieldLabel htmlFor="showcase-datasets">Datasets it uses</FieldLabel>
      {selected.length ? (
        <div className="flex flex-wrap gap-1.5">
          {selected.map((id) => {
            const known = byId.get(id);
            return (
              <Badge key={id} variant="secondary" className="h-6 gap-1 pr-1">
                {known ? datasetLabel(known) : id}
                <button
                  type="button"
                  aria-label={`Remove ${known ? datasetLabel(known) : id}`}
                  className="rounded-full p-0.5 hover:bg-background"
                  onClick={() => toggle(id)}
                >
                  <IconX className="size-3" />
                </button>
              </Badge>
            );
          })}
        </div>
      ) : null}
      <Input
        id="showcase-datasets"
        value={needle}
        placeholder="Search datasets by title, id or source"
        onChange={(event) => setNeedle(event.target.value)}
      />
      <div className="max-h-48 overflow-y-auto rounded-md border">
        {catalogue.isLoading ? (
          <p className="px-3 py-2 text-sm text-muted-foreground">Loading datasets…</p>
        ) : matches.length ? (
          matches.map((d) => (
            <label
              key={d.dataset_id}
              className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm hover:bg-muted"
            >
              <Checkbox
                checked={selected.includes(d.dataset_id)}
                onCheckedChange={() => toggle(d.dataset_id)}
              />
              <span className="min-w-0 flex-1 truncate">{datasetLabel(d)}</span>
              <span className="font-mono text-xs text-muted-foreground">
                {d.dataset_id}
              </span>
            </label>
          ))
        ) : (
          <p className="px-3 py-2 text-sm text-muted-foreground">
            No dataset matches “{needle}”.
          </p>
        )}
      </div>
    </Field>
  );
}
