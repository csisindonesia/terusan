import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { IconSettings } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { Endpoint, Snippet } from "~/components/code-snippet";
import { Section } from "~/components/section";
import { BELOW_STICKY_HEADER } from "~/components/sticky-header";
import { Button } from "~/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { api, apiBaseUrl } from "~/lib/api";
import {
  itemsOfKind,
  managesCollection,
  useShelf,
  type Collection,
} from "~/lib/workspace";

/**
 * The same request, in the tools this warehouse's readers use — as on an
 * indicator's API tab, with the one difference a collection's routes have:
 * they answer to a person, so every snippet sends a token.
 *
 * Each converts `value` explicitly. The API sends it as a string because
 * published statistics are decimal quantities, and a language that parses JSON
 * numbers into floats would quietly round a rupiah total before anyone saw it.
 */
type Language = "curl" | "python" | "r" | "javascript" | "duckdb";

const LANGUAGES: {
  id: Language;
  label: string;
  code: (url: string) => string;
  note?: string;
}[] = [
  {
    id: "curl",
    label: "curl",
    code: (url) =>
      `curl -s -H "Authorization: Bearer $TERUSAN_TOKEN" \\\n  '${url}' | jq '.data[0]'`,
  },
  {
    id: "python",
    label: "Python",
    code: (url) =>
      [
        "import os",
        "from decimal import Decimal",
        "",
        "import pandas as pd",
        "import requests",
        "",
        "response = requests.get(",
        `    "${url}",`,
        '    headers={"Authorization": f"Bearer {os.environ[\'TERUSAN_TOKEN\']}"},',
        "    timeout=30,",
        ")",
        "response.raise_for_status()",
        "payload = response.json()",
        "",
        'figures = pd.DataFrame(payload["data"])',
        "# A string on the wire, on purpose — see the note below.",
        'figures["value"] = figures["value"].apply(',
        "    lambda raw: Decimal(raw) if raw is not None else None",
        ")",
        "",
        'print(figures.groupby("indicator_id").size())',
      ].join("\n"),
    note: "Use `pd.to_numeric` instead where float precision is enough — for an index or a percentage it is. For rupiah totals it is not.",
  },
  {
    id: "r",
    label: "R",
    code: (url) =>
      [
        "library(httr2)",
        "library(dplyr)",
        "",
        `payload <- request("${url}") |>`,
        '  req_auth_bearer_token(Sys.getenv("TERUSAN_TOKEN")) |>',
        "  req_perform() |>",
        "  resp_body_json(simplifyVector = TRUE)",
        "",
        "figures <- payload$data |>",
        "  mutate(",
        "    value = as.numeric(value),",
        "    period_start = as.Date(period_start)",
        "  )",
        "",
        "count(figures, indicator_id)",
      ].join("\n"),
    note: "R's numeric is a double, so very large rupiah figures lose their last digits. Keep `value` as character where the exact figure matters.",
  },
  {
    id: "javascript",
    label: "JavaScript",
    code: (url) =>
      [
        "const response = await fetch(",
        `  "${url}",`,
        "  { headers: { Authorization: `Bearer ${process.env.TERUSAN_TOKEN}` } },",
        ");",
        "if (!response.ok) throw new Error(await response.text());",
        "const { data, meta } = await response.json();",
        "",
        "// `Number` is a double: fine for an index, lossy for a rupiah total.",
        "const figures = data.map((row) => ({",
        "  ...row,",
        "  value: row.value === null ? null : Number(row.value),",
        "}));",
      ].join("\n"),
  },
  {
    id: "duckdb",
    label: "DuckDB",
    code: (url) =>
      [
        "-- Straight from the API, no download step. The token rides on a secret",
        "-- scoped to this API, so it is not repeated in every query.",
        "INSTALL httpfs; LOAD httpfs;",
        "INSTALL json; LOAD json;",
        "",
        "CREATE SECRET terusan (",
        "  TYPE http,",
        `  SCOPE '${originOf(url)}',`,
        "  EXTRA_HTTP_HEADERS MAP {",
        "    'Authorization': 'Bearer ' || getenv('TERUSAN_TOKEN')",
        "  }",
        ");",
        "",
        "SELECT",
        "  t.row.indicator_id AS indicator_id,",
        "  t.row.period       AS period,",
        "  CAST(t.row.value AS DECIMAL(38, 9)) AS value,",
        "  t.row.unit         AS unit",
        `FROM read_json('${url}')`,
        "CROSS JOIN UNNEST(data) AS t(row)",
        "ORDER BY indicator_id, period;",
      ].join("\n"),
    note: "DECIMAL rather than DOUBLE, so the figures survive the cast. This is also how the warehouse itself stores them.",
  },
];

