import "@tanstack/react-table";

declare module "@tanstack/react-table" {
  interface ColumnMeta<TData extends RowData, TValue> {
    // Numeric columns right-align and use tabular figures, so a column of
    // values lines up at the decimal point rather than reading ragged.
    align?: "left" | "right";
    // A width for the column, as a utility class. Without one the table gives
    // the last column whatever is left over, so a clamped cell sits in a column
    // three times its width.
    width?: string;
  }
}
