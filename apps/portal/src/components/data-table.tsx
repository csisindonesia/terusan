import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { IconArrowDown, IconArrowUp, IconArrowsSort } from "@tabler/icons-react";
import { useState } from "react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "~/components/ui/table";
import { Skeleton } from "~/components/ui/skeleton";

type DataTableProps<TData> = {
  columns: ColumnDef<TData, any>[];
  data: TData[];
  isLoading?: boolean;
  emptyMessage?: string;
  /** Rows of skeleton to show while loading, matching the expected page size. */
  loadingRows?: number;
};

export function DataTable<TData>({
  columns,
  data,
  isLoading = false,
  emptyMessage = "No rows.",
  loadingRows = 8,
}: DataTableProps<TData>) {
  const [sorting, setSorting] = useState<SortingState>([]);

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    // Sorting is client-side over the page already fetched. Server-side
    // ordering is a separate concern, handled by the `order` parameter, and
    // mixing the two silently would sort one page against another's order.
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((group) => (
            <TableRow key={group.id}>
              {group.headers.map((header) => {
                const sortable = header.column.getCanSort();
                const direction = header.column.getIsSorted();
                return (
                  <TableHead
                    key={header.id}
                    className={header.column.columnDef.meta?.align === "right" ? "text-right" : ""}
                  >
                    {header.isPlaceholder ? null : sortable ? (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className="inline-flex items-center gap-1 hover:text-foreground"
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {direction === "asc" ? (
                          <IconArrowUp className="size-4" />
                        ) : direction === "desc" ? (
                          <IconArrowDown className="size-4" />
                        ) : (
                          <IconArrowsSort className="size-4 opacity-40" />
                        )}
                      </button>
                    ) : (
                      flexRender(header.column.columnDef.header, header.getContext())
                    )}
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
                {columns.map((_column, cell) => (
                  <TableCell key={`skeleton-${row}-${cell}`}>
                    <Skeleton className="h-4 w-full" />
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : table.getRowModel().rows.length ? (
            table.getRowModel().rows.map((row) => (
              <TableRow key={row.id}>
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
                colSpan={columns.length}
                className="h-24 text-center text-muted-foreground"
              >
                {emptyMessage}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}
