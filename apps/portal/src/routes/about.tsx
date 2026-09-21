import { Link, createFileRoute } from "@tanstack/react-router";

import { Separator } from "~/components/ui/separator";

export const Route = createFileRoute("/about")({ component: About });

/**
 * What this platform is and why it was built.
 *
 * A research organization's data portal has to answer a question no table can:
 * why trust this. The argument is in prose because it is an argument — the
 * pipeline's guarantees are only worth something if a reader knows what they
 * are being promised.
 */
function About() {
  return (
    <div className="max-w-3xl space-y-10">
      <div className="space-y-3">
        <h1 className="font-heading text-3xl font-semibold tracking-tight">
          About Terusan
        </h1>
        <p className="text-lg text-muted-foreground">
          A research data warehouse: heterogeneous Indonesian public data collected,
          preserved, normalized and served with its provenance intact.
        </p>
      </div>

      <section className="space-y-4">
        <h2 className="font-heading text-xl font-semibold tracking-tight">
          What this is
        </h2>
        <div className="space-y-4 text-sm leading-relaxed text-muted-foreground">
          <p>
            Terusan collects data from government portals, statistical agencies, APIs,
            scraped pages, regulations, news, PDFs and spreadsheets. It keeps the
            original file exactly as it was fetched, then parses, normalizes and
            catalogs it into figures you can filter, chart and download.
          </p>
          <p>
            The name comes from our tagline — <em>Nalar, Ajar, Terusan, Budi</em>. A
            terusan is a canal: it produces no water of its own, it carries water to
            where it is useful. That is the entire job here. The agencies make the data;
            this platform moves it, intact, to the people who need it.
          </p>
          <p>
            Every figure carries the document it came from and the pipeline run that
            produced it. Nothing is retyped by hand, and nothing is corrected silently —
            when an agency revises a number, the old value stays in the record next to
            the new one.
          </p>
          <p>
            Visualization lives in whatever tool you already use — Power BI, Superset,
            Metabase, Jupyter, R. This platform serves the data those tools read. The{" "}
            <Link to="/datasets" className="underline underline-offset-4">
              datasets
            </Link>
            ,{" "}
            <Link to="/indicators" className="underline underline-offset-4">
              indicators
            </Link>{" "}
            and{" "}
            <Link to="/observations" className="underline underline-offset-4">
              data explorer
            </Link>{" "}
            pages are the front door.
          </p>
        </div>
      </section>

      <Separator />

      <section className="space-y-4">
        <h2 className="font-heading text-xl font-semibold tracking-tight">
          Why good data
        </h2>
        <div className="space-y-4 text-sm leading-relaxed text-muted-foreground">
          <p>
            We are a research organization, and research is only as good as the data
            under it. An argument built on a number nobody can trace is not an argument
            — it is a claim with a chart attached. The difference matters most exactly
            when the stakes are highest: policy work, public debate, anything somebody
            has a reason to dispute.
          </p>
          <p>
            Public data also disappears. Portals get redesigned, files get replaced in
            place, an agency reorganizes and a decade of releases stops resolving.
            Keeping the original bytes is not hoarding — it is the only way a citation
            stays true a year later.
          </p>
          <p>
            So the rules here are deliberate. Raw files are immutable. Every
            transformation is code in a repository, not a click in a spreadsheet. Every
            figure keeps a line back to its document. The goal is that anyone — a
            colleague, a reviewer, a critic — can follow any number on this site back to
            the agency page it was fetched from, and check us.
          </p>
        </div>
      </section>

      <Separator />

      {/* Short and light on purpose. A wall of gratitude reads like a grant
          report; one honest paragraph reads like a thank you. */}
      <section className="space-y-3">
        <h2 className="font-heading text-xl font-semibold tracking-tight">Thanks</h2>
        <div className="space-y-4 text-sm leading-relaxed text-muted-foreground">
          <p>
            None of this runs on principle. It runs on a NAS, and the NAS was nobody's
            budget line until Dandy Rafitrandy told me to buy one and then paid for it —
            my sugar dandy, a title he never asked for and is not getting out of.
          </p>
          <p>
            The money came through Decarbonization for Development (DfD), the program he
            runs, which set out to decarbonize development and has ended up
            decarbonizing my storage bill as well. Thank you both. The disks are
            spinning.
          </p>
        </div>
      </section>
    </div>
  );
}
