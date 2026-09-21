import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { z } from "zod";

import { MailForm } from "~/components/mail-form";
import { Badge } from "~/components/ui/badge";
import { Separator } from "~/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "~/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import { formatDate } from "~/lib/format";
import { fixedIssues, openIssues, type DataIssue } from "~/lib/issues";
import { MAIL } from "~/lib/mailto";

const TABS = ["report", "open", "fixed"] as const;
type TabName = (typeof TABS)[number];

const searchSchema = z.object({
  // In the URL like every other view here, so "here is what is still broken"
  // is a link somebody can send rather than a click they have to describe.
  tab: z.enum(TABS).optional(),
});

export const Route = createFileRoute("/report")({
  validateSearch: searchSchema,
  component: Report,
});

/**
 * Reporting a figure that looks wrong, and the register of the ones that were.
 *
 * The form and the log sit on the same page on purpose: someone about to
 * report a problem should see first whether it is already known, and someone
 * who read a figure last month should be able to find out that it moved.
 */
function Report() {
  const { tab = "report" } = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });

  const open = openIssues();
  const fixed = fixedIssues();

  return (
    <div className="max-w-3xl space-y-8">
      <div className="space-y-3">
        <h1 className="font-heading text-3xl font-semibold tracking-tight">
          Report a problem
        </h1>
        <p className="text-lg text-muted-foreground">
          Wrong figures, broken pages and stalled sources go to{" "}
          <span className="font-mono text-base">{MAIL.report}</span>.
        </p>
      </div>

      <Tabs
        value={tab}
        onValueChange={(next) =>
          navigate({ search: (prev) => ({ ...prev, tab: next as TabName }) })
        }
      >
        <TabsList>
          <TabsTrigger value="report">Report</TabsTrigger>
          {/* The counts are on the tabs rather than inside them, because
              whether anything is currently wrong is the question a reader
              arrives with. */}
          <TabsTrigger value="open" className="gap-1.5">
            Known problems
            <Badge variant="secondary" className="tabular-nums">
              {open.length}
            </Badge>
          </TabsTrigger>
          <TabsTrigger value="fixed" className="gap-1.5">
            Fixed
            <Badge variant="secondary" className="tabular-nums">
              {fixed.length}
            </Badge>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="report" className="space-y-6 pt-4">
          <section className="space-y-4 text-sm leading-relaxed text-muted-foreground">
            <p>
              Every figure here was parsed out of a document by code, and code misreads
              documents: a merged header cell, a footnote marker glued to a number, a
              unit that changed in 2019 and was never announced. When a figure looks
              wrong, it usually is — either ours or the source's, and both are worth
              knowing about.
            </p>
            <p>
              If you can, paste the page address and the figure as shown. We check it
              against the original document, and if the document says something else,
              the fix lands in the pipeline rather than in the table — so it stays fixed
              on the next run, and the correction is logged under{" "}
              <button
                type="button"
                className="underline underline-offset-4"
                onClick={() =>
                  navigate({ search: (prev) => ({ ...prev, tab: "fixed" }) })
                }
              >
                fixed
              </button>
              .
            </p>
            <p>
              Anything that is not a data problem — access, sources, collaboration —
              belongs with{" "}
              <Link to="/contact" className="underline underline-offset-4">
                contact us
              </Link>
              .
            </p>
          </section>

          <Separator />

          <section className="space-y-4">
            <h2 className="font-heading text-xl font-semibold tracking-tight">
              What went wrong
            </h2>

            <MailForm
              to={MAIL.report}
              submitLabel="Open in mail"
              fields={[
                {
                  name: "where",
                  label: "Page or dataset",
                  hint: "The address of the page, or the dataset and indicator identifier.",
                  required: true,
                  placeholder: "/indicators/bi-retail-sales-index",
                },
                {
                  name: "problem",
                  label: "What you saw",
                  kind: "textarea",
                  required: true,
                  placeholder:
                    "The figure for March 2024 reads 1,234 — the BPS release has 12,340.",
                },
                {
                  name: "source",
                  label: "Source document",
                  hint: "A link to the agency page or file the figure should match.",
                },
                {
                  name: "reporter",
                  label: "Your email",
                  kind: "email",
                  hint: "Only so we can tell you what we found.",
                },
              ]}
              subject={(values) =>
                `[Terusan] Data problem: ${values.where ?? ""}`.trim()
              }
              body={(values) =>
                [
                  `Page or dataset: ${values.where ?? ""}`,
                  "",
                  "What I saw:",
                  values.problem ?? "",
                  "",
                  `Source document: ${values.source || "—"}`,
                  `Reporter: ${values.reporter || "—"}`,
                ].join("\n")
              }
            />
          </section>
        </TabsContent>

        <TabsContent value="open" className="space-y-4 pt-4">
          <p className="text-sm leading-relaxed text-muted-foreground">
            Problems we know about and have not fixed yet. Figures in the affected
            series may be wrong until they move to <em>fixed</em>.
          </p>
          <IssueTable
            issues={open}
            dateColumn="Reported"
            empty="Nothing known to be broken right now. If you have found something, the Report tab is where it goes."
          />
        </TabsContent>

        <TabsContent value="fixed" className="space-y-4 pt-4">
          <p className="text-sm leading-relaxed text-muted-foreground">
            Corrections already published. If you charted one of these series before the
            fix date, pull it again — the figures changed.
          </p>
          <IssueTable
            issues={fixed}
            dateColumn="Fixed"
            empty="No corrections published yet. Every one we make will be listed here rather than applied quietly."
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

/**
 * The register, as a table.
 *
 * The cause and the fix are printed under the summary rather than hidden
 * behind a row that expands: there are few enough entries to read them all,
 * and "what did you actually change" is the whole value of the log.
 */
function IssueTable({
  issues,
  dateColumn,
  empty,
}: {
  issues: DataIssue[];
  dateColumn: string;
  empty: string;
}) {
  if (!issues.length) {
    return (
      <div className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
        {empty}
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>What was wrong</TableHead>
          <TableHead className="w-[12rem]">Where</TableHead>
          <TableHead className="w-[8rem]">{dateColumn}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {issues.map((issue) => (
          <TableRow key={issue.id}>
            <TableCell className="align-top">
              <div className="space-y-1">
                <div className="font-medium">{issue.summary}</div>
                {issue.cause ? (
                  <div className="text-xs text-muted-foreground">
                    Cause: {issue.cause}
                  </div>
                ) : null}
                {issue.fix ? (
                  <div className="text-xs text-muted-foreground">Fix: {issue.fix}</div>
                ) : null}
                <div className="font-mono text-xs text-muted-foreground">
                  {issue.id}
                </div>
              </div>
            </TableCell>
            <TableCell className="align-top font-mono text-xs">{issue.scope}</TableCell>
            <TableCell className="align-top tabular-nums">
              {formatDate(issue.fixed ?? issue.reported)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
