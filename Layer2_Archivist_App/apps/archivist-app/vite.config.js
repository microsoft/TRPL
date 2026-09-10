import path from "path";
import { fileURLToPath } from "url";
import { dirname } from "path";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

/** @type {import('vite').UserConfig} */
export default defineConfig(({ mode }) => {
  // Extends 'process.env.*' with VITE_*-variables from '.env.(mode=production|development)'
  const env = loadEnv(mode, process.cwd(), '');
  
  // https://vitejs.dev/config/
  return {
    plugins: [react(), tailwindcss()],
    build: {
      outDir: "dist",
      emptyOutDir: true,
      sourcemap: true,
      commonjsOptions: {
        include: [/node_modules/],
        transformMixedEsModules: true,
      },
    },
    resolve: {
      preserveSymlinks: true,
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
      dedupe: ['react', 'react-dom'],
    },
    optimizeDeps: {
      include: ['react', 'react-dom', 'react-router-dom'],
      exclude: [],
    },
    server: {
      proxy: {
         "/login": {
          target: "http://127.0.0.1:8000",
          secure: false,
        },
        "/api": {
          target: "http://127.0.0.1:8000",
          secure: false,
        },
        "/ws": {
          target: "ws://127.0.0.1:8000",
          ws: true, // Enable WebSocket proxying
        },
      },
    },
    define: {
      'process.env': env,
    },
  };
});
