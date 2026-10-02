/**
 * The vocabularies the lake is classified by, and how much each term covers.
 *
 * A category, a frequency, a unit, a status, a place type: each is a column
 * whose values are meant to come from a short, agreed list. Two things go
 * wrong with such a list, and both are visible only when it is laid out in
 * full — a term spelled two ways (`monthly` and `Monthly`) that splits what
 * should be one bucket, and rows that carry no term at all. So every term is
 * listed with its count, and the unclassified are counted beside them.
 */

import { useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { IconDownload } from "@tabler/icons-react";
import { useState } from "react";
import { z } from "zod";

import { ChoiceFilter } from "~/components/choice-filter";
import { PageHeader } from "~/components/page-header";
import { SearchInput } from "~/components/search-input";
import { StatRow } from "~/components/stat-row";
import { StickyHeader } from "~/components/sticky-header";
import { TableToolbar } from "~/components/table-toolbar";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { api, type Classification } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount, formatShare } from "~/lib/format";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";

const searchSchema = z.object({
  table: listParam,
  gaps: z.union([z.literal("1"), z.literal(1)]).optional(),
  q: textParam,
});

export const Route = createFileRoute("/governance/classifications")({
  validateSearch: searchSchema,
  component: Classifications,
});

/** Terms shown before "show all": enough to see the shape of a vocabulary. */
const SHOWN = 12;

/**
 * Terms that differ only in case or spacing, which should almost always be
 * one term. Listed per vocabulary so the fix is a mapping, not a hunt.
 */
function nearDuplicates(classification: Classification): string[][] {
  const groups = new Map<string, string[]>();
  for (const { value } of classification.values) {
    const key = value.toLowerCase().replace(/[\s_-]+/g, "");
    groups.set(key, [...(groups.get(key) ?? []), value]);
  }
  return [...groups.values()].filter((group) => group.length > 1);
}

function Classifications() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const query = useQuery({
    queryKey: ["classifications"],
    queryFn: () => api.classifications(),
  });

  const all = query.data?.data ?? [];
  const tables = asTextList(search.table);
  const needle = asText(search.q)?.toLowerCase() ?? "";
  const gapsOnly = search.gaps !== undefined;

  const tableOptions = [...new Set(all.map((scheme) => scheme.table))].map((value) => ({
    value,
    label: value,
  }));

  const schemes = all
    .filter((scheme) => !tables.length || tables.includes(scheme.table))
    .filter(
      (scheme) =>
        !gapsOnly || scheme.unclassified > 0 || nearDuplicates(scheme).length > 0,
    )
    .map((scheme) => {
      if (!needle) return scheme;
      if (
        [scheme.label, scheme.scheme, scheme.column].some((text) =>
          text.toLowerCase().includes(needle),
        )
      ) {
        return scheme;
      }
      const values = scheme.values.filter((value) =>
        value.value.toLowerCase().includes(needle),
      );
      return values.length ? { ...scheme, values } : undefined;
    })
    .filter((scheme): scheme is Classification => Boolean(scheme));

  const withGaps = all.filter((scheme) => scheme.unclassified > 0).length;
  const withDuplicates = all.filter(
    (scheme) => nearDuplicates(scheme).length > 0,
  ).length;
  const terms = all.reduce((sum, scheme) => sum + scheme.values.length, 0);

  function set(next: Partial<z.infer<typeof searchSchema>>) {
    navigate({ search: (prev) => ({ ...prev, ...next }) });
  }

  function exportTerms() {
    const flat = schemes.flatMap((scheme) =>
      scheme.values.map((value) => ({
        scheme: scheme.scheme,
        table: scheme.table,
        column: scheme.column,
        value: value.value,
        count: value.count,
        unit: scheme.unit,
      })),
    );
    downloadCsv(
      "classifications.csv",
      toCsv(flat, [
        { key: "scheme", header: "scheme" },
        { key: "table", header: "table" },
        { key: "column", header: "column" },
        { key: "value", header: "value" },
        { key: "count", header: "count" },
        { key: "unit", header: "unit" },
      ]),
    );
  }

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Classifications"
            count={schemes.length}
            isLoading={query.isLoading}
            description="The vocabularies the lake is classified by — categories, frequencies, units, statuses, place and source types — with how much each term covers and how much carries none."
            actions={
              <Button
                variant="outline"
                size="sm"
                disabled={!schemes.length}
                onClick={exportTerms}
              >
                <IconDownload className="size-4" />
                Export
              </Button>
            }
          />
        }
        filters={
          <TableToolbar
            filters={
              <>
                <ChoiceFilter
                  label="Table"
                  options={tableOptions}
                  selected={tables}
                  onChange={(table) => set({ table })}
                />
                <Button
                  variant={gapsOnly ? "secondary" : "outline"}
                  size="sm"
                  className="h-8 rounded-full"
                  onClick={() => set({ gaps: gapsOnly ? undefined : "1" })}
                >
                  Needs attention only
                </Button>
                {tables.length || gapsOnly || search.q ? (
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
                placeholder="Search vocabularies and terms"
                onSearch={(q) => set({ q })}
              />
            }
          />
        }
      />

      <StatRow
        isLoading={query.isLoading}
        stats={[
          { label: "Vocabularies", value: formatCount(all.length) },
          { label: "Terms", value: formatCount(terms) },
          {
            label: "With unclassified rows",
            value: formatCount(withGaps),
            attention: withGaps > 0,
            onSelect: () => set({ gaps: "1" }),
          },
          {
            label: "With terms spelled two ways",
            value: formatCount(withDuplicates),
            attention: withDuplicates > 0,
            onSelect: () => set({ gaps: "1" }),
          },
        ]}
      />

      {query.isError ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
          {(query.error as Error).message}
        </p>
      ) : null}

      {query.isLoading ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {Array.from({ length: 4 }, (_, n) => (
            <Skeleton key={n} className="h-64 rounded-lg" />
          ))}
        </div>
      ) : schemes.length ? (
        <div className="grid items-start gap-4 lg:grid-cols-2">
          {schemes.map((scheme) => (
            <SchemeCard
              key={scheme.scheme}
              scheme={scheme}
              expanded={Boolean(needle)}
            />
          ))}
        </div>
      ) : (
        <p className="rounded-lg border px-4 py-10 text-center text-sm text-muted-foreground">
          {all.length
            ? "No vocabulary or term matches."
            : "No vocabularies are published yet."}
        </p>
      )}
    </div>
  );
}

