import { cloudflareTest } from '@cloudflare/vitest-plugin';
import { defineConfig } from 'vitest/config';
export default defineConfig({
  plugins: [cloudflareTest({
    wrangler: { configPath: './wrangler.jsonc' },
    miniflare: {
      bindings: { TEACH_INBOX_WEBHOOK_SECRET: 'test-only-secret-00000000000000000000', INTAKE_POLICIES: '{}' },
      queueConsumers: {},
    },
  })],
  test: { fileParallelism: false },
});
