import { Link } from "@tanstack/react-router";
import { IconSparkles } from "@tabler/icons-react";

import { Button } from "~/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "~/components/ui/tooltip";

/**
 * The way into the assistant from anywhere (see routes/assistant.tsx).
 *
 * Always shown: the page itself says so where the deployment has no model
 * behind it, and an icon that comes and goes with a capability report reads
 * as a broken bar.
 */
export function AskAI() {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon"
            aria-label="Ask the assistant"
            nativeButton={false}
            render={<Link to="/assistant/{-$chatId}" params={{ chatId: undefined }} />}
          >
            <IconSparkles />
          </Button>
        }
      />
      <TooltipContent>Ask the assistant</TooltipContent>
    </Tooltip>
  );
}
