const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests/browser', workers: 1, retries: 0,
  use: { baseURL: 'http://127.0.0.1:18089', browserName: 'chromium' },
  webServer: { command: 'PORT=18089 python3 server.py', url: 'http://127.0.0.1:18089/healthz', reuseExistingServer: false },
});
