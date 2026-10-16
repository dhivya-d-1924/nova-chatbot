import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    target: ["es2020", "safari14", "firefox90", "chrome90", "edge90"],
    rollupOptions: {
      output: { manualChunks: { markdown: ["react-markdown", "remark-gfm"], motion: ["framer-motion"] } },
    },
  },
  server: {
    port: 5173,
    // Same-origin in dev: cookies, CORS and SSE work without extra setup. `npm run dev -- --host` exposes the app on
    // your network for testing on phones and tablets; the backend can stay on localhost because Vite proxies /api.
    proxy: { "/api": { target: "http://localhost:5000", changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.js"],
    globals: true,
    include: ["src/**/*.test.{js,jsx}"],
    css: false,
  },
});
