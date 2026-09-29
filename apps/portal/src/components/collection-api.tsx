import { Link } from "@tanstack/react-router";
import { IconApi, IconCheck, IconCopy } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { copyToClipboard } from "~/components/row-actions";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { apiBaseUrl } from "~/lib/api";
import {
  managesCollection,
  updateCollection,
  useShelf,
  type Collection,
} from "~/lib/workspace";

/**
 * A collection's settings, of which there is one: whether its figures are
 * served through its own API path.
 *
 * The path answers every route the warehouse's figures do — observations,
 * series, facets — with the same filters and paging, narrowed to the series
 * in this collection, plus the catalogue entry of each series. Whoever is in
 * the collection reads it with their own API token, made on their profile,
 * so there is no key here to copy into a script and forget.
 *
 * Shown to members as well, read-only: they are who will call it, and the
 * owner is who turns it on.
 */
export function CollectionApi({ collection }: { collection: Collection }) {
  const shelf = useShelf();
  // Where the API is, as an absolute address a script can use. Resolved in
  // the browser because the server rendering this has no idea what hostname
  // the reader came in on when the API is served from the portal's own.
  const [base, setBase] = useState(apiBaseUrl);
  useEffect(() => {
    setBase(new URL(apiBaseUrl || "/", window.location.origin).origin);
  }, []);

  // A browser-kept shelf has no API to serve it from.
  if (shelf.mode !== "server") return null;
  const manages = managesCollection(collection) && shelf.writable;
  if (!collection.api && !manages) return null;

  const root = `${base}/v1/collections/${encodeURIComponent(collection.id)}`;
  const routes = [
    {
      path: "/indicators",
      what: "Each series in the collection: name, unit, frequency, and the span its figures cover.",
    },
    {
      path: "/observations",
      what: "Every figure, row by row — the same filters as /v1/observations (period_start, period_end, geo, status…), limit up to 10,000 and cursor paging with after.",
    },
    {
      path: "/observations/series",
      what: "The figures as a chart takes them: a line per place or commodity, bucketed to at most points (default 400).",
    },
    {
      path: "/observations/facets",
      what: "What the figures can be filtered by — places, periods, statuses — counted.",
    },
  ];
  const example = `curl -H "Authorization: Bearer $TERUSAN_TOKEN" \\\n  "${root}/observations?limit=10000"`;

  return (
    <section className="space-y-3 rounded-lg border p-4">
      <div className="flex items-start gap-3">
        <IconApi className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1 space-y-1">
          <h2 className="font-heading text-sm font-medium">API access</h2>
          <p className="text-sm text-muted-foreground">
            {collection.api
              ? "This collection's figures are served at the paths below, to you and everyone in it, signed in with their own API token."
              : "Serve this collection's figures at their own path, so a script or a notebook can read exactly these series without naming them."}
          </p>
        </div>
        {manages ? (
          <label className="flex shrink-0 items-center gap-2 text-sm">
            <Checkbox
              checked={collection.api ?? false}
              onCheckedChange={(checked) =>
                updateCollection(collection.id, { api: checked === true })
              }
              aria-label="Serve this collection through the API"
            />
            {collection.api ? "On" : "Off"}
          </label>
        ) : null}
      </div>

      {collection.api ? (
        <>
          <ul className="divide-y rounded-md border">
            {routes.map((route) => (
              <li key={route.path} className="space-y-0.5 px-3 py-2">
                <div className="flex items-center gap-2">
                  <code className="truncate font-mono text-xs">
                    <span className="text-muted-foreground">GET</span> {root}
                    {route.path}
                  </code>
                  <CopyButton text={`${root}${route.path}`} label={route.path} />
                </div>
                <p className="text-xs text-muted-foreground">{route.what}</p>
              </li>
            ))}
          </ul>

          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <p className="text-xs font-medium text-muted-foreground">Example</p>
              <CopyButton text={example} label="example" />
            </div>
            <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs">
              {example}
            </pre>
            <p className="text-xs text-muted-foreground">
              Make a token on your{" "}
              <Link to="/profile" className="underline underline-offset-4">
                profile
              </Link>
              . Anyone removed from the collection loses access with it; a series added
              to it is served on the next request.
            </p>
          </div>
        </>
      ) : null}
    </section>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      className="ml-auto shrink-0"
      aria-label={`Copy ${label}`}
      onClick={async () => {
        if (await copyToClipboard(text)) {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }
      }}
    >
      {copied ? <IconCheck className="size-4" /> : <IconCopy className="size-4" />}
    </Button>
  );
}