function SchemeCard({
  scheme,
  expanded,
}: {
  scheme: Classification;
  expanded: boolean;
}) {
  const [showAll, setShowAll] = useState(false);
  const classified = scheme.values.reduce((sum, value) => sum + value.count, 0);
  // Out of everything the column covers, so the bars and the unclassified
  // share are on one scale. A list column counts each term once per row, so
  // there the scale is the largest term instead.
  const whole =
    scheme.column === "tags"
      ? Math.max(1, ...scheme.values.map((value) => value.count))
      : classified + scheme.unclassified;
  const duplicates = nearDuplicates(scheme);
  const shown = showAll || expanded ? scheme.values : scheme.values.slice(0, SHOWN);

  return (
    <section className="overflow-hidden rounded-lg border">
      <header className="space-y-1 border-b bg-muted/40 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-heading text-sm font-semibold">{scheme.label}</h2>
          <span className="font-mono text-xs text-muted-foreground">
            {scheme.table}.{scheme.column}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>
            {formatCount(scheme.values.length)} term
            {scheme.values.length === 1 ? "" : "s"}, counted in {scheme.unit}
          </span>
          {scheme.unclassified > 0 ? (
            <Badge variant="destructive">
              {formatCount(scheme.unclassified)} {scheme.unit} unclassified
              {scheme.column === "tags"
                ? null
                : ` (${formatShare(scheme.unclassified, whole)})`}
            </Badge>
          ) : (
            <Badge variant="secondary">Every row classified</Badge>
          )}
        </div>
        {duplicates.length ? (
          <p className="text-xs text-destructive">
            Spelled more than one way:{" "}
            {duplicates
              .map((group) => group.map((v) => `“${v}”`).join(" / "))
              .join("; ")}
          </p>
        ) : null}
      </header>

      {scheme.values.length ? (
        <ul className="divide-y">
          {shown.map((value) => (
            <li key={value.value} className="relative px-4 py-1.5 text-sm">
              <div
                aria-hidden
                className="absolute inset-y-0 left-0 bg-muted"
                style={{ width: `${Math.max(0.5, (value.count / whole) * 100)}%` }}
              />
              <div className="relative flex items-baseline justify-between gap-3">
                <span
                  className="min-w-0 truncate font-mono text-xs"
                  title={value.value}
                >
                  {value.value}
                </span>
                <span className="shrink-0 text-xs tabular-nums">
                  {formatCount(value.count)}
                  {scheme.column === "tags" ? null : (
                    <span className="ml-1 text-muted-foreground">
                      {formatShare(value.count, whole)}
                    </span>
                  )}
                </span>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="px-4 py-6 text-center text-sm text-muted-foreground">
          No row carries a term yet.
        </p>
      )}

      {!expanded && scheme.values.length > SHOWN ? (
        <div className="border-t px-4 py-2">
          <Button variant="ghost" size="sm" onClick={() => setShowAll(!showAll)}>
            {showAll ? "Show fewer" : `Show all ${formatCount(scheme.values.length)}`}
          </Button>
        </div>
      ) : null}
    </section>
  );
}
