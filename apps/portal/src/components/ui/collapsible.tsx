import { Collapsible as CollapsiblePrimitive } from "@base-ui/react/collapsible";

import { cn } from "~/lib/utils";

function Collapsible({ ...props }: CollapsiblePrimitive.Root.Props) {
  return <CollapsiblePrimitive.Root data-slot="collapsible" {...props} />;
}

function CollapsibleTrigger({ ...props }: CollapsiblePrimitive.Trigger.Props) {
  return <CollapsiblePrimitive.Trigger data-slot="collapsible-trigger" {...props} />;
}

/**
 * The contents, animated between nothing and their natural height.
 *
 * Base UI measures the panel and publishes the result as
 * `--collapsible-panel-height`; height cannot transition against `auto`, so
 * that measurement is what the open and closed states interpolate between.
 */
function CollapsiblePanel({ className, ...props }: CollapsiblePrimitive.Panel.Props) {
  return (
    <CollapsiblePrimitive.Panel
      data-slot="collapsible-panel"
      className={cn(
        "h-(--collapsible-panel-height) overflow-hidden transition-[height] duration-200 ease-out starting:h-0 data-ending-style:h-0",
        className,
      )}
      {...props}
    />
  );
}

export { Collapsible, CollapsibleTrigger, CollapsiblePanel };
