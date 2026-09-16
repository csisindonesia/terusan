import "@tanstack/react-table";

declare module "@tanstack/react-table" {
  // Numeric columns right-align and use tabular figures, so a column of GDP
  // values lines up at the decimal point rather than reading ragged.
  interface ColumnMeta<TData extends RowData, TValue> {
    align?: "left" | "right";
  }
}
