import { defineConfig } from "vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import viteTsConfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  server: { port: 3000 },
  plugins: [
    viteTsConfigPaths({ projects: ["./tsconfig.json"] }),
    tailwindcss(),
    tanstackStart(),
    // After tanstackStart, which expects the React Refresh runtime this
    // provides. Without it the dev client entry 500s, nothing hydrates, and the
    // page renders server-side and then simply sits there.
    viteReact(),
  ],
});
