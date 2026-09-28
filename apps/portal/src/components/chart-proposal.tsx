import { IconChartLine, IconCheck } from "@tabler/icons-react";
import { useState } from "react";

import {
  Questionnaire,
  QuestionnaireActions,
  QuestionnaireChoice,
  QuestionnaireChoiceDescription,
  QuestionnaireChoices,
  QuestionnaireDescription,
  QuestionnaireItem,
  QuestionnaireSubmit,
  QuestionnaireTitle,
} from "~/components/ui/questionnaire";
import type { ChartProposal } from "~/lib/assistant";
import { cn } from "~/lib/utils";

/**
 * A chart the assistant proposes, as a one-item questionnaire: a choice to
 * tick per series and the button that draws it.
 *
 * The reply above already says what each series is and why it was chosen;
 * this is where the reader answers. Unticking a series draws the chart
 * without it; with the card focused, the number keys tick series and Enter
 * draws. Once answered — or once the conversation has moved on — the card
 * only records what was proposed.
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
        holidays: "Hari raya",
      }
    : {
        draw: "Draw chart",
        chosen: "series selected",
        done: "Chart drawn",
        title: "Proposed chart",
        holidays: "Holidays",
      };

  return (
    <Questionnaire
      className="mt-3 rounded-xl bg-card p-4"
      shortcuts={active ? "numbers" : undefined}
      onSubmit={(event) => {
        event.preventDefault();
        // In the order proposed, not the order ticked.
        const kept = proposal.series.map((s) => s.id).filter((id) => keep.includes(id));
        if (active && kept.length) onConfirm(kept);
      }}
    >
      <QuestionnaireItem name="series" multiple required>
        <QuestionnaireTitle className="flex items-center gap-2 text-sm">
          <IconChartLine className="size-4 shrink-0 text-muted-foreground" />
          <span className="text-muted-foreground">{words.title}:</span>
          <span className="truncate">{proposal.title}</span>
        </QuestionnaireTitle>
        {proposal.events?.length ? (
          // The holidays the series would be read around: not a choice here,
          // but what the chart is of as much as the series is.
          <QuestionnaireDescription className="text-xs">
            {words.holidays}:{" "}
            {proposal.events
              .map((e) => `${e.name} (${e.count}×, ${e.years})`)
              .join(" · ")}
          </QuestionnaireDescription>
        ) : null}
        {/* Inert rather than disabled once answered: the questionnaire reads
            a disabled choice as no answer and marks the item invalid, and a
            disabled item as one that does not apply, which it hides. */}
        <QuestionnaireChoices inert={!active} className={cn(!active && "opacity-80")}>
          {proposal.series.map((series) => (
            <QuestionnaireChoice
              key={series.id}
              value={series.id}
              checked={keep.includes(series.id)}
              onChange={(event) => {
                const next = event.currentTarget.checked;
                setKeep((current) =>
                  next
                    ? [...current, series.id]
                    : current.filter((kept) => kept !== series.id),
                );
              }}
            >
              <span>{series.label}</span>
              <QuestionnaireChoiceDescription className="text-xs">
                {[series.member, series.unit, `${series.from}–${series.to}`]
                  .filter(Boolean)
                  .join(" · ")}
              </QuestionnaireChoiceDescription>
            </QuestionnaireChoice>
          ))}
        </QuestionnaireChoices>
      </QuestionnaireItem>
      <QuestionnaireActions>
        <span className="text-xs text-muted-foreground">
          {keep.length}/{proposal.series.length} {words.chosen}
        </span>
        {confirmed ? (
          <span className="col-start-3 flex items-center gap-1.5 text-sm text-muted-foreground">
            <IconCheck className="size-4" />
            {words.done}
          </span>
        ) : (
          <QuestionnaireSubmit size="sm" disabled={!active || keep.length === 0}>
            <IconChartLine className="size-4" />
            {words.draw}
          </QuestionnaireSubmit>
        )}
      </QuestionnaireActions>
    </Questionnaire>
  );
}
