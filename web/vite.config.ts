import react from '@vitejs/plugin-react';
// vitest/config, not vite: the `test` block is not part of Vite's own schema.
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: Number(process.env.WEB_PORT ?? 5173),
    strictPort: true,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    css: false,
  },
});
