import {
  type ColumnDef,
  type RowSelectionState,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { IconArrowDown, IconArrowUp, IconArrowsSort } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { Checkbox } from "~/components/ui/checkbox";
import { Skeleton } from "~/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "~/components/ui/table";
import { cn } from "~/lib/utils";

type DataTableProps<TData> = {
  columns: ColumnDef<TData, any>[];
  data: TData[];
  isLoading?: boolean;
  emptyMessage?: string;
  /** Rows of skeleton while loading, matching the expected page size. */
  loadingRows?: number;
  /** Adds a checkbox column and reports what is ticked. */
  selectable?: boolean;
  /** Stable identity per row, so a selection survives a re-fetch. */
  getRowId?: (row: TData, index: number) => string;
  onSelectionChange?: (rows: TData[]) => void;
  /** Rendered above the table when something is selected. */
  renderSelectionActions?: (rows: TData[]) => React.ReactNode;
};

export function DataTable<TData>({
  columns,
  data,
  isLoading = false,
  emptyMessage = "No rows.",
  loadingRows = 8,
  selectable = false,
  getRowId,
  onSelectionChange,
  renderSelectionActions,
}: DataTableProps<TData>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [selection, setSelection] = useState<RowSelectionState>({});

  const table = useReactTable({
    data,
    columns,
    state: { sorting, rowSelection: selection },
    onSortingChange: setSorting,
    onRowSelectionChange: setSelection,
    enableRowSelection: selectable,
    getRowId,
    getCoreRowModel: getCoreRowModel(),
    // Sorting is client-side over the page already fetched. Server-side
    // ordering is a separate concern, handled by the `order` parameter, and
    // mixing the two silently would sort one page against another's order.
    getSortedRowModel: getSortedRowModel(),
  });

  const selectedRows = table.getSelectedRowModel().rows.map((row) => row.original);

  useEffect(() => {
    onSelectionChange?.(selectedRows);
    // Comparing by count rather than by the array, which is rebuilt each render
    // and would loop.
  }, [selection]); // eslint-disable-line react-hooks/exhaustive-deps

  const columnCount = columns.length + (selectable ? 1 : 0);

  return (
    <div className="space-y-2">
      {selectable && selectedRows.length > 0 ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-muted/40 px-3 py-2 text-sm">
          <span className="font-medium">
            {selectedRows.length} selected
          </span>
          <button
            type="button"
            onClick={() => table.resetRowSelection()}
            className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          >
            Clear
          </button>
          {renderSelectionActions ? (
            <div className="ml-auto flex items-center gap-2">
              {renderSelectionActions(selectedRows)}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader className="bg-muted/40">
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id} className="hover:bg-transparent">
                {selectable ? (
                  <TableHead className="w-10 pl-3">
                    <Checkbox
                      checked={table.getIsAllPageRowsSelected()}
                      // base-ui carries the partial state in its own prop
                      // rather than as a third value of `checked`.
                      indeterminate={
                        table.getIsSomePageRowsSelected() &&
                        !table.getIsAllPageRowsSelected()
                      }
                      onCheckedChange={(checked) =>
                        table.toggleAllPageRowsSelected(checked)
                      }
                      aria-label="Select all rows on this page"
                    />
                  </TableHead>
                ) : null}

                {group.headers.map((header) => {
                  const meta = header.column.columnDef.meta;
                  const sortable = header.column.getCanSort();
                  const direction = header.column.getIsSorted();

                  return (
                    <TableHead
                      key={header.id}
                      className={cn(
                        // A rule between headers, so a wide table reads as
                        // columns rather than as a run of words. Only between:
                        // a trailing one would fence the table off from the
                        // border it already has.
                        "border-r last:border-r-0",
                        meta?.align === "right" && "text-right",
                      )}
                    >
                      <span
                        className={cn(
                          "flex items-center gap-1.5",
                          meta?.align === "right" && "justify-end",
                        )}
                      >
                        {header.isPlaceholder
                          ? null
                          : flexRender(header.column.columnDef.header, header.getContext())}

                        {sortable ? (
                          <button
                            type="button"
                            onClick={header.column.getToggleSortingHandler()}
                            aria-label={`Sort by ${String(header.column.id)}`}
                            className="ml-auto rounded p-0.5 text-muted-foreground hover:bg-background hover:text-foreground"
                          >
                            {direction === "asc" ? (
                              <IconArrowUp className="size-4" />
                            ) : direction === "desc" ? (
                              <IconArrowDown className="size-4" />
                            ) : (
                              <IconArrowsSort className="size-4 opacity-50" />
                            )}
                          </button>
                        ) : null}
                      </span>
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>

          <TableBody>
            {isLoading ? (
              Array.from({ length: loadingRows }).map((_, row) => (
                <TableRow key={`skeleton-${row}`}>
                  {Array.from({ length: columnCount }).map((_cell, cell) => (
                    <TableCell key={`skeleton-${row}-${cell}`}>
                      <Skeleton className="h-4 w-full" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : table.getRowModel().rows.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id} data-state={row.getIsSelected() ? "selected" : undefined}>
                  {selectable ? (
                    <TableCell className="pl-3">
                      <Checkbox
                        checked={row.getIsSelected()}
                        onCheckedChange={(checked) => row.toggleSelected(checked)}
                        aria-label="Select row"
                      />
                    </TableCell>
                  ) : null}

                  {row.getVisibleCells().map((cell) => (
                    <TableCell
                      key={cell.id}
                      className={
                        cell.column.columnDef.meta?.align === "right"
                          ? "text-right tabular-nums"
                          : ""
                      }
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell
                  colSpan={columnCount}
                  className="h-24 text-center text-muted-foreground"
                >
                  {emptyMessage}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

/** A two-line cell: the value, and what qualifies it. */
export function StackedCell({
  primary,
  secondary,
}: {
  primary: React.ReactNode;
  secondary?: React.ReactNode;
}) {
  return (
    <div className="leading-tight">
      <div className="font-medium">{primary}</div>
      {secondary ? (
        <div className="text-xs text-muted-foreground">{secondary}</div>
      ) : null}
    </div>
  );
}
