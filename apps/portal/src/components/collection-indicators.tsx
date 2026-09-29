import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { IconTrash, IconX } from "@tabler/icons-react";

import { ClampedText } from "~/components/clamped-text";
import { DataTable, StackedCell } from "~/components/data-table";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { api, type Indicator } from "~/lib/api";
import { formatCount, formatDate, formatRelative } from "~/lib/format";
import { indicatorLabel } from "~/lib/labels";
import {
  filesInto,
  itemsOfKind,
  removeFromCollection,
  useShelf,
  type Collection,
  type CollectionItem,
} from "~/lib/workspace";

/**
 * How many identifiers one catalogue request names. The API refuses more than
 * two hundred in one `id` filter, so a larger collection is read in pieces.
 */
const IDS_PER_REQUEST = 200;

/**
 * The catalogue entry of every series in a collection, by identifier.
 *
 * Read from the lake-wide catalogue rather than from the collection's own
 * `/indicators` route, because that route answers only once the owner has
 * turned the API on, and a folder's table should not depend on a setting
 * about scripts.
 */
export function useCollectionIndicators(collection: Collection) {
  const ids = itemsOfKind(collection, "indicator").map((item) => item.id);
  return useQuery({
    queryKey: ["collection-indicators", ids],
    enabled: ids.length > 0,
    queryFn: async () => {
      const chunks: string[][] = [];
      for (let at = 0; at < ids.length; at += IDS_PER_REQUEST) {
        chunks.push(ids.slice(at, at + IDS_PER_REQUEST));
      }
      const pages = await Promise.all(
        chunks.map((id) => api.indicators({ id, limit: id.length })),
      );
      const byId = new Map<string, Indicator>();
      for (const page of pages) {
        for (const indicator of page.data) byId.set(indicator.indicator_id, indicator);
      }
      return byId;
    },
  });
}

/** One row of the table: what was filed, and what the catalogue says of it. */
type Row = {
  item: CollectionItem;
  /** Absent while loading, and for a series since dropped from the lake. */
  indicator?: Indicator;
};

/**
 * The series in a collection, as a table.
 *
 * A list said only what each series was called when it was filed. A table
 * says what a reader needs to decide whether the collection answers their
 * question — how often each series is published, in what unit, and over which
 * years — without opening eight tabs to find out.
 */
export function CollectionIndicators({ collection }: { collection: Collection }) {
  const shelf = useShelf();
  const catalogue = useCollectionIndicators(collection);
  const items = itemsOfKind(collection, "indicator");
  const removable = shelf.writable && filesInto(collection);

  const rows: Row[] = items.map((item) => ({
    item,
    indicator: catalogue.data?.get(item.id),
  }));
  const loading = catalogue.isLoading;

  // A cell for a catalogue fact: a skeleton until the catalogue arrives, and a
  // dash for a series it no longer holds — the "no longer held" is said once,
  // under the name, rather than in every column.
  const fact = (row: Row, render: (indicator: Indicator) => React.ReactNode) =>
    row.indicator ? (
      render(row.indicator)
    ) : loading ? (
      <Skeleton className="h-4 w-16" />
    ) : (
      <span className="text-muted-foreground">—</span>
    );

  const columns: ColumnDef<Row>[] = [
    {
      id: "indicator",
      header: "Indicator",
      accessorFn: (row) =>
        row.indicator ? indicatorLabel(row.indicator) : row.item.label,
      meta: { width: "w-72" },
      cell: ({ row }) => {
        const { item, indicator } = row.original;
        return (
          <Link
            to="/indicators/$indicatorId"
            params={{ indicatorId: item.id }}
            className="underline-offset-4 hover:underline"
          >
            <div className="leading-tight">
              <ClampedText className="max-w-[17rem] font-medium">
                {indicator ? indicatorLabel(indicator) : item.label}
              </ClampedText>
              {/* Clamped like the name: a publisher's code can be the whole
                  title again with a prefix, and it widens the column as much. */}
              <ClampedText className="max-w-[17rem] font-mono text-xs text-muted-foreground">
                {indicator?.code ?? item.id}
              </ClampedText>
              {!indicator && !loading && catalogue.isSuccess ? (
                <div className="text-xs text-destructive">
                  No longer in the warehouse
                </div>
              ) : null}
            </div>
          </Link>
        );
      },
    },
    {
      id: "frequency",
      header: "Frequency",
      accessorFn: (row) => row.indicator?.temporal_resolution ?? "",
      cell: ({ row }) =>
        fact(row.original, (indicator) => (
          <Badge variant="secondary">{indicator.temporal_resolution}</Badge>
        )),
    },
    {
      id: "unit",
      header: "Unit",
      accessorFn: (row) => row.indicator?.unit ?? "",
      cell: ({ row }) =>
        fact(row.original, (indicator) =>
          indicator.unit ? (
            <ClampedText className="max-w-[11rem]">{indicator.unit}</ClampedText>
          ) : (
            <span className="text-muted-foreground">—</span>
          ),
        ),
    },
    {
      id: "coverage",
      header: "Coverage",
      accessorFn: (row) => row.indicator?.period_end ?? "",
      cell: ({ row }) =>
        fact(row.original, (indicator) => (
          <StackedCell
            primary={`${indicator.period_start}–${indicator.period_end}`}
            secondary={`${formatCount(indicator.observations)} figures`}
          />
        )),
    },
    {
      id: "updated",
      header: "Latest update",
      accessorFn: (row) => row.indicator?.last_updated ?? "",
      cell: ({ row }) =>
        fact(row.original, (indicator) => (
          <StackedCell
            primary={formatDate(indicator.last_updated)}
            secondary={formatRelative(indicator.last_updated)}
          />
        )),
    },
    {
      id: "added",
      header: "Added",
      accessorFn: (row) => row.item.added_at,
      cell: ({ row }) => (
        // The note is the reader's own words and can run to a paragraph; one
        // of those would set the column's width for every row.
        <StackedCell
          primary={formatDate(row.original.item.added_at)}
          secondary={
            row.original.item.note ? (
              <ClampedText className="max-w-[12rem]">
                {row.original.item.note}
              </ClampedText>
            ) : undefined
          }
        />
      ),
    },
    ...(removable
      ? [
          {
            id: "actions",
            header: "",
            enableSorting: false,
            meta: { align: "right" as const },
            cell: ({ row }: { row: { original: Row } }) => (
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Remove ${row.original.item.label}`}
                onClick={() =>
                  removeFromCollection(collection.id, "indicator", row.original.item.id)
                }
              >
                <IconX className="size-4" />
              </Button>
            ),
          } satisfies ColumnDef<Row>,
        ]
      : []),
  ];

  return (
    <div className="space-y-2">
      {catalogue.isError ? (
        <p className="text-sm text-muted-foreground">
          The catalogue could not be read, so only what each series was called when it
          was filed is shown: {(catalogue.error as Error).message}
        </p>
      ) : null}
      <DataTable
        columns={columns}
        data={rows}
        getRowId={(row) => row.item.id}
        emptyMessage="No indicators here yet."
        // Tidying a collection is usually taking several series out at once,
        // and a cross per row makes that one click and one re-render each.
        selectable={removable}
        renderSelectionActions={(selected) => (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              for (const row of selected) {
                removeFromCollection(collection.id, "indicator", row.item.id);
              }
            }}
          >
            <IconTrash className="size-4" />
            Remove{" "}
            {selected.length === 1 ? "indicator" : `${selected.length} indicators`}
          </Button>
        )}
      />
    </div>
  );
}
