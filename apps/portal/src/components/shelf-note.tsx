import { IconAlertTriangle, IconCloud, IconDeviceDesktop } from "@tabler/icons-react";

import { useShelf } from "~/lib/workspace";

/**
 * Where the collections on this page are kept.
 *
 * Worth a line under the heading, because the two homes make different
 * promises. A shelf the API keeps has a URL a colleague can open and survives
 * this browser; one kept in the browser survives a reload and nothing else —
 * not another machine, not a cleared cache, not a link. A reader who assumes
 * the first while the second is true loses a morning's filing to a setting
 * nobody showed them.
 */
export function ShelfNote({ className }: { className?: string }) {
  const shelf = useShelf();

  if (shelf.mode === "loading") return null;

  return (
    <p className={className}>
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        {shelf.mode === "server" ? (
          <>
            <IconCloud className="size-3.5" />
            {shelf.writable
              ? "Kept by the API — these links open anywhere."
              : "Served read-only by the API; changes are refused."}
          </>
        ) : (
          <>
            <IconDeviceDesktop className="size-3.5" />
            Kept in this browser. Export to move them, or point the API at a collections
            database to give them URLs.
          </>
        )}
      </span>
      {shelf.error ? (
        <span className="ml-2 inline-flex items-center gap-1.5 text-xs text-destructive">
          <IconAlertTriangle className="size-3.5" />
          {shelf.error}
        </span>
      ) : null}
    </p>
  );
}
