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
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";
import { Readable } from "node:stream";

import handler from "./dist/server/server.js";

const PORT = Number(process.env.PORT ?? 3000);
const HOST = process.env.HOST ?? "127.0.0.1";
const CLIENT_DIR = resolve(import.meta.dirname, "dist/client");

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
    for (const item of Array.isArray(value) ? value : [value]) headers.append(name, item);
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
  res.writeHead(response.status, headers);

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
      res.writeHead(200, {
        "Content-Type": type,
        // Vite fingerprints filenames under /assets, so those are immutable.
        "Cache-Control": pathname.startsWith("/assets/")
          ? "public, max-age=31536000, immutable"
          : "public, max-age=3600",
      });
      createReadStream(file).pipe(res);
      return;
    }

    await send(res, await handler.fetch(toRequest(req)));
  } catch (error) {
    console.error("request failed", error);
    if (!res.headersSent) res.writeHead(500, { "Content-Type": "text/plain" });
    res.end("internal error");
  }
});

server.listen(PORT, HOST, () => {
  console.log(`portal http://${HOST}:${PORT}`);
});
