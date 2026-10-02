/**
 * The columns of every table in the lake, as the files declare them
 * (program.md §5–8).
 *
 * Read off the Parquet itself rather than from a schema someone wrote down,
 * so it cannot drift from what a query will actually find — and read across
 * every file of a table, so a column only newer files carry is listed too.
 * Bronze is all text by design; Silver is where types are decided, which is
 * why a `DECIMAL` there and a `VARCHAR` in Bronze for the same figure is the
 * system working rather than a mismatch.
 */

import { useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { IconCopy, IconDownload } from "@tabler/icons-react";
import { z } from "zod";

import { ChoiceFilter } from "~/components/choice-filter";
import { PageHeader } from "~/components/page-header";
import { copyToClipboard } from "~/components/row-actions";
import { SearchInput } from "~/components/search-input";
import { StickyHeader } from "~/components/sticky-header";
import { TableToolbar } from "~/components/table-toolbar";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { api, type TableSchema } from "~/lib/api";
import { downloadCsv, toCsv } from "~/lib/csv";
import { formatCount } from "~/lib/format";
import { titleFromId } from "~/lib/labels";
import { layerNote } from "~/lib/layers";
import { asText, asTextList, listParam, textParam } from "~/lib/search-params";

const searchSchema = z.object({
  layer: listParam,
  q: textParam,
});

export const Route = createFileRoute("/governance/schemas")({
  validateSearch: searchSchema,
  component: Schemas,
});

function Schemas() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const schemas = useQuery({ queryKey: ["schemas"], queryFn: () => api.schemas() });

  const all = schemas.data?.data ?? [];
  const layers = asTextList(search.layer);
  const needle = asText(search.q)?.toLowerCase() ?? "";

  const layerOptions = [...new Set(all.map((table) => table.layer))].map((value) => ({
    value,
    label: titleFromId(value),
  }));

  // A search matches a table by name, or narrows it to the columns that match:
  // "which tables carry `content_hash`" is the question this page is most
  // often asked.
  const tables = all
    .filter((table) => !layers.length || layers.includes(table.layer))
    .map((table) => {
      if (!needle) return table;
      const named = [table.name, table.dataset, table.slug].some((text) =>
        text.toLowerCase().includes(needle),
      );
      if (named) return table;
      const columns = table.columns.filter(
        (column) =>
          column.name.toLowerCase().includes(needle) ||
          column.type.toLowerCase().includes(needle),
      );
      return columns.length ? { ...table, columns } : undefined;
    })
    .filter((table): table is TableSchema => Boolean(table));

  const columnCount = tables.reduce((sum, table) => sum + table.columns.length, 0);

  function set(next: Partial<z.infer<typeof searchSchema>>) {
    navigate({ search: (prev) => ({ ...prev, ...next }) });
  }

  function exportColumns() {
    const flat = tables.flatMap((table) =>
      table.columns.map((column) => ({
        layer: table.layer,
        table: table.dataset,
        column: column.name,
        type: column.type,
      })),
    );
    downloadCsv(
      "lake-schemas.csv",
      toCsv(flat, [
        { key: "layer", header: "layer" },
        { key: "table", header: "table" },
        { key: "column", header: "column" },
        { key: "type", header: "type" },
      ]),
    );
  }

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <PageHeader
            title="Schemas"
            count={tables.length}
            isLoading={schemas.isLoading}
            description={`The columns of every table in the lake, read off the files themselves. ${
              schemas.isLoading ? "" : `${formatCount(columnCount)} columns shown.`
            }`}
            actions={
              <Button
                variant="outline"
                size="sm"
                disabled={!tables.length}
                onClick={exportColumns}
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
                  label="Layer"
                  options={layerOptions}
                  selected={layers}
                  onChange={(layer) => set({ layer })}
                />
                {layers.length || search.q ? (
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
                placeholder="Search tables, columns and types"
                onSearch={(q) => set({ q })}
              />
            }
          />
        }
      />

      {schemas.isError ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm">
          {(schemas.error as Error).message}
        </p>
      ) : null}

      {schemas.isLoading ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {Array.from({ length: 4 }, (_, n) => (
            <Skeleton key={n} className="h-64 rounded-lg" />
          ))}
        </div>
      ) : tables.length ? (
        <div className="grid items-start gap-4 lg:grid-cols-2">
          {tables.map((table) => (
            <TableCard key={table.slug} table={table} />
          ))}
        </div>
      ) : (
        <p className="rounded-lg border px-4 py-10 text-center text-sm text-muted-foreground">
          {all.length ? "No table or column matches." : "The lake holds no tables yet."}
        </p>
      )}
    </div>
  );
}

function TableCard({ table }: { table: TableSchema }) {
  return (
    <section className="overflow-hidden rounded-lg border">
      <header className="flex items-start justify-between gap-3 border-b bg-muted/40 px-4 py-3">
        <div className="min-w-0 space-y-0.5">
          <div className="flex items-center gap-2">
            <h2 className="font-heading text-sm font-semibold">{table.name}</h2>
            <Badge variant="outline" title={layerNote(table.layer)}>
              {titleFromId(table.layer)}
            </Badge>
          </div>
          <div className="font-mono text-xs text-muted-foreground">
            {table.layer}/{table.dataset} · {formatCount(table.columns.length)} column
            {table.columns.length === 1 ? "" : "s"}
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          title="Copy the column names"
          onClick={() =>
            void copyToClipboard(table.columns.map((column) => column.name).join(", "))
          }
        >
          <IconCopy className="size-4" />
        </Button>
      </header>
      <table className="w-full text-sm">
        <tbody className="divide-y">
          {table.columns.map((column) => (
            <tr key={column.name}>
              <td className="px-4 py-1.5 font-mono text-xs break-all">{column.name}</td>
              <td className="px-4 py-1.5 text-right font-mono text-xs break-all text-muted-foreground">
                {column.type}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
