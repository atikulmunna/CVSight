import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The API reads the same variable, so the proxy follows it to another port.
const apiUrl = `http://127.0.0.1:${process.env.SHELFSIGHT_API_PORT ?? "8000"}`;

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": apiUrl,
    },
  },
  // Canvas timings are measured against the preview build, so it needs the same
  // API proxy the development server has.
  preview: {
    host: "127.0.0.1",
    port: 4173,
    proxy: {
      "/api": apiUrl,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
