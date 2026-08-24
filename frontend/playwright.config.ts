import { defineConfig } from '@playwright/test'
import { existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = dirname(fileURLToPath(import.meta.url))
const repositoryRoot = resolve(frontendRoot, '..')
const repositoryPython = resolve(repositoryRoot, '.venv/bin/python')
const python = process.env.SAGE_E2E_PYTHON
  || (existsSync(repositoryPython) ? repositoryPython : 'python3')

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  outputDir: '../output/playwright/test-results',
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 10_000 },
  reporter: [
    ['line'],
    ['html', { outputFolder: '../output/playwright/report', open: 'never' }],
  ],
  use: {
    baseURL: 'http://127.0.0.1:4173',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  webServer: [
    {
      command: `${JSON.stringify(python)} -m tests.e2e.learning_assistant_server`,
      cwd: repositoryRoot,
      env: {
        PYTHONPATH: `${resolve(repositoryRoot, 'packages/sage_harness')}:${repositoryRoot}`,
      },
      url: 'http://127.0.0.1:8765/health',
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 4173 --strictPort',
      cwd: frontendRoot,
      env: { VITE_API_PROXY_TARGET: 'http://127.0.0.1:8765' },
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
})
