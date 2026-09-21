import { Link, createFileRoute } from "@tanstack/react-router";

import { MailForm } from "~/components/mail-form";
import { Separator } from "~/components/ui/separator";
import { MAIL } from "~/lib/mailto";

export const Route = createFileRoute("/contact")({ component: Contact });

/**
 * Who to write to, and about what.
 *
 * Two addresses rather than one: the platform's own mail and the one that
 * fixes data. Splitting them is not bureaucracy — a wrong figure needs the
 * person who owns the pipeline, and a request to collaborate does not.
 */
function Contact() {
  return (
    <div className="max-w-3xl space-y-10">
      <div className="space-y-3">
        <h1 className="font-heading text-3xl font-semibold tracking-tight">
          Contact us
        </h1>
        <p className="text-lg text-muted-foreground">
          Questions about the platform, its sources, access or collaboration reach{" "}
          <span className="font-mono text-base">{MAIL.contact}</span>.
        </p>
      </div>

      <section className="space-y-4">
        <div className="space-y-4 text-sm leading-relaxed text-muted-foreground">
          <p>
            Write here about anything to do with the platform itself: a source you think
            belongs in it, access for your team, an API question, a research
            collaboration, or how a figure was derived.
          </p>
          <p>
            Found a number that looks wrong, a page that fails to load, or a source that
            has quietly stopped updating? That goes to the people who run the pipelines
            instead —{" "}
            <Link to="/report" className="underline underline-offset-4">
              report a problem
            </Link>
            .
          </p>
        </div>
      </section>

      <Separator />

      <section className="space-y-4">
        <h2 className="font-heading text-xl font-semibold tracking-tight">
          Send a message
        </h2>
        <p className="text-sm leading-relaxed text-muted-foreground">
          This composes the mail in your own client and sends nothing by itself, so you
          keep a copy of what you asked and we cannot lose it in a form queue.
        </p>

        <MailForm
          to={MAIL.contact}
          submitLabel="Open in mail"
          fields={[
            { name: "name", label: "Your name", required: true },
            {
              name: "organization",
              label: "Organization",
              placeholder: "University, ministry, newsroom, company…",
            },
            {
              name: "subject",
              label: "Subject",
              required: true,
              placeholder: "Access for a research team",
            },
            {
              name: "message",
              label: "Message",
              kind: "textarea",
              required: true,
            },
          ]}
          subject={(values) => `[Terusan] ${values.subject ?? ""}`.trim()}
          body={(values) =>
            [
              values.message ?? "",
              "",
              "—",
              values.name ?? "",
              values.organization ?? "",
            ]
              .filter((line, index) => index < 3 || line)
              .join("\n")
          }
        />
      </section>
    </div>
  );
}
