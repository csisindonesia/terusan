import { useEffect, useState } from "react";
import { Link, createFileRoute } from "@tanstack/react-router";

import { Separator } from "~/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "~/components/ui/table";
import { apiBaseUrl } from "~/lib/api";
import { cn } from "~/lib/utils";

export const Route = createFileRoute("/help")({ component: Help });

/**
 * The page's own contents, in reading order.
 *
 * A menu rather than tabs: this is one document, not four views. Everything is
 * on the page at once, so the browser's find works across all of it and a
 * section is an address — `/help#vocabulary` — that can be pasted into an
 * answer.
 */
const SECTIONS = [
  { id: "getting-around", label: "Getting around" },
  { id: "habits", label: "Two habits" },
  { id: "vocabulary", label: "Vocabulary" },
  { id: "downloads", label: "Downloads" },
  { id: "api", label: "API" },
  { id: "questions", label: "Questions" },
];

/** The endpoints a reader is most likely to want, not the whole surface. */
const ENDPOINTS = [
  { path: "/v1/datasets", what: "Collections, one per source table" },
  { path: "/v1/indicators", what: "Series, with their unit and period range" },
  {
    path: "/v1/observations",
    what: "Figures, filterable by indicator, period, geography",
  },
  { path: "/v1/documents", what: "Source documents a figure was parsed out of" },
  { path: "/v1/regulations", what: "Regulations, with their articles and citations" },
  { path: "/v1/sources", what: "Where the data is collected from, and how" },
  { path: "/v1/runs", what: "Pipeline runs — what was ingested, and when" },
];

/** What the platform's nouns mean, in the order a reader meets them. */
const WORDS = [
  {
    term: "Source",
    meaning:
      "An agency, portal or API the data is collected from — BPS, Bank Indonesia, a ministry. A source says where data comes from and how it is fetched, not what is in it.",
  },
  {
    term: "Dataset",
    meaning:
      "One collection as the source publishes it: a table, a release, a recurring file. It is the unit that gets ingested and refreshed.",
  },
  {
    term: "Indicator",
    meaning:
      "One measured thing over time — a retail sales index, a policy rate, nickel export volume. An indicator carries a unit and a frequency; it does not carry values.",
  },
  {
    term: "Observation",
    meaning:
      "One figure: an indicator, a period, a value. Optionally a geography and a commodity. This is what the data explorer lists and what a chart is drawn from.",
  },
  {
    term: "Period",
    meaning:
      "When a figure is about, as the source labels it (2024, 2024-Q1, 2024-03) plus the exact dates it covers. The label is for reading; the bounds are what makes periods comparable across sources that label them differently.",
  },
  {
    term: "Document",
    meaning:
      "The file a figure was parsed out of — a PDF release, a spreadsheet, a scraped page — kept exactly as it was fetched. Every observation points back to one.",
  },
  {
    term: "Run",
    meaning:
      "One execution of a pipeline. It records what was fetched, what was written and whether it succeeded, so a figure can be traced to the moment it entered the warehouse.",
  },
  {
    term: "Topic and facet",
    meaning:
      "Tags. A topic is what a record is about; a facet is who published it, how often, in what unit. Same vocabulary, different question.",
  },
  {
    term: "Layers — raw, bronze, silver, gold",
    meaning:
      "How far a file has travelled. Raw is the untouched original, bronze is parsed, silver is normalized and typed, gold is what this portal and the API serve. Nothing is ever edited in place; a correction is a re-run.",
  },
];

/**
 * How to use the portal, what its words mean, and how to get the data out.
 *
 * Laid out as documentation: the contents down the side, the whole text beside
 * it. A reader arrives with one of four questions — how do I find things, what
 * does this word mean, how do I script it, why does this number look odd — and
 * the menu answers "is my question in here" before any of the prose does.
 */
