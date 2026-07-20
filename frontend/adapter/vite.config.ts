// @file vite.config.ts  adapter SPA 构建(H11 §一 F2:产物由后端静态托管,base=/app/)
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  plugins: [react()],
  base: '/app/',
  resolve: {
    alias: [
      {
        find: /^@gd\/ui-kit$/,
        replacement: fileURLToPath(new URL('../ui-kit/src/index.ts', import.meta.url)),
      },
      {
        find: /^@gd\/ui-kit\//,
        replacement: fileURLToPath(new URL('../ui-kit/src/', import.meta.url)),
      },
    ],
  },
  build: {
    outDir: '../../apps/adapter/web/dist',
    emptyOutDir: true,
    sourcemap: false,
  },
});
