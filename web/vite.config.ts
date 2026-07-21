import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { readFile } from "node:fs/promises";

const rootDir = dirname(fileURLToPath(import.meta.url));

// Dev-only static serving for the GEX snapshots the scraper writes to
// parquets/gex/<SYMBOL>_gex.json. Served fresh off disk each request (no cache)
// under /gex/<SYMBOL>.json so the March GEX overlay always sees the latest scrape.
function gexJsonServer(): Plugin {
  return {
    name: "gex-json-server",
    configureServer(server) {
      server.middlewares.use("/gex", (req, res, next) => {
        const match = /^\/([A-Za-z]+)\.json$/.exec((req.url ?? "").split("?")[0]);
        if (!match) return next();
        const symbol = match[1].toUpperCase();
        const file = resolve(rootDir, "..", "parquets", "gex", `${symbol}_gex.json`);
        readFile(file, "utf8").then(
          (data) => {
            res.setHeader("Content-Type", "application/json");
            res.setHeader("Cache-Control", "no-store");
            res.end(data);
          },
          () => {
            res.statusCode = 404;
            res.setHeader("Content-Type", "application/json");
            res.end("null");
          },
        );
      });
    },
  };
}

export default defineConfig({
  plugins: [
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
    gexJsonServer(),
  ],
  server: {
    allowedHosts: [".trycloudflare.com"],
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
      },
      "/bookmap": {
        target: "ws://127.0.0.1:8765",
        ws: true,
        rewrite: (path) => path.replace(/^\/bookmap/, ""),
      },
    },
  },
});
