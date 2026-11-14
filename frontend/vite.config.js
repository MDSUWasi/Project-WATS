import { defineConfig } from "vite";

export default defineConfig({
  root: "src",
  base: "/",
  publicDir: false,
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:5000",
      "/frames": "http://localhost:5000",
    },
  },
});
