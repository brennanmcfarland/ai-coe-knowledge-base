import { defineConfig, devices } from '@playwright/test'

const PORT = 8799

// Specs share one backend and its fixture ontology, and the editor spec mutates it, so specs
// run serially in file-name order (01-, 02-, ...).
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    // Built frontend served by the real backend against throwaway fixture data:
    // no ClickUp, no LLM, credentials kept in memory instead of the OS keyring.
    command:
      'node e2e/prepare-data.mjs && npm run build && ' +
      `uv run --project ../backend coe-wizard --data-dir e2e/.data --port ${PORT} --static-dir dist ` +
      'serve --memory-credentials',
    url: `http://127.0.0.1:${PORT}/api/auth/status`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
})
