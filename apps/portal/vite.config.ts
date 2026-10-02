import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import viteTsConfigPaths from "vite-tsconfig-paths";

// Where the serving layer listens, for the dev server's proxy below. A plain
// host and port rather than anything clever: `make dev` starts the API on the
// same machine, and `scripts/tunnel.sh` starts it there too.
const apiTarget =
  process.env.API_ORIGIN ?? `http://127.0.0.1:${process.env.API_PORT ?? "8080"}`;

// Hostnames vite will answer to. It refuses one it has never heard of, which
// is right against DNS rebinding and wrong for a tunnel, whose hostname is
// three random words chosen after this file was written. The suffix is
// allowed rather than the name, so the next tunnel needs no edit.
const allowedHosts = (process.env.PORTAL_ALLOWED_HOSTS ?? ".trycloudflare.com")
  .split(",")
  .map((host) => host.trim())
  .filter(Boolean);

// What the dev server will hand out from disk.
//
// Left alone, vite serves any file under the workspace root at
// `/@fs/<absolute path>`, and the workspace root here is the whole repository:
// the lake in `.data/` (the app database with its users and password hashes
// included), the run logs in `.cache/`, everything. That is harmless on
// localhost and an open file server once `scripts/tunnel.sh` puts this port
// on a public hostname, because vite answers these requests itself and the
// API's sign-in never sees them. So it gets the portal, the docs it renders
// and the installed packages, and nothing else.
const repo = fileURLToPath(new URL("../..", import.meta.url));
const fsAllow = [
  fileURLToPath(new URL(".", import.meta.url)),
  `${repo}docs`,
  `${repo}node_modules`,
];
// Vite's own list, which setting `deny` replaces rather than extends, and then
// the data files, refused even from a directory that is allowed.
const fsDeny = [
  ".env",
  ".env.*",
  "*.{crt,pem}",
  "**/.git/**",
  "**/.data/**",
  "**/.cache/**",
  "**/*.duckdb*",
  "**/*.parquet",
];

// Where the built portal's browser code will call the API, written beside the
// build for server.mjs.
//
// The production server sends a Content-Security-Policy, and that policy has
// to name the API's origin when it is not the portal's own: the data comes
// from there, the document preview frames it, and the news screenshots are
// images from it. The origin is fixed when the bundle is built — VITE_API_URL
// is read here and baked in — so it is recorded here too, from the same value
// Vite resolved, rather than asked of the environment at start-up, where it
// can differ from what the bundle actually says.
function apiOriginFile(): Plugin {
  let origin = "";
  return {
    name: "terusan:api-origin",
    apply: "build",
    configResolved(config) {
      // The same fallback as lib/api.ts, so the policy names what the bundle
      // calls when the variable is unset.
      const url =
        (config.env.VITE_API_URL as string | undefined) ?? "http://localhost:8080";
      try {
        origin = new URL(url).origin;
      } catch {
        origin = "";
      }
    },
    closeBundle() {
      const dist = fileURLToPath(new URL("./dist", import.meta.url));
      mkdirSync(dist, { recursive: true });
      writeFileSync(`${dist}/api-origin.json`, `${JSON.stringify({ origin })}\n`);
    },
  };
}

export default defineConfig(({ command }) => ({
  // The server build is run from an image that carries no node_modules, so
  // everything it imports has to be inside the bundle. Vite externalizes
  // dependencies in an SSR build by default, which leaves `import "react"` in
  // dist/server/server.js and a container that exits on the first line.
  //
  // Build only. In dev the same setting sends CommonJS dependencies through
  // the module runner, which evaluates them as ESM: react/index.js reaches
  // `module.exports` and SSR dies with `module is not defined`.
  ssr: {
    noExternal: command === "build" ? true : undefined,
  },
  server: {
    port: 3000,
    allowedHosts,
    fs: { allow: fsAllow, deny: fsDeny },
    // The API, served from the portal's own origin.
    //
    // A tunnel publishes one hostname. Without this the page would arrive from
    // it and then ask `localhost:8080` for its data, which is a machine the
    // reader does not have. Proxying puts both behind one origin, which also
    // keeps the session cookie same-site — two tunnel hostnames are not, since
    // `trycloudflare.com` is a public suffix, and the browser would drop it.
    proxy: {
      "/v1": { target: apiTarget, ws: true },
      "/healthz": { target: apiTarget },
      "/readyz": { target: apiTarget },
    },
  },
  build: {
    // The 32px favicon is under Vite's 4kB inline limit, and a `data:` URI in
    // a `<link rel="icon">` is read by browsers but not by everything that
    // crawls a page for one. Keep icons as real, cacheable files.
    assetsInlineLimit: (filePath) => (filePath.includes("favicon") ? false : undefined),
  },
  plugins: [
    viteTsConfigPaths({ projects: ["./tsconfig.json"] }),
    tailwindcss(),
    tanstackStart(),
    // After tanstackStart, which expects the React Refresh runtime this
    // provides. Without it the dev client entry 500s, nothing hydrates, and the
    // page renders server-side and then simply sits there.
    viteReact(),
    apiOriginFile(),
  ],
}));
