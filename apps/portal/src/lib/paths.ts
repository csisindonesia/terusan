/**
 * Telling a path on this site from an address that only looks like one.
 *
 * Several places take a target from somewhere the portal does not control — a
 * login `redirect`, a link the assistant wrote, an article's markdown, a
 * showcase entry — and must send the reader only to the portal's own pages.
 * "Starts with `/` but not `//`" is not enough: browsers read a backslash as a
 * slash, so `/\evil.example` opens another site, and they drop tabs and
 * newlines from a URL before reading it, so `/<tab>/evil.example` does too.
 * This is the one check all of them share, and it mirrors `portalPath` in
 * services/api/internal/httpapi/sso.go, which guards the same redirect on the
 * server.
 */

/**
 * ASCII control characters, anywhere in the value. A URL parser strips some of
 * them and silently removes others, so what the browser opens is no longer the
 * string that was checked.
 */
const CONTROL = /[\u0000-\u001f\u007f]/;

/**
 * A base the check resolves against. Any origin would do — only whether the
 * result stays on it matters — and a fixed one gives the same answer during
 * server rendering, where there is no `window.location`, as in the browser.
 */
const BASE = "https://portal.invalid";

/** Whether a value is a path on this site and cannot leave it. */
export function isPortalPath(value: string | null | undefined): boolean {
  if (!value || value[0] !== "/" || value[1] === "/") return false;
  if (value.includes("\\") || CONTROL.test(value)) return false;
  try {
    return new URL(value, BASE).origin === BASE;
  } catch {
    return false;
  }
}

/** The value if it is a path on this site, and the fallback otherwise. */
export function portalPath(value: string | null | undefined, fallback = "/"): string {
  return value && isPortalPath(value) ? value : fallback;
}
