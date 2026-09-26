import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { IconArrowLeft, IconCopy, IconExternalLink } from "@tabler/icons-react";
import { z } from "zod";

import { PageHeader } from "~/components/page-header";
import { RowActions, copyToClipboard } from "~/components/row-actions";
import { BELOW_STICKY_HEADER, StickyHeader } from "~/components/sticky-header";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import {
  api,
  type RegulationCitation,
  type RegulationDetail,
  type RegulationSection,
} from "~/lib/api";
import { formatCount } from "~/lib/format";

const TABS = ["text", "recitals", "citations"] as const;
type TabName = (typeof TABS)[number];

const searchSchema = z.object({
  tab: z.enum(TABS).optional(),
});

export const Route = createFileRoute("/regulations/$key")({
  validateSearch: searchSchema,
  component: RegulationPage,
});

/** How a lawyer cites it: instrument, number, year. */
function citationOf(regulation: RegulationDetail): string {
  return [
    regulation.instrument ?? "Peraturan",
    regulation.region_name,
    regulation.number && `No ${regulation.number}`,
    regulation.year && `Tahun ${regulation.year}`,
  ]
    .filter(Boolean)
    .join(" ");
}

/**
 * Why a regulation has no text here, in words rather than in a status code.
 *
 * The corpus records four reasons and they mean different things to a reader:
 * one is a conversion that produced nothing, another is a document whose
 * articles never closed. Showing `unbounded` and leaving it there is showing
 * our working, not theirs.
 */
const NO_TEXT: Record<string, string> = {
  no_sections:
    "The PDF converted, but nothing in it parsed as an article — usually a scan the text layer never covered.",
  unbounded:
    "The articles parsed but never closed, so the segmentation was discarded rather than published half-right.",
  empty: "The conversion produced an empty document.",
};

