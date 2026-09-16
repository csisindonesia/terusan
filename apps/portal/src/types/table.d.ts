import "@tanstack/react-table";

declare module "@tanstack/react-table" {
  interface ColumnMeta<TData extends RowData, TValue> {
    // Numeric columns right-align and use tabular figures, so a column of
    // values lines up at the decimal point rather than reading ragged.
    align?: "left" | "right";
  }
}
