/**
 * The row above a table: what narrows it, and what searches it.
 *
 * One row rather than two. Filters and search answer the same question — which
 * rows do I want — so putting them on separate lines makes the reader look in
 * two places and costs vertical space the table could use.
 *
 * Search sits right, where an action belongs, and wraps below the chips rather
 * than squashing them when the window is narrow.
 */
export function TableToolbar({
  filters,
  search,
}: {
  filters?: React.ReactNode;
  search?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {filters}
      {search ? (
        <div className="ml-auto w-full sm:w-auto sm:min-w-64">{search}</div>
      ) : null}
    </div>
  );
}
