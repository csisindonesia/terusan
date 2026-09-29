import { IconCopy } from "@tabler/icons-react";

import { copyToClipboard } from "~/components/row-actions";
import { Button } from "~/components/ui/button";

/*
 * The pieces an API tab is made of — a request to copy, and the paths beside
 * it — shared so that a collection's API reads the same as an indicator's.
 */

/** A block of code with a way to take it. */
export function Snippet({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <Button
          variant="ghost"
          size="sm"
          className="h-7"
          onClick={() => void copyToClipboard(value)}
        >
          <IconCopy className="size-3.5" />
          Copy
        </Button>
      </div>
      {/* Scrolls rather than wraps: a broken URL is a URL that will not paste.
          A code block reads as something to be copied and run rather than
          something to be read, and the surface says so before the text does.
          The steps are fixed rather than themed, because this block sets its
          own ground and the pairing has to hold on its own.

          It darkens against a light page and *lifts* against a dark one: the
          dark theme's ground is already near-black, so a darker block would
          differ from it by a contrast ratio of 1.00 — invisible. */}
      <pre className="overflow-x-auto rounded-lg bg-neutral-900 px-3 py-2.5 text-xs leading-relaxed text-neutral-100 dark:border dark:border-border dark:bg-neutral-800">
        <code>{value}</code>
      </pre>
    </div>
  );
}

export function Endpoint({ path, description }: { path: string; description: string }) {
  return (
    <div className="space-y-0.5">
      <dt>
        <code className="text-xs">{path}</code>
      </dt>
      <dd className="text-xs text-muted-foreground">{description}</dd>
    </div>
  );
}
