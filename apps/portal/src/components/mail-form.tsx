import { useState } from "react";
import { IconCheck, IconCopy, IconMail } from "@tabler/icons-react";

import { copyToClipboard } from "~/components/row-actions";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Textarea } from "~/components/ui/textarea";
import { mailtoHref } from "~/lib/mailto";

export type MailField = {
  name: string;
  label: string;
  /** Under the label, for a field whose answer is not obvious. */
  hint?: string;
  kind?: "text" | "email" | "textarea";
  placeholder?: string;
  required?: boolean;
};

/**
 * A form that composes a message and hands it to the reader's mail client.
 *
 * Nothing is posted anywhere. The fields exist to structure the message — a
 * report that names the page and the expected value is answerable, one that
 * says "the numbers look wrong" is not — and the button turns them into a
 * `mailto:` with the subject and body already written.
 *
 * The address is printed beside the button and can be copied, because a
 * browser with no mail client configured does nothing at all when a `mailto:`
 * is clicked, and a reader left staring at an unchanged page has no idea where
 * to write instead.
 */
export function MailForm({
  to,
  fields,
  subject,
  body,
  submitLabel = "Open in mail",
}: {
  to: string;
  fields: MailField[];
  subject: (values: Record<string, string>) => string;
  body: (values: Record<string, string>) => string;
  submitLabel?: string;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [copied, setCopied] = useState(false);

  const missing = fields.some((field) => field.required && !values[field.name]?.trim());

  function set(name: string, value: string) {
    setValues((previous) => ({ ...previous, [name]: value }));
  }

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        window.location.href = mailtoHref({
          to,
          subject: subject(values),
          body: body(values),
        });
      }}
    >
      {fields.map((field) => (
        <div key={field.name} className="space-y-1.5">
          <label htmlFor={field.name} className="text-sm font-medium">
            {field.label}
            {field.required ? null : (
              <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                optional
              </span>
            )}
          </label>
          {field.hint ? (
            <p className="text-xs text-muted-foreground">{field.hint}</p>
          ) : null}
          {field.kind === "textarea" ? (
            <Textarea
              id={field.name}
              name={field.name}
              rows={5}
              placeholder={field.placeholder}
              value={values[field.name] ?? ""}
              onChange={(event) => set(field.name, event.target.value)}
            />
          ) : (
            <Input
              id={field.name}
              name={field.name}
              type={field.kind === "email" ? "email" : "text"}
              placeholder={field.placeholder}
              value={values[field.name] ?? ""}
              onChange={(event) => set(field.name, event.target.value)}
            />
          )}
        </div>
      ))}

      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={missing}>
          <IconMail />
          {submitLabel}
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={async () => {
            setCopied(await copyToClipboard(to));
            window.setTimeout(() => setCopied(false), 2000);
          }}
        >
          {copied ? <IconCheck /> : <IconCopy />}
          <span className="font-mono text-xs">{to}</span>
        </Button>
      </div>
    </form>
  );
}
