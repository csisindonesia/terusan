import { Link } from "@tanstack/react-router";
import { IconSend, IconSparkles, IconUser } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";

import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "~/components/ui/sheet";
import { Textarea } from "~/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "~/components/ui/tooltip";
import { cn } from "~/lib/utils";

/**
 * Asking the warehouse a question in words — as a sketch, not as a feature.
 *
 * Nothing here talks to a model. The conversation is written down in this
 * file, the answers are canned, and every reply is drawn from figures that are
 * actually in this warehouse today so the sketch is arguing about a real
 * design rather than a plausible-looking one. The badge in the header says
 * "Preview" and the composer says what it would take to make it real, because
 * a chat box that invents an answer is the single worst thing a
 * source-traceable portal could ship.
 *
 * What the sketch is *for*: seeing whether an answer with its identifiers,
 * coverage and links attached is more useful than the same answer in prose.
 * Every canned reply names the series it is talking about and links to it, the
 * way a real one would have to.
 */

/**
 * Where an answer came from.
 *
 * A real answer would have to carry these, so the sketch does: an indicator by
 * the identifier the API actually serves, or a page that answers the same
 * question with its own provenance attached. The identifiers below are real —
 * every chip opens something.
 */
type Source =
  | { kind: "indicator"; id: string; label: string }
  | {
      kind: "page";
      to: "/observations" | "/commodities" | "/saved-queries";
      label: string;
    };

type Message = {
  role: "user" | "assistant";
  text: string;
  sources?: Source[];
};

/**
 * The seeded conversation.
 *
 * Grounded in what the warehouse holds: the identifiers, the coverage and the
 * counts below are the real ones. A demo that invents a series teaches a
 * reader a series that does not exist.
 */
const CONVERSATION: Message[] = [
  {
    role: "user",
    text: "What do you have on inflation, and how far back does it go?",
  },
  {
    role: "assistant",
    text: "Three series on three different calendars. BPS's Composite Consumer Price Index of 150 Cities is monthly from 2020-01 to 2026-08 — 80 figures, index points. FRED carries the long run: Consumer Price Index: All Items, monthly, 1968-02 to 2024-03, 674 figures. The World Bank's annual inflation rate covers 1960 to 2025.\n\nThe BPS index is the current one; the FRED series is what gives you the history it does not have.",
    sources: [
      { kind: "indicator", id: "6mxpn8s3", label: "Composite CPI of 150 Cities" },
      { kind: "indicator", id: "fv2r37v2", label: "CPI: All Items (FRED)" },
      { kind: "indicator", id: "3x6p3x67", label: "Inflation, consumer prices" },
    ],
  },
  {
    role: "user",
    text: "Can I compare palm oil exports against the world price?",
  },
  {
    role: "assistant",
    text: "Yes, but not on one axis. Bank Indonesia's SEKI table gives Total Value of Export of Palm oils, monthly from 2017-01 to 2026-07 in USD thousand. The price is Yahoo's crude palm oil close, daily from 2021-09-20 to 2026-09-17 in USD per tonne.\n\nDifferent units and different calendars, so they go into the combiner as two saved queries: it takes the union of the periods and folds the daily closes to a monthly mean, and it will tell you the two columns are not in the same unit rather than pretending they are.",
    sources: [
      { kind: "indicator", id: "188agog0", label: "Export of palm oils (SEKI)" },
      { kind: "indicator", id: "6i6osfxg", label: "Palm oil price close" },
      { kind: "page", to: "/saved-queries", label: "Combine them" },
    ],
  },
  {
    role: "user",
    text: "And rice prices?",
  },
  {
    role: "assistant",
    text: "Bank Indonesia's PIHPS survey: Food price retail, daily, 2026-09-10 to 2026-09-17 — a short window, 31 commodities, Beras among them with four quality grades priced separately.\n\nThat is the whole coverage right now: eight days. If you need a longer run of rice prices, nothing here has it yet, and that is worth a suggestion rather than a workaround.",
    sources: [
      { kind: "indicator", id: "b2ysi6ai", label: "Food price retail (PIHPS)" },
      { kind: "page", to: "/commodities", label: "Commodities" },
    ],
  },
];

/**
 * What a canned assistant says to anything else.
 *
 * It refuses rather than improvising. A portal whose whole claim is that every
 * figure carries its provenance cannot have a box in the corner that makes
 * figures up.
 */
