import { IconCopy, IconDotsVertical, IconPencil, IconTrash } from "@tabler/icons-react";
import type { Icon } from "@tabler/icons-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "~/components/ui/dropdown-menu";
import { Button } from "~/components/ui/button";

export type RowAction = {
  label: string;
  icon: Icon;
  onSelect?: () => void;
  /** Absent `onSelect` already disables an item; this explains why. */
  hint?: string;
  destructive?: boolean;
};

/**
 * The per-row menu.
 *
 * Edit and delete are listed and disabled: the serving layer is read-only —
 * four GET routes, no authentication (program.md §34) — so a working edit
 * would have nothing to call. Showing them says the row is meant to be
 * editable one day; wiring them to nothing would say it already is.
 */
export function RowActions({
  actions,
  unavailable,
  editable = false,
  bare = false,
  label = "Row actions",
}: {
  /** The things that do work, shown above the standard pair. */
  actions?: RowAction[];
  /** Further entries that need an endpoint nobody has built yet. */
  unavailable?: RowAction[];
  /** Set when the write endpoints exist, which today they do not. */
  editable?: boolean;
  /** Only `actions`, without the standard edit/delete pair. */
  bare?: boolean;
  label?: string;
}) {
  const standard: RowAction[] = bare ? [] : [
    ...(unavailable ?? []).map((action) => ({
      ...action,
      onSelect: editable ? action.onSelect : undefined,
    })),
    {
      label: "Edit",
      icon: IconPencil,
      hint: editable ? undefined : "The serving layer is read-only",
    },
    {
      label: "Delete",
      icon: IconTrash,
      destructive: true,
      hint: editable ? undefined : "The serving layer is read-only",
    },
  ];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={label}
            className="data-[popup-open]:bg-muted"
          >
            <IconDotsVertical />
          </Button>
        }
      />
      <DropdownMenuContent align="end" className="w-52">
        {actions?.length ? (
          <>
            {actions.map((action) => (
              <ActionItem key={action.label} action={action} />
            ))}
            {standard.length ? <DropdownMenuSeparator /> : null}
          </>
        ) : null}

        {standard.map((action) => (
          <ActionItem key={action.label} action={action} />
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ActionItem({ action }: { action: RowAction }) {
  return (
    <DropdownMenuItem
      disabled={!action.onSelect}
      onClick={action.onSelect}
      title={action.hint}
      variant={action.destructive ? "destructive" : undefined}
    >
      <action.icon />
      {action.label}
    </DropdownMenuItem>
  );
}

/** Copy text, reporting whether the browser allowed it. */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Denied permission, or an insecure origin. The caller decides what to say.
    return false;
  }
}

export { IconCopy };
