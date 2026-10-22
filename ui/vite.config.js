import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// The build lands in ui/dist: the Python server hands that directory to the
// browser, the image ships it as is.
export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    assetsDir: 'assets',
    // The names of the files carry a hash, so the cache can keep them.
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
  server: {
    port: 5180,
    proxy: {
      // The dev server talks to the Python service on the same machine.
      '/api': 'http://127.0.0.1:8090',
    },
  },
});
