/**
 * Serve the production build under Node.
 *
 * `vite build` emits a platform-agnostic fetch handler — `{ fetch(Request) }` —
 * rather than a listening server, so it can be deployed to Node, a worker
 * runtime or a serverless function without change. Running it under Node needs
 * this bridge: an http server that turns each request into a `Request`, and the
 * returned `Response` back into a Node response.
 *
 * Static assets are served from disk first. The handler renders routes; it does
 * not know about the hashed files in dist/client.
 */

import { createServer } from "node:http";
import { createReadStream, readFileSync } from "node:fs";
import { stat } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";
import { Readable } from "node:stream";

import handler from "./dist/server/server.js";

const PORT = Number(process.env.PORT ?? 3000);
const HOST = process.env.HOST ?? "127.0.0.1";
const CLIENT_DIR = resolve(import.meta.dirname, "dist/client");

/**
 * The API's origin, as the build recorded it (see `apiOriginFile` in
 * vite.config.ts). Empty when the API is served from the portal's own origin
 * or the record is missing, in which case `'self'` is all the policy names.
 */
function apiOrigin() {
  try {
    const { origin } = JSON.parse(
      readFileSync(resolve(import.meta.dirname, "dist/api-origin.json"), "utf8"),
    );
    return typeof origin === "string" ? origin : "";
  } catch {
    return "";
  }
}

const API_ORIGIN = apiOrigin();

/** `'self'`, and the API's origin when it is another one. */
function selfAnd(...sources) {
  return ["'self'", API_ORIGIN, ...sources].filter(Boolean).join(" ");
}

// The basemap behind the choropleth (components/choropleth-map.tsx). Its tiles
// are images, and a chart saved as a picture fetches them again to embed them.
const TILES = "https://server.arcgisonline.com";

/**
 * What the browser may load into a portal page, and who may frame one.
 *
 * Scripts are allowed inline, which is the weakest part of this and a choice:
 * every page carries inline scripts — the theme set before first paint
 * (THEME_INIT_SCRIPT in routes/__root.tsx) and TanStack Start's hydration
 * data, which differs on every response — so a strict policy needs a fresh
 * nonce threaded through the router into each of them. Until that is done the
 * policy still refuses scripts from any other host, plugins, a `<base>` that
 * re-points relative URLs, forms that post elsewhere and framing by another
 * site, and it limits where a page can send data to this site and the API.
 * Styles are inline for the same kind of reason: the server-rendered markup
 * carries `style` attributes, and Leaflet and the UI kit add their own.
 */
const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  // data: for the icons inlined into the CSS and for a chart rendered to a
  // picture; blob: for files built in the browser.
  `img-src ${selfAnd(TILES, "data:", "blob:")}`,
  "font-src 'self' data:",
  `connect-src ${selfAnd(TILES)}`,
  // The document preview is an iframe of the API's inline file route.
  `frame-src ${selfAnd()}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

/**
 * Sent with every response. A header the app already set is left alone, so a
 * route that needs something different can say so itself.
 */
const SECURITY_HEADERS = {
  "Content-Security-Policy": CONTENT_SECURITY_POLICY,
  // frame-ancestors does the same for browsers that read CSP; this is for the
  // ones that do not.
  "X-Frame-Options": "DENY",
  "X-Content-Type-Options": "nosniff",
  // A link out to a publisher tells it the reader came from this site, not
  // which page — the path can carry a search or a record a reader is looking
  // at.
  "Referrer-Policy": "strict-origin-when-cross-origin",
};

/** The security headers, under whatever the response already carries. */
function withSecurityHeaders(headers) {
  const merged = {};
  for (const [name, value] of [
    ...Object.entries(SECURITY_HEADERS),
    ...Object.entries(headers),
  ]) {
    merged[name.toLowerCase()] = value;
  }
  return merged;
}

const MEDIA_TYPES = {
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
};

/** Resolve a URL path to a file under dist/client, or null. */
async function staticFile(pathname) {
  // normalize collapses `..`; the prefix check then refuses anything that
  // climbed out of the client directory.
  const candidate = normalize(join(CLIENT_DIR, decodeURIComponent(pathname)));
  if (!candidate.startsWith(CLIENT_DIR)) return null;

  try {
    const info = await stat(candidate);
    return info.isFile() ? candidate : null;
  } catch {
    return null;
  }
}

function toRequest(req) {
  const url = new URL(req.url, `http://${req.headers.host ?? HOST}`);
  const headers = new Headers();
  for (const [name, value] of Object.entries(req.headers)) {
    if (value === undefined) continue;
    for (const item of Array.isArray(value) ? value : [value])
      headers.append(name, item);
  }

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  return new Request(url, {
    method: req.method,
    headers,
    body: hasBody ? Readable.toWeb(req) : undefined,
    // Required by undici when streaming a request body.
    duplex: hasBody ? "half" : undefined,
  });
}

async function send(res, response) {
  const headers = {};
  for (const [name, value] of response.headers) headers[name] = value;
  res.writeHead(response.status, withSecurityHeaders(headers));

  if (!response.body) {
    res.end();
    return;
  }
  Readable.fromWeb(response.body).pipe(res);
}

const server = createServer(async (req, res) => {
  try {
    const { pathname } = new URL(req.url, `http://${req.headers.host ?? HOST}`);

    const file = await staticFile(pathname);
    if (file) {
      const type = MEDIA_TYPES[extname(file)] ?? "application/octet-stream";
      res.writeHead(
        200,
        withSecurityHeaders({
          "Content-Type": type,
          // Vite fingerprints filenames under /assets, so those are immutable.
          "Cache-Control": pathname.startsWith("/assets/")
            ? "public, max-age=31536000, immutable"
            : "public, max-age=3600",
        }),
      );
      createReadStream(file).pipe(res);
      return;
    }

    await send(res, await handler.fetch(toRequest(req)));
  } catch (error) {
    console.error("request failed", error);
    if (!res.headersSent) {
      res.writeHead(500, withSecurityHeaders({ "Content-Type": "text/plain" }));
    }
    res.end("internal error");
  }
});

server.listen(PORT, HOST, () => {
  console.log(`portal http://${HOST}:${PORT}`);
});
