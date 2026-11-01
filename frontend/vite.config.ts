import { fileURLToPath } from "node:url"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Relative asset paths: the Python server serves the build from its own static folder.
  base: "./",
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    // `npm run dev` talks to a running `jobscout start` on its default port.
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  build: {
    outDir: "../src/jobscout/server/static",
    emptyOutDir: true,
  },
  test: {
    include: ["src/**/*.test.ts"],
  },
})
