import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    proxy: {
      "/projects": "http://127.0.0.1:8000",
      "/analyze": "http://127.0.0.1:8000",
      "/status": "http://127.0.0.1:8000",
      "/dsit": "http://127.0.0.1:8000",
      "/debug": "http://127.0.0.1:8000",
      "/healthz": "http://127.0.0.1:8000"
    }
  }
});