function RegulationPage() {
  const { key } = Route.useParams();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const tab: TabName = search.tab ?? "text";

  const detail = useQuery({
    queryKey: ["regulation", key],
    queryFn: () => api.regulation(key),
  });

  const regulation = detail.data?.data;
  const readable = regulation?.parse_status === "ok";

  const sections = useQuery({
    queryKey: ["regulation-sections", key],
    queryFn: () => api.regulationSections(key),
    enabled: Boolean(readable),
  });

  const citations = useQuery({
    queryKey: ["regulation-citations", key],
    queryFn: () => api.regulationCitations(key),
    enabled: Boolean(regulation),
  });

  if (detail.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (detail.isError || !regulation) {
    return (
      <div className="space-y-4">
        <Link
          to="/regulations"
          className="text-sm text-muted-foreground hover:underline"
        >
          <IconArrowLeft className="mr-1 inline size-4" />
          Regulations
        </Link>
        <p className="text-sm">No regulation is catalogued under {key}.</p>
      </div>
    );
  }

  const rows = sections.data?.data ?? [];
  const cited = citations.data?.data ?? [];

  return (
    <div className="space-y-5">
      <StickyHeader
        heading={
          <div className="space-y-3">
            <Link
              to="/regulations"
              className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline"
            >
              <IconArrowLeft className="size-4" />
              Regulations
            </Link>
            <PageHeader
              title={regulation.title}
              description={citationOf(regulation)}
              actions={
                <div className="flex items-center gap-2">
                  {regulation.pdf_url ? (
                    <Button
                      variant="outline"
                      size="sm"
                      render={
                        <a href={regulation.pdf_url} target="_blank" rel="noreferrer" />
                      }
                    >
                      <IconExternalLink className="size-4" />
                      Open the PDF
                    </Button>
                  ) : null}
                  <RowActions
                    actions={[
                      {
                        label: "Copy key",
                        icon: IconCopy,
                        onSelect: () => void copyToClipboard(regulation.key),
                      },
                      ...(regulation.detail_url
                        ? [
                            {
                              label: "Open the record at BPK",
                              icon: IconExternalLink,
                              onSelect: () =>
                                window.open(regulation.detail_url, "_blank"),
                            },
                          ]
                        : []),
                    ]}
                  />
                </div>
              }
            />
          </div>
        }
      />

      <div className="grid gap-8 lg:grid-cols-[16rem_minmax(0,1fr)]">
        {/* Metadata on the left: it is what a reader checks while reading an
            article, and the left edge is where the eye starts. */}
        <aside className={`lg:sticky lg:self-start ${BELOW_STICKY_HEADER}`}>
          <h2 className="font-heading text-sm font-semibold tracking-tight">About</h2>
          <dl className="mt-3 space-y-3">
            <Fact label="Instrument" value={regulation.instrument} />
            <Fact
              label="Region"
              value={regulation.region_name}
              hint={regulation.region_type}
            />
            <Fact label="Number" value={regulation.number} />
            <Fact label="Year" value={regulation.year?.toString()} />
            <Fact label="Enacted" value={regulation.enacted_date} />
            <Fact label="Published" value={regulation.published_date} />
            <Fact label="Subject" value={regulation.subject} />
            <Fact label="Status" value={regulation.legal_status ?? regulation.status} />
            <Fact
              label="Structure"
              value={
                readable
                  ? [
                      regulation.bab && `${formatCount(regulation.bab)} bab`,
                      regulation.pasal && `${formatCount(regulation.pasal)} pasal`,
                      regulation.ayat && `${formatCount(regulation.ayat)} ayat`,
                    ]
                      .filter(Boolean)
                      .join(" · ")
                  : undefined
              }
            />
            {/* Coverage below 1 means the conversion dropped pages. A reader
                quoting an article from a partial document should know that
                before they quote it, not after. */}
            {regulation.md_coverage !== undefined && regulation.md_coverage < 0.999 ? (
              <Fact
                label="Text coverage"
                value={`${Math.round(regulation.md_coverage * 100)}% of pages`}
                hint="The conversion did not keep every page"
              />
            ) : null}
            <Fact
              label="Source"
              value={regulation.source_name}
              hint={regulation.license}
            />
          </dl>
        </aside>

        <div className="min-w-0">
          <Tabs
            value={tab}
            onValueChange={(next) =>
              navigate({ search: (prev) => ({ ...prev, tab: next as TabName }) })
            }
          >
            <TabsList>
              <TabsTrigger value="text">
                Text
                {readable && rows.length ? (
                  <span className="ml-1.5 text-xs text-muted-foreground tabular-nums">
                    {formatCount(rows.length)}
                  </span>
                ) : null}
              </TabsTrigger>
              <TabsTrigger value="recitals">Recitals</TabsTrigger>
              <TabsTrigger value="citations">
                Citations
                {cited.length ? (
                  <span className="ml-1.5 text-xs text-muted-foreground tabular-nums">
                    {formatCount(cited.length)}
                  </span>
                ) : null}
              </TabsTrigger>
            </TabsList>

            <TabsContent value="text" className="pt-4">
              {!readable ? (
                <MissingText regulation={regulation} />
              ) : sections.isLoading ? (
                <div className="space-y-3">
                  <Skeleton className="h-4 w-1/3" />
                  <Skeleton className="h-20 w-full" />
                  <Skeleton className="h-20 w-full" />
                </div>
              ) : (
                <SectionList sections={rows} />
              )}
            </TabsContent>

            <TabsContent value="recitals" className="pt-4">
              <div className="space-y-6">
                <Recital label="Pembukaan" text={regulation.preamble} />
                <Recital label="Menimbang" text={regulation.menimbang} />
                <Recital label="Mengingat" text={regulation.mengingat} />
                <Recital label="Penutup" text={regulation.penutup} />
                {!regulation.preamble &&
                !regulation.menimbang &&
                !regulation.mengingat &&
                !regulation.penutup ? (
                  <p className="text-sm text-muted-foreground">
                    No recitals were parsed from this document.
                  </p>
                ) : null}
              </div>
            </TabsContent>

            <TabsContent value="citations" className="pt-4">
              <CitationList citations={cited} isLoading={citations.isLoading} />
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </div>
  );
}

function Fact({
  label,
  value,
  hint,
}: {
  label: string;
  value?: string | null;
  hint?: string | null;
}) {
  if (!value) return null;
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{value}</dd>
      {hint ? <dd className="text-xs text-muted-foreground">{hint}</dd> : null}
    </div>
  );
}

/** Why there is nothing to read, and where to go instead. */
function MissingText({ regulation }: { regulation: RegulationDetail }) {
  const reason = regulation.parse_status
    ? (NO_TEXT[regulation.parse_status] ??
      `The conversion recorded this as ${regulation.parse_status}.`)
    : "BPK catalogued this regulation but published no PDF we could convert.";

  return (
    <div className="space-y-3 rounded-lg border border-dashed p-6">
      <p className="text-sm font-medium">This regulation has no text here.</p>
      <p className="max-w-prose text-sm text-muted-foreground">{reason}</p>
      {regulation.pdf_url || regulation.detail_url ? (
        <p className="text-sm">
          <a
            className="underline underline-offset-4"
            href={regulation.pdf_url ?? regulation.detail_url}
            target="_blank"
            rel="noreferrer"
          >
            Read it at BPK
          </a>
        </p>
      ) : null}
    </div>
  );
}

/**
 * The articles, in document order.
 *
 * Bab headings are rendered from the rows that carry them rather than from a
 * separate outline, so a regulation whose structure the parser repaired reads
 * the way the parser understood it — which is the same thing every query over
 * this corpus sees.
 */
