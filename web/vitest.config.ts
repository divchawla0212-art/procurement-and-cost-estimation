import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config.ts'

// Layered on top of vite.config.ts (same pattern Vitest's own docs recommend)
// so the app's plugins/resolution stay identical between `vite build` and
// `vitest run`.
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test-setup.ts'],
    },
  }),
)
