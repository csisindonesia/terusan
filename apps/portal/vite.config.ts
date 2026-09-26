import { defineConfig } from "vite";
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
  ],
}));
