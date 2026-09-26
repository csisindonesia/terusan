import { IconChartLine, IconCheck } from "@tabler/icons-react";
import { useState } from "react";

import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import type { ChartProposal } from "~/lib/assistant";
import { cn } from "~/lib/utils";

/**
 * A chart the assistant proposes, with a box to tick per series and the
 * button that draws it.
 *
 * The reply above already says what each series is and why it was chosen;
 * this is where the reader answers. Unticking a series draws the chart
 * without it. Once answered — or once the conversation has moved on — the
 * card only records what was proposed.
 */
export function ChartProposalCard({
  proposal,
  active,
  confirmed,
  onConfirm,
}: {
  proposal: ChartProposal;
  /** Whether it can still be answered: the latest reply, nothing streaming. */
  active: boolean;
  /** Whether the reader went ahead with it. */
  confirmed: boolean;
  onConfirm: (keep: string[]) => void;
}) {
  const [keep, setKeep] = useState(() => proposal.series.map((s) => s.id));
  const id = proposal.confirm_text.startsWith("Ya");
  const words = id
    ? {
        draw: "Buat grafik",
        chosen: "seri dipilih",
        done: "Grafik dibuat",
        title: "Usulan grafik",
      }
    : {
        draw: "Draw chart",
        chosen: "series selected",
        done: "Chart drawn",
        title: "Proposed chart",
      };

  return (
    <div className="mt-3 rounded-xl border bg-card p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium">
        <IconChartLine className="size-4 text-muted-foreground" />
        <span className="text-muted-foreground">{words.title}:</span>
        <span className="truncate">{proposal.title}</span>
      </div>
      <ul className="space-y-2">
        {proposal.series.map((series) => {
          const checked = keep.includes(series.id);
          return (
            <li key={series.id}>
              <label
                className={cn(
                  "flex items-start gap-2.5 text-sm",
                  active ? "cursor-pointer" : "cursor-default opacity-80",
                )}
              >
                <Checkbox
                  className="mt-0.5"
                  checked={checked}
                  disabled={!active}
                  onCheckedChange={(next) =>
                    setKeep((current) =>
                      next
                        ? [...current, series.id]
                        : current.filter((kept) => kept !== series.id),
                    )
                  }
                />
                <span className="min-w-0">
                  <span
                    className={cn(!checked && "text-muted-foreground line-through")}
                  >
                    {series.label}
                  </span>
                  <span className="block text-xs text-muted-foreground">
                    {[series.member, series.unit, `${series.from}–${series.to}`]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </span>
              </label>
            </li>
          );
        })}
      </ul>
      <div className="mt-4 flex items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground">
          {keep.length}/{proposal.series.length} {words.chosen}
        </span>
        {confirmed ? (
          <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <IconCheck className="size-4" />
            {words.done}
          </span>
        ) : (
          <Button
            size="sm"
            disabled={!active || keep.length === 0}
            onClick={() => onConfirm(keep)}
          >
            <IconChartLine className="size-4" />
            {words.draw}
          </Button>
        )}
      </div>
    </div>
  );
}
