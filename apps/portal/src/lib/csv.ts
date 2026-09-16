/**
 * Turning rows into a CSV a spreadsheet will open correctly.
 *
 * Values are quoted rather than escaped case by case: a country name with a
 * comma, a title with a quotation mark and a description with a newline all
 * appear in this data, and each breaks a naive join.
 */

function quote(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return `"${text.replace(/"/g, '""')}"`;
}

export function toCsv<T extends Record<string, unknown>>(
  rows: T[],
  columns: { key: keyof T & string; header: string }[],
): string {
  const head = columns.map((column) => quote(column.header)).join(",");
  const body = rows.map((row) =>
    columns.map((column) => quote(row[column.key])).join(","),
  );
  return [head, ...body].join("\n");
}

/** Hand the browser a file to save. */
export function downloadCsv(filename: string, csv: string): void {
  // A BOM, so Excel reads the file as UTF-8 rather than as the local codepage
  // and mangles every accented place name.
  const blob = new Blob(["﻿", csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