function SectionList({ sections }: { sections: RegulationSection[] }) {
  if (!sections.length) {
    return (
      <p className="text-sm text-muted-foreground">
        The text parsed, but produced no articles.
      </p>
    );
  }

  // The corpus stores the numeral alone — bab "I", pasal "1" — and repeats the
  // pasal on every ayat beneath it. Printed raw that reads as a stray "1"
  // above each paragraph, so the words are supplied here and a label is drawn
  // only where it changes.
  let lastBab: string | undefined;
  let lastPasal: string | undefined;

  return (
    <div className="space-y-4">
      {sections.map((section) => {
        const babChanged = section.bab && section.bab !== lastBab;
        if (section.bab) lastBab = section.bab;

        const pasalChanged = section.pasal && section.pasal !== lastPasal;
        if (section.pasal) lastPasal = section.pasal;

        return (
          <div key={section.seq} className="space-y-1.5">
            {babChanged ? (
              <div className="pt-5">
                <h3 className="font-heading text-sm font-semibold tracking-wide uppercase">
                  BAB {section.bab}
                </h3>
                {section.bab_title ? (
                  <p className="text-sm text-muted-foreground">{section.bab_title}</p>
                ) : null}
              </div>
            ) : null}

            {section.bagian ? (
              <p className="pt-2 text-sm font-medium text-muted-foreground">
                Bagian {section.bagian}
              </p>
            ) : null}

            {pasalChanged ? (
              <p className="pt-2 text-sm font-semibold">Pasal {section.pasal}</p>
            ) : null}

            {/* The ayat number is already inside the text as "(1)", so it is
                not printed again here. */}
            <p className="text-sm leading-relaxed whitespace-pre-wrap">
              {reflow(section.text)}
            </p>
          </div>
        );
      })}
    </div>
  );
}

/** A line that opens a list item: "1.", "a.", "(1)", "b)", "-", "•". */
const LIST_ITEM = /^\s*(\(?\d+[.)]|\(?[a-z][.)]|[-•])\s/;

/**
 * Rejoins the lines a PDF broke at its own column width.
 *
 * The conversion keeps every line break of the page, so printed as-is a pasal
 * wraps at the width of the PDF rather than the width of the screen. A break
 * is kept only where it means something: before a list item, and around a
 * blank line between paragraphs.
 */
function reflow(text: string): string {
  const out: string[] = [];
  for (const line of text.split("\n")) {
    const previous = out.length ? out[out.length - 1] : undefined;
    if (
      previous === undefined ||
      !line.trim() ||
      !previous.trim() ||
      LIST_ITEM.test(line)
    ) {
      out.push(line);
    } else {
      out[out.length - 1] = `${previous.trimEnd()} ${line.trimStart()}`;
    }
  }
  return out.join("\n");
}

/**
 * What this regulation cites.
 *
 * Only a citation that resolved to exactly one document in the corpus becomes
 * a link. Four fifths name no jurisdiction, so "Perda No 1 Tahun 2015" matches
 * every same-numbered regulation in the country — linking one of them would be
 * picking a candidate and presenting it as the answer.
 */
function CitationList({
  citations,
  isLoading,
}: {
  citations: RegulationCitation[];
  isLoading: boolean;
}) {
  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="h-5 w-1/2" />
      </div>
    );
  }

  if (!citations.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No citations were parsed from this document.
      </p>
    );
  }

  return (
    <ol className="space-y-2">
      {citations.map((citation) => {
        const label =
          citation.cited_title ??
          [
            citation.instrument,
            citation.scope,
            citation.number && `No ${citation.number}`,
            citation.year && `Tahun ${citation.year}`,
          ]
            .filter(Boolean)
            .join(" ");

        return (
          <li key={citation.index} className="flex items-baseline gap-2 text-sm">
            <span className="w-6 shrink-0 text-xs text-muted-foreground tabular-nums">
              {citation.index + 1}
            </span>
            {citation.cite_key ? (
              <Link
                to="/regulations/$key"
                params={{ key: citation.cite_key }}
                className="underline underline-offset-4"
              >
                {label}
              </Link>
            ) : (
              <span>
                {label}
                <Badge variant="outline" className="ml-2 text-muted-foreground">
                  not in this corpus
                </Badge>
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function Recital({ label, text }: { label: string; text?: string }) {
  if (!text) return null;
  return (
    <section className="space-y-1.5">
      <h3 className="font-heading text-sm font-semibold tracking-wide uppercase">
        {label}
      </h3>
      <p className="text-sm leading-relaxed whitespace-pre-wrap">{reflow(text)}</p>
    </section>
  );
}
