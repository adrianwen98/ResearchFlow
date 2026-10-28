import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      ["/tasks", "/settings", "/health"].map((path) => [
        path,
        { target: process.env.WEBTASK_API_TARGET || "http://127.0.0.1:8000", changeOrigin: false },
      ]),
    ),
  },
});