function Help() {
  const active = useActiveSection(SECTIONS.map((section) => section.id));

  return (
    <div className="grid gap-8 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-12">
      {/* Below `lg` the menu sits above the text as a plain list of links;
          there is no room for a column beside a column of prose on a phone. */}
      <aside className="lg:sticky lg:top-[calc(var(--app-header)+1.5rem)] lg:self-start">
        <nav aria-label="On this page" className="space-y-1">
          {/* Bold and in the text colour: muted grey at the links' own weight
              reads as the first item of the list rather than its heading. */}
          <div className="px-2 pt-1 pb-3 text-xs font-bold tracking-widest text-foreground uppercase">
            On this page
          </div>
          {SECTIONS.map((section) => (
            <a
              key={section.id}
              href={`#${section.id}`}
              aria-current={active === section.id ? "true" : undefined}
              className={cn(
                "block rounded-md px-2 py-1 text-sm transition-colors hover:bg-accent hover:text-foreground",
                active === section.id
                  ? "bg-accent font-medium text-foreground"
                  : "text-muted-foreground",
              )}
            >
              {section.label}
            </a>
          ))}
        </nav>
      </aside>

      <div className="max-w-3xl space-y-10">
        <div className="space-y-3">
          <h1 className="font-heading text-3xl font-semibold tracking-tight">Help</h1>
          <p className="text-lg text-muted-foreground">
            Finding data, reading it correctly, and getting it out of here.
          </p>
        </div>

        <Section id="getting-around" title="Getting around">
          <p>
            There are two ways in. If you know roughly what you want, press <Key>⌘</Key>{" "}
            <Key>K</Key> — <Key>Ctrl</Key> <Key>K</Key> on Windows and Linux — and
            search indicators, datasets, documents and regulations from anywhere in the
            portal. If you are browsing, the sidebar is the map.
          </p>
          <dl className="space-y-3">
            <Entry
              term={
                <Link to="/datasets" className="underline underline-offset-4">
                  Datasets
                </Link>
              }
            >
              What has been collected, by source. Start here to see whether a subject is
              covered at all.
            </Entry>
            <Entry
              term={
                <Link to="/indicators" className="underline underline-offset-4">
                  Indicators
                </Link>
              }
            >
              One series at a time, with a chart, its summary statistics, the documents
              behind it and the runs that produced it.
            </Entry>
            <Entry
              term={
                <Link to="/observations" className="underline underline-offset-4">
                  Data Explorer
                </Link>
              }
            >
              Every figure in the warehouse, filtered across indicators, periods and
              geography at once. This is the page to use when the question spans more
              than one series.
            </Entry>
            <Entry
              term={
                <Link to="/documents" className="underline underline-offset-4">
                  Documents
                </Link>
              }
            >
              The originals. Every figure names the document it came from, and the
              document can be previewed and downloaded as it was fetched.
            </Entry>
          </dl>
        </Section>

        <Separator />

        <Section id="habits" title="Two habits worth knowing">
          <p>
            <strong className="text-foreground">Every filter is in the URL.</strong>{" "}
            Sort a table, narrow a period, pick three indicators — the address bar keeps
            up. Copy it and the person you send it to sees exactly your view, not the
            default one. It also means a citation can point at a filtered table rather
            than at a page plus instructions.
          </p>
          <p>
            <strong className="text-foreground">Grey menu items are not broken.</strong>{" "}
            The sidebar lists the whole shape of the platform, including the parts that
            are not built yet; those are shown disabled rather than hidden, so the
            navigation tells you what exists and what is coming instead of quietly
            growing a row each time something lands.
          </p>
        </Section>

        <Separator />

        <Section id="vocabulary" title="Vocabulary">
          <p>
            The nouns used across the portal, the API and the pipelines mean the same
            thing in all three. Most confusion here is a dataset–indicator–observation
            confusion.
          </p>
          <dl className="space-y-4">
            {WORDS.map((word) => (
              <Entry key={word.term} term={word.term}>
                {word.meaning}
              </Entry>
            ))}
          </dl>
        </Section>

        <Separator />

        <Section id="downloads" title="Downloads">
          <p>
            Every table has a download that writes a CSV of what is on screen — filters
            and sort included, not the unfiltered table. For anything larger than a page
            of results, or anything you want to re-run next month, use the API instead.
          </p>
        </Section>

        <Separator />

        <Section id="api" title="API">
          <p>
            Read-only HTTP, JSON, no key required. The base URL is{" "}
            <Code>{apiBaseUrl}</Code>.
          </p>
          <pre className="overflow-x-auto rounded-lg border bg-muted/50 p-3 font-mono text-xs">
            {`curl "${apiBaseUrl}/v1/observations?indicator=<id>&limit=100"`}
          </pre>
          <p>
            Every response uses one envelope: the payload under <Code>data</Code>, the
            paging and counts under <Code>meta</Code>, and a failure under{" "}
            <Code>error</Code>. Lists page with <Code>limit</Code> and{" "}
            <Code>offset</Code>; <Code>meta.has_more</Code> tells you when to stop.
          </p>

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[16rem]">Endpoint</TableHead>
                <TableHead>What it returns</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ENDPOINTS.map((endpoint) => (
                <TableRow key={endpoint.path}>
                  <TableCell className="font-mono text-xs">{endpoint.path}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {endpoint.what}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          <p>
            Each indicator page carries an API tab with the exact request for that
            series, parameters already filled in.
          </p>
        </Section>

        <Separator />

        <Section id="questions" title="Questions">
          <dl className="space-y-5">
            <Entry term="A figure here differs from the agency's website. Who is wrong?">
              Often neither. Agencies revise, and this warehouse keeps both the original
              and the revision rather than overwriting. Open the indicator, check the
              document behind the figure and its release date. If the document genuinely
              says something else than the table does, that is a parsing bug —{" "}
              <Link to="/report" className="underline underline-offset-4">
                report it
              </Link>
              .
            </Entry>
            <Entry term="How current is this?">
              Per source, not globally. Each dataset shows when it was last refreshed,
              and each indicator lists the runs that wrote it. A source that has gone
              quiet looks exactly like one that has not published yet, which is why the
              refresh date is on the page rather than a site-wide "updated today".
            </Entry>
            <Entry term="Can I cite a figure from here?">
              Cite the source document, which every figure names, and link the portal
              view alongside it if it helps a reader find it. The document is the
              authority; this platform is the canal, not the spring.
            </Entry>
            <Entry term="Why does a series stop partway?">
              Because the source stopped, changed its definition, or changed its unit.
              Where a break is known it is recorded rather than smoothed over — a series
              stitched across a definition change is a nicer chart and a worse fact.
            </Entry>
            <Entry term="Something is missing, or a page is empty.">
              If the serving layer is down, pages say so outright. If a source you need
              is not here at all,{" "}
              <Link to="/contact" className="underline underline-offset-4">
                tell us
              </Link>
              .
            </Entry>
            <Entry term="How is any of this actually built?">
              This page is about using the portal. How the warehouse is run, how a
              source is added, and which Indonesian portals are collected — and which
              cannot be, and what it would take — are in the{" "}
              <Link to="/docs" className="underline underline-offset-4">
                documentation
              </Link>
              , which is the repository's own, published with the version you are
              looking at.
            </Entry>
          </dl>
        </Section>
      </div>
    </div>
  );
}

/**
 * Which section the reader is in, for the menu.
 *
 * The observation band is the top of the viewport rather than its middle: a
 * heading scrolled just under the navbar is the one being read, and a band
 * that waits for the middle of the screen leaves the menu a section behind.
 */
function useActiveSection(ids: string[]): string | undefined {
  const [active, setActive] = useState<string>();

  useEffect(() => {
    const seen = new Map<string, boolean>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) seen.set(entry.target.id, entry.isIntersecting);
        // In document order, so a scroll that reveals two headings at once
        // names the upper one rather than whichever fired last.
        const first = ids.find((id) => seen.get(id));
        if (first) setActive(first);
      },
      { rootMargin: "-25% 0px -70% 0px" },
    );

    for (const id of ids) {
      const element = document.getElementById(id);
      if (element) observer.observe(element);
    }
    return () => observer.disconnect();
  }, [ids.join(",")]);

  return active;
}

/**
 * One section of the document.
 *
 * The heading carries the anchor and a scroll margin, because a link to
 * `#api` otherwise parks the heading underneath the sticky navbar.
 */
function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section
      id={id}
      className="scroll-mt-[calc(var(--app-header)+1.5rem)] space-y-4 text-sm leading-relaxed text-muted-foreground"
    >
      <h2 className="font-heading text-xl font-semibold tracking-tight text-foreground">
        {title}
      </h2>
      {children}
    </section>
  );
}

/** A term and what it means, as a definition list rather than a card grid. */
function Entry({
  term,
  children,
}: {
  term: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <dt className="text-sm font-medium text-foreground">{term}</dt>
      <dd className="text-sm leading-relaxed text-muted-foreground">{children}</dd>
    </div>
  );
}

/** An identifier as it is typed, not as prose. */
function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">{children}</code>
  );
}

/** A keycap, for a shortcut that is worth pressing rather than reading. */
function Key({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground">
      {children}
    </kbd>
  );
}