const FALLBACK: Message = {
  role: "assistant",
  text: "This is a preview — the conversation above is written into the page, and there is no model behind this box yet. Nothing here will answer a new question, and inventing one would be worse than saying so.\n\nWhat the real thing would need: the catalogue as context, retrieval over the documents the figures were read out of, and every answer carrying the indicator ids it used. Until then, search and the data explorer answer the same questions with their sources attached.",
  sources: [{ kind: "page", to: "/observations", label: "Data explorer" }],
};

export function AskAI() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>(CONVERSATION);
  const [draft, setDraft] = useState("");
  const [thinking, setThinking] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, thinking]);

  function send() {
    const text = draft.trim();
    if (!text || thinking) return;
    setMessages((current) => [...current, { role: "user", text }]);
    setDraft("");
    setThinking(true);
    // A beat before the refusal, because an instant answer reads as a bug
    // rather than as a reply. It is the only thing here pretending anything.
    window.setTimeout(() => {
      setMessages((current) => [...current, FALLBACK]);
      setThinking(false);
    }, 450);
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger
          render={
            <SheetTrigger
              render={
                <Button variant="ghost" size="icon" aria-label="Ask the warehouse">
                  <IconSparkles />
                </Button>
              }
            />
          }
        />
        <TooltipContent>Ask the warehouse — preview</TooltipContent>
      </Tooltip>

      <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-md">
        <SheetHeader className="border-b p-4">
          <SheetTitle className="flex items-center gap-2">
            <IconSparkles className="size-4" />
            Ask the warehouse
            {/* Said on the way in, not discovered on the way out. */}
            <Badge variant="outline" className="ml-auto">
              Preview
            </Badge>
          </SheetTitle>
          <SheetDescription>
            A sketch of what asking in words could look like. The exchange below is
            written into the page; no model is connected.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {messages.map((message, index) => (
            <Bubble key={index} message={message} />
          ))}
          {thinking ? <p className="text-xs text-muted-foreground">Thinking…</p> : null}
          <div ref={endRef} />
        </div>

        <div className="border-t p-3">
          <div className="flex items-end gap-2">
            <Textarea
              rows={2}
              value={draft}
              placeholder="Ask about a series, a source or a period…"
              aria-label="Ask the warehouse"
              className="resize-none"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  send();
                }
              }}
            />
            <Button
              size="icon"
              aria-label="Send"
              disabled={!draft.trim() || thinking}
              onClick={send}
            >
              <IconSend className="size-4" />
            </Button>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            Answers here are canned. For a figure you can cite, use{" "}
            <Link to="/search" className="underline underline-offset-4">
              search
            </Link>{" "}
            or the{" "}
            <Link to="/observations" className="underline underline-offset-4">
              data explorer
            </Link>
            .
          </p>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function SourceChip({ source }: { source: Source }) {
  const chip = (
    <Badge
      variant="secondary"
      className="font-normal transition-colors hover:bg-secondary/70"
    >
      {source.label}
    </Badge>
  );

  if (source.kind === "indicator") {
    return (
      <Link to="/indicators/$indicatorId" params={{ indicatorId: source.id }}>
        {chip}
      </Link>
    );
  }
  return <Link to={source.to}>{chip}</Link>;
}

function Bubble({ message }: { message: Message }) {
  const mine = message.role === "user";

  return (
    <div className={cn("flex gap-2", mine && "flex-row-reverse")}>
      <span
        className={cn(
          "flex size-7 shrink-0 items-center justify-center rounded-lg",
          mine ? "bg-muted" : "bg-primary/10 text-primary",
        )}
      >
        {mine ? <IconUser className="size-4" /> : <IconSparkles className="size-4" />}
      </span>

      <div className={cn("min-w-0 max-w-[85%] space-y-2", mine && "text-right")}>
        <div
          className={cn(
            "inline-block rounded-lg px-3 py-2 text-left text-sm whitespace-pre-line",
            mine ? "bg-muted" : "bg-card ring-1 ring-foreground/10",
          )}
        >
          {message.text}
        </div>

        {/* Where the answer came from, and they open. A real one would have to
            carry these — the sketch carries them so the shape of the answer is
            the thing being judged, not the prose. */}
        {message.sources?.length ? (
          <div className="flex flex-wrap gap-1.5">
            {message.sources.map((source) => (
              <SourceChip key={source.label} source={source} />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
