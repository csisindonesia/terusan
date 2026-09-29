/**
 * One titled part of a tab: a heading, what it is showing, and an action
 * beside the heading where the section has one. The indicator page draws the
 * same shape inline; this is it for pages that are split across files.
 */
export function Section({
  title,
  description,
  action,
  children,
}: {
  title: string;
  /** What the section is showing, and what it is not. */
  description?: React.ReactNode;
  action?: React.ReactNode;
  /** Optional: a section whose description says it all needs nothing below. */
  children?: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="font-heading text-lg font-semibold tracking-tight">{title}</h2>
          {description ? (
            <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
