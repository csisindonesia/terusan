/**
 * Mail addresses the portal hands out, and the link that opens one.
 *
 * There is no form endpoint behind these pages and deliberately so: a form
 * that posts to a server nobody monitors swallows the message and tells the
 * reader it was sent. A `mailto:` lands in a real inbox, and the sender keeps
 * a copy in their own Sent folder — which is what someone reporting a wrong
 * figure actually needs.
 */
export const MAIL = {
  /** Anything about the platform itself: access, sources, collaboration. */
  contact: "infrastructure@csis.or.id",
  /** A figure that looks wrong, a broken page, a source that stopped. */
  report: "dev@csis.or.id",
} as const;

/**
 * A `mailto:` URL with the subject and body already filled in.
 *
 * Every part is percent-encoded rather than dropped into the string, because a
 * newline or an `&` in a message body would otherwise end the body and start
 * inventing headers.
 */
export function mailtoHref({
  to,
  subject,
  body,
}: {
  to: string;
  subject: string;
  body: string;
}): string {
  const query = new URLSearchParams({ subject, body });
  // URLSearchParams encodes a space as "+", which a mail client shows
  // literally in the subject line rather than as a space.
  return `mailto:${to}?${query.toString().replace(/\+/g, "%20")}`;
}