/** The scheme and host of a URL, or the URL itself before it is absolute. */
function originOf(url: string): string {
  try {
    return new URL(url).origin;
  } catch {
    return url;
  }
}

/** Shown only before a real row has loaded, so the shape is never blank. */
const PLACEHOLDER_ROW = {
  observation_id: "obs_…",
  indicator_id: "…",
  period: "2025",
  period_start: "2025-01-01",
  period_end: "2025-12-31",
  temporal_resolution: "annual",
  value: "1571092130385400.000000000",
  unit: "IDR",
  status: "ok",
  value_unambiguous: true,
  geo_id: null,
  commodity_id: null,
  source_id: "…",
};

/** How many rows the example asks for: one response for most collections. */
const EXAMPLE_LIMIT = 10000;

/**
 * How to read this collection's figures without the portal.
 *
 * The same shape as an indicator's API tab, because it is the same API: the
 * observations, series and facets routes, with the same filters and paging,
 * narrowed to the series filed here. What differs is who may call it — the
 * collection's people, or anyone once its owner has made it public — and so
 * every request here carries a personal token rather than riding a cookie.
 */
export function CollectionApi({
  collection,
  onOpenSettings,
}: {
  collection: Collection;
  /** Where the owner turns the API on. */
  onOpenSettings: () => void;
}) {
  const shelf = useShelf();
  const [language, setLanguage] = useState<Language>("python");
  // Where the API is, as an absolute address a script can use. Resolved in
  // the browser because the server rendering this has no idea what hostname
  // the reader came in on when the API is served from the portal's own.
  const [base, setBase] = useState(apiBaseUrl);
  useEffect(() => {
    setBase(new URL(apiBaseUrl || "/", window.location.origin).origin);
  }, []);

  const indicators = itemsOfKind(collection, "indicator");
  const first = indicators[0]?.id;
  // A real row from a series in this collection rather than an invented one,
  // read from the lake-wide route so it shows even while the API is off.
  const sample = useQuery({
    queryKey: ["collection-api-sample", first],
    enabled: Boolean(first),
    queryFn: () => api.observations({ indicator: [first as string], limit: 1 }),
  });
  const row = sample.data?.data[0];

  if (shelf.mode !== "server") {
    return (
      <p className="max-w-3xl rounded-lg border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">
        This collection is kept in this browser, and a browser has no address a script
        can call. A deployment that keeps collections on the API — COLLECTIONS_DB set
        where it runs — serves each one at its own path.
      </p>
    );
  }

  const root = `${base}/v1/collections/${encodeURIComponent(collection.id)}`;
  const url = `${root}/observations?limit=${EXAMPLE_LIMIT}`;
  const manages = managesCollection(collection) && shelf.writable;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <div className="min-w-0 space-y-6">
        {collection.api ? null : (
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-dashed px-4 py-3">
            <p className="min-w-0 flex-1 text-sm text-muted-foreground">
              {manages
                ? "The API is off, so these requests answer 403 until you turn it on. An endpoint that other programs poll is a promise that renaming or emptying the collection will break something, which is why it starts off."
                : "The API is off, so these requests answer 403. Its owner can turn it on in the collection's settings."}
            </p>
            {manages ? (
              <Button variant="outline" size="sm" onClick={onOpenSettings}>
                <IconSettings className="size-4" />
                Turn on in settings
              </Button>
            ) : null}
          </div>
        )}

        <Section
          title="The request"
          description={
            <>
              Every figure of every series in this collection, in one call. It takes the
              same filters as <code>/v1/observations</code> — <code>period_start</code>,{" "}
              <code>period_end</code>, <code>geo</code>, <code>status</code> — and{" "}
              <code>indicator</code> narrows to some of the series here. A series filed
              later is served on the next request, so a script that reads this path does
              not need changing when the collection does.
            </>
          }
        >
          <div className="space-y-3">
            <Snippet label="Endpoint" value={url} />

            {/* One language at a time. A page that stacks five snippets makes
                the reader scroll past four they cannot use to reach the one
                they can. */}
            <Tabs
              value={language}
              onValueChange={(next) => setLanguage(next as Language)}
            >
              <TabsList variant="line">
                {LANGUAGES.map((entry) => (
                  <TabsTrigger key={entry.id} value={entry.id}>
                    {entry.label}
                  </TabsTrigger>
                ))}
              </TabsList>
              {LANGUAGES.map((entry) => (
                <TabsContent key={entry.id} value={entry.id} className="pt-2">
                  <Snippet label={entry.label} value={entry.code(url)} />
                  {entry.note ? (
                    <p className="pt-2 text-xs text-muted-foreground">{entry.note}</p>
                  ) : null}
                </TabsContent>
              ))}
            </Tabs>
          </div>
        </Section>

        <Section
          title="Who may call it"
          description={
            <>
              {collection.visibility === "public"
                ? "Anyone who can sign in to this deployment, because the collection is public. "
                : "Its owner and members only, because the collection is limited to them. "}
              Each caller sends their own API token, made on their{" "}
              <Link to="/profile" className="underline underline-offset-4">
                profile
              </Link>
              . There is no key per collection: a key would be a second way in that
              outlives the member who copied it, and a token already names a person who
              can be removed. Anyone removed loses access with it.
            </>
          }
        />

        <Section
          title="What comes back"
          description={
            <>
              Every response uses the same envelope: <code>data</code> holds the rows,{" "}
              <code>meta</code> holds the count and the paging state. A figure&rsquo;s{" "}
              <code>value</code> is a string, not a number — these are decimal
              quantities, and parsing them to a float here would reintroduce the drift
              the decimal storage exists to avoid.
            </>
          }
        >
          <Snippet
            label={row ? "One row, from a series in this collection" : "One row"}
            value={JSON.stringify(row ?? PLACEHOLDER_ROW, null, 2)}
          />
        </Section>

        <Section
          title="Paging"
          description={
            <>
              <code>limit</code> takes up to 10,000, and <code>after</code> takes the
              cursor from <code>meta</code> for the next page; <code>meta.total</code>{" "}
              is the count before paging and <code>meta.has_more</code> says whether
              another page exists. A collection of a few series is usually one response.
            </>
          }
        />
      </div>

      <aside className={`space-y-4 lg:sticky lg:self-start ${BELOW_STICKY_HEADER}`}>
        <h2 className="font-heading text-sm font-semibold tracking-tight">
          This collection&rsquo;s endpoints
        </h2>
        <dl className="space-y-3">
          <Endpoint
            path={`/v1/collections/${collection.id}/indicators`}
            description="Each series here: name, unit, frequency, and the span its figures cover."
          />
          <Endpoint
            path={`/v1/collections/${collection.id}/observations`}
            description="Every figure, row by row."
          />
          <Endpoint
            path={`/v1/collections/${collection.id}/observations/series`}
            description="The figures as a chart takes them: a line per place or commodity, bucketed to at most points (default 400)."
          />
          <Endpoint
            path={`/v1/collections/${collection.id}/observations/facets`}
            description="What the figures can be filtered by — places, periods, statuses — counted."
          />
          <Endpoint
            path={`/v1/collections/${collection.id}`}
            description="The collection itself: its name, who is in it, what is filed."
          />
        </dl>
      </aside>
    </div>
  );
}
